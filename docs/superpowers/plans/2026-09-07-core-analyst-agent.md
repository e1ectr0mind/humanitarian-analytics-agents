# Core + Analyst Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A CLI chat app where an orchestrator agent delegates data questions to an analyst subagent that writes pandas code, executes it in a local sandbox (raw data never reaches the LLM), and answers with tables/charts — grounded in project documentation.

**Architecture:** Claude Agent SDK (Python). Main loop = orchestrator; analyst is an `AgentDefinition` subagent whose only tools are an in-process MCP server (`list_datasets`, `profile_dataset`, `run_analysis`, `list_project_docs`, `read_project_doc`). A `PreToolUse` hook blocks direct file access to `workspace/data/`. The sandbox is a subprocess with a prelude that injects a PII-stripping `load_dataset()` helper, disables sockets, and installs a filesystem audit hook; its output passes a redaction/truncation guard. All traffic is logged to JSONL telemetry.

**Tech Stack:** Python 3.11+, `uv`, `claude-agent-sdk`, pandas, openpyxl, matplotlib, numpy, faker, pypdf, python-docx, rich, pytest (+pytest-asyncio), ruff, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-07-core-analyst-agent-design.md`

## Global Constraints

- Python **3.11+** (uses `tomllib`); package lives in `src/haa/`; repo root is the project root.
- All code, comments, docstrings, README — **English**. Planning docs — Russian.
- Default models: orchestrator `claude-opus-5`, analyst `claude-opus-5` (from spec §6).
- Defaults: `max_budget_usd=2.0`, `sandbox_timeout_s=60`, `output_limit_bytes=32768`, `table_row_cap=50` (spec §4).
- Workspace layout: `workspace/data/` (sandbox-only), `workspace/project_docs/` (readable into context), `workspace/charts/`, `workspace/logs/` (spec §3, §5).
- **PII boundary:** no code path may return raw dataset rows to the LLM. Profile = schema/stats only; sandbox output is filtered (spec §5).
- Every test except those marked `api` must run **without** `ANTHROPIC_API_KEY` and without network.
- TDD: each task writes failing tests first. Commit at the end of every task with the message given in the task.
- Run everything through `uv run ...` (e.g. `uv run pytest`, `uv run ruff check .`).
- If an exact `claude-agent-sdk` symbol/kwarg differs from this plan (the SDK evolves), adapt the thin adapter layer only (Tasks 10–11 flag the verification steps); never change the pure-logic modules to compensate.

---

## File Structure

```
pyproject.toml                  # project metadata, deps, pytest/ruff config, `haa` entry point
.gitignore                      # workspace/, .venv/, __pycache__, etc.
.github/workflows/ci.yml        # ruff + non-api pytest
README.md                       # portfolio-facing docs (Task 13)
src/haa/
  __init__.py                   # __version__
  config.py                     # HaaConfig dataclass + load_config()
  demo.py                       # synthetic demo dataset + fake logframe generator
  core/
    __init__.py
    hooks.py                    # deny_reason() pure logic + SDK PreToolUse adapter
    telemetry.py                # SessionTelemetry JSONL logger
    session.py                  # AnalyticsSession + events_from_message()
    agents/
      __init__.py
      orchestrator.py           # ORCHESTRATOR_PROMPT
      analyst.py                # ANALYST_PROMPT + build_analyst()
      registry.py               # build_agents()
    tools/
      __init__.py
      profiler.py               # discover_datasets, detect_pii_columns, profile_dataframe
      docs.py                   # list_docs, read_doc (md/txt/pdf/docx/xlsx/csv)
      datatools.py              # build_data_server() -> in-process MCP server
    sandbox/
      __init__.py
      guard.py                  # filter_output() redaction/truncation
      executor.py               # run_code() subprocess runner + prelude template
  cli/
    __init__.py
    app.py                      # argparse: `haa chat`, `haa demo`; REPL
demo/generate_demo_data.py      # thin wrapper around haa.demo (spec §6 path)
tests/
  conftest.py                   # demo_workspace fixture (session-scoped)
  test_config.py
  test_demo.py
  test_profiler.py
  test_guard.py
  test_executor.py
  test_docs.py
  test_hooks.py
  test_telemetry.py
  test_datatools.py
  test_agents.py
  test_session.py
  test_cli.py
  test_smoke_api.py             # marked `api`, excluded from CI
```

---

### Task 1: Scaffolding + config module

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `src/haa/__init__.py`, `src/haa/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `HaaConfig` frozen dataclass — fields `workspace: Path`, `orchestrator_model: str`, `analyst_model: str`, `max_budget_usd: float`, `sandbox_timeout_s: int`, `output_limit_bytes: int`, `table_row_cap: int`; properties `data_dir`, `docs_dir`, `charts_dir`, `logs_dir` (all `Path`). `load_config(workspace: Path, config_file: Path | None = None) -> HaaConfig` (creates the four dirs, raises `ConfigError` on missing workspace / unknown keys). `ConfigError(ValueError)`.

- [ ] **Step 1: Initialize project files**

`pyproject.toml`:

```toml
[project]
name = "haa"
version = "0.1.0"
description = "Humanitarian Analytics Agents — multi-agent data analytics with a hard PII boundary"
requires-python = ">=3.11"
dependencies = [
    "claude-agent-sdk>=0.1.0",
    "pandas>=2.2",
    "numpy>=1.26",
    "openpyxl>=3.1",
    "matplotlib>=3.8",
    "faker>=25.0",
    "pypdf>=4.0",
    "python-docx>=1.1",
    "rich>=13.7",
]

[project.scripts]
haa = "haa.cli.app:main"

[dependency-groups]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "ruff>=0.5"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/haa"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
addopts = "-m 'not api'"
markers = ["api: tests that call the Claude API (cost real money; run manually)"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
```

`.gitignore`:

```
.venv/
__pycache__/
*.pyc
workspace/
.pytest_cache/
.ruff_cache/
dist/
.env
```

`src/haa/__init__.py`:

```python
__version__ = "0.1.0"
```

- [ ] **Step 2: Write the failing tests**

`tests/test_config.py`:

```python
from pathlib import Path

import pytest

from haa.config import ConfigError, HaaConfig, load_config


def test_defaults(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    assert cfg.orchestrator_model == "claude-opus-5"
    assert cfg.analyst_model == "claude-opus-5"
    assert cfg.max_budget_usd == 2.0
    assert cfg.sandbox_timeout_s == 60
    assert cfg.output_limit_bytes == 32768
    assert cfg.table_row_cap == 50


def test_creates_workspace_subdirs(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    assert cfg.data_dir == tmp_path.resolve() / "data"
    assert cfg.docs_dir == tmp_path.resolve() / "project_docs"
    for d in (cfg.data_dir, cfg.docs_dir, cfg.charts_dir, cfg.logs_dir):
        assert d.is_dir()


def test_reads_config_toml(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text(
        'analyst_model = "claude-sonnet-5"\nmax_budget_usd = 0.5\n', encoding="utf-8"
    )
    cfg = load_config(tmp_path)
    assert cfg.analyst_model == "claude-sonnet-5"
    assert cfg.max_budget_usd == 0.5
    assert cfg.orchestrator_model == "claude-opus-5"  # untouched default


def test_unknown_key_rejected(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("tabel_row_cap = 10\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="tabel_row_cap"):
        load_config(tmp_path)


def test_missing_workspace_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="does not exist"):
        load_config(tmp_path / "nope")


def test_config_is_frozen(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    with pytest.raises(Exception):
        cfg.max_budget_usd = 99  # type: ignore[misc]
    assert isinstance(cfg, HaaConfig)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'haa.config'`

- [ ] **Step 4: Implement `src/haa/config.py`**

```python
"""Configuration for a HAA workspace session."""

from __future__ import annotations

import dataclasses
import tomllib
from dataclasses import dataclass
from pathlib import Path


class ConfigError(ValueError):
    """Raised for invalid workspace or config.toml contents."""


@dataclass(frozen=True)
class HaaConfig:
    workspace: Path
    orchestrator_model: str = "claude-opus-5"
    analyst_model: str = "claude-opus-5"
    max_budget_usd: float = 2.0
    sandbox_timeout_s: int = 60
    output_limit_bytes: int = 32768
    table_row_cap: int = 50

    @property
    def data_dir(self) -> Path:
        return self.workspace / "data"

    @property
    def docs_dir(self) -> Path:
        return self.workspace / "project_docs"

    @property
    def charts_dir(self) -> Path:
        return self.workspace / "charts"

    @property
    def logs_dir(self) -> Path:
        return self.workspace / "logs"


_TUNABLE = {f.name for f in dataclasses.fields(HaaConfig)} - {"workspace"}


def load_config(workspace: Path, config_file: Path | None = None) -> HaaConfig:
    workspace = workspace.resolve()
    if not workspace.is_dir():
        raise ConfigError(f"Workspace does not exist: {workspace}")

    values: dict[str, object] = {}
    path = config_file or workspace / "config.toml"
    if path.is_file():
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        unknown = set(data) - _TUNABLE
        if unknown:
            raise ConfigError(f"Unknown config keys: {', '.join(sorted(unknown))}")
        values = data

    cfg = HaaConfig(workspace=workspace, **values)  # type: ignore[arg-type]
    for d in (cfg.data_dir, cfg.docs_dir, cfg.charts_dir, cfg.logs_dir):
        d.mkdir(parents=True, exist_ok=True)
    return cfg
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv sync && uv run pytest tests/test_config.py -v` — Expected: all PASS.
Run: `uv run ruff check .` — Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .gitignore src/haa tests/test_config.py uv.lock
git commit -m "feat: project scaffolding and workspace config"
```

---

### Task 2: Synthetic demo dataset + fake logframe

**Files:**
- Create: `src/haa/demo.py`, `demo/generate_demo_data.py`, `tests/conftest.py`
- Test: `tests/test_demo.py`

**Interfaces:**
- Consumes: `HaaConfig` dirs convention (writes into `workspace/data/`, `workspace/project_docs/`).
- Produces: `generate(workspace: Path, rows: int = 3000, seed: int = 42) -> Path` — writes `data/beneficiaries.xlsx` (returns its path) and `project_docs/logframe.md`. Deterministic for a given seed. Dataset facts later tasks rely on: **3030 total rows, 3000 unique `_uuid`** (30 duplicated submissions), columns listed in Step 2, ~3% missing `head_sex`, 15 rows with `head_age == 999`, 10 rows with 2027 `submission_date`, Kharkiv oblast spelling variants. Fixture `demo_workspace` (session-scoped) for all tests.

- [ ] **Step 1: Write the failing tests**

`tests/conftest.py`:

```python
from pathlib import Path

