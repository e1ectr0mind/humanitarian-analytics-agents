"""Run analyst-written pandas code in a restricted subprocess.

Isolation is best-effort (accidental-leak protection, not anti-malware — spec §5):
sockets are disabled, a filesystem audit hook denies open() outside allowed
roots, stdout/stderr are captured, and a hard timeout kills the process.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from haa.config import HaaConfig
from haa.core.tools.profiler import discover_datasets


@dataclass
class ExecutionResult:
    stdout: str
    stderr: str
    returncode: int
    timed_out: bool = False


_PRELUDE = """\
import json as _json, os as _os, socket as _socket, sys as _sys

_CFG = _json.loads({cfg_json!r})


class _NoNetwork(_socket.socket):
    def connect(self, *a, **kw):
        raise RuntimeError("network access is disabled in the analysis sandbox")

    def connect_ex(self, *a, **kw):
        raise RuntimeError("network access is disabled in the analysis sandbox")


_socket.socket = _NoNetwork

import pandas as pd

pd.set_option("display.max_rows", _CFG["table_row_cap"])
pd.set_option("display.width", 200)
try:
    import matplotlib

    matplotlib.use("Agg")
except Exception:
    pass

_ALLOWED = tuple(_os.path.normcase(r) for r in _CFG["allowed_roots"])


def _audit(event, args):
    if event == "open" and args and args[0] is not None and isinstance(args[0], (str, bytes)):
        path = _os.path.normcase(_os.path.abspath(_os.fsdecode(args[0])))
        if not any(path == root or path.startswith(root + _os.sep) for root in _ALLOWED):
            raise PermissionError(f"sandbox: access outside workspace denied: {{path}}")


_sys.addaudithook(_audit)

_PII = _CFG["pii_columns"]
_DATASETS = _CFG["datasets"]
CHARTS_DIR = _CFG["charts_dir"]


def load_dataset(name: str) -> "pd.DataFrame":
    if name not in _DATASETS:
        raise KeyError(f"unknown dataset {{name!r}}; available: {{sorted(_DATASETS)}}")
    path = _DATASETS[name]
    df = pd.read_csv(path) if path.lower().endswith(".csv") else pd.read_excel(path)
    drop = [c for c in _PII.get(name, []) if c in df.columns]
    return df.drop(columns=drop)


# --- agent code below ---
"""


def _allowed_roots(config: HaaConfig) -> list[str]:
    # NOTE: deliberately does NOT include the system temp dir — pytest tmp dirs
    # live there, and the deny test relies on temp being outside the sandbox.
    roots = {
        str(config.workspace),
        sys.prefix,
        sys.base_prefix,
        str(Path.home() / ".matplotlib"),
        # font locations matplotlib reads lazily at draw/savefig time:
        "C:\\Windows\\Fonts",
        "/usr/share/fonts",
        "/System/Library/Fonts",
        str(Path.home() / ".fonts"),
    }
    try:
        import matplotlib

        roots.add(matplotlib.get_data_path())
        roots.add(matplotlib.get_configdir())
        roots.add(matplotlib.get_cachedir())
    except Exception:
        pass
    roots.update(p for p in sys.path if p)
    return sorted(roots)


def run_code(code: str, config: HaaConfig, pii_map: dict[str, list[str]]) -> ExecutionResult:
    datasets = {name: str(path) for name, path in discover_datasets(config.data_dir).items()}
    cfg = {
        "table_row_cap": config.table_row_cap,
        "charts_dir": str(config.charts_dir),
        "datasets": datasets,
        "pii_columns": pii_map,
        "allowed_roots": _allowed_roots(config),
    }
    script = _PRELUDE.format(cfg_json=json.dumps(cfg)) + code
    script_path = config.logs_dir / "last_analysis.py"
    script_path.write_text(script, encoding="utf-8")

    try:
        proc = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=config.sandbox_timeout_s,
            cwd=str(config.workspace),
        )
    except subprocess.TimeoutExpired as exc:
        return ExecutionResult(
            stdout=(exc.stdout or b"").decode("utf-8", "replace")
            if isinstance(exc.stdout, bytes)
            else (exc.stdout or ""),
            stderr=f"execution timed out after {config.sandbox_timeout_s}s",
            returncode=-1,
            timed_out=True,
        )
    return ExecutionResult(stdout=proc.stdout, stderr=proc.stderr, returncode=proc.returncode)
