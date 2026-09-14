"""Dataset resolution for reports: clean-copy preference, PII detection, period scoping."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from haa.core.tools.profiler import detect_pii_columns, discover_datasets, listable_columns


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
    listable_columns: frozenset[str] = field(default_factory=frozenset)
    raw_updated: date | None = None

    @property
    def copy_label(self) -> str:
        return "clean" if self.used_clean else "raw"

    @property
    def stale_warning(self) -> str | None:
        if self.raw_updated is None:
            return None
        return (
            f"The clean copy {self.path.name} is older than the raw file (updated "
            f"{self.raw_updated.isoformat()}) — records added since the last cleaning "
            "are not in this report; re-run the cleaning to include them."
        )


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


def _is_date_like(value: object) -> bool:
    # datetime and pd.Timestamp are subclasses of date
    return isinstance(value, (str, date, np.datetime64))


def parse_dates(series: pd.Series) -> pd.Series:
    """Parse mixed date/datetime values; unparseable ones become NaT.

    In a text (object) column only strings and date/datetime values count: a number or
    bool there (e.g. an unformatted Excel serial like 46174) would otherwise parse as a
    1970 timestamp, so it becomes NaT — a row without a valid date. Numeric columns are
    refused before parsing (check_not_numeric_date).

    Values carrying a UTC offset (Kobo timestamps) are compared in UTC, so a
    submission shortly after local midnight can fall on the previous date.
    """
    if series.dtype == object:
        series = series.where(series.map(_is_date_like).astype(bool))
    parsed = pd.to_datetime(series, errors="coerce", format="mixed", utc=True)
    return parsed.dt.tz_convert(None)


def check_not_numeric_date(series: pd.Series, name: str) -> None:
    """Refuse a numeric 'date' column with a friendly error.

    A numeric dtype (including bool) parses under pd.to_datetime as a nonsense epoch
    date rather than becoming NaT, so it must be checked before parsing. A column that
    is entirely empty is not an error — its rows simply have no valid date.
    """
    if pd.api.types.is_numeric_dtype(series) and series.notna().any():
        raise ReportError(
            f"Column {name!r} holds numbers, not dates — pick a date column "
            "(profile_dataset shows each column's type)."
        )


def resolve_dataset(data_dir: Path, name: str) -> tuple[Path, bool]:
    found = discover_datasets(data_dir)
    clean = found.get(f"{name}_clean")
    if clean is not None:
        return clean, True
    if name in found:
        return found[name], name.endswith("_clean")
    known = ", ".join(sorted(found)) or "(none)"
    raise DatasetNotFound(f"Dataset {name!r} not found in workspace/data/. Available: {known}")


def _stale_raw_date(data_dir: Path, name: str, clean_path: Path) -> date | None:
    """Local-time modification date of the raw file for `name`, when the clean copy in
    use is older than it; None when there is no such raw file, it is not newer, or its
    modification time cannot be read (e.g. it vanished mid-check).

    `name` may itself end with `_clean` (a dataset requested directly by its clean
    name) — the raw counterpart is `name` with that suffix stripped, not `name` itself.
    """
    raw_name = name.removesuffix("_clean")
    raw = discover_datasets(data_dir).get(raw_name)
    if raw is None or raw == clean_path:
        return None
    try:
        raw_mtime = raw.stat().st_mtime
        if raw_mtime <= clean_path.stat().st_mtime:
            return None
    except OSError:
        return None
    return datetime.fromtimestamp(raw_mtime).date()


def load_scoped(
    data_dir: Path, name: str, period: Period | None
) -> tuple[pd.DataFrame, SourceInfo, list[str]]:
    path, used_clean = resolve_dataset(data_dir, name)
    raw_updated = _stale_raw_date(data_dir, name, path) if used_clean else None
    try:
        df = pd.read_csv(path) if path.suffix.lower() == ".csv" else pd.read_excel(path)
    except OSError as exc:
        reason = exc.strerror or "the file could not be opened"
        raise ReportError(
            f"Could not read {path.name}: {reason}. If it is open in Excel, close it and try again."
        ) from exc
    except Exception as exc:
        # parser error text can quote cell values, so it is never passed on
        raise ReportError(
            f"Could not read {path.name} as a table — open it in Excel, check that it is a "
            "valid .xlsx or .csv file, save it and try again."
        ) from exc
    pii = detect_pii_columns(df)
    total = len(df)
    listable = listable_columns(df, pii)
    bad_dates = 0
    if period is not None:
        if period.field not in df.columns:
            columns = ", ".join(str(c) for c in df.columns)
            raise ReportError(
                f"Period column {period.field!r} not found in {path.name}. Columns: {columns}"
            )
        if period.field in pii:
            raise ReportError(
                f"Period column {period.field!r} looks like personal data, so reports cannot "
                "filter by it — pick another date column, or rename it in the clean copy if "
                "it is not personal data."
            )
        check_not_numeric_date(df[period.field], period.field)
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
        listable_columns=listable,
        raw_updated=raw_updated,
    )
    return df, info, pii