import pytest

from haa.demo import generate


@pytest.fixture(scope="session")
def demo_workspace(tmp_path_factory: pytest.TempPathFactory) -> Path:
    ws = tmp_path_factory.mktemp("demo_ws")
    (ws / "data").mkdir()
    (ws / "project_docs").mkdir()
    generate(ws)
    return ws
```

`tests/test_demo.py`:

```python
from pathlib import Path

import pandas as pd

from haa.demo import OBLASTS, generate

EXPECTED_COLUMNS = [
    "_uuid", "submission_date", "oblast", "raion", "hromada", "settlement_type",
    "hh_size", "head_sex", "head_age", "disability_in_hh", "displacement_status",
    "services_received", "satisfaction", "resp_name", "resp_phone",
    "gps_lat", "gps_lon", "enumerator", "comment",
]


def _load(ws: Path) -> pd.DataFrame:
    return pd.read_excel(ws / "data" / "beneficiaries.xlsx")


def test_shape_and_columns(demo_workspace: Path) -> None:
    df = _load(demo_workspace)
    assert list(df.columns) == EXPECTED_COLUMNS
    assert len(df) == 3030
    assert df["_uuid"].nunique() == 3000


def test_intentional_defects(demo_workspace: Path) -> None:
    df = _load(demo_workspace)
    assert df["head_sex"].isna().sum() > 50                      # ~3% missing
    assert (df["head_age"] == 999).sum() == 15                   # impossible ages
    assert (pd.to_datetime(df["submission_date"]) > "2026-12-31").sum() == 10
    variants = {o for o in df["oblast"].unique() if "Харків" in o}
    assert len(variants) >= 2                                    # spelling variants


def test_oblasts_are_frontline_set(demo_workspace: Path) -> None:
    df = _load(demo_workspace)
    assert set(df["oblast"].unique()) <= set(OBLASTS) | {"Харківська обл."}
    assert df["oblast"].nunique() >= 8


def test_pii_columns_have_realistic_values(demo_workspace: Path) -> None:
    df = _load(demo_workspace)
    assert df["resp_phone"].astype(str).str.startswith("+380").all()
    assert df["gps_lat"].between(44, 53).all()
    assert df["resp_name"].str.len().gt(3).all()


def test_deterministic(tmp_path: Path, demo_workspace: Path) -> None:
    (tmp_path / "data").mkdir()
    (tmp_path / "project_docs").mkdir()
    generate(tmp_path)
    a = _load(tmp_path)
    b = _load(demo_workspace)
    pd.testing.assert_frame_equal(a, b)


def test_logframe_written(demo_workspace: Path) -> None:
    text = (demo_workspace / "project_docs" / "logframe.md").read_text(encoding="utf-8")
    assert "Indicator 1.1" in text
    assert "2500" in text  # target value used by the smoke eval
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_demo.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'haa.demo'`

- [ ] **Step 3: Implement `src/haa/demo.py`**

```python
"""Deterministic synthetic Kobo-style demo dataset + fake project logframe.

Everything here is FAKE: names, phones, GPS points. The dataset exists so the
project can be demoed and tested without any real beneficiary data, and so the
PII boundary is visibly exercised (spec §7).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import numpy as np
import pandas as pd
from faker import Faker

OBLASTS = [
    "Донецька", "Луганська", "Харківська", "Запорізька",
    "Херсонська", "Дніпропетровська", "Сумська", "Миколаївська",
]
SERVICES = ["health", "mhpss", "cash", "nfi", "protection"]

LOGFRAME = """# Project ABC-123 — Emergency Multi-Sectoral Assistance (DEMO, fake data)

## Logframe indicators

| Indicator | Definition | Target |
|---|---|---|
| Indicator 1.1 | # of unique households reached with at least one service | 2500 |
| Indicator 1.2 | % of reached households that are female-headed | 55% |
| Indicator 2.1 | # of households receiving cash assistance | 1200 |

## Notes

- Reporting period: June–August 2026.
- A household counts as "reached" once per unique submission (`_uuid`).
- Disaggregate all indicators by oblast and by sex of head of household (SADD).
"""


def generate(workspace: Path, rows: int = 3000, seed: int = 42) -> Path:
    rng = np.random.default_rng(seed)
    fake = Faker("uk_UA")
    Faker.seed(seed)

    dates = pd.to_datetime("2026-06-01") + pd.to_timedelta(
        rng.integers(0, 92, rows), unit="D"
    )
    df = pd.DataFrame(
        {
            "_uuid": [str(uuid.UUID(int=int(i))) for i in rng.integers(0, 2**63, rows)],
            "submission_date": dates.strftime("%Y-%m-%d"),
            "oblast": rng.choice(OBLASTS, rows),
            "raion": [f"Район-{i}" for i in rng.integers(1, 6, rows)],
            "hromada": [f"Громада-{i}" for i in rng.integers(1, 25, rows)],
            "settlement_type": rng.choice(["urban", "rural"], rows, p=[0.6, 0.4]),
            "hh_size": rng.integers(1, 9, rows),
            "head_sex": rng.choice(["female", "male"], rows, p=[0.58, 0.42]).astype(object),
            "head_age": rng.integers(18, 90, rows),
            "disability_in_hh": rng.choice(["yes", "no"], rows, p=[0.25, 0.75]),
            "displacement_status": rng.choice(
                ["idp", "host_community", "returnee"], rows, p=[0.45, 0.4, 0.15]
            ),
            "services_received": [
                " ".join(sorted(rng.choice(SERVICES, size=rng.integers(1, 4), replace=False)))
                for _ in range(rows)
            ],
            "satisfaction": rng.integers(1, 6, rows),
            "resp_name": [fake.name() for _ in range(rows)],
            "resp_phone": [f"+380{rng.integers(50, 99)}{rng.integers(1000000, 9999999)}" for _ in range(rows)],
            "gps_lat": np.round(rng.uniform(46.0, 50.5, rows), 6),
            "gps_lon": np.round(rng.uniform(29.5, 38.5, rows), 6),
            "enumerator": rng.choice([fake.name() for _ in range(12)], rows),
            "comment": [
                fake.sentence(nb_words=8) if rng.random() < 0.3 else "" for _ in range(rows)
            ],
        }
    )

    # Intentional data-quality defects (spec §7).
    missing_sex = rng.choice(rows, size=int(rows * 0.03), replace=False)
    df.loc[missing_sex, "head_sex"] = None
    df.loc[rng.choice(rows, size=15, replace=False), "head_age"] = 999
    future = rng.choice(rows, size=10, replace=False)
    df.loc[future, "submission_date"] = "2027-01-15"
    kharkiv = df.index[df["oblast"] == "Харківська"]
    df.loc[kharkiv[: max(1, len(kharkiv) // 20)], "oblast"] = "Харківська обл."
    dupes = df.sample(30, random_state=seed)
    df = pd.concat([df, dupes], ignore_index=True)

    out = workspace / "data" / "beneficiaries.xlsx"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(out, index=False)

    docs = workspace / "project_docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "logframe.md").write_text(LOGFRAME, encoding="utf-8")
    return out


def main() -> None:  # pragma: no cover - thin wrapper
    import argparse

    p = argparse.ArgumentParser(description="Generate the synthetic demo workspace")
    p.add_argument("--workspace", type=Path, default=Path("workspace"))
    args = p.parse_args()
    args.workspace.mkdir(parents=True, exist_ok=True)
    path = generate(args.workspace)
    print(f"Demo data written to {path}")


if __name__ == "__main__":  # pragma: no cover
    main()
```

`demo/generate_demo_data.py` (spec §6 keeps this path):

```python
"""Thin wrapper so `python demo/generate_demo_data.py` works as documented."""
from haa.demo import main

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_demo.py -v` — Expected: all PASS (first run generates ~3s).
Run: `uv run ruff check .` — Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add src/haa/demo.py demo/generate_demo_data.py tests/conftest.py tests/test_demo.py
git commit -m "feat: deterministic synthetic demo dataset with fake PII and logframe"
```

---

### Task 3: Dataset discovery, profiler, PII detection

**Files:**
- Create: `src/haa/core/__init__.py`, `src/haa/core/tools/__init__.py`, `src/haa/core/tools/profiler.py`
- Test: `tests/test_profiler.py`

**Interfaces:**
- Consumes: `demo_workspace` fixture.
- Produces: `discover_datasets(data_dir: Path) -> dict[str, Path]` (stem → file, `.csv/.xlsx/.xls` only); `detect_pii_columns(df: pd.DataFrame) -> list[str]`; `profile_dataframe(df: pd.DataFrame, name: str, pii_columns: list[str]) -> dict` (JSON-safe). Regexes `PHONE_RE`, `GPS_PAIR_RE` reused by Task 4's guard. Profile dict shape: `{"dataset", "rows", "columns": [{"name", "dtype", "null_rate", "pii", "values"?, "min"?, "max"?}]}` — PII columns never carry `values`/`min`/`max`.

- [ ] **Step 1: Write the failing tests**

`tests/test_profiler.py`:

```python
import json
from pathlib import Path

import pandas as pd

from haa.core.tools.profiler import (
    detect_pii_columns,
    discover_datasets,
    profile_dataframe,
)


def _df(ws: Path) -> pd.DataFrame:
    return pd.read_excel(ws / "data" / "beneficiaries.xlsx")


def test_discover_datasets(demo_workspace: Path) -> None:
    found = discover_datasets(demo_workspace / "data")
    assert found == {"beneficiaries": demo_workspace / "data" / "beneficiaries.xlsx"}


def test_discover_ignores_non_tabular(tmp_path: Path) -> None:
    (tmp_path / "a.xlsx").touch()
    (tmp_path / "b.csv").touch()
    (tmp_path / "notes.txt").touch()
    assert set(discover_datasets(tmp_path)) == {"a", "b"}


def test_detect_pii_columns(demo_workspace: Path) -> None:
    pii = set(detect_pii_columns(_df(demo_workspace)))
    assert {"resp_name", "resp_phone", "gps_lat", "gps_lon", "enumerator", "comment"} <= pii
    assert "oblast" not in pii
    assert "head_sex" not in pii


def test_detect_pii_by_value_pattern() -> None:
    df = pd.DataFrame({"contact": ["+380671234567", "+380509876543", "0671112233"]})
    assert detect_pii_columns(df) == ["contact"]


def test_profile_shape_and_no_pii_values(demo_workspace: Path) -> None:
    df = _df(demo_workspace)
    pii = detect_pii_columns(df)
    profile = profile_dataframe(df, "beneficiaries", pii)
    assert profile["dataset"] == "beneficiaries"
    assert profile["rows"] == 3030
    cols = {c["name"]: c for c in profile["columns"]}
    assert cols["oblast"]["pii"] is False
    assert "Донецька" in cols["oblast"]["values"]
    assert cols["head_sex"]["null_rate"] > 0.02
    assert cols["resp_phone"]["pii"] is True
    for c in profile["columns"]:
        if c["pii"]:
            assert "values" not in c and "min" not in c and "max" not in c
    # no raw phone anywhere in the serialized profile
    assert "+380" not in json.dumps(profile, ensure_ascii=False)


def test_profile_is_json_serializable(demo_workspace: Path) -> None:
    df = _df(demo_workspace)
    json.dumps(profile_dataframe(df, "x", detect_pii_columns(df)), ensure_ascii=False)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_profiler.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'haa.core'`

- [ ] **Step 3: Implement `src/haa/core/tools/profiler.py`** (plus empty `__init__.py` files)

```python
"""Dataset discovery and safe profiling: schema and stats only, never raw rows."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

