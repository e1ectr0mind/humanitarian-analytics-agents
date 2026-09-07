"""Append-only JSONL session log: every turn, tool call, executed code, and cost."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path


class SessionTelemetry:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._events = 0
        self._tool_calls = 0
        self._code_runs = 0
        self._pii_blocks = 0
        self._cost_usd = 0.0

    def log(self, kind: str, **payload: object) -> None:
        record = {"ts": datetime.now(UTC).isoformat(), "kind": kind, **payload}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=repr) + "\n")
        self._events += 1
        if kind == "tool_use":
            self._tool_calls += 1
        elif kind == "code_executed":
            self._code_runs += 1
        elif kind == "pii_block":
            self._pii_blocks += 1
        elif kind == "result":
            cost = payload.get("cost_usd")
            if isinstance(cost, (int, float)):
                self._cost_usd += float(cost)

    def summary(self) -> dict:
        return {
            "events": self._events,
            "tool_calls": self._tool_calls,
            "code_runs": self._code_runs,
            "pii_blocks": self._pii_blocks,
            "cost_usd": round(self._cost_usd, 6),
        }
