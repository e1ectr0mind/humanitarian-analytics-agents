"""Single place where agent roles are registered (future roles plug in here)."""

from claude_agent_sdk import AgentDefinition

from haa.config import HaaConfig
from haa.core.agents.analyst import build_analyst
from haa.core.agents.cleaner import build_cleaner
from haa.core.agents.designer import build_designer


def build_agents(config: HaaConfig) -> dict[str, AgentDefinition]:
    return {
        "analyst": build_analyst(config),
        "cleaner": build_cleaner(config),
        "designer": build_designer(config),
    }