TABULAR_SUFFIXES = {".csv", ".xlsx", ".xls"}
MAX_CATEGORY_VALUES = 30

PII_NAME_RE = re.compile(
    r"name|імʼя|ім'я|имя|прізвище|фамил|phone|тел|gps|lat|lon|coord|address|"
    r"адрес|email|comment|коммент|enumerator|інтерв",
    re.IGNORECASE,
)
PHONE_RE = re.compile(r"\+?38\s?0\d{2}[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}|\b0\d{9}\b|\+\d{11,14}")
GPS_PAIR_RE = re.compile(r"\b\d{2}\.\d{4,}\s*,\s*\d{2}\.\d{4,}\b")

_VALUE_SAMPLE = 50
_VALUE_HIT_THRESHOLD = 0.3


def discover_datasets(data_dir: Path) -> dict[str, Path]:
    if not data_dir.is_dir():
        return {}
    return {
        p.stem: p
        for p in sorted(data_dir.iterdir())
        if p.suffix.lower() in TABULAR_SUFFIXES and p.is_file()
    }


def detect_pii_columns(df: pd.DataFrame) -> list[str]:
    pii: list[str] = []
    for col in df.columns:
        if PII_NAME_RE.search(str(col)):
            pii.append(col)
            continue
        sample = df[col].dropna().astype(str).head(_VALUE_SAMPLE)
        if len(sample) == 0:
            continue
        hits = sum(bool(PHONE_RE.search(v) or GPS_PAIR_RE.search(v)) for v in sample)
        if hits / len(sample) >= _VALUE_HIT_THRESHOLD:
            pii.append(col)
    return pii


def profile_dataframe(df: pd.DataFrame, name: str, pii_columns: list[str]) -> dict:
    pii = set(pii_columns)
    columns: list[dict] = []
    for col in df.columns:
        series = df[col]
        info: dict = {
            "name": str(col),
            "dtype": str(series.dtype),
            "null_rate": round(float(series.isna().mean()), 4),
            "pii": col in pii,
        }
        if col not in pii:
            non_null = series.dropna()
            if non_null.nunique() <= MAX_CATEGORY_VALUES:
                info["values"] = sorted(str(v) for v in non_null.unique())
            elif pd.api.types.is_numeric_dtype(series):
                info["min"] = float(non_null.min()) if len(non_null) else None
                info["max"] = float(non_null.max()) if len(non_null) else None
        columns.append(info)
    return {"dataset": name, "rows": int(len(df)), "columns": columns}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_profiler.py -v` — Expected: all PASS.
If `enumerator`/`comment` detection fails, extend `PII_NAME_RE` alternation — do not weaken assertions.

- [ ] **Step 5: Commit**

```bash
git add src/haa/core tests/test_profiler.py
git commit -m "feat: dataset discovery and PII-aware safe profiler"
```

---

### Task 4: Output guard (redaction + truncation)

**Files:**
- Create: `src/haa/core/sandbox/__init__.py`, `src/haa/core/sandbox/guard.py`
- Test: `tests/test_guard.py`

**Interfaces:**
- Consumes: `PHONE_RE`, `GPS_PAIR_RE` from `haa.core.tools.profiler`.
- Produces: `FilterResult` dataclass (`text: str`, `notices: list[str]`); `filter_output(text: str, *, row_cap: int, size_cap: int) -> FilterResult`. Line cap = `row_cap + 10` (headroom for headers/summary lines).

- [ ] **Step 1: Write the failing tests**

`tests/test_guard.py`:

```python
from haa.core.sandbox.guard import filter_output


def test_redacts_phones() -> None:
    res = filter_output("call +380671234567 or 0509876543", row_cap=50, size_cap=32768)
    assert "+380671234567" not in res.text
    assert "0509876543" not in res.text
    assert res.text.count("[REDACTED]") == 2
    assert any("redact" in n.lower() for n in res.notices)


def test_redacts_gps_pairs() -> None:
    res = filter_output("point at 48.53421, 35.12345 ok", row_cap=50, size_cap=32768)
    assert "48.53421" not in res.text


def test_plain_aggregates_untouched() -> None:
    table = "oblast  count\nДонецька  512\nСумська  380"
    res = filter_output(table, row_cap=50, size_cap=32768)
    assert res.text == table
    assert res.notices == []


def test_line_cap() -> None:
    text = "\n".join(f"row {i}" for i in range(500))
    res = filter_output(text, row_cap=50, size_cap=32768)
    assert len(res.text.splitlines()) == 60  # row_cap + 10
    assert any("440 more lines" in n for n in res.notices)


def test_size_cap() -> None:
    res = filter_output("x" * 100_000, row_cap=1000, size_cap=1000)
    assert len(res.text.encode()) <= 1000
    assert any("truncated" in n.lower() for n in res.notices)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_guard.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'haa.core.sandbox'`

- [ ] **Step 3: Implement `src/haa/core/sandbox/guard.py`**

```python
"""Last-line-of-defense filter for sandbox output before it reaches the LLM."""

from __future__ import annotations

from dataclasses import dataclass, field

from haa.core.tools.profiler import GPS_PAIR_RE, PHONE_RE

REDACTED = "[REDACTED]"
_LINE_HEADROOM = 10


@dataclass
class FilterResult:
    text: str
    notices: list[str] = field(default_factory=list)


def filter_output(text: str, *, row_cap: int, size_cap: int) -> FilterResult:
    notices: list[str] = []

    redactions = 0
    for pattern in (PHONE_RE, GPS_PAIR_RE):
        text, n = pattern.subn(REDACTED, text)
        redactions += n
    if redactions:
        notices.append(f"[guard] redacted {redactions} PII-looking value(s)")

    lines = text.splitlines()
    cap = row_cap + _LINE_HEADROOM
    if len(lines) > cap:
        hidden = len(lines) - cap
        text = "\n".join(lines[:cap])
        notices.append(f"[guard] output truncated: {hidden} more lines hidden")

    raw = text.encode("utf-8")
    if len(raw) > size_cap:
        text = raw[:size_cap].decode("utf-8", errors="ignore")
        notices.append("[guard] output truncated to size limit")

    return FilterResult(text=text, notices=notices)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_guard.py -v` — Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/haa/core/sandbox tests/test_guard.py
git commit -m "feat: sandbox output guard with PII redaction and truncation"
```

