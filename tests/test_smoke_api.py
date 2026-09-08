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
    cfg = load_config(demo_workspace)

    import yaml

    from haa.indicators.registry import registry_path

    registry_path(cfg.workspace).write_text(
        yaml.safe_dump(
            {
                "indicators": [
                    {
                        "code": "1.1",
                        "name": {"uk": "Домогосподарства", "en": "Households reached"},
                        "definition": "Unique households with at least one service",
                        "target": {"value": 2500, "unit": "households"},
                        "source": "beneficiaries",
                        "measure": {
                            "dataset": "beneficiaries",
                            "aggregation": "count_unique",
                            "field": "_uuid",
                            "filter": None,
                        },
                    }
                ]
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    async with AnalyticsSession(cfg) as session:
        chunks = [
            e.text
            async for e in session.ask(
                "What is the target for Indicator 1.1 and what is our current progress?"
            )
            if e.kind == "text"
        ]
    answer = "\n".join(chunks)

    nums = _numbers(answer)
    assert 2500 in nums                      # target read from logframe.md
    assert df["_uuid"].nunique() in nums     # reached, computed from data

    log = session.telemetry.path.read_text(encoding="utf-8")
    assert "mcp__forms__read_indicators" in log, "analyst did not consult the registry"


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


async def test_cleaner_produces_clean_copy(demo_workspace: Path, df: pd.DataFrame) -> None:
    import dataclasses

    cfg = dataclasses.replace(load_config(demo_workspace), max_budget_usd=4.0)
    async with AnalyticsSession(cfg) as session:
        async for _ in session.ask(
            "Почисти датасет beneficiaries: дубли, невозможные значения, даты, "
            "разнобой категорий. Сырой файл не трогай."
        ):
            pass
        log_text = session.telemetry.path.read_text(encoding="utf-8")

    clean_path = cfg.data_dir / "beneficiaries_clean.xlsx"
    assert clean_path.exists(), "clean copy not created"
    clean = pd.read_excel(clean_path)
    assert len(clean) == df["_uuid"].nunique()          # 3000: duplicates dropped
    assert clean["_uuid"].is_unique
    assert not (clean["head_age"] == 999).any()          # impossible ages gone
    assert "resp_phone" in clean.columns                 # PII columns preserved

    reports = list(cfg.reports_dir.glob("*cleaning_report*.md"))
    assert reports, "cleaning report not created"
    report_text = reports[0].read_text(encoding="utf-8")
    assert (len(df) - df["_uuid"].nunique()) in _numbers(report_text)  # 30 duplicates reported

    for value in (*df["resp_phone"].astype(str), *df["resp_name"].astype(str)):
        assert value not in log_text

    raw_again = pd.read_excel(demo_workspace / "data" / "beneficiaries.xlsx")
    pd.testing.assert_frame_equal(raw_again, df)         # raw byte-identical


async def test_indicator_extraction_builds_registry(demo_workspace: Path) -> None:
    import yaml

    from haa.indicators.registry import registry_path, validate_registry

    cfg = load_config(demo_workspace)
    registry_path(cfg.workspace).unlink(missing_ok=True)
    async with AnalyticsSession(cfg) as session:
        async for _ in session.ask(
            "Витягни індикатори з логфрейму проєкту у реєстр індикаторів."
        ):
            pass

    data = yaml.safe_load(registry_path(cfg.workspace).read_text(encoding="utf-8"))
    assert validate_registry(data) == []
    codes = {str(i["code"]) for i in data["indicators"]}
    assert "1.1" in codes
    target = next(i for i in data["indicators"] if str(i["code"]) == "1.1")["target"]
    assert target["value"] == 2500  # the value stated in demo logframe.md


async def test_designer_produces_compiling_form(demo_workspace: Path) -> None:
    from haa.forms.compiler import compile_check

    cfg = load_config(demo_workspace)
    async with AnalyticsSession(cfg) as session:
        async for _ in session.ask(
            "Створи форму пост-дистрибуційного моніторингу 'pdm': згода, стать і вік "
            "голови домогосподарства, отримані послуги, задоволеність."
        ):
            pass

    xlsx = cfg.forms_dir / "pdm.xlsx"
    assert xlsx.exists(), "designer did not produce the XLSForm"
    assert (cfg.forms_dir / "pdm.form.yaml").exists()
    assert compile_check(xlsx) == []

    import openpyxl

    wb = openpyxl.load_workbook(xlsx)
    header = [c.value for c in wb["survey"][1]]
    assert "label::Українська (uk)" in header and "label::English (en)" in header

    question_names = [
        str(row[1]).lower()
        for row in wb["survey"].iter_rows(min_row=2, values_only=True)
        if row[1] and str(row[0]) not in ("begin_group", "end_group")
    ]
    tokens = {t for name in question_names for t in name.split("_")}

    consent_found = any(
        "consent" in name or "zgoda" in name or "згода" in name
        for name in question_names
    )
    assert consent_found, f"no consent question found: {question_names}"
    sadd_tokens = {"sex", "gender", "stat", "статі", "стать"}
    assert tokens & sadd_tokens, f"no sex/gender question found: {question_names}"
