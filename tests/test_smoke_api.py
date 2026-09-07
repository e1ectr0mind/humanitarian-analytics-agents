"""Smoke evals: real API calls against the synthetic demo workspace.

Run manually: uv run pytest -m api -v
Each test costs real money (a few cents at default models). Requires
ANTHROPIC_API_KEY in the environment.
"""

import os
import re
from pathlib import Path

import pandas as pd
import pytest

from haa.config import load_config
from haa.core.session import AnalyticsSession


def _numbers(text: str) -> set[int]:
    """Extract whole numbers, tolerating '3 000' / '3,000' style thousand separators."""
    cleaned = re.sub(r"(?<=\d)[\s ,](?=\d\d\d\b)", "", text)
    return {int(n) for n in re.findall(r"\d+", cleaned)}

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
    assert expected in _numbers(answer)


async def test_households_per_oblast(demo_workspace: Path, df: pd.DataFrame) -> None:
    top_oblast = df["oblast"].value_counts().idxmax()
    raw_count = int(df["oblast"].value_counts().max())
    dedup_count = int(df.drop_duplicates("_uuid")["oblast"].value_counts()[top_oblast])
    answer = await _answer(demo_workspace, "Скільки домогосподарств по областях? Дай таблицю.")
    assert top_oblast.split()[0][:5] in answer
    nums = _numbers(answer)
    assert raw_count in nums or dedup_count in nums


async def test_duplicates_found(demo_workspace: Path, df: pd.DataFrame) -> None:
    expected_dupes = len(df) - df["_uuid"].nunique()  # 30
    answer = await _answer(demo_workspace, "Есть ли дубликаты сабмишенов? Сколько?")
    assert expected_dupes in _numbers(answer)


async def test_indicator_progress_uses_logframe(demo_workspace: Path, df: pd.DataFrame) -> None:
    answer = await _answer(
        demo_workspace, "What is the target for Indicator 1.1 and what is our current progress?"
    )
    nums = _numbers(answer)
    assert 2500 in nums                      # target read from logframe.md
    assert df["_uuid"].nunique() in nums     # reached, computed from data


async def test_chart_saved(demo_workspace: Path) -> None:
    cfg = load_config(demo_workspace)
    # Earlier evals may have saved charts proactively; the agent may legitimately
    # reuse a filename and overwrite one, so track mtimes, not just names.
    before = {p.name: p.stat().st_mtime for p in cfg.charts_dir.glob("*.png")}
    async with AnalyticsSession(cfg) as session:
        async for _ in session.ask(
            "Побудуй і збережи графік кількості домогосподарств по областях."
        ):
            pass
    after = {p.name: p.stat().st_mtime for p in cfg.charts_dir.glob("*.png")}
    new_or_updated = [
        name
        for name, mtime in after.items()
        if name not in before or mtime > before[name]
    ]
    assert new_or_updated  # a chart file was created or refreshed by this question


async def test_no_raw_pii_in_telemetry(demo_workspace: Path) -> None:
    cfg = load_config(demo_workspace)
    async with AnalyticsSession(cfg) as session:
        async for _ in session.ask("Give a quick overview of the beneficiaries dataset."):
            pass
        log_text = session.telemetry.path.read_text(encoding="utf-8")
    df = pd.read_excel(demo_workspace / "data" / "beneficiaries.xlsx")
    for phone in df["resp_phone"].astype(str):
        assert phone not in log_text
    for name in df["resp_name"].astype(str):
        assert name not in log_text