---

### Task 5: Sandbox executor with loader prelude

**Files:**
- Create: `src/haa/core/sandbox/executor.py`
- Test: `tests/test_executor.py`

**Interfaces:**
- Consumes: `HaaConfig` (timeout, dirs, `table_row_cap`); `discover_datasets` from profiler.
- Produces: `ExecutionResult` dataclass (`stdout: str`, `stderr: str`, `returncode: int`, `timed_out: bool`); `run_code(code: str, config: HaaConfig, pii_map: dict[str, list[str]]) -> ExecutionResult`. Inside the sandbox, agent code gets: `load_dataset(name: str) -> pd.DataFrame` (PII columns dropped), `CHARTS_DIR: str`, pandas pre-imported as `pd` with `display.max_rows = table_row_cap`, matplotlib forced to `Agg`, sockets disabled, best-effort FS audit hook (open outside allowed roots → `PermissionError`).

- [ ] **Step 1: Write the failing tests**

`tests/test_executor.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_executor.py -v`
Expected: FAIL — `ImportError` (executor missing)

- [ ] **Step 3: Implement `src/haa/core/sandbox/executor.py`**

```python
"""Run analyst-written pandas code in a restricted subprocess.

Isolation is best-effort (accidental-leak protection, not anti-malware — spec §5):
sockets are disabled, a filesystem audit hook denies open() outside allowed
roots, stdout/stderr are captured, and a hard timeout kills the process.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
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
        if not path.startswith(_ALLOWED):
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
            stdout=(exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
            stderr=f"execution timed out after {config.sandbox_timeout_s}s",
            returncode=-1,
            timed_out=True,
        )
    return ExecutionResult(stdout=proc.stdout, stderr=proc.stderr, returncode=proc.returncode)
```

Implementation note: `_PRELUDE` is rendered with `str.format` — every literal `{` `}` in the prelude body must be doubled (`{{path}}`, `{{name!r}}` etc., as shown); only `{cfg_json!r}` is a real placeholder.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_executor.py -v` — Expected: all PASS.
Also remove the now-unused `import tempfile` if the linter flags it.
Watch for known traps: (a) matplotlib opens system font files lazily at `savefig` time — the font directories are already in `_allowed_roots`, but if a chart run fails with `PermissionError` on a font or cache path, add that specific parent directory to `_allowed_roots` rather than weakening the hook; (b) on Windows, audit-hook paths need `os.path.normcase` on both sides — already in the code; (c) never re-add the system temp dir to the roots — `test_open_outside_workspace_denied` depends on temp being denied.

- [ ] **Step 5: Commit**

```bash
git add src/haa/core/sandbox/executor.py tests/test_executor.py
git commit -m "feat: sandboxed code executor with PII-stripping loader prelude"
```

---

### Task 6: Project docs reading

**Files:**
- Create: `src/haa/core/tools/docs.py`
- Test: `tests/test_docs.py`

**Interfaces:**
- Consumes: `HaaConfig.docs_dir` convention.
- Produces: `SUPPORTED_DOC_SUFFIXES = {".md", ".txt", ".pdf", ".docx", ".xlsx", ".csv"}`; `list_docs(docs_dir: Path) -> list[dict]` (each `{"name", "type", "size_kb"}`); `read_doc(docs_dir: Path, name: str, max_chars: int = 30000) -> str`; `DocError(ValueError)` for unknown/unsupported/traversal paths.

- [ ] **Step 1: Write the failing tests**

`tests/test_docs.py`:

```python
from pathlib import Path

import openpyxl
import pytest
from docx import Document

from haa.core.tools.docs import DocError, list_docs, read_doc


@pytest.fixture()
def docs_dir(tmp_path: Path) -> Path:
    d = tmp_path / "project_docs"
    d.mkdir()
    (d / "logframe.md").write_text("# Logframe\nIndicator 1.1 target 2500", encoding="utf-8")
    doc = Document()
    doc.add_paragraph("Project proposal narrative.")
    doc.add_table(rows=1, cols=2).rows[0].cells[0].text = "Indicator"
    doc.save(d / "proposal.docx")
    wb = openpyxl.Workbook()
    wb.active.append(["indicator", "target"])
    wb.active.append(["1.1", 2500])
    wb.save(d / "targets.xlsx")
    (d / "ignore.exe").write_bytes(b"MZ")
    return d


def test_list_docs(docs_dir: Path) -> None:
    names = {d["name"] for d in list_docs(docs_dir)}
    assert names == {"logframe.md", "proposal.docx", "targets.xlsx"}
    assert all("size_kb" in d and "type" in d for d in list_docs(docs_dir))


def test_read_markdown(docs_dir: Path) -> None:
    assert "target 2500" in read_doc(docs_dir, "logframe.md")


def test_read_docx_paragraphs_and_tables(docs_dir: Path) -> None:
    text = read_doc(docs_dir, "proposal.docx")
    assert "Project proposal narrative." in text
    assert "Indicator" in text


def test_read_xlsx_as_rows(docs_dir: Path) -> None:
    text = read_doc(docs_dir, "targets.xlsx")
    assert "indicator" in text and "2500" in text


def test_max_chars_cap(docs_dir: Path) -> None:
    (docs_dir / "big.txt").write_text("word " * 100_000, encoding="utf-8")
    text = read_doc(docs_dir, "big.txt", max_chars=500)
    assert len(text) <= 550  # cap + truncation notice


def test_unknown_doc_raises(docs_dir: Path) -> None:
    with pytest.raises(DocError, match="not found"):
        read_doc(docs_dir, "missing.md")


def test_unsupported_type_raises(docs_dir: Path) -> None:
    with pytest.raises(DocError, match="[Uu]nsupported"):
        read_doc(docs_dir, "ignore.exe")


