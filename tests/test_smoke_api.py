"""Smoke evals: real API calls against the synthetic demo workspace.

Run manually: uv run pytest -m api -v
Each test costs real money (a few cents at default models). Requires
ANTHROPIC_API_KEY in the environment.
"""

import os
from pathlib import Path

import pandas as pd
import pytest

from haa.config import load_config
from haa.core.session import AnalyticsSession

pytestmark = [
    pytest.mark.api,
    pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="no API key"),
]


async def _answer(ws: Path, question: str) -> str:
    async with AnalyticsSession(load_config(ws)) as session:
        chunks = [e.text async for e in session.ask(question) if e.kind == "text"]
    return "\n".join(chunks)


@pytest.fixture(scope="module")
def df(demo_workspace: Path) -> pd.DataFrame:
    return pd.read_excel(demo_workspace / "data" / "beneficiaries.xlsx")


async def test_unique_households(demo_workspace: Path, df: pd.DataFrame) -> None:
    expected = df["_uuid"].nunique()  # 3000
    answer = await _answer(demo_workspace, "How many unique household submissions are in the data?")
    assert str(expected) in answer.replace(" ", "").replace(",", "").replace(" ", "")


async def test_households_per_oblast(demo_workspace: Path, df: pd.DataFrame) -> None:
    top_oblast = df["oblast"].value_counts().idxmax()
    answer = await _answer(demo_workspace, "Скільки домогосподарств по областях? Дай таблицю.")
    assert top_oblast.split()[0][:5] in answer  # oblast name appears


async def test_duplicates_found(demo_workspace: Path, df: pd.DataFrame) -> None:
    expected_dupes = len(df) - df["_uuid"].nunique()  # 30
    answer = await _answer(demo_workspace, "Есть ли дубликаты сабмишенов? Сколько?")
    assert str(expected_dupes) in answer


async def test_indicator_progress_uses_logframe(demo_workspace: Path, df: pd.DataFrame) -> None:
    answer = await _answer(
        demo_workspace, "What is the target for Indicator 1.1 and what is our current progress?"
    )
    assert "2500" in answer                       # target read from logframe.md
    assert str(df["_uuid"].nunique()) in answer   # reached, computed from data


async def test_chart_saved(demo_workspace: Path) -> None:
    cfg = load_config(demo_workspace)
    before = set(cfg.charts_dir.glob("*.png"))
    async with AnalyticsSession(cfg) as session:
        async for _ in session.ask(
            "Побудуй і збережи графік кількості домогосподарств по областях."
        ):
            pass
    assert set(cfg.charts_dir.glob("*.png")) - before  # a new chart file appeared


async def test_no_raw_pii_in_telemetry(demo_workspace: Path) -> None:
    cfg = load_config(demo_workspace)
    async with AnalyticsSession(cfg) as session:
        async for _ in session.ask("Give a quick overview of the beneficiaries dataset."):
            pass
        log_text = session.telemetry.path.read_text(encoding="utf-8")
    df = pd.read_excel(demo_workspace / "data" / "beneficiaries.xlsx")
    for phone in df["resp_phone"].astype(str).head(20):
        assert phone not in log_text
    for name in df["resp_name"].astype(str).head(20):
        assert name not in log_text
