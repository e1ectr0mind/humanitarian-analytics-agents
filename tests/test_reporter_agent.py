from pathlib import Path

from haa.config import load_config
from haa.core.agents.reporter import REPORTER_PROMPT, build_reporter
from haa.core.tools.reporttools import REPORT_TOOL_NAMES


def _cfg(tmp_path: Path):
    return load_config(tmp_path)


def test_reporter_definition(tmp_path: Path) -> None:
    agent = build_reporter(_cfg(tmp_path))
    assert agent.model == "claude-opus-5"
    assert set(REPORT_TOOL_NAMES) <= set(agent.tools)
    assert "mcp__forms__read_indicators" in agent.tools
    assert "mcp__data__profile_dataset" in agent.tools
    assert "mcp__data__read_project_doc" in agent.tools
    assert "mcp__data__run_analysis" not in agent.tools
    assert set(agent.mcpServers) == {"reports", "forms", "data"}
    assert agent.prompt == REPORTER_PROMPT


def test_reporter_model_configurable(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text('reporter_model = "claude-sonnet-5"', encoding="utf-8")
    assert build_reporter(_cfg(tmp_path)).model == "claude-sonnet-5"


def test_reporter_prompt_discipline() -> None:
    text = REPORTER_PROMPT.lower()
    for needle in (
        "never compute", "read_indicators", "build_indicator_report", "not computable",
        "designer", "profile_dataset", "pii", "confirmation", "all records",
        "user's language",
    ):
        assert needle in text, needle


def test_reporter_prompt_mentions_clean_copy_guidance() -> None:
    text = REPORTER_PROMPT.lower()
    for needle in ("clean copy", "re-running the cleaning", "_clean"):
        assert needle in text, needle


def test_reporter_prompt_carries_5w_schema() -> None:
    for needle in (
        "exactly these keys", "fixed:", "where: [oblast, raion, hromada]",
        "granularity: month", "month | none", 'split: " "', "id_field: _uuid",
        "low-cardinality SADD columns only", "at most 30 distinct values",
    ):
        assert needle in REPORTER_PROMPT, needle