def test_path_traversal_rejected(docs_dir: Path, tmp_path: Path) -> None:
    (tmp_path / "outside.md").write_text("secret", encoding="utf-8")
    with pytest.raises(DocError):
        read_doc(docs_dir, "../outside.md")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_docs.py -v`
Expected: FAIL — `ImportError` (docs module missing)

- [ ] **Step 3: Implement `src/haa/core/tools/docs.py`**

```python
"""Read project documentation (logframes, proposals) into LLM context.

This is the deliberate exception to the PII boundary (spec §5): files under
project_docs/ are MEANT to be read by the model. Data files never live here.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

SUPPORTED_DOC_SUFFIXES = {".md", ".txt", ".pdf", ".docx", ".xlsx", ".csv"}
_XLSX_ROW_CAP = 200


class DocError(ValueError):
    """Unknown, unsupported, or out-of-tree document request."""


def list_docs(docs_dir: Path) -> list[dict]:
    if not docs_dir.is_dir():
        return []
    return [
        {
            "name": p.name,
            "type": p.suffix.lstrip(".").lower(),
            "size_kb": round(p.stat().st_size / 1024, 1),
        }
        for p in sorted(docs_dir.iterdir())
        if p.is_file() and p.suffix.lower() in SUPPORTED_DOC_SUFFIXES
    ]


def _resolve(docs_dir: Path, name: str) -> Path:
    path = (docs_dir / name).resolve()
    if docs_dir.resolve() not in path.parents:
        raise DocError(f"Document path escapes project_docs/: {name}")
    if not path.is_file():
        raise DocError(f"Document not found: {name}")
    if path.suffix.lower() not in SUPPORTED_DOC_SUFFIXES:
        raise DocError(f"Unsupported document type: {path.suffix}")
    return path


def _read_pdf(path: Path) -> str:
    from pypdf import PdfReader

    return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)


def _read_docx(path: Path) -> str:
    from docx import Document

    doc = Document(str(path))
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            parts.append("\t".join(cell.text for cell in row.cells))
    return "\n".join(parts)


def _read_xlsx(path: Path) -> str:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out = io.StringIO()
    for ws in wb.worksheets:
        out.write(f"## sheet: {ws.title}\n")
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= _XLSX_ROW_CAP:
                out.write("[... more rows omitted ...]\n")
                break
            out.write("\t".join("" if v is None else str(v) for v in row) + "\n")
    return out.getvalue()


def _read_csv(path: Path) -> str:
    out = io.StringIO()
    with path.open(newline="", encoding="utf-8", errors="replace") as fh:
        for i, row in enumerate(csv.reader(fh)):
            if i >= _XLSX_ROW_CAP:
                out.write("[... more rows omitted ...]\n")
                break
            out.write("\t".join(row) + "\n")
    return out.getvalue()


_READERS = {
    ".md": lambda p: p.read_text(encoding="utf-8", errors="replace"),
    ".txt": lambda p: p.read_text(encoding="utf-8", errors="replace"),
    ".pdf": _read_pdf,
    ".docx": _read_docx,
    ".xlsx": _read_xlsx,
    ".csv": _read_csv,
}


def read_doc(docs_dir: Path, name: str, max_chars: int = 30000) -> str:
    path = _resolve(docs_dir, name)
    text = _READERS[path.suffix.lower()](path)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[... document truncated ...]"
    return text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_docs.py -v` — Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/haa/core/tools/docs.py tests/test_docs.py
git commit -m "feat: project docs listing and text extraction (md/txt/pdf/docx/xlsx/csv)"
```

---

### Task 7: PII hook (pure logic + SDK adapter)

**Files:**
- Create: `src/haa/core/hooks.py`
- Test: `tests/test_hooks.py`

**Interfaces:**
- Consumes: `HaaConfig.data_dir`, `HaaConfig.docs_dir`.
- Produces: `deny_reason(tool_name: str, tool_input: dict, config: HaaConfig) -> str | None` (pure, unit-tested); `make_pretooluse_hook(config: HaaConfig, on_block=None)` returning an async SDK-shaped callback; `BLOCK_MESSAGE` constant. `on_block` is an optional `Callable[[str, str], None]` (tool_name, reason) used by telemetry in Task 11.

- [ ] **Step 1: Write the failing tests**

`tests/test_hooks.py`:

```python
import sys
from pathlib import Path

import pytest

from haa.config import load_config
from haa.core.hooks import deny_reason, make_pretooluse_hook


@pytest.fixture()
def cfg(tmp_path: Path):
    return load_config(tmp_path)


def test_read_of_data_file_denied(cfg) -> None:
    reason = deny_reason("Read", {"file_path": str(cfg.data_dir / "beneficiaries.xlsx")}, cfg)
    assert reason is not None
    assert "profile_dataset" in reason


def test_grep_and_glob_in_data_denied(cfg) -> None:
    assert deny_reason("Grep", {"path": str(cfg.data_dir)}, cfg)
    assert deny_reason("Glob", {"path": str(cfg.data_dir), "pattern": "*.xlsx"}, cfg)


def test_bash_touching_data_dir_denied(cfg) -> None:
    assert deny_reason("Bash", {"command": f"cat {cfg.data_dir / 'x.csv'}"}, cfg)


def test_project_docs_allowed(cfg) -> None:
    assert deny_reason("Read", {"file_path": str(cfg.docs_dir / "logframe.md")}, cfg) is None


def test_unrelated_paths_allowed(cfg, tmp_path: Path) -> None:
    assert deny_reason("Read", {"file_path": str(tmp_path / "README.md")}, cfg) is None
    assert deny_reason("Bash", {"command": "echo hello"}, cfg) is None


def test_mcp_tools_allowed(cfg) -> None:
    assert deny_reason("mcp__data__run_analysis", {"code": "print(1)"}, cfg) is None


@pytest.mark.skipif(sys.platform != "win32", reason="normcase folds case only on Windows")
def test_case_insensitive_on_windows_style_paths(cfg) -> None:
    path = str(cfg.data_dir / "file.csv").upper()
    assert deny_reason("Read", {"file_path": path}, cfg) is not None


async def test_adapter_denies_with_sdk_shape(cfg) -> None:
    blocked: list[tuple[str, str]] = []
    hook = make_pretooluse_hook(cfg, on_block=lambda t, r: blocked.append((t, r)))
    out = await hook(
        {"tool_name": "Read", "tool_input": {"file_path": str(cfg.data_dir / "a.csv")}},
        None,
        None,
    )
    decision = out["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert blocked and blocked[0][0] == "Read"


async def test_adapter_allows_with_empty_dict(cfg) -> None:
    hook = make_pretooluse_hook(cfg)
    out = await hook({"tool_name": "Bash", "tool_input": {"command": "ls"}}, None, None)
    assert out == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_hooks.py -v`
Expected: FAIL — `ImportError` (hooks module missing)

- [ ] **Step 3: Implement `src/haa/core/hooks.py`**

```python
"""PreToolUse hook enforcing the PII boundary: no direct reads of workspace/data."""

from __future__ import annotations

import os
from collections.abc import Callable

from haa.config import HaaConfig

BLOCK_MESSAGE = (
    "Direct access to raw data files is blocked by the PII boundary. "
    "Use profile_dataset / run_analysis from the data toolset instead."
)

_FILE_TOOLS = {"Read", "Grep", "Glob", "Edit", "Write", "NotebookEdit"}
_PATH_KEYS = ("file_path", "path", "notebook_path")


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _under_data_dir(path: str, config: HaaConfig) -> bool:
    return _norm(path).startswith(_norm(str(config.data_dir)))


def deny_reason(tool_name: str, tool_input: dict, config: HaaConfig) -> str | None:
    if tool_name in _FILE_TOOLS:
        for key in _PATH_KEYS:
            value = tool_input.get(key)
            if isinstance(value, str) and value and _under_data_dir(value, config):
                return BLOCK_MESSAGE
    elif tool_name == "Bash":
        command = str(tool_input.get("command", ""))
        needle = os.path.normcase(str(config.data_dir))
        if needle in os.path.normcase(command):
            return BLOCK_MESSAGE
    return None


def make_pretooluse_hook(
    config: HaaConfig, on_block: Callable[[str, str], None] | None = None
):
    async def hook(input_data: dict, tool_use_id, context) -> dict:
        tool_name = str(input_data.get("tool_name", ""))
        tool_input = input_data.get("tool_input") or {}
        reason = deny_reason(tool_name, tool_input, config)
        if reason is None:
            return {}
        if on_block is not None:
            on_block(tool_name, reason)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    return hook
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_hooks.py -v` — Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/haa/core/hooks.py tests/test_hooks.py
git commit -m "feat: PreToolUse PII hook denying direct raw-data access"
```

---

### Task 8: Telemetry

**Files:**
- Create: `src/haa/core/telemetry.py`
- Test: `tests/test_telemetry.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `SessionTelemetry(path: Path)` — `.log(kind: str, **payload) -> None` (appends one JSON line `{"ts": iso8601, "kind": ..., **payload}`), `.summary() -> dict` with keys `events`, `tool_calls`, `code_runs`, `pii_blocks`, `cost_usd`. Known kinds used by later tasks: `user_message`, `assistant_text`, `tool_use`, `tool_result`, `code_executed`, `pii_block`, `result`.

- [ ] **Step 1: Write the failing tests**

`tests/test_telemetry.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_telemetry.py -v` — Expected: FAIL (module missing).

- [ ] **Step 3: Implement `src/haa/core/telemetry.py`**

```python
"""Append-only JSONL session log: every turn, tool call, executed code, and cost."""

from __future__ import annotations

import json
from datetime import datetime, timezone
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
        record = {"ts": datetime.now(timezone.utc).isoformat(), "kind": kind, **payload}
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_telemetry.py -v` — Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/haa/core/telemetry.py tests/test_telemetry.py
git commit -m "feat: JSONL session telemetry with cost and PII-block counters"
```

---

### Task 9: Data MCP server (wires profiler, sandbox, docs, telemetry)

**Files:**
- Create: `src/haa/core/tools/datatools.py`
- Test: `tests/test_datatools.py`

**Interfaces:**
- Consumes: `discover_datasets`, `detect_pii_columns`, `profile_dataframe` (Task 3); `run_code` (Task 5); `filter_output` (Task 4); `list_docs`, `read_doc` (Task 6); `SessionTelemetry` (Task 8).
- Produces: `DATA_TOOL_NAMES: list[str]` = `["mcp__data__list_datasets", "mcp__data__profile_dataset", "mcp__data__run_analysis", "mcp__data__list_project_docs", "mcp__data__read_project_doc"]`; `DataToolbox(config: HaaConfig, telemetry: SessionTelemetry)` holding per-session caches (`pii_map`, profiles) and the **consecutive-failure counter** (spec §8: after 3 consecutive failed executions, `run_analysis` short-circuits with an honest-refusal message until `reset_failures()` is called — the session calls it on each new user question). Plain sync methods `list_datasets() -> str`, `profile_dataset(name) -> str`, `run_analysis(code) -> str`, `list_project_docs() -> str`, `read_project_doc(name) -> str`, `reset_failures() -> None` — unit tests target these directly. `build_data_server(toolbox: DataToolbox)` → the in-process MCP server object (register under key `"data"` in `mcp_servers`).

- [ ] **Step 1: Write the failing tests**

`tests/test_datatools.py`:

```python
import json
from pathlib import Path

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_datatools.py -v` — Expected: FAIL (module missing).

- [ ] **Step 3: Implement `src/haa/core/tools/datatools.py`**

```python
"""In-process MCP server exposing the data toolset to agents.

