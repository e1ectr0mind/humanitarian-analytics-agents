from pathlib import Path

from haa.config import load_config
from haa.core.agents.analyst import build_analyst
from haa.core.agents.designer import DESIGNER_PROMPT, build_designer
from haa.core.agents.registry import build_agents
from haa.core.tools.formtools import FORM_TOOL_NAMES


def _cfg(tmp_path: Path):
    return load_config(tmp_path)


def test_designer_definition(tmp_path: Path) -> None:
    agent = build_designer(_cfg(tmp_path))
    assert agent.model == "claude-opus-5"
    assert set(FORM_TOOL_NAMES) <= set(agent.tools)
    assert "mcp__data__read_project_doc" in agent.tools
    assert "mcp__data__run_analysis" not in agent.tools
    assert set(agent.mcpServers) == {"forms", "data"}
    assert agent.prompt == DESIGNER_PROMPT


def test_designer_model_configurable(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text('designer_model = "claude-sonnet-5"', encoding="utf-8")
    assert build_designer(_cfg(tmp_path)).model == "claude-sonnet-5"


def test_registry_has_all_roles(tmp_path: Path) -> None:
    assert set(build_agents(_cfg(tmp_path))) == {"analyst", "cleaner", "designer", "reporter"}


def test_analyst_can_read_indicators(tmp_path: Path) -> None:
    agent = build_analyst(_cfg(tmp_path))
    assert "mcp__forms__read_indicators" in agent.tools
    assert "forms" in agent.mcpServers


def test_designer_prompt_discipline() -> None:
    for needle in (
        "consent", "sadd", "uk", "en", "save_form", "pyxform",
        "read_indicators", "never fabricate", "upload",
    ):
        assert needle.lower() in DESIGNER_PROMPT.lower(), needle


def test_designer_prompt_carries_registry_schema() -> None:
    for needle in (
        'code: "1.1"',
        "verbatim",
        "never invent",
        "source: beneficiaries",
        "count | count_unique | sum | percent",
        "numerator",
        "numerator: {field: _uuid,",
        "count distinct non-null values of their `field`",
        "must be real column names from profile_dataset",
        ".isna(), .notna(), .isin([...]), .str.contains/startswith/endswith(...)",
        "backticks for column names with spaces",
    ):
        assert needle in DESIGNER_PROMPT, needle
    assert "numerator: {field: head_sex" not in DESIGNER_PROMPT
