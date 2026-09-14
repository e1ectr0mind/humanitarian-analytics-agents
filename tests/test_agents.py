from pathlib import Path

from haa.config import load_config
from haa.core.agents.analyst import ANALYST_PROMPT, build_analyst
from haa.core.agents.orchestrator import ORCHESTRATOR_PROMPT
from haa.core.agents.registry import build_agents
from haa.core.tools.datatools import DATA_TOOL_NAMES


def _cfg(tmp_path: Path):
    return load_config(tmp_path)


def test_analyst_definition_fields(tmp_path: Path) -> None:
    agent = build_analyst(_cfg(tmp_path))
    assert agent.model == "claude-opus-5"
    assert set(DATA_TOOL_NAMES) <= set(agent.tools)
    assert "mcp__forms__read_indicators" in agent.tools
    assert "mcp__reports__compute_indicators" in agent.tools
    assert "reports" in agent.mcpServers
    assert agent.prompt == ANALYST_PROMPT
    assert "data" in agent.description.lower()


def test_analyst_model_configurable(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text('analyst_model = "claude-sonnet-5"', encoding="utf-8")
    assert build_analyst(_cfg(tmp_path)).model == "claude-sonnet-5"


def test_registry(tmp_path: Path) -> None:
    agents = build_agents(_cfg(tmp_path))
    assert set(agents) == {"analyst", "cleaner", "designer", "reporter"}


def test_prompts_carry_discipline() -> None:
    for needle in (
        "never fabricate", "SADD", "load_dataset", "aggregat", "read_indicators",
        "compute_indicators",
    ):
        assert needle.lower() in ANALYST_PROMPT.lower()
    for needle in ("delegate", "analyst", "honest", "cleaner", "pull", "designer", "reporter"):
        assert needle.lower() in ORCHESTRATOR_PROMPT.lower()