DataToolbox holds the logic (plain, testable); the @tool wrappers only adapt
to the SDK's content-block format.
"""

from __future__ import annotations

import json

import pandas as pd

from haa.config import HaaConfig
from haa.core.sandbox.executor import run_code
from haa.core.sandbox.guard import filter_output
from haa.core.telemetry import SessionTelemetry
from haa.core.tools.docs import DocError, list_docs, read_doc
from haa.core.tools.profiler import (
    detect_pii_columns,
    discover_datasets,
    profile_dataframe,
)

DATA_TOOL_NAMES = [
    "mcp__data__list_datasets",
    "mcp__data__profile_dataset",
    "mcp__data__run_analysis",
    "mcp__data__list_project_docs",
    "mcp__data__read_project_doc",
]

MAX_CONSECUTIVE_FAILURES = 3
_FAILURE_STOP_MESSAGE = (
    f"Execution disabled after {MAX_CONSECUTIVE_FAILURES} consecutive failed runs "
    "(spec: 3-attempt limit lives in the core, not the prompt). Stop trying; "
    "report honestly to the user what was attempted and why it failed. "
    "Do not invent results."
)


class DataToolbox:
    def __init__(self, config: HaaConfig, telemetry: SessionTelemetry) -> None:
        self.config = config
        self.telemetry = telemetry
        self._pii_map: dict[str, list[str]] = {}
        self._profiles: dict[str, dict] = {}
        self._consecutive_failures = 0

    def reset_failures(self) -> None:
        self._consecutive_failures = 0

    # -- datasets ---------------------------------------------------------
    def list_datasets(self) -> str:
        found = discover_datasets(self.config.data_dir)
        if not found:
            return "No datasets found in workspace/data/ (supported: .csv, .xlsx, .xls)."
        lines = [f"- {name} ({path.name}, {path.stat().st_size // 1024} KB)" for name, path in found.items()]
        return "Available datasets:\n" + "\n".join(lines)

    def _ensure_profiled(self, name: str) -> dict | None:
        if name in self._profiles:
            return self._profiles[name]
        found = discover_datasets(self.config.data_dir)
        if name not in found:
            return None
        path = found[name]
        df = pd.read_csv(path) if path.suffix.lower() == ".csv" else pd.read_excel(path)
        pii = detect_pii_columns(df)
        self._pii_map[name] = pii
        self._profiles[name] = profile_dataframe(df, name, pii)
        return self._profiles[name]

    def profile_dataset(self, name: str) -> str:
        profile = self._ensure_profiled(name)
        if profile is None:
            known = ", ".join(sorted(discover_datasets(self.config.data_dir))) or "(none)"
            return f"Unknown dataset {name!r}. Available: {known}"
        return json.dumps(profile, ensure_ascii=False, indent=1)

    def run_analysis(self, code: str) -> str:
        if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            return _FAILURE_STOP_MESSAGE
        # Profile every known dataset once so the PII map covers load_dataset calls.
        for ds in discover_datasets(self.config.data_dir):
            self._ensure_profiled(ds)
        self.telemetry.log("code_executed", code=code)
        result = run_code(code, self.config, self._pii_map)
        failed = result.timed_out or result.returncode != 0
        self._consecutive_failures = self._consecutive_failures + 1 if failed else 0
        if result.timed_out:
            return f"Execution timed out after {self.config.sandbox_timeout_s}s. Simplify the code."
        filtered = filter_output(
            result.stdout,
            row_cap=self.config.table_row_cap,
            size_cap=self.config.output_limit_bytes,
        )
        parts = [filtered.text] if filtered.text.strip() else []
        parts.extend(filtered.notices)
        if result.returncode != 0:
            err = filter_output(
                result.stderr, row_cap=self.config.table_row_cap, size_cap=4096
            ).text
            parts.append(f"[error, exit code {result.returncode}]\n{err}")
        return "\n".join(parts) or "(no output — use print() to return results)"

    # -- project docs -----------------------------------------------------
    def list_project_docs(self) -> str:
        docs = list_docs(self.config.docs_dir)
        if not docs:
            return "No project documents in workspace/project_docs/."
        return "Project documents:\n" + "\n".join(
            f"- {d['name']} ({d['type']}, {d['size_kb']} KB)" for d in docs
        )

    def read_project_doc(self, name: str) -> str:
        try:
            return read_doc(self.config.docs_dir, name)
        except DocError as exc:
            return f"Document not found or unreadable: {exc}"


def build_data_server(box: DataToolbox):
    from claude_agent_sdk import create_sdk_mcp_server, tool

    def _text(result: str) -> dict:
        return {"content": [{"type": "text", "text": result}]}

    @tool("list_datasets", "List tabular datasets available in the workspace", {})
    async def list_datasets(args: dict) -> dict:
        return _text(box.list_datasets())

    @tool(
        "profile_dataset",
        "Safe profile of a dataset: columns, dtypes, null rates, category values. "
        "Never returns raw rows.",
        {"name": str},
    )
    async def profile_dataset(args: dict) -> dict:
        return _text(box.profile_dataset(str(args["name"])))

    @tool(
        "run_analysis",
        "Execute pandas code in the local sandbox. Use load_dataset(name) to load "
        "data (PII columns are stripped automatically); print() results; save "
        "charts into CHARTS_DIR. Raw rows never reach you — work with aggregates.",
        {"code": str},
    )
    async def run_analysis(args: dict) -> dict:
        return _text(box.run_analysis(str(args["code"])))

    @tool("list_project_docs", "List project documentation files", {})
    async def list_project_docs(args: dict) -> dict:
        return _text(box.list_project_docs())

    @tool("read_project_doc", "Read one project document as text", {"name": str})
    async def read_project_doc(args: dict) -> dict:
        return _text(box.read_project_doc(str(args["name"])))

    return create_sdk_mcp_server(
        name="data",
        version="0.1.0",
        tools=[list_datasets, profile_dataset, run_analysis, list_project_docs, read_project_doc],
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_datatools.py -v` — Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/haa/core/tools/datatools.py tests/test_datatools.py
git commit -m "feat: in-process data MCP server wiring profiler, sandbox, docs, telemetry"
```

---

### Task 10: Agent definitions (orchestrator + analyst)

**Files:**
- Create: `src/haa/core/agents/__init__.py`, `src/haa/core/agents/orchestrator.py`, `src/haa/core/agents/analyst.py`, `src/haa/core/agents/registry.py`
- Test: `tests/test_agents.py`

**Interfaces:**
- Consumes: `HaaConfig.analyst_model`; `DATA_TOOL_NAMES` (Task 9).
- Produces: `ORCHESTRATOR_PROMPT: str`; `ANALYST_PROMPT: str`; `build_analyst(config: HaaConfig) -> AgentDefinition`; `build_agents(config: HaaConfig) -> dict[str, AgentDefinition]` (key `"analyst"`). Adding a future role = new module + one registry line.

- [ ] **Step 1: Write the failing tests**

`tests/test_agents.py`:

```python
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
    assert set(agent.tools) == set(DATA_TOOL_NAMES)
    assert agent.prompt == ANALYST_PROMPT
    assert "data" in agent.description.lower()


def test_analyst_model_configurable(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text('analyst_model = "claude-sonnet-5"', encoding="utf-8")
    assert build_analyst(_cfg(tmp_path)).model == "claude-sonnet-5"


def test_registry(tmp_path: Path) -> None:
    agents = build_agents(_cfg(tmp_path))
    assert set(agents) == {"analyst"}


def test_prompts_carry_discipline() -> None:
    for needle in ("never fabricate", "SADD", "load_dataset", "aggregat"):
        assert needle.lower() in ANALYST_PROMPT.lower()
    for needle in ("delegate", "analyst", "honest"):
        assert needle.lower() in ORCHESTRATOR_PROMPT.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_agents.py -v` — Expected: FAIL (modules missing).

- [ ] **Step 3: Implement the three modules**

`src/haa/core/agents/orchestrator.py`:

```python
"""System prompt for the main-loop orchestrator agent."""

ORCHESTRATOR_PROMPT = """\
You are the orchestrator of a humanitarian data-analytics assistant used by an
information-management analyst. You answer in the user's language (Russian,
Ukrainian, or English).

Routing rules:
- Any question about datasets, indicators, numbers, trends, or data quality:
  delegate to the `analyst` subagent via the Task tool. Pass the user's question
  verbatim plus any relevant conversation context.
- Meta questions (what can you do, what data is loaded): you may answer directly,
  using list_datasets / list_project_docs if needed.

Honesty rules (non-negotiable):
- Be honest about failures: if the analyst could not compute something, say so
  and explain why. Never invent or estimate numbers that were not computed.
- Numbers in answers must come from executed analysis, not from memory.
- Raw beneficiary data is protected by a PII boundary; you and the analyst work
  only with schemas, aggregates, and project documentation.
"""
```

`src/haa/core/agents/analyst.py`:

```python
"""Analyst subagent: writes pandas code, interprets aggregates, never sees raw rows."""

from claude_agent_sdk import AgentDefinition

from haa.config import HaaConfig
from haa.core.tools.datatools import DATA_TOOL_NAMES

ANALYST_PROMPT = """\
You are a senior humanitarian data analyst (IMC-style discipline). You answer
questions about datasets by writing pandas code, never by guessing.

Workflow for every data question:
1. list_datasets, then profile_dataset for anything you have not profiled yet.
   The profile is your only view of the schema — you cannot see raw rows.
2. If the question involves project targets or indicator definitions, check
   list_project_docs / read_project_doc first.
3. Write pandas code and call run_analysis. In the sandbox:
   - load data ONLY via load_dataset("<name>") — PII columns are stripped;
   - print() the aggregates you need; keep tables small (they are truncated);
   - save charts with matplotlib into CHARTS_DIR (plt.savefig(f"{CHARTS_DIR}/name.png"))
     and mention the saved path in your answer.
4. Validate before concluding: check group sizes, null rates in key fields,
   duplicates, and obviously invalid values; mention material caveats.

Discipline (non-negotiable):
- NEVER fabricate a number. Every figure in your answer must appear in
  run_analysis output. If code fails three times, report honestly what failed.
- Work with aggregates only; never try to print raw rows or PII.
- Disaggregate by sex/age/disability (SADD) when the data allows and it is
  relevant to the question.
- State assumptions explicitly (e.g., how duplicates or missing values were
  handled). Distinguish correlation from causation.
