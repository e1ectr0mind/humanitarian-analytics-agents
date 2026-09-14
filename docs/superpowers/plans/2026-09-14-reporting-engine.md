# Deterministic Reports (slice 4a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A deterministic indicator engine plus two reports — indicator progress against logframe targets and the 5W matrix — written as markdown + xlsx by a new `reporter` agent role and by a `haa report` CLI that needs no LLM.

**Architecture:** A new pure-logic package `src/haa/reporting/` (no LLM): `sources` resolves datasets (clean-copy preference, period scoping, PII detection), `measures` evaluates one `measure` block, `engine` evaluates the whole indicators registry, `mapping` validates and stores `workspace/5w.yaml`, `fivew` builds the 5W table, `render` writes md + xlsx. A fourth in-process MCP server `reports` (`core/tools/reporttools.py`) exposes it to a fourth agent role `reporter` (no `run_analysis`), to the analyst (`compute_indicators`) and to the CLI.

**Tech Stack:** Python 3.12, pandas 3.0.5 (locked in uv.lock), openpyxl, PyYAML, claude-agent-sdk; pytest + ruff.

**Spec:** `docs/superpowers/specs/2026-09-14-reporting-engine-design.md`

## Global Constraints

- Report numbers come only from the deterministic engine; the `reporter` role has no `mcp__data__run_analysis`.
- Report files: `reports/indicators_<YYYY-MM-DD>.md` + `.xlsx` and `reports/5w_<YYYY-MM-DD>.md` + `.xlsx`; a same-day rebuild overwrites.
- `<dataset>_clean.xlsx` is preferred when present; every report states which copy it used.
- The reporting period is optional `{field, start, end}`, inclusive of the whole end day, applied before any computation; a report without one says "all records".
- PII guard: no PII column (the profiler's detector) in `measure.field`, in a `measure` filter, in `disaggregation`, or in any 5W column. Tools return aggregates and file paths, never data rows.
- `5w.yaml` validation is strict: unknown keys rejected at every level, addressed error messages, nothing written on failure.
- Every user-facing error is one friendly sentence naming the next action — never a traceback.
- No new dependencies.
- `PYTEST` in the steps means the project venv's pytest: from the main checkout `.\.venv\Scripts\python.exe -m pytest`; from a git worktree `$env:PYTHONPATH='<worktree>\src'; C:\Users\vpolishchuk\projects\data-analyst\.venv\Scripts\python.exe -m pytest`. `uv` is not on PATH, and the venv's editable install points at the main checkout, so PYTHONPATH is mandatory in a worktree. `RUFF` means the same interpreter with `-m ruff check .`.
- Commits: conventional messages in English, ending with the Co-Authored-By trailer given in the dispatch.

## Refinements to the spec

The plan argues from the spec; these are the places it goes further. Every code block in Tasks 1–7 and the reporter prompt in Task 8 were run on a throwaway prototype first (116 tests green, ruff clean, pandas 3.0.5).

1. **File layout.** Spec §3 lists `engine.py`, `fivew.py`, `storage.py`, `render.py`. The plan splits `measures.py` (one measure block) out of `engine.py` (registry orchestration), adds `sources.py` (dataset resolution shared by both reports), and puts storage in `mapping.py` next to the 5W schema, leaving `fivew.py` to build the table. No task edits a file that another task of this plan created.
2. **Date parsing** (`sources.parse_dates`). pandas' default parser turns `2026-08-31 18:30` into NaT when the column starts with plain dates (the record silently drops out of the period), and raises "Mixed timezones detected" on Kobo timestamps such as `2026-08-31T18:30:00+03:00`. The plan parses with `format="mixed", utc=True`; values carrying an offset are compared in UTC (stated in the docstring).
3. **PII guard on filters.** A `filter` that names a PII column is a membership test, so it is refused like a PII `field`.
4. **Disaggregation errors are per dimension.** A missing or PII disaggregation column records a breakdown error; the indicator's headline value is still computed (spec §5 says the refusal is "по конкретному элементу").
5. **Provenance in xlsx.** Both workbooks get an `About` sheet (generated date, period, source file, clean/raw, row counts), so the period and the source travel with the file that leaves the team.
6. **`SourceInfo`** also carries `rows_total` and `rows_bad_date` (the "rows without a valid date" number in the header).
7. **5W schema details.** `when` is required (`granularity` defaults to `month`), `fixed` is optional; output-column collisions and the reserved names `Period`, `Activity`, `Beneficiaries` are rejected. Unique reach counts beneficiaries with at least one activity; records without an activity are counted separately.
8. **Telemetry payload.** Spec §3 names the field `kind`, but `SessionTelemetry.log(kind, **payload)` already uses `kind` for the event name (the prototype crashed with "got multiple values for argument 'kind'"). The payload field is `report`: `report_built` with `report="indicators"` or `report="5w"`.
9. **Optional tool arguments.** The SDK marks every argument of a `{name: type}` schema as required (verified: `_build_input_schema`), so the three period tools declare a full JSON Schema with `"required": []` (`PERIOD_SCHEMA`).
10. **Analyst prompt.** Its measure semantics said `count` ignores `field` and that disaggregation names are conceptual; both now match the engine (`count` with `field` = non-null values; disaggregation entries are real columns).

## File Structure

| File | Responsibility |
|---|---|
| `src/haa/reporting/__init__.py` | package marker — empty, like the other packages |
| `src/haa/reporting/sources.py` | `ReportError`, `Period`, `SourceInfo`; clean-copy resolution, date parsing, period scoping, PII detection |
| `src/haa/reporting/measures.py` | one `measure` block on a frame: aggregations, filters, PII guard, category helpers |
| `src/haa/reporting/engine.py` | result model, breakdowns, registry evaluation across datasets |
| `src/haa/reporting/mapping.py` | `5w.yaml`: strict validation, parse / load / save |
| `src/haa/reporting/fivew.py` | 5W table: grouping, activity split, distinct counts, disaggregation columns |
| `src/haa/reporting/render.py` | markdown + xlsx for both reports |
| `src/haa/core/tools/reporttools.py` | `ReportToolbox`, `run_summary`, the `reports` MCP server |
| `src/haa/core/agents/reporter.py` | `REPORTER_PROMPT`, `build_reporter` |
| `config.py`, `core/agents/{registry,analyst,orchestrator}.py`, `core/session.py`, `cli/app.py`, `README.md` | modified: wiring |
| `tests/test_reporting_{sources,measures,engine,mapping,fivew,render}.py`, `tests/test_reporttools.py`, `tests/test_reporter_agent.py` | new tests |
| `tests/conftest.py`, `tests/test_{agents,cleaner_agent,designer_agent,config,session,cli,smoke_api}.py` | modified tests |

---

### Task 1: Report data sources — clean-copy preference, period scoping, PII detection

**Files:**
- Create: `src/haa/reporting/__init__.py` (empty file)
- Create: `src/haa/reporting/sources.py`
- Modify: `tests/conftest.py` (full replacement below; the existing `demo_workspace` fixture is kept as is)
- Test: `tests/test_reporting_sources.py`

**Interfaces:**
- Consumes: existing `haa.core.tools.profiler.detect_pii_columns(df) -> list[str]` and `discover_datasets(data_dir) -> dict[str, Path]`.
- Produces:
  - `class ReportError(ValueError)`; `class DatasetNotFound(ReportError)`
  - `@dataclass(frozen=True) Period(field: str, start: date, end: date)`; `Period.label() -> str`, e.g. `"2026-06-01 .. 2026-08-31 (by submission_date)"`
  - `@dataclass(frozen=True) SourceInfo(dataset: str, path: Path, used_clean: bool, rows_total: int, rows_in_scope: int, rows_excluded_by_period: int, rows_bad_date: int)`
  - `parse_period(date_field: str | None, start: str | None, end: str | None) -> Period | None`
  - `parse_dates(series: pd.Series) -> pd.Series`
  - `resolve_dataset(data_dir: Path, name: str) -> tuple[Path, bool]`
  - `load_scoped(data_dir: Path, name: str, period: Period | None) -> tuple[pd.DataFrame, SourceInfo, list[str]]` — the list is the PII columns
  - `tests/conftest.py`: constant `DEMO_REGISTRY` (1.1 `count_unique`, 1.2 `percent`, 2.1 without `measure`) and fixture `report_workspace` — a private, mutable copy of the demo dataset + logframe with `indicators.yaml` written

- [ ] **Step 1: Replace `tests/conftest.py` and write the failing tests**

`tests/conftest.py`:

```python
import shutil
from pathlib import Path

import pytest
import yaml

from haa.demo import generate

DEMO_REGISTRY = {
    "indicators": [
        {
            "code": "1.1",
            "name": {"uk": "Охоплені домогосподарства", "en": "Households reached"},
            "definition": "Unique households with at least one service (by _uuid)",
            "target": {"value": 2500, "unit": "households"},
            "disaggregation": ["oblast", "head_sex"],
            "source": "beneficiaries",
            "measure": {
                "dataset": "beneficiaries",
                "aggregation": "count_unique",
                "field": "_uuid",
                "filter": None,
            },
        },
        {
            "code": "1.2",
            "name": {"uk": "Частка домогосподарств з жінкою на чолі", "en": "% female-headed"},
            "definition": "Share of reached households headed by a woman",
            "target": {"value": 55, "unit": "percent"},
            "disaggregation": ["oblast"],
            "source": "beneficiaries",
            "measure": {
                "dataset": "beneficiaries",
                "aggregation": "percent",
                "numerator": {"field": "_uuid", "filter": "head_sex == 'female'"},
                "denominator": {"field": "_uuid", "filter": None},
            },
        },
        {
            "code": "2.1",
            "name": {"uk": "Домогосподарства з грошовою допомогою", "en": "Households with cash"},
            "definition": "Households receiving cash assistance",
            "target": {"value": 1200, "unit": "households"},
            "disaggregation": ["oblast", "head_sex"],
            "source": "beneficiaries",
        },
    ]
}


@pytest.fixture(scope="session")
def demo_workspace(tmp_path_factory: pytest.TempPathFactory) -> Path:
    ws = tmp_path_factory.mktemp("demo_ws")
    (ws / "data").mkdir()
    (ws / "project_docs").mkdir()
    generate(ws)
    return ws


@pytest.fixture()
def report_workspace(tmp_path: Path, demo_workspace: Path) -> Path:
    """A private copy of the demo dataset plus DEMO_REGISTRY — safe to mutate."""
    (tmp_path / "data").mkdir()
    (tmp_path / "project_docs").mkdir()
    shutil.copy(demo_workspace / "data" / "beneficiaries.xlsx", tmp_path / "data")
    shutil.copy(demo_workspace / "project_docs" / "logframe.md", tmp_path / "project_docs")
    (tmp_path / "indicators.yaml").write_text(
        yaml.safe_dump(DEMO_REGISTRY, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return tmp_path
```

`tests/test_reporting_sources.py`:

```python
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from haa.reporting.sources import (
    DatasetNotFound,
    Period,
    ReportError,
    load_scoped,
    parse_period,
    resolve_dataset,
)


def _write_csv(path: Path, rows: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_parse_period_none_without_dates() -> None:
    assert parse_period("submission_date", None, None) is None
    assert parse_period(None, "", "") is None


def test_parse_period_valid() -> None:
    period = parse_period("submission_date", "2026-06-01", "2026-08-31")
    assert period == Period("submission_date", date(2026, 6, 1), date(2026, 8, 31))
    assert period.label() == "2026-06-01 .. 2026-08-31 (by submission_date)"


def test_parse_period_needs_both_dates() -> None:
    with pytest.raises(ReportError, match="both"):
        parse_period("submission_date", "2026-06-01", None)


def test_parse_period_needs_date_field() -> None:
    with pytest.raises(ReportError, match="date_field"):
        parse_period(None, "2026-06-01", "2026-08-31")


def test_parse_period_rejects_bad_date() -> None:
    with pytest.raises(ReportError, match="2026-06-01"):
        parse_period("d", "June 1", "2026-08-31")


def test_parse_period_rejects_reversed_range() -> None:
    with pytest.raises(ReportError, match="after"):
        parse_period("d", "2026-09-01", "2026-08-31")


def test_resolve_prefers_clean_copy(tmp_path: Path) -> None:
    _write_csv(tmp_path / "hh.csv", {"a": [1]})
    _write_csv(tmp_path / "hh_clean.csv", {"a": [1]})
    path, used_clean = resolve_dataset(tmp_path, "hh")
    assert path.name == "hh_clean.csv" and used_clean is True


def test_resolve_raw_when_no_clean_copy(tmp_path: Path) -> None:
    _write_csv(tmp_path / "hh.csv", {"a": [1]})
    path, used_clean = resolve_dataset(tmp_path, "hh")
    assert path.name == "hh.csv" and used_clean is False


def test_resolve_missing_lists_available(tmp_path: Path) -> None:
    _write_csv(tmp_path / "hh.csv", {"a": [1]})
    with pytest.raises(DatasetNotFound, match="Available: hh"):
        resolve_dataset(tmp_path, "ghost")


def test_dataset_not_found_is_a_report_error() -> None:
    assert issubclass(DatasetNotFound, ReportError)


def test_load_scoped_applies_inclusive_period(tmp_path: Path) -> None:
    _write_csv(
        tmp_path / "hh.csv",
        {
            "id": ["a", "b", "c", "d", "e"],
            "when": ["2026-05-31", "2026-06-01", "2026-08-31 18:30", "not a date", "2026-09-01"],
        },
    )
    period = Period("when", date(2026, 6, 1), date(2026, 8, 31))
    df, info, _ = load_scoped(tmp_path, "hh", period)
    assert sorted(df["id"]) == ["b", "c"]
    assert info.rows_total == 5
    assert info.rows_in_scope == 2
    assert info.rows_excluded_by_period == 3
    assert info.rows_bad_date == 1


def test_load_scoped_without_period_keeps_everything(tmp_path: Path) -> None:
    _write_csv(tmp_path / "hh.csv", {"id": ["a", "b"], "when": ["x", "y"]})
    df, info, _ = load_scoped(tmp_path, "hh", None)
    assert len(df) == 2
    assert (info.rows_in_scope, info.rows_excluded_by_period, info.rows_bad_date) == (2, 0, 0)


def test_load_scoped_missing_period_column(tmp_path: Path) -> None:
    _write_csv(tmp_path / "hh.csv", {"id": ["a"]})
    with pytest.raises(ReportError, match="'when' not found"):
        load_scoped(tmp_path, "hh", Period("when", date(2026, 6, 1), date(2026, 8, 31)))


def test_load_scoped_demo_detects_pii_and_counts(demo_workspace: Path) -> None:
    raw = pd.read_excel(demo_workspace / "data" / "beneficiaries.xlsx")
    df, info, pii = load_scoped(demo_workspace / "data", "beneficiaries", None)
    assert {"resp_name", "resp_phone"} <= set(pii)
    assert "_uuid" not in pii and "oblast" not in pii
    assert info.rows_total == len(raw) and info.used_clean is False
    assert len(df) == len(raw)


def test_load_scoped_handles_utc_offset_timestamps(tmp_path: Path) -> None:
    _write_csv(
        tmp_path / "hh.csv",
        {
            "id": ["a", "b", "c"],
            "when": ["2026-06-15T10:00:00+03:00", "2026-06-16", "2026-09-10T09:00:00+03:00"],
        },
    )
    df, info, _ = load_scoped(tmp_path, "hh", Period("when", date(2026, 6, 1), date(2026, 8, 31)))
    assert sorted(df["id"]) == ["a", "b"]
    assert info.rows_bad_date == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTEST tests/test_reporting_sources.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'haa.reporting'`.

- [ ] **Step 3: Implement**

Create the empty file `src/haa/reporting/__init__.py`, then `src/haa/reporting/sources.py`:

```python
"""Dataset resolution for reports: clean-copy preference, PII detection, period scoping."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from haa.core.tools.profiler import detect_pii_columns, discover_datasets


class ReportError(ValueError):
    """Friendly, user-facing failure that stops the whole report."""


class DatasetNotFound(ReportError):
    """The dataset a report or an indicator refers to is not in workspace/data/."""


@dataclass(frozen=True)
class Period:
    field: str
    start: date
    end: date

    def label(self) -> str:
        return f"{self.start.isoformat()} .. {self.end.isoformat()} (by {self.field})"


@dataclass(frozen=True)
class SourceInfo:
    dataset: str
    path: Path
    used_clean: bool
    rows_total: int
    rows_in_scope: int
    rows_excluded_by_period: int
    rows_bad_date: int


def parse_period(date_field: str | None, start: str | None, end: str | None) -> Period | None:
    if not start and not end:
        return None
    if not (start and end):
        raise ReportError("Give both a start and an end date (YYYY-MM-DD), or neither.")
    if not date_field:
        raise ReportError(
            "A reporting period needs date_field — the dataset column that holds the date."
        )
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        raise ReportError(
            f"Invalid period {start!r}..{end!r} — use dates like 2026-06-01."
        ) from None
    if first > last:
        raise ReportError(f"The period start {start} is after its end {end}.")
    return Period(field=date_field, start=first, end=last)


def parse_dates(series: pd.Series) -> pd.Series:
    """Parse mixed date/datetime values; unparseable ones become NaT.

    Values carrying a UTC offset (Kobo timestamps) are compared in UTC, so a
    submission shortly after local midnight can fall on the previous date.
    """
    parsed = pd.to_datetime(series, errors="coerce", format="mixed", utc=True)
    return parsed.dt.tz_convert(None)


def resolve_dataset(data_dir: Path, name: str) -> tuple[Path, bool]:
    found = discover_datasets(data_dir)
    clean = found.get(f"{name}_clean")
    if clean is not None:
        return clean, True
    if name in found:
        return found[name], name.endswith("_clean")
    known = ", ".join(sorted(found)) or "(none)"
    raise DatasetNotFound(f"Dataset {name!r} not found in workspace/data/. Available: {known}")


def load_scoped(
    data_dir: Path, name: str, period: Period | None
) -> tuple[pd.DataFrame, SourceInfo, list[str]]:
    path, used_clean = resolve_dataset(data_dir, name)
    try:
        df = pd.read_csv(path) if path.suffix.lower() == ".csv" else pd.read_excel(path)
    except Exception as exc:
        raise ReportError(
            f"Could not read {path.name}: {exc}. If it is open in Excel, close it and try again."
        ) from exc
    pii = detect_pii_columns(df)
    total = len(df)
    bad_dates = 0
    if period is not None:
        if period.field not in df.columns:
            columns = ", ".join(str(c) for c in df.columns)
            raise ReportError(
                f"Period column {period.field!r} not found in {path.name}. Columns: {columns}"
            )
        dates = parse_dates(df[period.field])
        bad_dates = int(dates.isna().sum())
        start = pd.Timestamp(period.start)
        end_exclusive = pd.Timestamp(period.end) + pd.Timedelta(days=1)
        keep = dates.notna() & (dates >= start) & (dates < end_exclusive)
        df = df.loc[keep].reset_index(drop=True)
    info = SourceInfo(
        dataset=name,
        path=path,
        used_clean=used_clean,
        rows_total=total,
        rows_in_scope=len(df),
        rows_excluded_by_period=total - len(df),
        rows_bad_date=bad_dates,
    )
    return df, info, pii
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTEST tests/test_reporting_sources.py -q`, then `RUFF`
Expected: 15 passed, no warnings; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/haa/reporting/__init__.py src/haa/reporting/sources.py tests/conftest.py tests/test_reporting_sources.py
git commit -m "feat: report data sources with clean-copy preference, period scoping and PII detection"
```

---

### Task 2: Measures — one measure block on a data frame

**Files:**
- Create: `src/haa/reporting/measures.py`
- Test: `tests/test_reporting_measures.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (pandas only).
- Produces:
  - `MISSING = "(missing)"`
  - `class NotComputable(Exception)` with attribute `reason: str`
  - `category_keys(series: pd.Series) -> pd.Series` — values as strings, missing values as `MISSING`
  - `ordered_categories(keys: pd.Series) -> list[str]` — sorted by name, `MISSING` last
  - `check_column(df: pd.DataFrame, column: object, pii: list[str]) -> str` — raises `NotComputable` for a PII or absent column
  - `evaluate_measure(df: pd.DataFrame, measure: dict, pii: list[str]) -> float` — semantics: `count` = rows after `filter` (or non-null `field` values); `count_unique` = distinct non-null `field` values; `sum` = numeric sum of `field`; `percent` = 100 × numerator / denominator, each the distinct non-null values of its `field` after its own `filter`
  - Test module constants `DF`, `PII`, `PERCENT_FEMALE` — imported by Task 3's tests

- [ ] **Step 1: Write the failing tests**

`tests/test_reporting_measures.py`:

```python
import copy

import pandas as pd
import pytest

from haa.reporting.measures import (
    MISSING,
    NotComputable,
    category_keys,
    evaluate_measure,
    ordered_categories,
)

DF = pd.DataFrame(
    {
        "_uuid": ["a", "b", "c", "c", "d"],
        "head_sex": ["female", "male", "female", "female", None],
        "oblast": ["X", "X", "Y", "Y", "Y"],
        "hh_size": [3, 5, 2, 2, 4],
        "services": ["cash health", "health", "cash", "cash", "nfi"],
        "resp_phone": ["+380 1", "+380 2", "+380 3", "+380 3", "+380 4"],
    }
)
PII = ["resp_phone"]
PERCENT_FEMALE = {
    "dataset": "hh",
    "aggregation": "percent",
    "numerator": {"field": "_uuid", "filter": "head_sex == 'female'"},
    "denominator": {"field": "_uuid", "filter": None},
}


def test_count_rows() -> None:
    assert evaluate_measure(DF, {"aggregation": "count"}, PII) == 5.0


def test_count_non_null_field() -> None:
    assert evaluate_measure(DF, {"aggregation": "count", "field": "head_sex"}, PII) == 4.0


def test_count_with_filter() -> None:
    measure = {"aggregation": "count", "filter": "head_sex == 'female'"}
    assert evaluate_measure(DF, measure, PII) == 3.0


def test_count_unique() -> None:
    assert evaluate_measure(DF, {"aggregation": "count_unique", "field": "_uuid"}, PII) == 4.0


def test_count_unique_with_filter() -> None:
    measure = {"aggregation": "count_unique", "field": "_uuid", "filter": "head_sex == 'female'"}
    assert evaluate_measure(DF, measure, PII) == 2.0


def test_sum() -> None:
    assert evaluate_measure(DF, {"aggregation": "sum", "field": "hh_size"}, PII) == 16.0


def test_percent_uses_distinct_values() -> None:
    assert evaluate_measure(DF, PERCENT_FEMALE, PII) == 50.0


def test_percent_zero_denominator() -> None:
    measure = copy.deepcopy(PERCENT_FEMALE)
    measure["denominator"]["filter"] = "hh_size > 100"
    with pytest.raises(NotComputable, match="denominator"):
        evaluate_measure(DF, measure, PII)


def test_missing_column() -> None:
    with pytest.raises(NotComputable, match="'sex' not found.*profile_dataset"):
        evaluate_measure(DF, {"aggregation": "count_unique", "field": "sex"}, PII)


def test_pii_field_refused() -> None:
    with pytest.raises(NotComputable, match="personal data"):
        evaluate_measure(DF, {"aggregation": "count_unique", "field": "resp_phone"}, PII)


def test_pii_in_filter_refused() -> None:
    measure = {"aggregation": "count", "filter": "resp_phone == '+380 1'"}
    with pytest.raises(NotComputable, match="personal data"):
        evaluate_measure(DF, measure, PII)


def test_bad_filter_is_reported() -> None:
    measure = {"aggregation": "count", "filter": "head_sex === 'female'"}
    with pytest.raises(NotComputable, match="failed"):
        evaluate_measure(DF, measure, PII)


def test_non_numeric_sum() -> None:
    with pytest.raises(NotComputable, match="non-numeric"):
        evaluate_measure(DF, {"aggregation": "sum", "field": "services"}, PII)


def test_unknown_aggregation() -> None:
    with pytest.raises(NotComputable, match="median"):
        evaluate_measure(DF, {"aggregation": "median", "field": "hh_size"}, PII)


def test_category_keys_and_order() -> None:
    keys = category_keys(pd.Series(["b", None, "a", "b"]))
    assert keys.tolist() == ["b", MISSING, "a", "b"]
    assert ordered_categories(keys) == ["a", "b", MISSING]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTEST tests/test_reporting_measures.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'haa.reporting.measures'`.

- [ ] **Step 3: Implement**

`src/haa/reporting/measures.py`:

```python
"""Evaluate one indicator `measure` block against a data frame."""

from __future__ import annotations

import re

import pandas as pd

MISSING = "(missing)"


class NotComputable(Exception):
    """An indicator, or one breakdown category, cannot be computed; carries the reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def category_keys(series: pd.Series) -> pd.Series:
    """Stringify category values; missing values become MISSING."""
    return series.astype(str).mask(series.isna(), MISSING)


def ordered_categories(keys: pd.Series) -> list[str]:
    """Categories sorted by name, with MISSING last."""
    present = set(keys.unique())
    ordered = sorted(c for c in present if c != MISSING)
    if MISSING in present:
        ordered.append(MISSING)
    return ordered


def check_column(df: pd.DataFrame, column: object, pii: list[str]) -> str:
    if not isinstance(column, str) or not column:
        raise NotComputable("the measure does not name a column")
    if column in pii:
        raise NotComputable(
            f"column {column!r} is personal data — aggregating by it is not allowed"
        )
    if column not in df.columns:
        raise NotComputable(
            f"column {column!r} not found in the dataset — check profile_dataset"
        )
    return column


def _apply_filter(df: pd.DataFrame, query: object, pii: list[str]) -> pd.DataFrame:
    if query is None or query == "":
        return df
    text = str(query)
    for column in pii:
        if re.search(rf"(?<!\w){re.escape(str(column))}(?!\w)", text):
            raise NotComputable(
                f"filter {text!r} uses column {column!r}, which is personal data — not allowed"
            )
    try:
        return df.query(text)
    except Exception as exc:
        detail = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        raise NotComputable(f"filter {text!r} failed: {detail}") from exc


def _distinct(df: pd.DataFrame, column: str) -> int:
    return int(df[column].dropna().nunique())


def evaluate_measure(df: pd.DataFrame, measure: dict, pii: list[str]) -> float:
    aggregation = measure.get("aggregation")
    if aggregation == "percent":
        parts: list[int] = []
        for key in ("numerator", "denominator"):
            spec = measure.get(key)
            if not isinstance(spec, dict):
                raise NotComputable(f"percent needs a {key} block")
            column = check_column(df, spec.get("field"), pii)
            parts.append(_distinct(_apply_filter(df, spec.get("filter"), pii), column))
        numerator, denominator = parts
        if denominator == 0:
            raise NotComputable("the denominator is 0 — nothing to divide by")
        return 100.0 * numerator / denominator
    column = measure.get("field")
    if column is not None:
        column = check_column(df, column, pii)
    scoped = _apply_filter(df, measure.get("filter"), pii)
    if aggregation == "count":
        return float(scoped[column].notna().sum()) if column else float(len(scoped))
    if aggregation not in ("count_unique", "sum"):
        raise NotComputable(f"unknown aggregation {aggregation!r}")
    if not column:
        raise NotComputable(f"{aggregation} needs a field")
    if aggregation == "count_unique":
        return float(_distinct(scoped, column))
    values = scoped[column]
    numeric = pd.to_numeric(values, errors="coerce")
    non_numeric = int(numeric.isna().sum() - values.isna().sum())
    if non_numeric:
        raise NotComputable(
            f"column {column!r} has {non_numeric} non-numeric values — cannot sum"
        )
    return float(numeric.sum())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTEST tests/test_reporting_measures.py -q`, then `RUFF`
Expected: 15 passed; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/haa/reporting/measures.py tests/test_reporting_measures.py
git commit -m "feat: deterministic evaluation of indicator measure blocks with a PII guard"
```

---

### Task 3: Registry engine — indicators, breakdowns, statuses

**Files:**
- Create: `src/haa/reporting/engine.py`
- Test: `tests/test_reporting_engine.py`

**Interfaces:**
- Consumes: Task 1 `DatasetNotFound`, `Period`, `ReportError`, `SourceInfo`, `load_scoped`; Task 2 `NotComputable`, `category_keys`, `check_column`, `evaluate_measure`, `ordered_categories` (+ test constants `DF`, `PII`, `PERCENT_FEMALE`); existing `haa.indicators.registry.validate_registry`; conftest `DEMO_REGISTRY`, `demo_workspace`.
- Produces:
  - `@dataclass(frozen=True) BreakdownRow(category: str, value: float | None, reason: str | None = None)`
  - `@dataclass(frozen=True) IndicatorResult(code: str, name: dict, dataset: str | None, target: float | None, unit: str | None, actual: float | None, progress_pct: float | None, status: str, reason: str | None = None, breakdowns: dict[str, list[BreakdownRow]] = {}, breakdown_errors: dict[str, str] = {})` — `status` is `"computed"` or `"not_computable"`
  - `@dataclass(frozen=True) ReportRun(period: Period | None, sources: dict[str, SourceInfo], results: list[IndicatorResult])` with properties `computed` and `not_computable`
  - `evaluate_indicator(item: dict, df: pd.DataFrame, pii: list[str], dataset: str) -> IndicatorResult`
  - `evaluate_registry(registry: dict, data_dir: Path, period: Period | None) -> ReportRun` — raises `ReportError` for an invalid or empty registry and for a missing period column; an unknown dataset or a failing measure is a per-indicator `not_computable` status

- [ ] **Step 1: Write the failing tests**

`tests/test_reporting_engine.py`:

```python
import copy
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from haa.reporting.engine import evaluate_indicator, evaluate_registry
from haa.reporting.measures import MISSING
from haa.reporting.sources import Period, ReportError
from tests.conftest import DEMO_REGISTRY
from tests.test_reporting_measures import DF, PERCENT_FEMALE, PII


# -- evaluate_indicator ---------------------------------------------------------
def _item(measure: dict, **extra) -> dict:
    return {
        "code": "1.1",
        "name": {"uk": "Охоплені", "en": "Reached"},
        "definition": "d",
        "target": {"value": 2, "unit": "households"},
        "measure": measure,
        **extra,
    }


def test_indicator_actual_and_progress() -> None:
    item = _item({"dataset": "hh", "aggregation": "count_unique", "field": "_uuid"})
    result = evaluate_indicator(item, DF, PII, "hh")
    assert result.status == "computed"
    assert (result.actual, result.target, result.progress_pct) == (4.0, 2.0, 200.0)
    assert result.unit == "households" and result.dataset == "hh"


def test_breakdown_puts_missing_last() -> None:
    item = _item(
        {"dataset": "hh", "aggregation": "count_unique", "field": "_uuid"},
        disaggregation=["head_sex"],
    )
    rows = evaluate_indicator(item, DF, PII, "hh").breakdowns["head_sex"]
    assert [(r.category, r.value) for r in rows] == [
        ("female", 2.0), ("male", 1.0), (MISSING, 1.0),
    ]


def test_percent_breakdown_is_within_category() -> None:
    item = _item(PERCENT_FEMALE, disaggregation=["oblast"])
    rows = evaluate_indicator(item, DF, PII, "hh").breakdowns["oblast"]
    # X: a (female), b (male) -> 50%; Y: c (female), d (missing sex) -> 50%
    assert [(r.category, r.value) for r in rows] == [("X", 50.0), ("Y", 50.0)]


def test_breakdown_category_not_computable_keeps_reason() -> None:
    measure = copy.deepcopy(PERCENT_FEMALE)
    measure["denominator"]["filter"] = "oblast == 'X'"
    item = _item(measure, disaggregation=["oblast"])
    rows = evaluate_indicator(item, DF, PII, "hh").breakdowns["oblast"]
    assert rows[1].category == "Y" and rows[1].value is None
    assert "denominator" in rows[1].reason


def test_missing_or_pii_dimension_is_a_breakdown_error_only() -> None:
    item = _item(
        {"dataset": "hh", "aggregation": "count_unique", "field": "_uuid"},
        disaggregation=["sex", "resp_phone"],
    )
    result = evaluate_indicator(item, DF, PII, "hh")
    assert result.status == "computed" and result.actual == 4.0
    assert "not found" in result.breakdown_errors["sex"]
    assert "personal data" in result.breakdown_errors["resp_phone"]
    assert result.breakdowns == {}


def test_failing_measure_makes_indicator_not_computable() -> None:
    item = _item({"dataset": "hh", "aggregation": "count_unique", "field": "sex"})
    result = evaluate_indicator(item, DF, PII, "hh")
    assert result.status == "not_computable" and result.actual is None
    assert "not found" in result.reason
    assert result.target == 2.0


# -- evaluate_registry ------------------------------------------------------------
@pytest.fixture(scope="module")
def demo_df(demo_workspace: Path) -> pd.DataFrame:
    return pd.read_excel(demo_workspace / "data" / "beneficiaries.xlsx")


@pytest.fixture(scope="module")
def demo_run(demo_workspace: Path):
    return evaluate_registry(DEMO_REGISTRY, demo_workspace / "data", None)


def test_registry_on_demo_matches_independent_pandas(demo_run, demo_df: pd.DataFrame) -> None:
    by_code = {r.code: r for r in demo_run.results}
    reached = demo_df["_uuid"].nunique()
    female = demo_df.loc[demo_df["head_sex"] == "female", "_uuid"].nunique()
    assert by_code["1.1"].actual == reached == 3000
    assert by_code["1.1"].progress_pct == round(100 * reached / 2500, 1)
    assert by_code["1.2"].actual == pytest.approx(100 * female / reached)
    assert by_code["2.1"].status == "not_computable"
    assert "measure" in by_code["2.1"].reason


def test_registry_demo_breakdowns(demo_run, demo_df: pd.DataFrame) -> None:
    result = next(r for r in demo_run.results if r.code == "1.1")
    by_oblast = result.breakdowns["oblast"]
    assert sum(r.value for r in by_oblast) == 3000  # each household lives in one oblast
    assert "Харківська обл." in {r.category for r in by_oblast}
    assert result.breakdowns["head_sex"][-1].category == MISSING


def test_registry_demo_source_info(demo_run, demo_df: pd.DataFrame) -> None:
    source = demo_run.sources["beneficiaries"]
    assert source.used_clean is False and source.rows_total == len(demo_df)
    assert [r.code for r in demo_run.computed] == ["1.1", "1.2"]
    assert [r.code for r in demo_run.not_computable] == ["2.1"]


def test_registry_period_filters_before_computing(demo_workspace: Path, demo_df) -> None:
    period = Period("submission_date", date(2026, 6, 1), date(2026, 8, 31))
    run = evaluate_registry(DEMO_REGISTRY, demo_workspace / "data", period)
    dates = pd.to_datetime(demo_df["submission_date"])
    in_range = demo_df[(dates >= "2026-06-01") & (dates <= "2026-08-31")]
    source = run.sources["beneficiaries"]
    assert source.rows_in_scope == len(in_range)
    assert source.rows_excluded_by_period == len(demo_df) - len(in_range) > 0
    assert run.results[0].actual == in_range["_uuid"].nunique()


def test_registry_unknown_dataset_is_per_indicator(demo_workspace: Path) -> None:
    registry = copy.deepcopy(DEMO_REGISTRY)
    registry["indicators"][0]["measure"]["dataset"] = "ghost"
    run = evaluate_registry(registry, demo_workspace / "data", None)
    assert run.results[0].status == "not_computable"
    assert "'ghost' not found" in run.results[0].reason
    assert run.results[1].status == "computed"
    assert set(run.sources) == {"beneficiaries"}


def test_registry_prefers_clean_copy(tmp_path: Path) -> None:
    pd.DataFrame({"_uuid": ["a", "a", "b"]}).to_csv(tmp_path / "beneficiaries.csv", index=False)
    pd.DataFrame({"_uuid": ["a", "b", "c"]}).to_csv(
        tmp_path / "beneficiaries_clean.csv", index=False
    )
    registry = {
        "indicators": [
            {
                "code": "1",
                "name": {"uk": "Рядки", "en": "Rows"},
                "definition": "rows",
                "measure": {"dataset": "beneficiaries", "aggregation": "count"},
            }
        ]
    }
    run = evaluate_registry(registry, tmp_path, None)
    assert run.results[0].actual == 3.0
    assert run.sources["beneficiaries"].used_clean is True


def test_registry_invalid_raises() -> None:
    with pytest.raises(ReportError, match="invalid"):
        evaluate_registry({"indicators": [{"code": 5}]}, Path("."), None)


def test_registry_empty_raises() -> None:
    with pytest.raises(ReportError, match="empty"):
        evaluate_registry({"indicators": []}, Path("."), None)


def test_registry_missing_period_column_raises(demo_workspace: Path) -> None:
    period = Period("visit_date", date(2026, 6, 1), date(2026, 8, 31))
    with pytest.raises(ReportError, match="visit_date"):
        evaluate_registry(DEMO_REGISTRY, demo_workspace / "data", period)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTEST tests/test_reporting_engine.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'haa.reporting.engine'`.

- [ ] **Step 3: Implement**

`src/haa/reporting/engine.py`:

```python
"""Registry-level indicator evaluation: result model, breakdowns, dataset scoping."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from haa.indicators.registry import validate_registry
from haa.reporting.measures import (
    NotComputable,
    category_keys,
    check_column,
    evaluate_measure,
    ordered_categories,
)
from haa.reporting.sources import DatasetNotFound, Period, ReportError, SourceInfo, load_scoped


@dataclass(frozen=True)
class BreakdownRow:
    category: str
    value: float | None
    reason: str | None = None


@dataclass(frozen=True)
class IndicatorResult:
    code: str
    name: dict
    dataset: str | None
    target: float | None
    unit: str | None
    actual: float | None
    progress_pct: float | None
    status: str  # "computed" | "not_computable"
    reason: str | None = None
    breakdowns: dict[str, list[BreakdownRow]] = field(default_factory=dict)
    breakdown_errors: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ReportRun:
    period: Period | None
    sources: dict[str, SourceInfo]
    results: list[IndicatorResult]

    @property
    def computed(self) -> list[IndicatorResult]:
        return [r for r in self.results if r.status == "computed"]

    @property
    def not_computable(self) -> list[IndicatorResult]:
        return [r for r in self.results if r.status == "not_computable"]


def _breakdown(
    df: pd.DataFrame, measure: dict, dimension: str, pii: list[str]
) -> list[BreakdownRow]:
    column = check_column(df, dimension, pii)
    keys = category_keys(df[column])
    rows: list[BreakdownRow] = []
    for category in ordered_categories(keys):
        subset = df.loc[keys == category]
        try:
            rows.append(BreakdownRow(category, evaluate_measure(subset, measure, pii)))
        except NotComputable as exc:
            rows.append(BreakdownRow(category, None, exc.reason))
    return rows


def _target(item: dict) -> tuple[float | None, str | None]:
    target = item.get("target") or {}
    value = target.get("value")
    return (float(value) if value is not None else None), target.get("unit")


def _progress(actual: float, target: float | None) -> float | None:
    if target is None or target == 0:
        return None
    return round(100.0 * actual / target, 1)


def _not_computable(item: dict, reason: str, dataset: str | None) -> IndicatorResult:
    target, unit = _target(item)
    return IndicatorResult(
        code=str(item.get("code")),
        name=dict(item.get("name") or {}),
        dataset=dataset,
        target=target,
        unit=unit,
        actual=None,
        progress_pct=None,
        status="not_computable",
        reason=reason,
    )


def evaluate_indicator(
    item: dict, df: pd.DataFrame, pii: list[str], dataset: str
) -> IndicatorResult:
    measure = item["measure"]
    try:
        actual = evaluate_measure(df, measure, pii)
    except NotComputable as exc:
        return _not_computable(item, exc.reason, dataset)
    breakdowns: dict[str, list[BreakdownRow]] = {}
    breakdown_errors: dict[str, str] = {}
    for dimension in item.get("disaggregation") or []:
        try:
            breakdowns[dimension] = _breakdown(df, measure, dimension, pii)
        except NotComputable as exc:
            breakdown_errors[dimension] = exc.reason
    target, unit = _target(item)
    return IndicatorResult(
        code=str(item["code"]),
        name=dict(item.get("name") or {}),
        dataset=dataset,
        target=target,
        unit=unit,
        actual=actual,
        progress_pct=_progress(actual, target),
        status="computed",
        breakdowns=breakdowns,
        breakdown_errors=breakdown_errors,
    )


def evaluate_registry(registry: dict, data_dir: Path, period: Period | None) -> ReportRun:
    errors = validate_registry(registry)
    if errors:
        raise ReportError(
            "indicators.yaml is invalid — fix it or ask the designer:\n"
            + "\n".join(f"- {e}" for e in errors)
        )
    indicators = registry.get("indicators") or []
    if not indicators:
        raise ReportError(
            "The indicators registry is empty — ask to extract indicators from the "
            "project logframe first."
        )
    loaded: dict[str, tuple[pd.DataFrame, SourceInfo, list[str]] | str] = {}
    results: list[IndicatorResult] = []
    for item in indicators:
        measure = item.get("measure")
        if not measure:
            results.append(
                _not_computable(
                    item, "no measure block — add one to the registry (designer)", None
                )
            )
            continue
        name = measure["dataset"]
        if name not in loaded:
            try:
                loaded[name] = load_scoped(data_dir, name, period)
            except DatasetNotFound as exc:
                loaded[name] = str(exc)
        entry = loaded[name]
        if isinstance(entry, str):
            results.append(_not_computable(item, entry, name))
            continue
        df, _, pii = entry
        results.append(evaluate_indicator(item, df, pii, name))
    sources = {
        name: entry[1] for name, entry in loaded.items() if not isinstance(entry, str)
    }
    return ReportRun(period=period, sources=sources, results=results)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTEST tests/test_reporting_engine.py tests/test_reporting_measures.py -q`, then `RUFF`
Expected: 30 passed; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/haa/reporting/engine.py tests/test_reporting_engine.py
git commit -m "feat: indicator registry engine with breakdowns and per-indicator statuses"
```

---

### Task 4: 5W mapping — strict schema and storage

**Files:**
- Create: `src/haa/reporting/mapping.py`
- Test: `tests/test_reporting_mapping.py`

**Interfaces:**
- Consumes: Task 1 `ReportError`.
- Produces:
  - `MAPPING_FILE = "5w.yaml"`; `RESERVED_COLUMNS = ("Period", "Activity", "Beneficiaries")`
  - `class MappingError(ReportError)`
  - `validate_mapping(data: object) -> list[str]` — `[]` means valid
  - `mapping_path(workspace: Path) -> Path`; `parse_mapping_yaml(text: str) -> dict`; `load_mapping(workspace: Path) -> dict | None` (None when the file is absent); `save_mapping(workspace: Path, data: dict) -> Path`
  - Test module constant `VALID_MAPPING` — imported by Task 7's tests
- Schema (spec §3): top keys `dataset` (required), `fixed` (optional mapping of output column → scalar), `where` (required non-empty list of columns), `when` (required: `field`, optional `granularity: month | none`, default `month`), `what` (required: `field`, optional non-empty `split`), `whom` (required: `id_field`, optional `disaggregation` list).

- [ ] **Step 1: Write the failing tests**

`tests/test_reporting_mapping.py`:

```python
import copy
from pathlib import Path

import pytest

from haa.reporting.mapping import (
    MappingError,
    load_mapping,
    mapping_path,
    parse_mapping_yaml,
    save_mapping,
    validate_mapping,
)
from haa.reporting.sources import ReportError

VALID_MAPPING = {
    "dataset": "beneficiaries",
    "fixed": {"Organization": "IMC", "Project": "ABC-123"},
    "where": ["oblast", "raion", "hromada"],
    "when": {"field": "submission_date", "granularity": "month"},
    "what": {"field": "services_received", "split": " "},
    "whom": {"id_field": "_uuid", "disaggregation": ["head_sex"]},
}


@pytest.fixture()
def mapping() -> dict:
    return copy.deepcopy(VALID_MAPPING)


def test_valid(mapping: dict) -> None:
    assert validate_mapping(mapping) == []


def test_minimal_valid() -> None:
    minimal = {
        "dataset": "beneficiaries",
        "where": ["oblast"],
        "when": {"field": "submission_date"},
        "what": {"field": "services_received"},
        "whom": {"id_field": "_uuid"},
    }
    assert validate_mapping(minimal) == []


def test_not_a_mapping() -> None:
    assert validate_mapping(["oblast"]) != []


def test_unknown_top_key(mapping: dict) -> None:
    mapping["sector"] = "health"
    assert any("sector" in e and "unknown" in e for e in validate_mapping(mapping))


def test_unknown_nested_key(mapping: dict) -> None:
    mapping["when"]["format"] = "%Y"
    assert any(e.startswith("when:") and "format" in e for e in validate_mapping(mapping))


def test_non_string_unknown_keys_do_not_raise(mapping: dict) -> None:
    mapping[1] = "x"
    assert any("unknown" in e for e in validate_mapping(mapping))


def test_dataset_required(mapping: dict) -> None:
    del mapping["dataset"]
    assert any(e.startswith("dataset:") for e in validate_mapping(mapping))


def test_where_must_be_non_empty(mapping: dict) -> None:
    mapping["where"] = []
    assert any(e.startswith("where:") for e in validate_mapping(mapping))


def test_where_items_are_column_names(mapping: dict) -> None:
    mapping["where"] = ["oblast", 7]
    assert "where[1]: expected a column name" in validate_mapping(mapping)


def test_where_duplicates(mapping: dict) -> None:
    mapping["where"] = ["oblast", "oblast"]
    assert any("duplicate" in e for e in validate_mapping(mapping))


def test_where_reserved_name(mapping: dict) -> None:
    mapping["where"] = ["Activity"]
    assert any("reserved" in e for e in validate_mapping(mapping))


def test_bad_granularity(mapping: dict) -> None:
    mapping["when"]["granularity"] = "week"
    assert any("week" in e for e in validate_mapping(mapping))


def test_unhashable_granularity_does_not_raise(mapping: dict) -> None:
    mapping["when"]["granularity"] = ["month"]
    assert any("granularity" in e for e in validate_mapping(mapping))


def test_what_field_required(mapping: dict) -> None:
    del mapping["what"]["field"]
    assert any(e.startswith("what.field") for e in validate_mapping(mapping))


def test_empty_split_rejected(mapping: dict) -> None:
    mapping["what"]["split"] = ""
    assert any(e.startswith("what.split") for e in validate_mapping(mapping))


def test_whom_id_field_required(mapping: dict) -> None:
    del mapping["whom"]["id_field"]
    assert any(e.startswith("whom.id_field") for e in validate_mapping(mapping))


def test_whom_disaggregation_must_be_names(mapping: dict) -> None:
    mapping["whom"]["disaggregation"] = "head_sex"
    assert any(e.startswith("whom.disaggregation") for e in validate_mapping(mapping))


def test_fixed_collides_with_output_column(mapping: dict) -> None:
    mapping["fixed"]["Beneficiaries"] = "x"
    mapping["fixed"]["oblast"] = "y"
    errors = validate_mapping(mapping)
    assert "fixed.Beneficiaries: collides with an output column" in errors
    assert "fixed.oblast: collides with an output column" in errors


def test_fixed_value_must_be_scalar(mapping: dict) -> None:
    mapping["fixed"]["Donor"] = ["ECHO"]
    assert "fixed.Donor: expected a single value" in validate_mapping(mapping)


def test_storage_round_trip(tmp_path: Path, mapping: dict) -> None:
    mapping["fixed"]["Organization"] = "МКМ"
    save_mapping(tmp_path, mapping)
    assert load_mapping(tmp_path) == mapping
    assert "МКМ" in mapping_path(tmp_path).read_text(encoding="utf-8")


def test_load_missing_returns_none(tmp_path: Path) -> None:
    assert load_mapping(tmp_path) is None


def test_parse_bad_yaml() -> None:
    with pytest.raises(MappingError, match="parse"):
        parse_mapping_yaml("where: [\n")


def test_parse_non_mapping() -> None:
    with pytest.raises(MappingError, match="mapping"):
        parse_mapping_yaml("- oblast\n")


def test_mapping_error_is_report_error() -> None:
    assert issubclass(MappingError, ReportError)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTEST tests/test_reporting_mapping.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'haa.reporting.mapping'`.

- [ ] **Step 3: Implement**

`src/haa/reporting/mapping.py`:

```python
"""The 5W mapping file (workspace/5w.yaml): strict schema validation and storage."""

from __future__ import annotations

from pathlib import Path

import yaml

from haa.reporting.sources import ReportError

MAPPING_FILE = "5w.yaml"
TOP_KEYS = {"dataset", "fixed", "where", "when", "what", "whom"}
WHEN_KEYS = {"field", "granularity"}
WHAT_KEYS = {"field", "split"}
WHOM_KEYS = {"id_field", "disaggregation"}
GRANULARITIES = ("month", "none")
RESERVED_COLUMNS = ("Period", "Activity", "Beneficiaries")


class MappingError(ReportError):
    """The 5W mapping file cannot be parsed."""


def _is_name(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _unknown_keys(block: dict, allowed: set[str], label: str, errors: list[str]) -> None:
    unknown = sorted(str(key) for key in block if key not in allowed)
    if unknown:
        errors.append(
            f"{label}: unknown key(s) {unknown}; supported: {', '.join(sorted(allowed))}"
        )


def _name_list(value: object, label: str, errors: list[str], required: bool) -> list[str]:
    if value is None and not required:
        return []
    if not isinstance(value, list) or (required and not value):
        kind = "a non-empty list" if required else "a list"
        errors.append(f"{label}: expected {kind} of column names")
        return []
    names: list[str] = []
    for index, item in enumerate(value):
        if _is_name(item):
            names.append(item)
        else:
            errors.append(f"{label}[{index}]: expected a column name")
    if len(set(names)) != len(names):
        errors.append(f"{label}: duplicate column names")
    return names


def validate_mapping(data: object) -> list[str]:
    """Return addressed, human-readable errors for a 5w.yaml mapping ([] means valid)."""
    if not isinstance(data, dict):
        return ["5w.yaml: expected a mapping with dataset, where, when, what and whom"]
    errors: list[str] = []
    _unknown_keys(data, TOP_KEYS, "5w.yaml", errors)
    if not _is_name(data.get("dataset")):
        errors.append("dataset: expected a dataset name")
    where = _name_list(data.get("where"), "where", errors, required=True)
    for name in where:
        if name in RESERVED_COLUMNS:
            errors.append(f"where: {name!r} is a reserved output column name")

    when = data.get("when")
    if not isinstance(when, dict):
        errors.append("when: expected a mapping with 'field' and optional 'granularity'")
    else:
        _unknown_keys(when, WHEN_KEYS, "when", errors)
        if not _is_name(when.get("field")):
            errors.append("when.field: expected a date column name")
        granularity = when.get("granularity", "month")
        if not isinstance(granularity, str) or granularity not in GRANULARITIES:
            errors.append(f"when.granularity: unknown {granularity!r}; allowed: month, none")

    what = data.get("what")
    if not isinstance(what, dict):
        errors.append("what: expected a mapping with 'field' and optional 'split'")
    else:
        _unknown_keys(what, WHAT_KEYS, "what", errors)
        if not _is_name(what.get("field")):
            errors.append("what.field: expected the activity/service column name")
        split = what.get("split")
        if split is not None and (not isinstance(split, str) or split == ""):
            errors.append("what.split: expected a separator string such as ' '")

    whom = data.get("whom")
    if not isinstance(whom, dict):
        errors.append("whom: expected a mapping with 'id_field' and optional 'disaggregation'")
    else:
        _unknown_keys(whom, WHOM_KEYS, "whom", errors)
        if not _is_name(whom.get("id_field")):
            errors.append("whom.id_field: expected the unique beneficiary ID column name")
        _name_list(whom.get("disaggregation"), "whom.disaggregation", errors, required=False)

    fixed = data.get("fixed")
    if fixed is not None:
        if not isinstance(fixed, dict):
            errors.append("fixed: expected a mapping of output column to value")
        else:
            taken = set(RESERVED_COLUMNS) | set(where)
            for key, value in fixed.items():
                if not _is_name(key):
                    errors.append(f"fixed: column names must be non-empty strings (got {key!r})")
                elif key in taken:
                    errors.append(f"fixed.{key}: collides with an output column")
                elif value is None or isinstance(value, (dict, list)):
                    errors.append(f"fixed.{key}: expected a single value")
    return errors


def mapping_path(workspace: Path) -> Path:
    return workspace / MAPPING_FILE


def parse_mapping_yaml(text: str) -> dict:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise MappingError(f"Could not parse 5w.yaml: {exc}") from exc
    if not isinstance(data, dict):
        raise MappingError("5w.yaml must be a YAML mapping with dataset, where, when, what, whom")
    return data


def load_mapping(workspace: Path) -> dict | None:
    path = mapping_path(workspace)
    if not path.is_file():
        return None
    return parse_mapping_yaml(path.read_text(encoding="utf-8"))


def save_mapping(workspace: Path, data: dict) -> Path:
    path = mapping_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTEST tests/test_reporting_mapping.py -q`, then `RUFF`
Expected: 24 passed; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/haa/reporting/mapping.py tests/test_reporting_mapping.py
git commit -m "feat: strict 5W mapping schema with addressed errors and YAML storage"
```

---

### Task 5: 5W table builder

**Files:**
- Create: `src/haa/reporting/fivew.py`
- Test: `tests/test_reporting_fivew.py`

**Interfaces:**
- Consumes: Task 1 `ReportError`, `parse_dates` (and `load_scoped` in a test); Task 2 `category_keys`, `ordered_categories`, `MISSING` (tests). `build_5w` expects a mapping that already passed Task 4's `validate_mapping` — it does not validate again.
- Produces:
  - `@dataclass(frozen=True) FiveWTable(columns: list[str], rows: list[dict], id_field: str, unique_reach: int, rows_without_activity: int)`
  - `build_5w(df: pd.DataFrame, mapping: dict, pii: list[str]) -> FiveWTable` — columns in order: `fixed` keys, `where` columns, `Period` (only for `granularity: month`), `Activity`, `Beneficiaries`, then one `<dimension>=<category>` column per disaggregation category (`(missing)` last). Row values are plain Python scalars. Raises `ReportError` for a missing or PII column.

- [ ] **Step 1: Write the failing tests**

`tests/test_reporting_fivew.py`:

```python
import copy
from pathlib import Path

import pandas as pd
import pytest

from haa.reporting.fivew import build_5w
from haa.reporting.measures import MISSING
from haa.reporting.sources import ReportError, load_scoped

DF = pd.DataFrame(
    {
        "_uuid": ["a", "b", "c", "d", "e"],
        "oblast": ["X", "X", "Y", "Y", None],
        "submission_date": ["2026-06-03", "2026-06-20", "2026-07-01", "bad", "2026-07-15"],
        "services_received": ["cash health", "health", "cash", "", "nfi"],
        "head_sex": ["female", "male", None, "female", "male"],
        "resp_phone": ["+380 1", "+380 2", "+380 3", "+380 4", "+380 5"],
    }
)
PII = ["resp_phone"]
MAPPING = {
    "dataset": "hh",
    "fixed": {"Organization": "IMC"},
    "where": ["oblast"],
    "when": {"field": "submission_date", "granularity": "month"},
    "what": {"field": "services_received", "split": " "},
    "whom": {"id_field": "_uuid", "disaggregation": ["head_sex"]},
}


def _by_key(table) -> dict:
    return {(r["oblast"], r["Period"], r["Activity"]): r for r in table.rows}


def test_columns_in_order() -> None:
    table = build_5w(DF, MAPPING, PII)
    assert table.columns == [
        "Organization", "oblast", "Period", "Activity", "Beneficiaries",
        "head_sex=female", "head_sex=male", f"head_sex={MISSING}",
    ]


def test_rows_count_distinct_beneficiaries() -> None:
    rows = _by_key(build_5w(DF, MAPPING, PII))
    assert set(rows) == {
        ("X", "2026-06", "cash"),
        ("X", "2026-06", "health"),
        ("Y", "2026-07", "cash"),
        (MISSING, "2026-07", "nfi"),
    }
    health = rows[("X", "2026-06", "health")]
    assert health["Beneficiaries"] == 2
    assert (health["head_sex=female"], health["head_sex=male"]) == (1, 1)
    assert rows[("Y", "2026-07", "cash")][f"head_sex={MISSING}"] == 1
    assert all(r["Organization"] == "IMC" for r in rows.values())


def test_split_counts_a_household_once_per_activity() -> None:
    table = build_5w(DF, MAPPING, PII)
    assert sum(r["Beneficiaries"] for r in table.rows) == 5
    assert table.unique_reach == 4
    assert table.rows_without_activity == 1
    assert table.id_field == "_uuid"


def test_without_split_the_activity_is_the_whole_value() -> None:
    mapping = copy.deepcopy(MAPPING)
    del mapping["what"]["split"]
    activities = {r["Activity"] for r in build_5w(DF, mapping, PII).rows}
    assert "cash health" in activities and "health" in activities


def test_granularity_none_drops_period() -> None:
    mapping = copy.deepcopy(MAPPING)
    mapping["when"]["granularity"] = "none"
    table = build_5w(DF, mapping, PII)
    assert "Period" not in table.columns
    assert len(table.rows) == 4


def test_missing_column_raises() -> None:
    mapping = copy.deepcopy(MAPPING)
    mapping["where"] = ["raion"]
    with pytest.raises(ReportError, match="'raion'.*profile_dataset"):
        build_5w(DF, mapping, PII)


def test_pii_column_refused() -> None:
    mapping = copy.deepcopy(MAPPING)
    mapping["whom"]["disaggregation"] = ["resp_phone"]
    with pytest.raises(ReportError, match="personal data"):
        build_5w(DF, mapping, PII)


def test_empty_frame() -> None:
    table = build_5w(DF.iloc[0:0], MAPPING, PII)
    assert table.rows == [] and table.unique_reach == 0
    assert table.columns == ["Organization", "oblast", "Period", "Activity", "Beneficiaries"]


def test_row_values_are_plain_python() -> None:
    row = build_5w(DF, MAPPING, PII).rows[0]
    assert type(row["Beneficiaries"]) is int
    assert type(row["oblast"]) is str


def test_demo_unique_reach_matches_independent_pandas(demo_workspace: Path) -> None:
    df, _, pii = load_scoped(demo_workspace / "data", "beneficiaries", None)
    mapping = copy.deepcopy(MAPPING)
    mapping["dataset"] = "beneficiaries"
    mapping["where"] = ["oblast", "raion"]
    table = build_5w(df, mapping, pii)
    assert table.unique_reach == df["_uuid"].nunique() == 3000
    assert table.rows and all(r["Beneficiaries"] > 0 for r in table.rows)
    assert table.rows_without_activity == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTEST tests/test_reporting_fivew.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'haa.reporting.fivew'`.

- [ ] **Step 3: Implement**

`src/haa/reporting/fivew.py`:

```python
"""Build the 5W matrix from a validated mapping."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from haa.reporting.measures import category_keys, ordered_categories
from haa.reporting.sources import ReportError, parse_dates


@dataclass(frozen=True)
class FiveWTable:
    columns: list[str]
    rows: list[dict]
    id_field: str
    unique_reach: int
    rows_without_activity: int


def _activities(value: object, split: str | None) -> list[str]:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return []
    text = str(value).strip()
    if not text:
        return []
    if split:
        return [part.strip() for part in text.split(split) if part.strip()]
    return [text]


def _plain(value: object) -> object:
    return value.item() if hasattr(value, "item") else value


def build_5w(df: pd.DataFrame, mapping: dict, pii: list[str]) -> FiveWTable:
    """Group rows into 5W lines counting distinct beneficiaries per activity."""
    where = list(mapping["where"])
    when = mapping["when"]
    what = mapping["what"]
    whom = mapping["whom"]
    id_field = whom["id_field"]
    dimensions = list(whom.get("disaggregation") or [])
    fixed = dict(mapping.get("fixed") or {})

    for column in [*where, when["field"], what["field"], id_field, *dimensions]:
        if column in pii:
            raise ReportError(f"column {column!r} is personal data — it cannot be used in a 5W")
        if column not in df.columns:
            raise ReportError(
                f"column {column!r} from 5w.yaml not found in the dataset — check profile_dataset"
            )

    work = pd.DataFrame(index=df.index)
    for column in where:
        work[column] = category_keys(df[column])
    keys = list(where)
    if when.get("granularity", "month") == "month":
        dates = parse_dates(df[when["field"]])
        work["Period"] = category_keys(dates.dt.strftime("%Y-%m"))
        keys.append("Period")
    split = what.get("split")
    work["Activity"] = pd.Series(
        [_activities(value, split) for value in df[what["field"]].tolist()],
        index=df.index,
        dtype=object,
    )
    keys.append("Activity")
    work["__id"] = df[id_field]
    for dimension in dimensions:
        work[f"__dim_{dimension}"] = category_keys(df[dimension])

    rows_without_activity = int((work["Activity"].map(len) == 0).sum())
    exploded = work.explode("Activity")
    exploded = exploded[exploded["Activity"].notna()]

    base_columns = [*fixed, *keys, "Beneficiaries"]
    if exploded.empty:
        return FiveWTable(
            columns=base_columns,
            rows=[],
            id_field=id_field,
            unique_reach=0,
            rows_without_activity=rows_without_activity,
        )

    table = (
        exploded.groupby(keys, sort=True)["__id"].nunique().rename("Beneficiaries").reset_index()
    )
    extra_columns: list[str] = []
    for dimension in dimensions:
        column = f"__dim_{dimension}"
        categories = ordered_categories(exploded[column])
        counts = (
            exploded.groupby([*keys, column], sort=True)["__id"]
            .nunique()
            .unstack(column, fill_value=0)
            .reindex(columns=categories, fill_value=0)
        )
        labels = [f"{dimension}={category}" for category in categories]
        counts.columns = labels
        table = table.merge(counts.reset_index(), on=keys, how="left")
        extra_columns.extend(labels)
    for position, (name, value) in enumerate(fixed.items()):
        table.insert(position, name, value)

    columns = [*base_columns, *extra_columns]
    rows = [
        {column: _plain(record[column]) for column in columns}
        for record in table[columns].to_dict(orient="records")
    ]
    return FiveWTable(
        columns=columns,
        rows=rows,
        id_field=id_field,
        unique_reach=int(exploded["__id"].nunique()),
        rows_without_activity=rows_without_activity,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTEST tests/test_reporting_fivew.py -q`, then `RUFF`
Expected: 10 passed; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/haa/reporting/fivew.py tests/test_reporting_fivew.py
git commit -m "feat: 5W matrix builder counting distinct beneficiaries per activity"
```

---

### Task 6: Rendering — markdown and xlsx for both reports

**Files:**
- Create: `src/haa/reporting/render.py`
- Test: `tests/test_reporting_render.py`

**Interfaces:**
- Consumes: Task 1 `Period`, `SourceInfo`; Task 3 `BreakdownRow`, `IndicatorResult`, `ReportRun`; Task 5 `FiveWTable`; Task 2 `MISSING` (tests). openpyxl.
- Produces:
  - `report_paths(reports_dir: Path, kind: str, today: date) -> tuple[Path, Path]` — `(<kind>_<YYYY-MM-DD>.md, <kind>_<YYYY-MM-DD>.xlsx)`
  - `format_value(value: float | None, unit: str | None = None) -> str` — `3000.0 → "3000"`, `57.63, "percent" → "57.6%"`, `None → "—"`
  - `period_label(period: Period | None) -> str` — `"all records"` when None
  - `render_indicator_report(run: ReportRun, md_path: Path, xlsx_path: Path, generated: date) -> None` — xlsx sheets `Summary`, `About`, then one sheet per disaggregation dimension (sanitized, unique titles)
  - `render_5w(table: FiveWTable, source: SourceInfo, period: Period | None, md_path: Path, xlsx_path: Path, generated: date, md_row_cap: int = 200) -> None` — xlsx sheets `5W`, `About`; the `About` sheet has the row `Unique reach (distinct <id_field>)`
  - Both create parent directories; neither catches `OSError` (the toolbox does).

- [ ] **Step 1: Write the failing tests**

`tests/test_reporting_render.py`:

```python
from datetime import date
from pathlib import Path

import openpyxl

from haa.reporting.engine import BreakdownRow, IndicatorResult, ReportRun
from haa.reporting.fivew import FiveWTable
from haa.reporting.measures import MISSING
from haa.reporting.render import (
    format_value,
    render_5w,
    render_indicator_report,
    report_paths,
)
from haa.reporting.sources import Period, SourceInfo

GENERATED = date(2026, 9, 14)
PERIOD = Period("submission_date", date(2026, 6, 1), date(2026, 8, 31))
SOURCE = SourceInfo(
    dataset="beneficiaries",
    path=Path("data/beneficiaries_clean.xlsx"),
    used_clean=True,
    rows_total=3030,
    rows_in_scope=3000,
    rows_excluded_by_period=30,
    rows_bad_date=2,
)
RUN = ReportRun(
    period=PERIOD,
    sources={"beneficiaries": SOURCE},
    results=[
        IndicatorResult(
            code="1.1",
            name={"uk": "Охоплені", "en": "Reached"},
            dataset="beneficiaries",
            target=2500.0,
            unit="households",
            actual=3000.0,
            progress_pct=120.0,
            status="computed",
            breakdowns={
                "oblast": [BreakdownRow("Донецька", 400.0), BreakdownRow(MISSING, 5.0)],
            },
            breakdown_errors={
                "sex": "column 'sex' not found in the dataset — check profile_dataset",
            },
        ),
        IndicatorResult(
            code="1.2",
            name={"uk": "Частка", "en": "Share | female"},
            dataset="beneficiaries",
            target=55.0,
            unit="percent",
            actual=57.63,
            progress_pct=104.8,
            status="computed",
        ),
        IndicatorResult(
            code="2.1",
            name={"uk": "Грошова", "en": "Cash"},
            dataset=None,
            target=1200.0,
            unit="households",
            actual=None,
            progress_pct=None,
            status="not_computable",
            reason="no measure block — add one to the registry (designer)",
        ),
    ],
)
TABLE = FiveWTable(
    columns=["Organization", "oblast", "Period", "Activity", "Beneficiaries"],
    rows=[
        {"Organization": "IMC", "oblast": "X", "Period": "2026-06", "Activity": "cash",
         "Beneficiaries": 1},
        {"Organization": "IMC", "oblast": "X", "Period": "2026-06", "Activity": "health",
         "Beneficiaries": 2},
        {"Organization": "IMC", "oblast": "Y", "Period": "2026-07", "Activity": "cash",
         "Beneficiaries": 1},
    ],
    id_field="_uuid",
    unique_reach=2,
    rows_without_activity=1,
)


def _paths(tmp_path: Path, kind: str) -> tuple[Path, Path]:
    return report_paths(tmp_path / "reports", kind, GENERATED)


def _rows(ws) -> list[list]:
    return [list(r) for r in ws.iter_rows(values_only=True)]


def test_report_paths(tmp_path: Path) -> None:
    md, xlsx = report_paths(tmp_path, "indicators", GENERATED)
    assert md.name == "indicators_2026-09-14.md" and xlsx.name == "indicators_2026-09-14.xlsx"


def test_format_value() -> None:
    assert format_value(3000.0, "households") == "3000"
    assert format_value(57.63, "percent") == "57.6%"
    assert format_value(120.0, "percent") == "120%"
    assert format_value(None) == "—"


def test_indicator_markdown(tmp_path: Path) -> None:
    md, xlsx = _paths(tmp_path, "indicators")
    render_indicator_report(RUN, md, xlsx, GENERATED)
    text = md.read_text(encoding="utf-8")
    assert "Period: 2026-06-01 .. 2026-08-31 (by submission_date)" in text
    assert "| beneficiaries | beneficiaries_clean.xlsx | clean | 3030 | 3000 | 30 | 2 |" in text
    assert "| 1.1 | Охоплені | Reached | 2500 | 3000 | 120% |" in text
    assert "| 1.2 | Частка | Share \\| female | 55% | 57.6% | 104.8% |" in text
    assert "| 2.1 | Грошова | Cash | 1200 | — | — |" in text
    assert "- **2.1** — no measure block" in text
    assert f"| {MISSING} | 5 |" in text
    assert "**sex**: not available — column 'sex' not found" in text


def test_indicator_markdown_without_period_and_empty_scope(tmp_path: Path) -> None:
    empty = SourceInfo("beneficiaries", Path("b.xlsx"), False, 10, 0, 10, 0)
    run = ReportRun(period=None, sources={"beneficiaries": empty}, results=RUN.results)
    md, xlsx = _paths(tmp_path, "indicators")
    render_indicator_report(run, md, xlsx, GENERATED)
    text = md.read_text(encoding="utf-8")
    assert "Period: all records" in text
    assert "no rows in scope for beneficiaries" in text


def test_indicator_xlsx(tmp_path: Path) -> None:
    md, xlsx = _paths(tmp_path, "indicators")
    render_indicator_report(RUN, md, xlsx, GENERATED)
    wb = openpyxl.load_workbook(xlsx)
    assert wb.sheetnames == ["Summary", "About", "oblast"]
    summary = _rows(wb["Summary"])
    assert summary[0] == [
        "Code", "Indicator (uk)", "Indicator (en)", "Target", "Unit",
        "Actual", "Progress %", "Status", "Reason",
    ]
    assert summary[1] == ["1.1", "Охоплені", "Reached", 2500, "households", 3000, 120,
                          "computed", None]
    assert summary[2][5] == 57.63
    assert summary[3][5] is None and summary[3][7] == "not_computable"
    about = _rows(wb["About"])
    assert about[0][:2] == ["Generated", "2026-09-14"]
    assert about[1][1] == "2026-06-01 .. 2026-08-31 (by submission_date)"
    assert about[4] == ["beneficiaries", "beneficiaries_clean.xlsx", "clean", 3030, 3000, 30, 2]
    assert wb["About"]["A4"].font.bold
    assert _rows(wb["oblast"])[1:] == [["1.1", "Донецька", 400, None], ["1.1", MISSING, 5, None]]


def test_sheet_titles_are_sanitized_and_unique(tmp_path: Path) -> None:
    result = RUN.results[0]
    run = ReportRun(
        period=None,
        sources={},
        results=[
            IndicatorResult(
                code="1", name=result.name, dataset="b", target=None, unit=None, actual=1.0,
                progress_pct=None, status="computed",
                breakdowns={"a/b:c": [BreakdownRow("x", 1.0)], "summary": [BreakdownRow("y", 1.0)]},
            )
        ],
    )
    md, xlsx = _paths(tmp_path, "indicators")
    render_indicator_report(run, md, xlsx, GENERATED)
    assert openpyxl.load_workbook(xlsx).sheetnames == ["Summary", "About", "abc", "summary_2"]


def test_5w_markdown(tmp_path: Path) -> None:
    md, xlsx = _paths(tmp_path, "5w")
    render_5w(TABLE, SOURCE, PERIOD, md, xlsx, GENERATED)
    text = md.read_text(encoding="utf-8")
    assert "Dataset: beneficiaries (beneficiaries_clean.xlsx, clean copy)" in text
    assert "| Organization | oblast | Period | Activity | Beneficiaries |" in text
    assert "| IMC | X | 2026-06 | health | 2 |" in text
    assert "**Unique reach (distinct _uuid):** 2" in text
    assert "**Records without any activity:** 1" in text


def test_5w_markdown_row_cap(tmp_path: Path) -> None:
    md, xlsx = _paths(tmp_path, "5w")
    render_5w(TABLE, SOURCE, None, md, xlsx, GENERATED, md_row_cap=2)
    text = md.read_text(encoding="utf-8")
    assert "| IMC | Y | 2026-07 | cash | 1 |" not in text
    assert "first 2 of 3 rows" in text
    assert len(_rows(openpyxl.load_workbook(xlsx)["5W"])) == 4  # the xlsx keeps every row


def test_5w_xlsx(tmp_path: Path) -> None:
    md, xlsx = _paths(tmp_path, "5w")
    render_5w(TABLE, SOURCE, PERIOD, md, xlsx, GENERATED)
    wb = openpyxl.load_workbook(xlsx)
    assert wb.sheetnames == ["5W", "About"]
    rows = _rows(wb["5W"])
    assert rows[0] == TABLE.columns
    assert rows[2] == ["IMC", "X", "2026-06", "health", 2]
    about = {r[0]: r[1] for r in _rows(wb["About"])}
    assert about["Unique reach (distinct _uuid)"] == 2
    assert about["Copy"] == "clean"


def test_5w_empty_table_warns(tmp_path: Path) -> None:
    empty = FiveWTable(columns=TABLE.columns, rows=[], id_field="_uuid", unique_reach=0,
                       rows_without_activity=0)
    md, xlsx = _paths(tmp_path, "5w")
    render_5w(empty, SOURCE, None, md, xlsx, GENERATED)
    assert "table is empty" in md.read_text(encoding="utf-8")
    assert _rows(openpyxl.load_workbook(xlsx)["5W"]) == [TABLE.columns]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTEST tests/test_reporting_render.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'haa.reporting.render'`.

- [ ] **Step 3: Implement**

`src/haa/reporting/render.py`:

```python
"""Render report results to markdown and xlsx (openpyxl)."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

from haa.reporting.engine import ReportRun
from haa.reporting.fivew import FiveWTable
from haa.reporting.sources import Period, SourceInfo

SUMMARY_MD_HEADER = ["Code", "Indicator (uk)", "Indicator (en)", "Target", "Actual", "Progress"]
SUMMARY_XLSX_HEADER = [
    "Code", "Indicator (uk)", "Indicator (en)", "Target", "Unit",
    "Actual", "Progress %", "Status", "Reason",
]
SOURCE_HEADER = [
    "Dataset", "File", "Copy", "Rows total", "Rows in scope",
    "Excluded by period", "Rows without a valid date",
]
BREAKDOWN_XLSX_HEADER = ["Code", "Category", "Value", "Note"]

_INVALID_TITLE = re.compile(r"[\[\]:*?/\\]")


def report_paths(reports_dir: Path, kind: str, today: date) -> tuple[Path, Path]:
    stem = f"{kind}_{today.isoformat()}"
    return reports_dir / f"{stem}.md", reports_dir / f"{stem}.xlsx"


def format_value(value: float | None, unit: str | None = None) -> str:
    if value is None:
        return "—"
    text = str(int(value)) if float(value).is_integer() else f"{value:.1f}"
    return f"{text}%" if unit == "percent" else text


def period_label(period: Period | None) -> str:
    return period.label() if period is not None else "all records"


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _md_table(header: list[str], rows: list[list[object]]) -> list[str]:
    lines = [
        "| " + " | ".join(_cell(h) for h in header) + " |",
        "|" + "---|" * len(header),
    ]
    lines += ["| " + " | ".join(_cell(v) for v in row) + " |" for row in rows]
    return lines


def _xl(value: object) -> object:
    if isinstance(value, float):
        return int(value) if value.is_integer() else round(value, 4)
    return value


def _sheet_title(name: str, taken: set[str]) -> str:
    base = _INVALID_TITLE.sub("", str(name)).strip()[:31] or "Sheet"
    title, n = base, 2
    while title.lower() in {t.lower() for t in taken}:
        suffix = f"_{n}"
        title = base[: 31 - len(suffix)] + suffix
        n += 1
    taken.add(title)
    return title


def _write_table(ws, header: list[str], rows: list[list[object]]) -> None:
    ws.append(header)
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append([_xl(v) for v in row])


def _source_rows(sources: dict[str, SourceInfo]) -> list[list[object]]:
    return [
        [
            s.dataset, s.path.name, "clean" if s.used_clean else "raw", s.rows_total,
            s.rows_in_scope, s.rows_excluded_by_period, s.rows_bad_date,
        ]
        for s in sources.values()
    ]


def render_indicator_report(
    run: ReportRun, md_path: Path, xlsx_path: Path, generated: date
) -> None:
    lines = [
        "# Indicator progress report",
        "",
        f"Generated: {generated.isoformat()}",
        f"Period: {period_label(run.period)}",
        "",
    ]
    if run.sources:
        lines += ["## Sources", "", *_md_table(SOURCE_HEADER, _source_rows(run.sources)), ""]
        for source in run.sources.values():
            if source.rows_in_scope == 0:
                lines += [
                    f"> **Warning:** no rows in scope for {source.dataset} — its indicators "
                    "are 0 or not computable.",
                    "",
                ]
    summary = [
        [
            r.code, r.name.get("uk", ""), r.name.get("en", ""),
            format_value(r.target, r.unit), format_value(r.actual, r.unit),
            format_value(r.progress_pct, "percent"),
        ]
        for r in run.results
    ]
    lines += ["## Summary", "", *_md_table(SUMMARY_MD_HEADER, summary), ""]
    if run.not_computable:
        lines += ["## Not computable", ""]
        lines += [f"- **{r.code}** — {r.reason}" for r in run.not_computable]
        lines.append("")
    detailed = [r for r in run.computed if r.breakdowns or r.breakdown_errors]
    if detailed:
        lines += ["## Disaggregation", ""]
        for r in detailed:
            lines += [f"### {r.code} — {r.name.get('en') or r.name.get('uk', '')}", ""]
            for dimension, rows in r.breakdowns.items():
                table_rows = [
                    [
                        row.category,
                        format_value(row.value, r.unit)
                        if row.reason is None
                        else f"— ({row.reason})",
                    ]
                    for row in rows
                ]
                lines += [f"**{dimension}**", "", *_md_table([dimension, "Value"], table_rows), ""]
            for dimension, reason in r.breakdown_errors.items():
                lines += [f"**{dimension}**: not available — {reason}", ""]
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines), encoding="utf-8")

    wb = Workbook()
    taken: set[str] = set()
    summary_ws = wb.active
    summary_ws.title = _sheet_title("Summary", taken)
    _write_table(
        summary_ws,
        SUMMARY_XLSX_HEADER,
        [
            [
                r.code, r.name.get("uk", ""), r.name.get("en", ""), r.target, r.unit,
                r.actual, r.progress_pct, r.status, r.reason,
            ]
            for r in run.results
        ],
    )
    summary_ws.freeze_panes = "A2"
    about = wb.create_sheet(_sheet_title("About", taken))
    about.append(["Generated", generated.isoformat()])
    about.append(["Period", period_label(run.period)])
    about.append([None])
    _write_table(about, SOURCE_HEADER, _source_rows(run.sources))
    by_dimension: dict[str, list[list[object]]] = {}
    for r in run.computed:
        for dimension, rows in r.breakdowns.items():
            by_dimension.setdefault(dimension, []).extend(
                [r.code, row.category, row.value, row.reason] for row in rows
            )
    for dimension, rows in by_dimension.items():
        ws = wb.create_sheet(_sheet_title(dimension, taken))
        _write_table(ws, BREAKDOWN_XLSX_HEADER, rows)
        ws.freeze_panes = "A2"
    xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(xlsx_path)


def render_5w(
    table: FiveWTable,
    source: SourceInfo,
    period: Period | None,
    md_path: Path,
    xlsx_path: Path,
    generated: date,
    md_row_cap: int = 200,
) -> None:
    copy = "clean" if source.used_clean else "raw"
    lines = [
        "# 5W report",
        "",
        f"Generated: {generated.isoformat()}",
        f"Dataset: {source.dataset} ({source.path.name}, {copy} copy)",
        f"Period: {period_label(period)}",
        f"Rows in scope: {source.rows_in_scope} (excluded by period: "
        f"{source.rows_excluded_by_period}; rows without a valid date: {source.rows_bad_date})",
        "",
    ]
    if not table.rows:
        lines += ["> **Warning:** no activity rows in scope — the table is empty.", ""]
    shown = table.rows[:md_row_cap]
    lines += _md_table(table.columns, [[row[c] for c in table.columns] for row in shown])
    lines.append("")
    if len(table.rows) > md_row_cap:
        lines += [
            f"_Showing the first {md_row_cap} of {len(table.rows)} rows — "
            "the .xlsx has all of them._",
            "",
        ]
    lines += [
        f"**Unique reach (distinct {table.id_field}):** {table.unique_reach}",
        "",
        "A beneficiary with several activities appears in several rows, so the "
        "Beneficiaries column does not add up to the unique reach.",
        "",
        f"**Records without any activity:** {table.rows_without_activity}",
    ]
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines), encoding="utf-8")

    wb = Workbook()
    ws = wb.active
    ws.title = "5W"
    _write_table(ws, table.columns, [[row[c] for c in table.columns] for row in table.rows])
    ws.freeze_panes = "A2"
    about = wb.create_sheet("About")
    for label, value in [
        ("Generated", generated.isoformat()),
        ("Period", period_label(period)),
        ("Dataset", source.dataset),
        ("File", source.path.name),
        ("Copy", copy),
        ("Rows in scope", source.rows_in_scope),
        ("Excluded by period", source.rows_excluded_by_period),
        ("Rows without a valid date", source.rows_bad_date),
        (f"Unique reach (distinct {table.id_field})", table.unique_reach),
        ("Records without any activity", table.rows_without_activity),
    ]:
        about.append([label, value])
    xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(xlsx_path)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTEST tests/test_reporting_render.py -q`, then `RUFF`
Expected: 10 passed; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/haa/reporting/render.py tests/test_reporting_render.py
git commit -m "feat: markdown and xlsx rendering for indicator and 5W reports"
```

---

### Task 7: The `reports` MCP server

**Files:**
- Create: `src/haa/core/tools/reporttools.py`
- Test: `tests/test_reporttools.py`

**Interfaces:**
- Consumes: Tasks 1–6; existing `haa.config.HaaConfig`, `haa.core.telemetry.SessionTelemetry`, `haa.indicators.registry.load_registry` / `RegistryError`; conftest fixture `report_workspace`; Task 4's `VALID_MAPPING`.
- Produces (Tasks 8–10 rely on these exact names):
  - `REPORT_TOOL_NAMES = ["mcp__reports__compute_indicators", "mcp__reports__build_indicator_report", "mcp__reports__read_5w_mapping", "mcp__reports__save_5w_mapping", "mcp__reports__build_5w"]`
  - `PERIOD_SCHEMA` — full JSON Schema, `"required": []`
  - `run_summary(run: ReportRun) -> str`
  - `class ReportToolbox(config: HaaConfig, telemetry: SessionTelemetry, today: Callable[[], date] = date.today)` with methods, all returning `str`: `compute_indicators(date_field=None, start=None, end=None)`, `build_indicator_report(date_field=None, start=None, end=None)`, `read_5w_mapping()`, `save_5w_mapping(yaml_text: str)`, `build_5w(date_field=None, start=None, end=None)` — `build_5w` defaults `date_field` to the mapping's `when.field`
  - Telemetry: `report_built` with `report="indicators"`, `computed`, `not_computable`, `rows`, `paths`; `report_built` with `report="5w"`, `rows`, `unique_reach`, `paths`; `mapping_saved` with `dataset`, `columns`
  - `build_reports_server(box: ReportToolbox)` — in-process MCP server named `reports`

- [ ] **Step 1: Write the failing tests**

`tests/test_reporttools.py`:

```python
from datetime import date
from pathlib import Path

import openpyxl
import pandas as pd
import pytest
import yaml

from haa.config import load_config
from haa.core.telemetry import SessionTelemetry
from haa.core.tools.reporttools import PERIOD_SCHEMA, REPORT_TOOL_NAMES, ReportToolbox
from tests.test_reporting_mapping import VALID_MAPPING

TODAY = date(2026, 9, 14)
MAPPING_YAML = yaml.safe_dump(VALID_MAPPING, allow_unicode=True, sort_keys=False)


@pytest.fixture()
def box(report_workspace: Path) -> ReportToolbox:
    cfg = load_config(report_workspace)
    return ReportToolbox(cfg, SessionTelemetry(cfg.logs_dir / "t.jsonl"), today=lambda: TODAY)


def _log(box: ReportToolbox) -> str:
    path = box.telemetry.path
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_tool_names_constant() -> None:
    assert REPORT_TOOL_NAMES == [
        "mcp__reports__compute_indicators",
        "mcp__reports__build_indicator_report",
        "mcp__reports__read_5w_mapping",
        "mcp__reports__save_5w_mapping",
        "mcp__reports__build_5w",
    ]


def test_period_schema_makes_arguments_optional() -> None:
    assert PERIOD_SCHEMA["type"] == "object" and PERIOD_SCHEMA["required"] == []
    assert set(PERIOD_SCHEMA["properties"]) == {"date_field", "start", "end"}


def test_compute_indicators_summary(box: ReportToolbox) -> None:
    out = box.compute_indicators()
    assert "Period: all records" in out
    assert (
        "- 1.1 Охоплені домогосподарства / Households reached: actual 3000, "
        "target 2500 = 120% of target"
    ) in out
    assert "by oblast:" in out and "by head_sex:" in out
    assert "Not computable (1):" in out and "no measure block" in out
    assert not list(box.config.reports_dir.iterdir())  # preview only, no files


def test_compute_indicators_with_period(box: ReportToolbox) -> None:
    out = box.compute_indicators("submission_date", "2026-06-01", "2026-08-31")
    assert "Period: 2026-06-01 .. 2026-08-31 (by submission_date)" in out
    assert "excluded by period" in out


def test_compute_indicators_bad_period_is_friendly(box: ReportToolbox) -> None:
    out = box.compute_indicators("submission_date", "2026-06-01", None)
    assert "both" in out and "Traceback" not in out


def test_compute_indicators_empty_registry(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    box = ReportToolbox(cfg, SessionTelemetry(cfg.logs_dir / "t.jsonl"))
    assert "empty" in box.compute_indicators()


def test_compute_indicators_broken_registry_yaml(box: ReportToolbox) -> None:
    (box.config.workspace / "indicators.yaml").write_text("indicators: [\n", encoding="utf-8")
    assert "indicators.yaml could not be read" in box.compute_indicators()


def test_build_indicator_report_writes_files_and_logs(box: ReportToolbox) -> None:
    out = box.build_indicator_report()
    md = box.config.reports_dir / "indicators_2026-09-14.md"
    xlsx = box.config.reports_dir / "indicators_2026-09-14.xlsx"
    assert md.is_file() and xlsx.is_file()
    assert (
        "Report files: reports/indicators_2026-09-14.md, reports/indicators_2026-09-14.xlsx"
    ) in out
    first = next(openpyxl.load_workbook(xlsx)["Summary"].iter_rows(min_row=2, values_only=True))
    assert first[0] == "1.1" and first[5] == 3000
    log = _log(box)
    assert '"kind": "report_built"' in log and '"report": "indicators"' in log
    assert '"computed": 2' in log and '"not_computable": 1' in log


def test_build_indicator_report_write_failure_is_friendly(
    box: ReportToolbox, monkeypatch
) -> None:
    import haa.core.tools.reporttools as rt

    def boom(*args):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(rt, "render_indicator_report", boom)
    out = box.build_indicator_report()
    assert "close it" in out and "Traceback" not in out


def test_read_mapping_when_missing(box: ReportToolbox) -> None:
    assert "no 5W mapping yet" in box.read_5w_mapping()


def test_save_mapping_invalid_writes_nothing(box: ReportToolbox) -> None:
    out = box.save_5w_mapping(yaml.safe_dump(dict(VALID_MAPPING, sector="health")))
    assert "nothing was saved" in out and "sector" in out
    assert not (box.config.workspace / "5w.yaml").exists()


def test_save_mapping_bad_yaml(box: ReportToolbox) -> None:
    assert "Could not parse" in box.save_5w_mapping("where: [\n")


def test_save_read_and_replace_mapping(box: ReportToolbox) -> None:
    first = box.save_5w_mapping(MAPPING_YAML)
    assert "saved" in first and "replaced" not in first
    assert "oblast" in box.read_5w_mapping()
    assert "replaced the previous mapping" in box.save_5w_mapping(MAPPING_YAML)
    assert '"kind": "mapping_saved"' in _log(box)


def test_build_5w_without_mapping(box: ReportToolbox) -> None:
    assert "no 5W mapping yet" in box.build_5w()


def test_build_5w_invalid_mapping_file(box: ReportToolbox) -> None:
    (box.config.workspace / "5w.yaml").write_text("dataset: beneficiaries\n", encoding="utf-8")
    assert "5w.yaml is invalid" in box.build_5w()


def test_build_5w_writes_files_and_logs(box: ReportToolbox) -> None:
    box.save_5w_mapping(MAPPING_YAML)
    out = box.build_5w()
    assert "Unique reach (distinct _uuid): 3000" in out
    assert (box.config.reports_dir / "5w_2026-09-14.md").is_file()
    assert (box.config.reports_dir / "5w_2026-09-14.xlsx").is_file()
    assert '"report": "5w"' in _log(box)


def test_build_5w_period_defaults_to_when_field(box: ReportToolbox) -> None:
    box.save_5w_mapping(MAPPING_YAML)
    assert "by submission_date" in box.build_5w(None, "2026-06-01", "2026-08-31")


def test_tool_outputs_never_contain_pii(box: ReportToolbox, report_workspace: Path) -> None:
    raw = pd.read_excel(report_workspace / "data" / "beneficiaries.xlsx")
    box.save_5w_mapping(MAPPING_YAML)
    text = box.compute_indicators() + box.build_indicator_report() + box.build_5w()
    for value in (*raw["resp_phone"].astype(str), *raw["resp_name"].astype(str)):
        assert value not in text


def test_build_server_importable(box: ReportToolbox) -> None:
    from haa.core.tools.reporttools import build_reports_server

    assert build_reports_server(box) is not None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTEST tests/test_reporttools.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'haa.core.tools.reporttools'`.

- [ ] **Step 3: Implement**

`src/haa/core/tools/reporttools.py`:

```python
"""In-process MCP server for deterministic reports: indicator progress and the 5W matrix."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import yaml

from haa.config import HaaConfig
from haa.core.telemetry import SessionTelemetry
from haa.indicators.registry import RegistryError, load_registry
from haa.reporting import fivew
from haa.reporting.engine import ReportRun, evaluate_registry
from haa.reporting.mapping import (
    load_mapping,
    mapping_path,
    parse_mapping_yaml,
    save_mapping,
    validate_mapping,
)
from haa.reporting.render import (
    format_value,
    period_label,
    render_5w,
    render_indicator_report,
    report_paths,
)
from haa.reporting.sources import ReportError, load_scoped, parse_period

REPORT_TOOL_NAMES = [
    "mcp__reports__compute_indicators",
    "mcp__reports__build_indicator_report",
    "mcp__reports__read_5w_mapping",
    "mcp__reports__save_5w_mapping",
    "mcp__reports__build_5w",
]

# A dict of name -> type makes the SDK mark every argument required; the period
# arguments are optional, so these tools declare a full JSON Schema instead.
PERIOD_SCHEMA = {
    "type": "object",
    "properties": {
        "date_field": {
            "type": "string",
            "description": "Dataset column holding the date (the 5W defaults to when.field)",
        },
        "start": {"type": "string", "description": "Period start, YYYY-MM-DD"},
        "end": {"type": "string", "description": "Period end, YYYY-MM-DD (inclusive)"},
    },
    "required": [],
}

MAX_CATEGORIES_IN_SUMMARY = 30
_WRITE_FAILED = (
    "Could not write the report files: {detail}. "
    "If a report .xlsx is open in Excel, close it and try again."
)


def _name(names: dict) -> str:
    return " / ".join(part for part in (names.get("uk"), names.get("en")) if part)


def run_summary(run: ReportRun) -> str:
    """Plain-text digest of a report run: aggregates only, never rows."""
    lines = [f"Period: {period_label(run.period)}"]
    for source in run.sources.values():
        copy = "clean" if source.used_clean else "raw"
        lines.append(
            f"Source {source.dataset}: {source.path.name} ({copy} copy), "
            f"{source.rows_in_scope} rows in scope, "
            f"{source.rows_excluded_by_period} excluded by period"
        )
    lines.append(f"Computed ({len(run.computed)}):")
    for r in run.computed:
        target = f", target {format_value(r.target, r.unit)}" if r.target is not None else ""
        progress = (
            f" = {format_value(r.progress_pct, 'percent')} of target"
            if r.progress_pct is not None
            else ""
        )
        lines.append(
            f"- {r.code} {_name(r.name)}: actual {format_value(r.actual, r.unit)}"
            f"{target}{progress}"
        )
        for dimension, rows in r.breakdowns.items():
            shown = rows[:MAX_CATEGORIES_IN_SUMMARY]
            parts = [
                f"{row.category} {format_value(row.value, r.unit)}"
                if row.reason is None
                else f"{row.category} n/a ({row.reason})"
                for row in shown
            ]
            more = (
                f"; and {len(rows) - len(shown)} more (see the report file)"
                if len(rows) > len(shown)
                else ""
            )
            lines.append(f"    by {dimension}: {'; '.join(parts)}{more}")
        for dimension, reason in r.breakdown_errors.items():
            lines.append(f"    by {dimension}: not available — {reason}")
    lines.append(f"Not computable ({len(run.not_computable)}):")
    lines += [f"- {r.code} {_name(r.name)}: {r.reason}" for r in run.not_computable]
    return "\n".join(lines)


class ReportToolbox:
    def __init__(
        self,
        config: HaaConfig,
        telemetry: SessionTelemetry,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.config = config
        self.telemetry = telemetry
        self._today = today

    # -- indicators -------------------------------------------------------
    def _run(self, date_field: str | None, start: str | None, end: str | None) -> ReportRun:
        period = parse_period(date_field, start, end)
        try:
            registry = load_registry(self.config.workspace)
        except RegistryError as exc:
            raise ReportError(f"indicators.yaml could not be read — {exc}") from exc
        return evaluate_registry(registry, self.config.data_dir, period)

    def compute_indicators(
        self, date_field: str | None = None, start: str | None = None, end: str | None = None
    ) -> str:
        try:
            run = self._run(date_field, start, end)
        except ReportError as exc:
            return str(exc)
        return run_summary(run)

    def build_indicator_report(
        self, date_field: str | None = None, start: str | None = None, end: str | None = None
    ) -> str:
        try:
            run = self._run(date_field, start, end)
        except ReportError as exc:
            return str(exc)
        today = self._today()
        md_path, xlsx_path = report_paths(self.config.reports_dir, "indicators", today)
        try:
            render_indicator_report(run, md_path, xlsx_path, today)
        except OSError as exc:
            return _WRITE_FAILED.format(detail=exc.strerror or exc)
        self.telemetry.log(
            "report_built",
            report="indicators",
            computed=len(run.computed),
            not_computable=len(run.not_computable),
            rows=sum(s.rows_in_scope for s in run.sources.values()),
            paths=[md_path.name, xlsx_path.name],
        )
        return (
            f"{run_summary(run)}\n"
            f"Report files: reports/{md_path.name}, reports/{xlsx_path.name}"
        )

    # -- 5W ---------------------------------------------------------------
    def read_5w_mapping(self) -> str:
        try:
            mapping = load_mapping(self.config.workspace)
        except ReportError as exc:
            return str(exc)
        if mapping is None:
            return (
                "There is no 5W mapping yet (workspace/5w.yaml). Profile the dataset, "
                "then propose one with save_5w_mapping."
            )
        return yaml.safe_dump(mapping, allow_unicode=True, sort_keys=False)

    def save_5w_mapping(self, yaml_text: str) -> str:
        try:
            mapping = parse_mapping_yaml(yaml_text)
        except ReportError as exc:
            return str(exc)
        errors = validate_mapping(mapping)
        if errors:
            return "5W mapping validation failed — nothing was saved:\n" + "\n".join(
                f"- {e}" for e in errors
            )
        replaced = mapping_path(self.config.workspace).is_file()
        try:
            save_mapping(self.config.workspace, mapping)
        except OSError as exc:
            return f"Could not write 5w.yaml: {exc.strerror or exc}."
        self.telemetry.log(
            "mapping_saved", dataset=mapping["dataset"], columns=list(mapping["where"])
        )
        note = " (replaced the previous mapping)" if replaced else ""
        return (
            f"5W mapping saved to 5w.yaml{note}: dataset {mapping['dataset']!r}, "
            f"where = {', '.join(mapping['where'])}. Build the table with build_5w."
        )

    def build_5w(
        self, date_field: str | None = None, start: str | None = None, end: str | None = None
    ) -> str:
        try:
            mapping = load_mapping(self.config.workspace)
            if mapping is None:
                return (
                    "There is no 5W mapping yet (workspace/5w.yaml) — ask the reporter "
                    "to propose one."
                )
            errors = validate_mapping(mapping)
            if errors:
                return "5w.yaml is invalid — fix it by hand or save a corrected mapping:\n" + (
                    "\n".join(f"- {e}" for e in errors)
                )
            period = parse_period(date_field or mapping["when"]["field"], start, end)
            df, source, pii = load_scoped(self.config.data_dir, mapping["dataset"], period)
            table = fivew.build_5w(df, mapping, pii)
        except ReportError as exc:
            return str(exc)
        today = self._today()
        md_path, xlsx_path = report_paths(self.config.reports_dir, "5w", today)
        try:
            render_5w(table, source, period, md_path, xlsx_path, today)
        except OSError as exc:
            return _WRITE_FAILED.format(detail=exc.strerror or exc)
        self.telemetry.log(
            "report_built",
            report="5w",
            rows=len(table.rows),
            unique_reach=table.unique_reach,
            paths=[md_path.name, xlsx_path.name],
        )
        copy = "clean" if source.used_clean else "raw"
        return (
            f"5W built: {len(table.rows)} rows; columns: {', '.join(table.columns)}.\n"
            f"Unique reach (distinct {table.id_field}): {table.unique_reach}; "
            f"records without any activity: {table.rows_without_activity}.\n"
            f"Period: {period_label(period)}; source {source.path.name} ({copy} copy), "
            f"{source.rows_in_scope} rows in scope.\n"
            f"Report files: reports/{md_path.name}, reports/{xlsx_path.name}"
        )


def build_reports_server(box: ReportToolbox):
    import asyncio

    from claude_agent_sdk import create_sdk_mcp_server, tool

    def _text(result: str) -> dict:
        return {"content": [{"type": "text", "text": result}]}

    def _period(args: dict) -> tuple[str | None, str | None, str | None]:
        date_field, start, end = (
            str(args[key]) if args.get(key) else None for key in ("date_field", "start", "end")
        )
        return date_field, start, end

    @tool(
        "compute_indicators",
        "Compute every registry indicator that has a machine-readable measure: actual, "
        "target, % progress and disaggregation. Deterministic — quote these numbers. "
        "Optional period: date_field + start + end (YYYY-MM-DD).",
        PERIOD_SCHEMA,
    )
    async def compute_indicators_tool(args: dict) -> dict:
        return _text(await asyncio.to_thread(box.compute_indicators, *_period(args)))

    @tool(
        "build_indicator_report",
        "Write the indicator progress report (reports/indicators_<date>.md and .xlsx) and "
        "return its summary. Optional period: date_field + start + end (YYYY-MM-DD).",
        PERIOD_SCHEMA,
    )
    async def build_indicator_report_tool(args: dict) -> dict:
        return _text(await asyncio.to_thread(box.build_indicator_report, *_period(args)))

    @tool("read_5w_mapping", "Read the 5W mapping (workspace/5w.yaml) as YAML", {})
    async def read_5w_mapping_tool(args: dict) -> dict:
        return _text(box.read_5w_mapping())

    @tool(
        "save_5w_mapping",
        "Validate and save the 5W mapping (YAML). Returns validation errors instead of "
        "saving when the mapping is wrong.",
        {"yaml_text": str},
    )
    async def save_5w_mapping_tool(args: dict) -> dict:
        return _text(box.save_5w_mapping(str(args["yaml_text"])))

    @tool(
        "build_5w",
        "Build the 5W matrix from workspace/5w.yaml (reports/5w_<date>.md and .xlsx) and "
        "return its summary. Optional period: start + end (YYYY-MM-DD); date_field "
        "defaults to the mapping's when.field.",
        PERIOD_SCHEMA,
    )
    async def build_5w_tool(args: dict) -> dict:
        return _text(await asyncio.to_thread(box.build_5w, *_period(args)))

    return create_sdk_mcp_server(
        name="reports",
        version="0.1.0",
        tools=[
            compute_indicators_tool,
            build_indicator_report_tool,
            read_5w_mapping_tool,
            save_5w_mapping_tool,
            build_5w_tool,
        ],
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTEST tests/test_reporttools.py -q`, then `RUFF`
Expected: 19 passed; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/haa/core/tools/reporttools.py tests/test_reporttools.py
git commit -m "feat: reports MCP server with deterministic indicator and 5W tools"
```

---

### Task 8: The `reporter` agent role

**Files:**
- Create: `src/haa/core/agents/reporter.py`
- Modify: `src/haa/config.py` (one field), `src/haa/core/agents/registry.py` (full replacement below)
- Test: create `tests/test_reporter_agent.py`; modify `tests/test_config.py`, `tests/test_agents.py`, `tests/test_cleaner_agent.py`, `tests/test_designer_agent.py`

**Interfaces:**
- Consumes: Task 7 `REPORT_TOOL_NAMES`.
- Produces: `HaaConfig.reporter_model: str = "claude-opus-5"`; `REPORTER_PROMPT`; `build_reporter(config: HaaConfig) -> AgentDefinition` with tools = `REPORT_TOOL_NAMES` + `mcp__forms__read_indicators`, `mcp__data__list_datasets`, `mcp__data__profile_dataset`, `mcp__data__list_project_docs`, `mcp__data__read_project_doc` (no `run_analysis`), `mcpServers=["reports", "forms", "data"]`; `build_agents()` returns four roles.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_reporter_agent.py`:

```python
from pathlib import Path

from haa.config import load_config
from haa.core.agents.reporter import REPORTER_PROMPT, build_reporter
from haa.core.tools.reporttools import REPORT_TOOL_NAMES


def _cfg(tmp_path: Path):
    return load_config(tmp_path)


def test_reporter_definition(tmp_path: Path) -> None:
    agent = build_reporter(_cfg(tmp_path))
    assert agent.model == "claude-opus-5"
    assert set(REPORT_TOOL_NAMES) <= set(agent.tools)
    assert "mcp__forms__read_indicators" in agent.tools
    assert "mcp__data__profile_dataset" in agent.tools
    assert "mcp__data__read_project_doc" in agent.tools
    assert "mcp__data__run_analysis" not in agent.tools
    assert set(agent.mcpServers) == {"reports", "forms", "data"}
    assert agent.prompt == REPORTER_PROMPT


def test_reporter_model_configurable(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text('reporter_model = "claude-sonnet-5"', encoding="utf-8")
    assert build_reporter(_cfg(tmp_path)).model == "claude-sonnet-5"


def test_reporter_prompt_discipline() -> None:
    text = REPORTER_PROMPT.lower()
    for needle in (
        "never compute", "read_indicators", "build_indicator_report", "not computable",
        "designer", "profile_dataset", "pii", "confirmation", "all records",
        "user's language",
    ):
        assert needle in text, needle


def test_reporter_prompt_carries_5w_schema() -> None:
    for needle in (
        "exactly these keys", "fixed:", "where: [oblast, raion, hromada]",
        "granularity: month", "month | none", 'split: " "', "id_field: _uuid",
    ):
        assert needle in REPORTER_PROMPT, needle
```

Append to `tests/test_config.py`:

```python
def test_reporter_model_default_and_override(tmp_path: Path) -> None:
    assert load_config(tmp_path).reporter_model == "claude-opus-5"
    (tmp_path / "config.toml").write_text('reporter_model = "claude-sonnet-5"', encoding="utf-8")
    assert load_config(tmp_path).reporter_model == "claude-sonnet-5"
```

Update the three existing role-set assertions to include the new role:

- `tests/test_agents.py`, in `test_registry`: replace `assert set(agents) == {"analyst", "cleaner", "designer"}` with `assert set(agents) == {"analyst", "cleaner", "designer", "reporter"}`
- `tests/test_cleaner_agent.py`, in `test_registry_has_all_roles`: replace `{"analyst", "cleaner", "designer"}` with `{"analyst", "cleaner", "designer", "reporter"}`
- `tests/test_designer_agent.py`: replace the whole function

```python
def test_registry_has_three_roles(tmp_path: Path) -> None:
    assert set(build_agents(_cfg(tmp_path))) == {"analyst", "cleaner", "designer"}
```

with

```python
def test_registry_has_all_roles(tmp_path: Path) -> None:
    assert set(build_agents(_cfg(tmp_path))) == {"analyst", "cleaner", "designer", "reporter"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTEST tests/test_reporter_agent.py tests/test_config.py tests/test_agents.py tests/test_cleaner_agent.py tests/test_designer_agent.py -q`
Expected: `tests/test_reporter_agent.py` fails to collect (`ModuleNotFoundError: No module named 'haa.core.agents.reporter'`); `test_reporter_model_default_and_override` fails with `AttributeError: 'HaaConfig' object has no attribute 'reporter_model'`; the three role-set tests fail because `"reporter"` is missing.

- [ ] **Step 3: Implement**

In `src/haa/config.py`, add the field directly after `designer_model: str = "claude-opus-5"`:

```python
    reporter_model: str = "claude-opus-5"
```

Create `src/haa/core/agents/reporter.py`:

```python
"""Reporter subagent: deterministic indicator-progress and 5W reports."""

from claude_agent_sdk import AgentDefinition

from haa.config import HaaConfig
from haa.core.tools.reporttools import REPORT_TOOL_NAMES

CONTEXT_TOOLS = [
    "mcp__forms__read_indicators",
    "mcp__data__list_datasets",
    "mcp__data__profile_dataset",
    "mcp__data__list_project_docs",
    "mcp__data__read_project_doc",
]

REPORTER_PROMPT = """\
You produce programme reports for humanitarian M&E: the indicator progress
report and the 5W matrix. Every number comes from the report tools — you have no
code execution, and you never compute, estimate or round a figure yourself.

Indicator progress report:
- Call read_indicators first. If the registry is empty, say so and suggest
  asking the designer to extract indicators from the logframe.
- compute_indicators previews the numbers; build_indicator_report writes
  reports/indicators_<date>.md and .xlsx and returns the same summary.
- Tell the user which indicators were computed (actual, target, % progress),
  which are not computable and why, any breakdown problems, and the file paths.
- A not computable indicator or a broken breakdown is fixed in the registry:
  point the user to the designer (add a measure block, or name a real dataset
  column in disaggregation).

5W matrix:
- Call read_5w_mapping. If a mapping exists, call build_5w.
- If there is none: list_datasets and profile_dataset, read the project
  documents (list_project_docs / read_project_doc) for the organization and
  project names, then write a mapping that uses ONLY column names shown in the
  profile. If the documents do not name the organization, leave `fixed` out
  rather than guessing. Save it with save_5w_mapping, fix every validation error
  and retry, then call build_5w in the same turn and show the user the saved
  mapping so they can correct it.
- Replacing an existing mapping needs the user's explicit confirmation first.
- Never use a column the profile marks as PII.
- 5w.yaml schema — use exactly these keys, no others (values are examples):

    dataset: beneficiaries             # dataset name from list_datasets
    fixed:                             # optional constant columns (Who, project)
      Organization: IMC
      Project: ABC-123
    where: [oblast, raion, hromada]    # admin-level columns, broadest first
    when: {field: submission_date, granularity: month}   # month | none
    what: {field: services_received, split: " "}         # split is optional
    whom:
      id_field: _uuid                  # unique beneficiary / household ID
      disaggregation: [head_sex]       # optional SADD columns

Reporting period:
- If the user names a period ("for August", "June to August 2026"), pass start
  and end as YYYY-MM-DD, plus date_field for the indicator report (the 5W
  defaults to its when.field). Without a period the report covers all records
  — say so.

Answer in the user's language and quote numbers exactly as the tools return them.
"""


def build_reporter(config: HaaConfig) -> AgentDefinition:
    return AgentDefinition(
        description=(
            "Programme reports: the indicator progress report against logframe targets "
            "and the 5W matrix, written as markdown + xlsx by a deterministic engine. "
            "Delegate report / 5W / звіт / отчёт requests here."
        ),
        prompt=REPORTER_PROMPT,
        tools=[*REPORT_TOOL_NAMES, *CONTEXT_TOOLS],
        model=config.reporter_model,
        mcpServers=["reports", "forms", "data"],
    )
```

Replace `src/haa/core/agents/registry.py` with:

```python
"""Single place where agent roles are registered (future roles plug in here)."""

from claude_agent_sdk import AgentDefinition

from haa.config import HaaConfig
from haa.core.agents.analyst import build_analyst
from haa.core.agents.cleaner import build_cleaner
from haa.core.agents.designer import build_designer
from haa.core.agents.reporter import build_reporter


def build_agents(config: HaaConfig) -> dict[str, AgentDefinition]:
    return {
        "analyst": build_analyst(config),
        "cleaner": build_cleaner(config),
        "designer": build_designer(config),
        "reporter": build_reporter(config),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTEST tests/test_reporter_agent.py tests/test_config.py tests/test_agents.py tests/test_cleaner_agent.py tests/test_designer_agent.py -q`, then `RUFF`
Expected: all pass; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/haa/config.py src/haa/core/agents/reporter.py src/haa/core/agents/registry.py tests/test_reporter_agent.py tests/test_config.py tests/test_agents.py tests/test_cleaner_agent.py tests/test_designer_agent.py
git commit -m "feat: reporter agent role for deterministic reports"
```

---

### Task 9: Wiring — session, analyst, orchestrator

**Files:**
- Modify: `src/haa/core/session.py`, `src/haa/core/agents/analyst.py`, `src/haa/core/agents/orchestrator.py`
- Test: modify `tests/test_session.py`, `tests/test_agents.py`

**Interfaces:**
- Consumes: Task 7 `REPORT_TOOL_NAMES`, `ReportToolbox`, `build_reports_server`; Task 8 `reporter` role.
- Produces: the session exposes the `reports` MCP server and allows `REPORT_TOOL_NAMES`; the analyst gains `mcp__reports__compute_indicators`; the orchestrator routes report requests to `reporter`.

- [ ] **Step 1: Write the failing tests**

`tests/test_session.py` — add the import directly after the `formtools` import:

```python
from haa.core.tools.reporttools import REPORT_TOOL_NAMES
```

and in `test_build_options`, directly after `assert "forms" in opts.mcp_servers`, add:

```python
    assert "reports" in opts.mcp_servers
    assert "reporter" in opts.agents
    assert set(REPORT_TOOL_NAMES) <= set(opts.allowed_tools)
```

`tests/test_agents.py` — in `test_analyst_definition_fields`, directly after `assert "mcp__forms__read_indicators" in agent.tools`, add:

```python
    assert "mcp__reports__compute_indicators" in agent.tools
    assert "reports" in agent.mcpServers
```

and replace the body of `test_prompts_carry_discipline` with:

```python
    for needle in (
        "never fabricate", "SADD", "load_dataset", "aggregat", "read_indicators",
        "compute_indicators",
    ):
        assert needle.lower() in ANALYST_PROMPT.lower()
    for needle in ("delegate", "analyst", "honest", "cleaner", "pull", "designer", "reporter"):
        assert needle.lower() in ORCHESTRATOR_PROMPT.lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTEST tests/test_session.py tests/test_agents.py -q`
Expected: `test_build_options` fails on `"reports" in opts.mcp_servers`; `test_analyst_definition_fields` fails on the compute_indicators tool; `test_prompts_carry_discipline` fails on `compute_indicators`.

- [ ] **Step 3: Implement**

`src/haa/core/session.py`:

1. Directly after `from haa.core.tools.formtools import FORM_TOOL_NAMES, FormToolbox, build_forms_server` add:

```python
from haa.core.tools.reporttools import REPORT_TOOL_NAMES, ReportToolbox, build_reports_server
```

2. In `AnalyticsSession.__init__`, directly after `self._forms_server = build_forms_server(self._form_toolbox)` add:

```python
        self._report_toolbox = ReportToolbox(config, self.telemetry)
        self._reports_server = build_reports_server(self._report_toolbox)
```

3. In `build_options`, add `"reports": self._reports_server,` as the last entry of `mcp_servers` (after `"forms": self._forms_server,`), and `*REPORT_TOOL_NAMES,` as the last entry of `allowed_tools` (after `*FORM_TOOL_NAMES,`).

`src/haa/core/agents/analyst.py`:

1. Replace step 2 of `ANALYST_PROMPT` — the whole paragraph from `2. If the question mentions an indicator` through `to real columns yourself with profile_dataset.` — with:

```text
2. If the question mentions an indicator, a target or programme progress, call
   read_indicators and then compute_indicators — it evaluates every indicator
   with a machine-readable measure deterministically (actual, target, % progress,
   breakdowns by the registry's disaggregation columns). Quote its numbers; do
   not recompute them. Only for an indicator it reports as not computable may
   you compute a value yourself with run_analysis — and then say that the
   registry measure is missing or broken. Measure semantics for that fallback:
   `count` = rows after the filter (or non-null values of `field`);
   `count_unique` = distinct non-null values of `field`; `sum` = sum of `field`;
   `percent` = 100 * numerator / denominator, each the distinct non-null values
   of its `field` after its own `filter`. A `filter` is a pandas query applied
   BEFORE the aggregation.
```

2. In `build_analyst`, replace

```python
        tools=[*DATA_TOOL_NAMES, "mcp__forms__read_indicators"],
        model=config.analyst_model,
        # Declare access to the in-process "data" and "forms" MCP servers explicitly
        # rather than relying on the subagent inheriting the main loop's servers.
        mcpServers=["data", "forms"],
```

with

```python
        tools=[
            *DATA_TOOL_NAMES,
            "mcp__forms__read_indicators",
            "mcp__reports__compute_indicators",
        ],
        model=config.analyst_model,
        # Declare access to the in-process MCP servers explicitly rather than relying
        # on the subagent inheriting the main loop's servers.
        mcpServers=["data", "forms", "reports"],
```

`src/haa/core/agents/orchestrator.py` — in `ORCHESTRATOR_PROMPT`, directly after the routing rule about the indicators registry and survey forms (the one that delegates to the designer subagent), add:

```text
- Requests to produce a report — the indicator progress report or a 5W matrix
  (report, звіт, отчёт, 5W) — delegate to the `reporter` subagent. A question
  about one indicator's value or progress is analytical: it goes to the analyst.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTEST tests/test_session.py tests/test_agents.py tests/test_designer_agent.py -q`, then `RUFF`
Expected: all pass; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/haa/core/session.py src/haa/core/agents/analyst.py src/haa/core/agents/orchestrator.py tests/test_session.py tests/test_agents.py
git commit -m "feat: wire the reports server into the session, analyst and orchestrator"
```

---

### Task 10: CLI — `haa report indicators|5w` without the LLM

**Files:**
- Modify: `src/haa/cli/app.py`
- Test: modify `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 7 `ReportToolbox`; existing `load_config`, `ConfigError`, `SessionTelemetry`; conftest `report_workspace`.
- Produces: `run_report(workspace: Path, kind: str, period: str | None, date_field: str | None) -> str`; parser subcommand `report` with positional `kind` (`indicators` | `5w`) and options `--workspace`, `--period START..END`, `--date-field`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cli.py`, change the first import line to:

```python
from haa.cli.app import build_parser, run_connect, run_pull, run_report, validate_workspace
```

and append:

```python
def test_parser_report() -> None:
    a = build_parser().parse_args([
        "report", "indicators",
        "--period", "2026-06-01..2026-08-31",
        "--date-field", "submission_date",
    ])
    assert a.command == "report" and a.kind == "indicators"
    assert a.period == "2026-06-01..2026-08-31" and a.date_field == "submission_date"
    b = build_parser().parse_args(["report", "5w"])
    assert b.kind == "5w" and b.period is None and b.date_field is None


def test_run_report_indicators(report_workspace: Path) -> None:
    out = run_report(report_workspace, "indicators", None, None)
    assert "1.1" in out and "Report files:" in out
    assert list((report_workspace / "reports").glob("indicators_*.xlsx"))


def test_run_report_with_period(report_workspace: Path) -> None:
    out = run_report(report_workspace, "indicators", "2026-06-01..2026-08-31", "submission_date")
    assert "2026-06-01 .. 2026-08-31 (by submission_date)" in out


def test_run_report_bad_period_format(report_workspace: Path) -> None:
    out = run_report(report_workspace, "indicators", "2026-06-01", "submission_date")
    assert "START..END" in out


def test_run_report_5w_without_mapping(report_workspace: Path) -> None:
    assert "no 5W mapping yet" in run_report(report_workspace, "5w", None, None)


def test_run_report_5w_with_mapping(report_workspace: Path) -> None:
    from haa.reporting.mapping import save_mapping
    from tests.test_reporting_mapping import VALID_MAPPING

    save_mapping(report_workspace, VALID_MAPPING)
    out = run_report(report_workspace, "5w", None, None)
    assert "Unique reach (distinct _uuid): 3000" in out
    assert list((report_workspace / "reports").glob("5w_*.xlsx"))


def test_run_report_missing_workspace(tmp_path: Path) -> None:
    assert "does not exist" in run_report(tmp_path / "nope", "indicators", None, None)


def test_main_report_prints_summary(report_workspace: Path, monkeypatch, capsys) -> None:
    import haa.cli.app as app

    monkeypatch.setattr(
        "sys.argv", ["haa", "report", "indicators", "--workspace", str(report_workspace)]
    )
    assert app.main() == 0
    assert "Report files:" in capsys.readouterr().out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTEST tests/test_cli.py -q`
Expected: collection error — `ImportError: cannot import name 'run_report' from 'haa.cli.app'`.

- [ ] **Step 3: Implement**

In `src/haa/cli/app.py`:

1. Directly after the `run_pull` function, add:

```python
def run_report(workspace: Path, kind: str, period: str | None, date_field: str | None) -> str:
    from datetime import UTC, datetime

    from haa.core.telemetry import SessionTelemetry
    from haa.core.tools.reporttools import ReportToolbox

    start = end = None
    if period:
        if ".." not in period:
            return "Use --period START..END, for example 2026-06-01..2026-08-31."
        start, end = (part.strip() for part in period.split("..", 1))
    try:
        cfg = load_config(workspace)
    except ConfigError as exc:
        return str(exc)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    box = ReportToolbox(cfg, SessionTelemetry(cfg.logs_dir / f"cli-report-{stamp}.jsonl"))
    if kind == "indicators":
        return box.build_indicator_report(date_field, start, end)
    return box.build_5w(date_field, start, end)
```

2. In `build_parser`, directly before `return parser`, add:

```python
    report = sub.add_parser("report", help="Build a report without the LLM (indicators or 5W)")
    report.add_argument("kind", choices=["indicators", "5w"])
    report.add_argument("--workspace", type=Path, default=Path("workspace"))
    report.add_argument(
        "--period", default=None, help="START..END, for example 2026-06-01..2026-08-31"
    )
    report.add_argument(
        "--date-field",
        dest="date_field",
        default=None,
        help="Date column for the period (the 5W defaults to its when.field)",
    )
```

3. In `main()`, directly after the `if args.command == "pull":` block, add:

```python
    if args.command == "report":
        console.print(
            run_report(args.workspace, args.kind, args.period, args.date_field),
            markup=False,
            highlight=False,
        )
        return 0
```

(`markup=False` keeps rich from interpreting brackets in dataset values or validation messages such as `where[1]`.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTEST tests/test_cli.py -q`, then `RUFF`
Expected: all pass; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/haa/cli/app.py tests/test_cli.py
git commit -m "feat: haa report command builds indicator and 5W reports without the LLM"
```

---

### Task 11: API evals (manual, `api` marker)

**Files:**
- Modify: `tests/test_smoke_api.py` (append two tests; add one assertion to `test_indicator_progress_uses_logframe`)

**Interfaces:**
- Consumes: the full session (Tasks 7–9), conftest `DEMO_REGISTRY`, `demo_workspace`; Task 4 `validate_mapping`, `load_mapping`, `mapping_path`.
- Produces: acceptance evidence for spec §7 criteria 1, 4 and 5 through the real agent. The expectations are computed independently in the test with pandas / openpyxl.

These tests call the real API and are deselected by default (`addopts = -m 'not api and not remote'`), so there is no offline red/green cycle; the user runs them. An earlier cleaner eval may have left `beneficiaries_clean.xlsx` in the shared demo workspace — the indicator eval computes its expectation from whichever copy the engine prefers, and the 5W unique reach is 3000 either way.

- [ ] **Step 1: Extend the analyst eval and append the reporter evals**

In `test_indicator_progress_uses_logframe`, directly after `assert "mcp__forms__read_indicators" in log, "analyst did not consult the registry"`, add (spec §7 criterion 5 — the analyst answers progress questions through the engine):

```python
    assert "mcp__reports__compute_indicators" in log, "analyst did not use the indicator engine"
```

Append to `tests/test_smoke_api.py`:

```python
async def test_reporter_builds_indicator_report(demo_workspace: Path) -> None:
    import time
    from datetime import date

    import openpyxl
    import yaml

    from haa.indicators.registry import registry_path
    from tests.conftest import DEMO_REGISTRY

    cfg = load_config(demo_workspace)
    registry_path(cfg.workspace).write_text(
        yaml.safe_dump(DEMO_REGISTRY, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    started = time.time()
    async with AnalyticsSession(cfg) as session:
        async for _ in session.ask("Зроби звіт про прогрес за індикаторами проєкту."):
            pass
        log_text = session.telemetry.path.read_text(encoding="utf-8")

    xlsx = cfg.reports_dir / f"indicators_{date.today().isoformat()}.xlsx"
    assert xlsx.is_file() and xlsx.stat().st_mtime >= started, "indicator report not written"
    assert xlsx.with_suffix(".md").is_file()

    clean = cfg.data_dir / "beneficiaries_clean.xlsx"  # the engine prefers a clean copy
    source = pd.read_excel(clean if clean.is_file() else cfg.data_dir / "beneficiaries.xlsx")
    reached = source["_uuid"].nunique()
    female = source.loc[source["head_sex"] == "female", "_uuid"].nunique()
    summary = {
        row[0]: row
        for row in openpyxl.load_workbook(xlsx)["Summary"].iter_rows(min_row=2, values_only=True)
    }
    assert summary["1.1"][5] == reached
    assert summary["1.2"][5] == pytest.approx(100 * female / reached, abs=0.01)
    assert summary["2.1"][7] == "not_computable"

    assert '"kind": "report_built"' in log_text
    assert "mcp__data__run_analysis" not in log_text, "report numbers must come from the engine"


async def test_reporter_proposes_mapping_and_builds_5w(demo_workspace: Path) -> None:
    import time
    from datetime import date

    import openpyxl

    from haa.reporting.mapping import load_mapping, mapping_path, validate_mapping

    cfg = load_config(demo_workspace)
    mapping_path(cfg.workspace).unlink(missing_ok=True)
    started = time.time()
    async with AnalyticsSession(cfg) as session:
        async for _ in session.ask("Зроби 5W-матрицю за даними beneficiaries."):
            pass
        log_text = session.telemetry.path.read_text(encoding="utf-8")

    mapping = load_mapping(cfg.workspace)
    assert mapping is not None, "reporter did not save a 5W mapping"
    assert validate_mapping(mapping) == []
    assert mapping["whom"]["id_field"] == "_uuid"

    xlsx = cfg.reports_dir / f"5w_{date.today().isoformat()}.xlsx"
    assert xlsx.is_file() and xlsx.stat().st_mtime >= started, "5W report not written"
    workbook = openpyxl.load_workbook(xlsx)
    rows = list(workbook["5W"].iter_rows(values_only=True))
    header, body = list(rows[0]), rows[1:]
    assert set(mapping["where"]) <= set(header)
    beneficiaries = header.index("Beneficiaries")
    assert body and all(row[beneficiaries] > 0 for row in body)
    about = {row[0]: row[1] for row in workbook["About"].iter_rows(values_only=True)}
    assert about["Unique reach (distinct _uuid)"] == 3000
    assert "mcp__data__run_analysis" not in log_text
```

- [ ] **Step 2: Verify the evals are collected and deselected offline**

Run: `PYTEST tests/test_smoke_api.py -m api --collect-only -q`
Expected: the list includes `test_reporter_builds_indicator_report` and `test_reporter_proposes_mapping_and_builds_5w`.
Run: `PYTEST tests/test_smoke_api.py -q`
Expected: all deselected (no API calls). Then `RUFF` — clean.

- [ ] **Step 3: Commit**

```bash
git add tests/test_smoke_api.py
git commit -m "test: api evals for the reporter and the analyst's use of the indicator engine"
```

---

### Task 12: README and full verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the README**

Insert a new section directly after the `## Indicators and forms` section (before `## Architecture`):

```markdown
## Reports

Ask: *"Build the indicator progress report"* — the reporter agent runs a
deterministic engine over every indicator in `workspace/indicators.yaml` that
has a `measure` block and writes `workspace/reports/indicators_<date>.md` and
`.xlsx`: target, actual, % progress, and a breakdown per disaggregation column.
Indicators it cannot compute are listed with the reason — nothing is estimated.

Ask: *"Make a 5W matrix"* — the reporter proposes `workspace/5w.yaml` (which
columns are Where, When, What and Whom) from the dataset profile, then writes
`workspace/reports/5w_<date>.md` and `.xlsx`. Edit `5w.yaml` by hand and rebuild
any time.

Both reports prefer `<dataset>_clean.xlsx` when the cleaner has produced one,
take an optional reporting period, and also run without the LLM (no API key):

    uv run haa report indicators --period 2026-06-01..2026-08-31 --date-field submission_date
    uv run haa report 5w --period 2026-06-01..2026-08-31
```

In `## Roadmap`, replace the two lines

```markdown
**Completed:** Core + analyst agent, connectors/cleaner, indicator registry + XLSForm designer, SharePoint connector.
**Remaining:** reporting agent (5W/MEAL), Power BI engineer, web UI. Design docs live
```

with

```markdown
**Completed:** Core + analyst agent, connectors/cleaner, indicator registry + XLSForm designer, SharePoint connector, deterministic reports (indicator progress, 5W).
**Remaining:** narrative reports (donor narrative, dataset summary as docx), Power BI engineer, web UI. Design docs live
```

- [ ] **Step 2: Full offline suite and lint**

Run: `PYTEST -q`, then `RUFF`
Expected: 414 passed, 14 deselected (293 before this slice + 121 new offline tests; 2 new api evals deselected); ruff clean. Windows note: the sandbox/executor tests very rarely fail with exit code `3221225794` (0xC0000142, a subprocess start flake) — re-run only those files once before treating it as real.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: deterministic reports and roadmap update"
```

- [ ] **Step 4: Manual acceptance (user-run, outside CI)**

In a terminal with `ANTHROPIC_API_KEY` set, from the main checkout after merge:

```
.\.venv\Scripts\python.exe -m pytest -m api -k reporter -v
```

Also run `.\.venv\Scripts\python.exe -m pytest -m api -k indicator_progress -v` (the extended analyst eval). All three must be PASSED, not SKIPPED — this closes spec §7 criterion 5 and the live half of criterion 6.
