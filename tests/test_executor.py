from pathlib import Path

from haa.config import load_config
from haa.core.sandbox.executor import run_code


def _cfg(ws: Path, **kw):
    cfg = load_config(ws)
    if kw:
        import dataclasses

        cfg = dataclasses.replace(cfg, **kw)
    return cfg


PII = {"beneficiaries": ["resp_name", "resp_phone", "gps_lat", "gps_lon", "enumerator", "comment"]}


def test_runs_simple_code(demo_workspace: Path) -> None:
    res = run_code("print(2 + 2)", _cfg(demo_workspace), {})
    assert res.returncode == 0 and not res.timed_out
    assert res.stdout.strip() == "4"


def test_load_dataset_drops_pii(demo_workspace: Path) -> None:
    code = 'df = load_dataset("beneficiaries")\nprint(sorted(df.columns))'
    res = run_code(code, _cfg(demo_workspace), PII)
    assert res.returncode == 0, res.stderr
    assert "resp_phone" not in res.stdout
    assert "oblast" in res.stdout


def test_aggregation_works(demo_workspace: Path) -> None:
    code = 'df = load_dataset("beneficiaries")\nprint(df["_uuid"].nunique())'
    res = run_code(code, _cfg(demo_workspace), PII)
    assert res.stdout.strip() == "3000"


def test_pandas_row_display_capped(demo_workspace: Path) -> None:
    code = "import pandas as _p\nprint(pd.get_option('display.max_rows'))"
    res = run_code(code, _cfg(demo_workspace), {})
    assert res.stdout.strip() == "50"


def test_network_blocked(demo_workspace: Path) -> None:
    code = (
        "import socket\n"
        "s = socket.socket()\n"
        "s.connect(('example.com', 80))\n"
    )
    res = run_code(code, _cfg(demo_workspace), {})
    assert res.returncode != 0
    assert "disabled" in res.stderr.lower() or "network" in res.stderr.lower()


def test_timeout(demo_workspace: Path) -> None:
    res = run_code("while True:\n    pass", _cfg(demo_workspace, sandbox_timeout_s=2), {})
    assert res.timed_out


def test_open_outside_workspace_denied(demo_workspace: Path, tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret", encoding="utf-8")
    code = f"print(open({str(secret)!r}).read())"
    res = run_code(code, _cfg(demo_workspace), {})
    assert res.returncode != 0
    assert "top secret" not in res.stdout


def test_stderr_returned_on_error(demo_workspace: Path) -> None:
    res = run_code("1/0", _cfg(demo_workspace), {})
    assert res.returncode != 0
    assert "ZeroDivisionError" in res.stderr
