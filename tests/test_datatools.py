import json
import os
import time
from pathlib import Path

import pandas as pd

from haa.config import load_config
from haa.core.telemetry import SessionTelemetry
from haa.core.tools.datatools import DATA_TOOL_NAMES, DataToolbox


def _toolbox(ws: Path) -> DataToolbox:
    cfg = load_config(ws)
    return DataToolbox(cfg, SessionTelemetry(cfg.logs_dir / "t.jsonl"))


def test_tool_names_constant() -> None:
    assert DATA_TOOL_NAMES == [
        "mcp__data__list_datasets",
        "mcp__data__profile_dataset",
        "mcp__data__run_analysis",
        "mcp__data__list_project_docs",
        "mcp__data__read_project_doc",
    ]


def test_list_datasets(demo_workspace: Path) -> None:
    out = _toolbox(demo_workspace).list_datasets()
    assert "beneficiaries" in out


def test_profile_contains_schema_not_pii(demo_workspace: Path) -> None:
    out = _toolbox(demo_workspace).profile_dataset("beneficiaries")
    profile = json.loads(out)
    names = [c["name"] for c in profile["columns"]]
    assert "oblast" in names and "resp_phone" in names
    assert "+380" not in out


def test_profile_unknown_dataset_friendly(demo_workspace: Path) -> None:
    out = _toolbox(demo_workspace).profile_dataset("nope")
    assert "Unknown dataset" in out and "beneficiaries" in out


def test_run_analysis_happy_path(demo_workspace: Path) -> None:
    tb = _toolbox(demo_workspace)
    out = tb.run_analysis('df = load_dataset("beneficiaries")\nprint(df["_uuid"].nunique())')
    assert "3000" in out


def test_run_analysis_strips_pii_and_redacts(demo_workspace: Path) -> None:
    tb = _toolbox(demo_workspace)
    out = tb.run_analysis("print('+380671234567')")
    assert "+380671234567" not in out
    assert "[REDACTED]" in out


def test_run_analysis_error_returns_stderr(demo_workspace: Path) -> None:
    out = _toolbox(demo_workspace).run_analysis("1/0")
    assert "ZeroDivisionError" in out


def test_run_analysis_logs_code(demo_workspace: Path) -> None:
    cfg = load_config(demo_workspace)
    tele = SessionTelemetry(cfg.logs_dir / "log2.jsonl")
    DataToolbox(cfg, tele).run_analysis("print(1)")
    assert tele.summary()["code_runs"] == 1


def test_docs_tools(demo_workspace: Path) -> None:
    tb = _toolbox(demo_workspace)
    assert "logframe.md" in tb.list_project_docs()
    assert "Indicator 1.1" in tb.read_project_doc("logframe.md")
    assert "not found" in tb.read_project_doc("missing.md")


def test_three_consecutive_failures_short_circuit(demo_workspace: Path) -> None:
    tb = _toolbox(demo_workspace)
    for _ in range(3):
        tb.run_analysis("1/0")
    out = tb.run_analysis("print('should not run')")
    assert "consecutive" in out.lower()
    assert "should not run" not in out
    tb.reset_failures()
    assert "1" in tb.run_analysis("print(1)")


def test_success_resets_failure_counter(demo_workspace: Path) -> None:
    tb = _toolbox(demo_workspace)
    tb.run_analysis("1/0")
    tb.run_analysis("1/0")
    tb.run_analysis("print('ok')")  # success resets
    tb.run_analysis("1/0")
    out = tb.run_analysis("print(2)")
    assert "2" in out  # still executing: no 3-in-a-row streak


def test_build_data_server_importable(demo_workspace: Path) -> None:
    from haa.core.tools.datatools import build_data_server

    server = build_data_server(_toolbox(demo_workspace))
    assert server is not None


def test_corrupt_dataset_friendly(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    (cfg.data_dir / "broken.xlsx").write_bytes(b"not a real xlsx")
    tb = DataToolbox(cfg, SessionTelemetry(cfg.logs_dir / "t.jsonl"))
    out = tb.profile_dataset("broken")
    assert "Failed to read dataset" in out
    assert "1" in tb.run_analysis("print(1)")  # preamble must not crash


def test_profile_cache_invalidated_on_mtime(demo_workspace: Path, tmp_path: Path) -> None:
    import shutil

    ws = tmp_path
    (ws / "data").mkdir()
    (ws / "project_docs").mkdir()
    shutil.copy(demo_workspace / "data" / "beneficiaries.xlsx", ws / "data" / "beneficiaries.xlsx")
    tb = _toolbox(ws)
    first = tb.profile_dataset("beneficiaries")
    df = pd.read_excel(ws / "data" / "beneficiaries.xlsx").head(10)
    target = ws / "data" / "beneficiaries.xlsx"
    df.to_excel(target, index=False)
    bumped = time.time() + 5
    os.utime(target, (bumped, bumped))
    second = tb.profile_dataset("beneficiaries")
    assert '"rows": 10' in second and second != first


def test_pii_access_logged(demo_workspace: Path) -> None:
    cfg = load_config(demo_workspace)
    tele = SessionTelemetry(cfg.logs_dir / "pii.jsonl")
    DataToolbox(cfg, tele).run_analysis(
        'df = load_dataset("beneficiaries", include_pii=True)\nprint(len(df))'
    )
    log = tele.path.read_text(encoding="utf-8")
    assert '"kind": "pii_access"' in log


def test_no_pii_access_event_without_flag(demo_workspace: Path) -> None:
    cfg = load_config(demo_workspace)
    tele = SessionTelemetry(cfg.logs_dir / "nopii.jsonl")
    DataToolbox(cfg, tele).run_analysis("print(1)")
    assert "pii_access" not in tele.path.read_text(encoding="utf-8")


def test_pii_access_logged_with_spaces(demo_workspace: Path) -> None:
    cfg = load_config(demo_workspace)
    tele = SessionTelemetry(cfg.logs_dir / "pii2.jsonl")
    DataToolbox(cfg, tele).run_analysis(
        'df = load_dataset("beneficiaries", include_pii = True)\nprint(len(df))'
    )
    assert '"kind": "pii_access"' in tele.path.read_text(encoding="utf-8")
