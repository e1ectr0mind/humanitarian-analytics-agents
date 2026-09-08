from pathlib import Path

from haa.config import load_config
from haa.core.agents.cleaner import CLEANER_PROMPT, build_cleaner
from haa.core.agents.registry import build_agents
from haa.core.tools.datatools import DATA_TOOL_NAMES


def _cfg(tmp_path: Path):
    return load_config(tmp_path)


def test_cleaner_definition(tmp_path: Path) -> None:
    agent = build_cleaner(_cfg(tmp_path))
    assert agent.model == "claude-opus-5"
    assert set(agent.tools) == set(DATA_TOOL_NAMES)
    assert agent.prompt == CLEANER_PROMPT


def test_cleaner_model_configurable(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text('cleaner_model = "claude-sonnet-5"', encoding="utf-8")
    assert build_cleaner(_cfg(tmp_path)).model == "claude-sonnet-5"


def test_registry_has_all_roles(tmp_path: Path) -> None:
    assert set(build_agents(_cfg(tmp_path))) == {"analyst", "cleaner", "designer"}


def test_cleaner_prompt_discipline() -> None:
    for needle in (
        "_clean.xlsx", "cleaning_report", "REPORTS_DIR", "include_pii=True",
        "never print", "unique", "raw file", "never fabricate",
    ):
        assert needle.lower() in CLEANER_PROMPT.lower(), needle