"""


def build_analyst(config: HaaConfig) -> AgentDefinition:
    return AgentDefinition(
        description=(
            "Data analyst for workspace datasets: profiling, pandas analysis, "
            "indicator calculations, charts. Delegate any data question here."
        ),
        prompt=ANALYST_PROMPT,
        tools=list(DATA_TOOL_NAMES),
        model=config.analyst_model,
    )
```

`src/haa/core/agents/registry.py`:

```python
"""Single place where agent roles are registered (future roles plug in here)."""

from claude_agent_sdk import AgentDefinition

from haa.config import HaaConfig
from haa.core.agents.analyst import build_analyst


def build_agents(config: HaaConfig) -> dict[str, AgentDefinition]:
    return {"analyst": build_analyst(config)}
```

- [ ] **Step 4: Verify SDK symbols, then run tests**

Run: `uv run python -c "import dataclasses; from claude_agent_sdk import AgentDefinition; print([f.name for f in dataclasses.fields(AgentDefinition)])"`
Expected: field names include `description`, `prompt`, `tools`, `model`. If the installed SDK names differ (e.g. `model` accepts only aliases), adapt `build_analyst` — not the tests' intent (model value must come from config).

Run: `uv run pytest tests/test_agents.py -v` — Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/haa/core/agents tests/test_agents.py
git commit -m "feat: orchestrator and analyst agent definitions with role registry"
```

---

### Task 11: Session (options assembly, event stream, budget)

**Files:**
- Create: `src/haa/core/session.py`
- Test: `tests/test_session.py`

**Interfaces:**
- Consumes: everything above — `build_agents`, `DataToolbox`/`build_data_server`, `DATA_TOOL_NAMES`, `make_pretooluse_hook`, `SessionTelemetry`, `HaaConfig`. The session owns the `DataToolbox` instance and calls `toolbox.reset_failures()` at the start of every `ask()` (the 3-failure limit is per user question).
- Produces: `SessionEvent` dataclass (`kind: str` in `{"text", "tool", "result"}`, `text: str`); `events_from_message(msg: object, telemetry: SessionTelemetry) -> list[SessionEvent]` (duck-typed on `type(msg).__name__` — testable with fakes); `BudgetExceeded(RuntimeError)`; `AnalyticsSession(config)` — async context manager with `.ask(question: str) -> AsyncIterator[SessionEvent]`, `.telemetry` attribute, `.build_options() -> ClaudeAgentOptions`. Budget enforced client-side too: once accumulated cost ≥ `max_budget_usd`, the next `ask()` raises `BudgetExceeded`.

- [ ] **Step 1: Write the failing tests**

`tests/test_session.py`:

```python
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
        [TextBlock("Looking at the data."), ToolUseBlock("mcp__data__run_analysis", {"code": "print(1)"})]
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_session.py -v` — Expected: FAIL (module missing).

- [ ] **Step 3: Implement `src/haa/core/session.py`**

```python
"""AnalyticsSession: the single entry point into the core for any UI."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone

from haa.config import HaaConfig
from haa.core.agents.orchestrator import ORCHESTRATOR_PROMPT
from haa.core.agents.registry import build_agents
from haa.core.hooks import make_pretooluse_hook
from haa.core.telemetry import SessionTelemetry
from haa.core.tools.datatools import DATA_TOOL_NAMES, DataToolbox, build_data_server


class BudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class SessionEvent:
    kind: str  # "text" | "tool" | "result"
    text: str


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
        telemetry.log("result", cost_usd=cost)
        text = f"cost=${cost:.4f}" if isinstance(cost, (int, float)) else "done"
        events.append(SessionEvent("result", text))
    return events


class AnalyticsSession:
    def __init__(self, config: HaaConfig) -> None:
        self.config = config
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        self.telemetry = SessionTelemetry(config.logs_dir / f"session-{stamp}.jsonl")
        self._toolbox = DataToolbox(config, self.telemetry)
        self._server = build_data_server(self._toolbox)
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
            mcp_servers={"data": self._server},
            allowed_tools=["Task", *DATA_TOOL_NAMES],
            disallowed_tools=["WebSearch", "WebFetch"],
            permission_mode="dontAsk",
            hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[hook])]},
            max_budget_usd=self.config.max_budget_usd,
            cwd=str(self.config.workspace),
        )

    def _check_budget(self) -> None:
        if self._spent_usd >= self.config.max_budget_usd:
            raise BudgetExceeded(
                f"Session budget exhausted: ${self._spent_usd:.2f} of "
                f"${self.config.max_budget_usd:.2f}. Start a new session or raise "
                "max_budget_usd in config.toml."
            )

    async def __aenter__(self) -> "AnalyticsSession":
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
                if event.kind == "result" and event.text.startswith("cost=$"):
                    self._spent_usd += float(event.text.removeprefix("cost=$"))
                yield event
```

- [ ] **Step 4: Verify SDK option fields, then run tests**

Run: `uv run python -c "import dataclasses; from claude_agent_sdk import ClaudeAgentOptions; print(sorted(f.name for f in dataclasses.fields(ClaudeAgentOptions)))"`
Check that `model`, `system_prompt`, `agents`, `mcp_servers`, `allowed_tools`, `disallowed_tools`, `permission_mode`, `hooks`, `cwd` exist. If `max_budget_usd` is absent in the installed SDK version, remove that kwarg from `build_options` — the client-side `_check_budget` guard already covers the requirement. If `permission_mode="dontAsk"` is rejected, use the closest deny-by-default literal the SDK offers and update the test's expected value to match.

Run: `uv run pytest tests/test_session.py -v` — Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/haa/core/session.py tests/test_session.py
git commit -m "feat: analytics session with options assembly, event stream, budget guard"
```

---

### Task 12: CLI REPL

**Files:**
- Create: `src/haa/cli/__init__.py`, `src/haa/cli/app.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `AnalyticsSession`, `load_config`/`ConfigError`, `haa.demo.generate`.
- Produces: console entry `haa` with subcommands `chat` (`--workspace`, `--config`) and `demo` (`--workspace`); `build_parser() -> argparse.ArgumentParser`; `validate_workspace(cfg) -> list[str]` (warnings: no datasets / no docs). REPL commands: `/quit`, `/exit`, `/cost`.

- [ ] **Step 1: Write the failing tests**

`tests/test_cli.py`:

```python
from pathlib import Path

from haa.cli.app import build_parser, validate_workspace
from haa.config import load_config


def test_parser_chat_defaults() -> None:
    args = build_parser().parse_args(["chat"])
    assert args.command == "chat"
    assert args.workspace == Path("workspace")


def test_parser_demo() -> None:
    args = build_parser().parse_args(["demo", "--workspace", "ws2"])
    assert args.command == "demo"
    assert args.workspace == Path("ws2")


def test_validate_empty_workspace_warns(tmp_path: Path) -> None:
    warnings = validate_workspace(load_config(tmp_path))
    assert any("dataset" in w.lower() for w in warnings)
    assert any("haa demo" in w for w in warnings)


