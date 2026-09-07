"""AnalyticsSession: the single entry point into the core for any UI."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

from haa.config import HaaConfig
from haa.core.agents.orchestrator import ORCHESTRATOR_PROMPT
from haa.core.agents.registry import build_agents
from haa.core.hooks import make_pretooluse_hook
from haa.core.telemetry import SessionTelemetry
from haa.core.tools.datatools import DATA_TOOL_NAMES, DataToolbox, build_data_server
from haa.core.tools.sourcetools import SOURCE_TOOL_NAMES, SourceToolbox, build_sources_server


class BudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class SessionEvent:
    kind: str  # "text" | "tool" | "result" | "error"
    text: str
    cost: float | None = None


def _block_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        )
    return str(content)


def events_from_message(msg: object, telemetry: SessionTelemetry) -> list[SessionEvent]:
    events: list[SessionEvent] = []
    kind = type(msg).__name__
    if kind == "AssistantMessage":
        for block in getattr(msg, "content", None) or []:
            bkind = type(block).__name__
            if bkind == "TextBlock":
                telemetry.log("assistant_text", text=block.text)
                events.append(SessionEvent("text", block.text))
            elif bkind == "ToolUseBlock":
                telemetry.log("tool_use", tool=block.name, input=block.input)
                events.append(SessionEvent("tool", block.name))
    elif kind == "UserMessage":
        for block in getattr(msg, "content", None) or []:
            if type(block).__name__ == "ToolResultBlock":
                telemetry.log("tool_result", content=_block_text(block.content))
    elif kind == "ResultMessage":
        cost = getattr(msg, "total_cost_usd", None)
        subtype = getattr(msg, "subtype", None)
        is_error = getattr(msg, "is_error", False)
        usage = getattr(msg, "usage", None)
        telemetry.log(
            "result", cost_usd=cost, subtype=subtype, is_error=bool(is_error), usage=usage
        )
        if is_error or (subtype and "error" in str(subtype)):
            spent = f"{cost:.4f}" if isinstance(cost, (int, float)) else "unknown"
            if subtype == "error_max_budget_usd":
                text = (
                    f"budget exhausted mid-turn (spent so far: ${spent}); "
                    "partial results above"
                )
            else:
                text = f"error during analysis (subtype={subtype}, spent so far: ${spent})"
            events.append(SessionEvent("error", text, cost=cost))
        else:
            text = f"cost=${cost:.4f}" if isinstance(cost, (int, float)) else "done"
            events.append(SessionEvent("result", text, cost=cost))
    return events


class AnalyticsSession:
    def __init__(self, config: HaaConfig) -> None:
        self.config = config
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        self.telemetry = SessionTelemetry(config.logs_dir / f"session-{stamp}.jsonl")
        self._toolbox = DataToolbox(config, self.telemetry)
        self._server = build_data_server(self._toolbox)
        self._source_toolbox = SourceToolbox(config, self.telemetry)
        self._sources_server = build_sources_server(self._source_toolbox)
        self._spent_usd = 0.0
        self._client = None

    def build_options(self):
        from claude_agent_sdk import ClaudeAgentOptions, HookMatcher

        hook = make_pretooluse_hook(
            self.config,
            on_block=lambda tool, reason: self.telemetry.log("pii_block", tool=tool, reason=reason),
        )
        return ClaudeAgentOptions(
            model=self.config.orchestrator_model,
            system_prompt=ORCHESTRATOR_PROMPT,
            agents=build_agents(self.config),
            mcp_servers={"data": self._server, "sources": self._sources_server},
            allowed_tools=["Agent", "Task", *DATA_TOOL_NAMES, *SOURCE_TOOL_NAMES],
            disallowed_tools=["WebSearch", "WebFetch"],
            permission_mode="dontAsk",
            hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[hook])]},
            max_budget_usd=self.config.max_budget_usd,
            cwd=str(self.config.workspace),
            setting_sources=[],
            strict_mcp_config=True,
        )

    def _check_budget(self) -> None:
        if self._spent_usd >= self.config.max_budget_usd:
            raise BudgetExceeded(
                f"Session budget exhausted: ${self._spent_usd:.2f} of "
                f"${self.config.max_budget_usd:.2f}. Start a new session or raise "
                "max_budget_usd in config.toml."
            )

    async def __aenter__(self) -> AnalyticsSession:
        from claude_agent_sdk import ClaudeSDKClient

        self._client = ClaudeSDKClient(options=self.build_options())
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._client is not None:
            await self._client.__aexit__(*exc_info)
            self._client = None

    async def ask(self, question: str) -> AsyncIterator[SessionEvent]:
        assert self._client is not None, "use `async with AnalyticsSession(cfg)`"
        self._check_budget()
        self._toolbox.reset_failures()  # 3-failure limit is per user question
        self.telemetry.log("user_message", text=question)
        await self._client.query(question)
        async for msg in self._client.receive_response():
            for event in events_from_message(msg, self.telemetry):
                if event.kind in ("result", "error"):
                    self._spent_usd += event.cost or 0.0
                yield event
