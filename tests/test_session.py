from pathlib import Path

import pytest

from haa.config import load_config
from haa.core.session import (
    AnalyticsSession,
    BudgetExceeded,
    SessionEvent,
    events_from_message,
)
from haa.core.telemetry import SessionTelemetry
from haa.core.tools.datatools import DATA_TOOL_NAMES


# --- fakes whose class names mimic SDK message/block types -----------------
class TextBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class ToolUseBlock:
    def __init__(self, name: str, input: dict) -> None:
        self.name = name
        self.input = input


class ToolResultBlock:
    def __init__(self, content) -> None:
        self.content = content


class AssistantMessage:
    def __init__(self, content: list) -> None:
        self.content = content


class UserMessage:
    def __init__(self, content: list) -> None:
        self.content = content


class ResultMessage:
    def __init__(self, total_cost_usd: float) -> None:
        self.total_cost_usd = total_cost_usd


def _tele(tmp_path: Path) -> SessionTelemetry:
    return SessionTelemetry(tmp_path / "t.jsonl")


def test_text_and_tool_events(tmp_path: Path) -> None:
    tele = _tele(tmp_path)
    msg = AssistantMessage(
        [
            TextBlock("Looking at the data."),
            ToolUseBlock("mcp__data__run_analysis", {"code": "print(1)"}),
        ]
    )
    events = events_from_message(msg, tele)
    assert events == [
        SessionEvent("text", "Looking at the data."),
        SessionEvent("tool", "mcp__data__run_analysis"),
    ]
    assert tele.summary()["tool_calls"] == 1


def test_tool_results_logged_not_yielded(tmp_path: Path) -> None:
    tele = _tele(tmp_path)
    events = events_from_message(UserMessage([ToolResultBlock("3000")]), tele)
    assert events == []
    assert "3000" in (tmp_path / "t.jsonl").read_text()


def test_result_event_with_cost(tmp_path: Path) -> None:
    tele = _tele(tmp_path)
    events = events_from_message(ResultMessage(0.042), tele)
    assert events[0].kind == "result"
    assert "0.042" in events[0].text
    assert tele.summary()["cost_usd"] == pytest.approx(0.042)


def test_unknown_message_ignored(tmp_path: Path) -> None:
    assert events_from_message(object(), _tele(tmp_path)) == []


def test_build_options(demo_workspace: Path) -> None:
    session = AnalyticsSession(load_config(demo_workspace))
    opts = session.build_options()
    assert opts.model == "claude-opus-5"
    assert "analyst" in opts.agents
    assert "data" in opts.mcp_servers
    assert set(DATA_TOOL_NAMES) <= set(opts.allowed_tools)
    assert "Task" in opts.allowed_tools
    assert opts.permission_mode == "dontAsk"
    assert opts.hooks  # PreToolUse PII hook registered


def test_budget_guard(demo_workspace: Path) -> None:
    session = AnalyticsSession(load_config(demo_workspace))
    session._spent_usd = 2.5  # over the $2 default
    with pytest.raises(BudgetExceeded, match="budget"):
        session._check_budget()
