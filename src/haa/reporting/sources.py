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
        if period.field in pii:
            raise ReportError(
                f"Period column {period.field!r} looks like personal data, so reports cannot "
                "filter by it — pick another date column, or rename it in the clean copy if "
                "it is not personal data."
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
