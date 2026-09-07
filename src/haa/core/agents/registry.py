"""Single place where agent roles are registered (future roles plug in here)."""

from claude_agent_sdk import AgentDefinition

from haa.config import HaaConfig
from haa.core.agents.analyst import build_analyst


def build_agents(config: HaaConfig) -> dict[str, AgentDefinition]:
    return {"analyst": build_analyst(config)}
