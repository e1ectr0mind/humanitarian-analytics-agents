import json
from pathlib import Path

from haa.core.telemetry import SessionTelemetry


def test_appends_jsonl(tmp_path: Path) -> None:
    t = SessionTelemetry(tmp_path / "s.jsonl")
    t.log("user_message", text="hi")
    t.log("tool_use", tool="mcp__data__run_analysis", input={"code": "print(1)"})
    lines = [json.loads(x) for x in (tmp_path / "s.jsonl").read_text().splitlines()]
    assert [x["kind"] for x in lines] == ["user_message", "tool_use"]
    assert all("ts" in x for x in lines)


def test_summary_counts(tmp_path: Path) -> None:
    t = SessionTelemetry(tmp_path / "s.jsonl")
    t.log("tool_use", tool="a")
    t.log("code_executed", code="print(1)")
    t.log("pii_block", tool="Read", reason="nope")
    t.log("result", cost_usd=0.0123)
    t.log("result", cost_usd=0.02)
    s = t.summary()
    assert s["events"] == 5
    assert s["tool_calls"] == 1
    assert s["code_runs"] == 1
    assert s["pii_blocks"] == 1
    assert abs(s["cost_usd"] - 0.0323) < 1e-9


def test_non_serializable_payload_degrades_gracefully(tmp_path: Path) -> None:
    t = SessionTelemetry(tmp_path / "s.jsonl")
    t.log("tool_use", tool="x", input={"weird": object()})
    line = json.loads((tmp_path / "s.jsonl").read_text().splitlines()[0])
    assert line["kind"] == "tool_use"  # logged via repr fallback, not crashed