def test_validate_demo_workspace_clean(demo_workspace: Path) -> None:
    assert validate_workspace(load_config(demo_workspace)) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -v` — Expected: FAIL (module missing).

- [ ] **Step 3: Implement `src/haa/cli/app.py`**

```python
"""CLI: `haa chat` (REPL over AnalyticsSession) and `haa demo` (generate data)."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown

from haa.config import ConfigError, HaaConfig, load_config
from haa.core.session import AnalyticsSession, BudgetExceeded


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="haa", description="Humanitarian Analytics Agents")
    sub = parser.add_subparsers(dest="command", required=True)

    chat = sub.add_parser("chat", help="Interactive analytics chat")
    chat.add_argument("--workspace", type=Path, default=Path("workspace"))
    chat.add_argument("--config", type=Path, default=None)

    demo = sub.add_parser("demo", help="Generate the synthetic demo workspace")
    demo.add_argument("--workspace", type=Path, default=Path("workspace"))
    return parser


def validate_workspace(cfg: HaaConfig) -> list[str]:
    from haa.core.tools.profiler import discover_datasets

    warnings: list[str] = []
    if not discover_datasets(cfg.data_dir):
        warnings.append(
            "No datasets in workspace/data/ — add .csv/.xlsx files, or run `haa demo` "
            "to generate synthetic demo data."
        )
    if not any(cfg.docs_dir.iterdir()):
        warnings.append("No project docs in workspace/project_docs/ (optional).")
    return warnings


async def _repl(cfg: HaaConfig, console: Console) -> None:
    async with AnalyticsSession(cfg) as session:
        console.print("[bold]haa[/bold] — ask about your data. /cost for spend, /quit to exit.")
        while True:
            try:
                question = console.input("[bold cyan]you>[/bold cyan] ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not question:
                continue
            if question in {"/quit", "/exit"}:
                break
            if question == "/cost":
                console.print(session.telemetry.summary())
                continue
            try:
                async for event in session.ask(question):
                    if event.kind == "text":
                        console.print(Markdown(event.text))
                    elif event.kind == "tool":
                        console.print(f"[dim]· {event.text}[/dim]")
                    else:
                        console.print(f"[dim]{event.text}[/dim]")
            except BudgetExceeded as exc:
                console.print(f"[red]{exc}[/red]")
                break
    console.print(f"Session log: {session.telemetry.path}")
    console.print(session.telemetry.summary())


def main() -> int:
    args = build_parser().parse_args()
    console = Console()

    if args.command == "demo":
        from haa.demo import generate

        args.workspace.mkdir(parents=True, exist_ok=True)
        path = generate(args.workspace)
        console.print(f"Demo data written to {path}")
        return 0

    try:
        cfg = load_config(args.workspace, args.config)
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1
    for warning in validate_workspace(cfg):
        console.print(f"[yellow]{warning}[/yellow]")
    try:
        asyncio.run(_repl(cfg, console))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests, then a manual smoke check**

Run: `uv run pytest tests/test_cli.py -v` — Expected: all PASS.
Run: `uv run haa demo --workspace workspace` — Expected: prints the xlsx path; `workspace/` stays untracked (gitignored).
Optional (needs `ANTHROPIC_API_KEY`, costs money — skip in CI): `uv run haa chat --workspace workspace`, ask one question, `/quit`.

- [ ] **Step 5: Commit**

```bash
git add src/haa/cli tests/test_cli.py
git commit -m "feat: haa CLI with chat REPL and demo generator subcommand"
```

---

### Task 13: CI + README

**Files:**
- Create: `.github/workflows/ci.yml`, `README.md`

**Interfaces:**
- Consumes: the whole test suite; `addopts = "-m 'not api'"` already excludes API tests.
- Produces: green CI on push; portfolio-facing README.

- [ ] **Step 1: Write `.github/workflows/ci.yml`**

```yaml
name: ci
on:
  push:
  pull_request:
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
      - run: uv sync --all-groups
      - run: uv run ruff check .
      - run: uv run pytest -v
```

- [ ] **Step 2: Write `README.md`**

Full content (adjust the repo URL when pushing to GitHub):

```markdown
# Humanitarian Analytics Agents (haa)

A multi-agent data-analytics assistant for humanitarian information management,
built on the Claude Agent SDK. Ask questions about your survey data
(KoboToolbox-style exports) in plain language; an analyst agent writes pandas
code, runs it locally, and answers with validated aggregates and charts —
**raw beneficiary data never leaves your machine**.

## Why this exists

Humanitarian datasets are full of personal data (names, phones, GPS). Sending
them to an LLM API is not acceptable. This project demonstrates an architecture
where agents analyze data they cannot see: they work through schemas,
statistics, and locally executed code.

## The PII boundary (threat model)

We protect against *accidental* leakage of personal data into API calls, not
against a malicious agent. Four enforced layers:

1. **Prompt discipline** — agents are instructed to work with aggregates only.
2. **PreToolUse hook** — direct file reads of `workspace/data/` are denied at
   the framework level.
3. **Sandbox loader** — `load_dataset()` strips auto-detected PII columns
   before code ever touches a DataFrame.
4. **Output guard** — sandbox output is redacted (phones, GPS) and truncated
   before it reaches the model; every executed snippet is logged for audit.

Known limitation: sandbox code could read files directly, bypassing the loader
(layer 4 and the audit log exist for exactly that case). `workspace/project_docs/`
is a deliberate exception — project documentation (logframes, proposals) is
*meant* to be read by the model. Never put beneficiary data there.

## Quickstart

    uv sync
    uv run haa demo --workspace workspace     # synthetic dataset, fake PII
    export ANTHROPIC_API_KEY=sk-ant-...
    uv run haa chat --workspace workspace

Try: *"How many unique households per oblast? Disaggregate by sex of head."*
or *"How are we progressing toward the Indicator 1.1 target?"*

## Architecture

    CLI (rich REPL)
      └─ AnalyticsSession  ──ClaudeAgentOptions──►  Claude Agent SDK
           ├─ orchestrator (main loop) ──Task──► analyst subagent
           ├─ PreToolUse PII hook (deny raw-data reads)
           ├─ in-process MCP server: list_datasets / profile_dataset /
           │    run_analysis / list_project_docs / read_project_doc
           └─ telemetry (JSONL: every turn, tool call, code snippet, cost)
                     │
                     ▼
           local sandbox subprocess (no network, workspace-only,
           PII-stripping load_dataset, output guard)

## Cost control

Sessions carry a budget (`max_budget_usd`, default $2). Spend is tracked from
API result messages and written to the session log; `/cost` shows it live.

## Development

    uv run pytest          # unit tests, no API key needed
    uv run pytest -m api   # smoke evals against the demo dataset (costs money)
    uv run ruff check .

Everything except `tests/test_smoke_api.py` runs offline. CI runs lint + unit
tests on every push.

## Roadmap

This is subproject 1 of 6: core + analyst agent. Next: source connectors
(KoboToolbox, ona.io, SharePoint), a data-cleaning agent, indicator registry +
XLSForm designer, reporting (5W/MEAL), a Power BI engineer agent (via MCP),
and a web UI. Design docs live in `docs/superpowers/specs/`.
```

- [ ] **Step 3: Verify everything passes locally**

Run: `uv run ruff check . && uv run pytest -v`
Expected: clean lint, all non-api tests PASS.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml README.md
git commit -m "docs: README with threat model and quickstart; add CI workflow"
```

---

### Task 14: Smoke eval (API, manual)

**Files:**
- Test: `tests/test_smoke_api.py`

**Interfaces:**
- Consumes: `AnalyticsSession`, demo workspace, `ANTHROPIC_API_KEY` from the environment.
- Produces: `pytest -m api` suite of 6 checks — canonical questions with independently computed expected numbers, a chart-saving scenario, and a telemetry PII scan. Not run in CI.

- [ ] **Step 1: Write the eval tests**

`tests/test_smoke_api.py`:

```python
"""Smoke evals: real API calls against the synthetic demo workspace.

Run manually: uv run pytest -m api -v
Each test costs real money (a few cents at default models). Requires
ANTHROPIC_API_KEY in the environment.
"""

import os
from pathlib import Path

import pandas as pd
import pytest

from haa.config import load_config
from haa.core.session import AnalyticsSession

pytestmark = [
    pytest.mark.api,
    pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="no API key"),
]


async def _answer(ws: Path, question: str) -> str:
    async with AnalyticsSession(load_config(ws)) as session:
        chunks = [e.text async for e in session.ask(question) if e.kind == "text"]
    return "\n".join(chunks)


@pytest.fixture(scope="module")
def df(demo_workspace: Path) -> pd.DataFrame:
    return pd.read_excel(demo_workspace / "data" / "beneficiaries.xlsx")


async def test_unique_households(demo_workspace: Path, df: pd.DataFrame) -> None:
    expected = df["_uuid"].nunique()  # 3000
    answer = await _answer(demo_workspace, "How many unique household submissions are in the data?")
    assert str(expected) in answer.replace(" ", "").replace(",", "").replace(" ", "")


async def test_households_per_oblast(demo_workspace: Path, df: pd.DataFrame) -> None:
    top_oblast = df["oblast"].value_counts().idxmax()
    answer = await _answer(demo_workspace, "Скільки домогосподарств по областях? Дай таблицю.")
    assert top_oblast.split()[0][:5] in answer  # oblast name appears


async def test_duplicates_found(demo_workspace: Path, df: pd.DataFrame) -> None:
    expected_dupes = len(df) - df["_uuid"].nunique()  # 30
    answer = await _answer(demo_workspace, "Есть ли дубликаты сабмишенов? Сколько?")
    assert str(expected_dupes) in answer


async def test_indicator_progress_uses_logframe(demo_workspace: Path, df: pd.DataFrame) -> None:
    answer = await _answer(
        demo_workspace, "What is the target for Indicator 1.1 and what is our current progress?"
    )
    assert "2500" in answer                       # target read from logframe.md
    assert str(df["_uuid"].nunique()) in answer   # reached, computed from data


async def test_chart_saved(demo_workspace: Path) -> None:
    cfg = load_config(demo_workspace)
    before = set(cfg.charts_dir.glob("*.png"))
    async with AnalyticsSession(cfg) as session:
        async for _ in session.ask(
            "Побудуй і збережи графік кількості домогосподарств по областях."
        ):
            pass
    assert set(cfg.charts_dir.glob("*.png")) - before  # a new chart file appeared


async def test_no_raw_pii_in_telemetry(demo_workspace: Path) -> None:
    cfg = load_config(demo_workspace)
    async with AnalyticsSession(cfg) as session:
        async for _ in session.ask("Give a quick overview of the beneficiaries dataset."):
            pass
        log_text = session.telemetry.path.read_text(encoding="utf-8")
    df = pd.read_excel(demo_workspace / "data" / "beneficiaries.xlsx")
    for phone in df["resp_phone"].astype(str).head(20):
        assert phone not in log_text
    for name in df["resp_name"].astype(str).head(20):
        assert name not in log_text
```

- [ ] **Step 2: Verify the suite is excluded by default**

Run: `uv run pytest -v` — Expected: smoke tests show as deselected, everything else PASS.

- [ ] **Step 3: Run the eval manually (requires API key; costs money)**

Run: `uv run pytest -m api -v`
Expected: all PASS. If an assertion fails on *formatting* (e.g. the model writes "3 000"), strengthen the normalization in the test, not the agent prompt. If it fails on *substance* (wrong number, fabricated value), treat it as a real bug: check telemetry (`workspace/logs/`) for the executed code and fix prompts/tools accordingly.

- [ ] **Step 4: Commit**

```bash
git add tests/test_smoke_api.py
git commit -m "test: API smoke evals with independently computed expected values"
```

---

## Acceptance Checklist (from spec §10)

- [ ] `uv run haa chat --workspace ./workspace` opens a working chat (Task 12).
- [ ] Demo scenario: `haa demo` → canonical question → correct aggregate table + saved chart (Tasks 2, 12, 14).
- [ ] Context scenario: Indicator 1.1 question uses `logframe.md` + computed fact (Task 14).
- [ ] No raw data rows in any API call — verifiable via telemetry (Tasks 8, 11, 14 `test_no_raw_pii_in_telemetry`).
- [ ] Unit tests + ruff green in CI; smoke eval green locally (Tasks 13, 14).
- [ ] README: architecture diagram, demo instructions, PII threat model incl. `project_docs/` warning (Task 13).
