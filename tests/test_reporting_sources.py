import os
import time
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from haa.reporting.sources import (
    DatasetNotFound,
    Period,
    ReportError,
    SourceInfo,
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


def test_load_scoped_listable_columns_from_full_dataset(demo_workspace: Path) -> None:
    _, info, pii = load_scoped(demo_workspace / "data", "beneficiaries", None)
    assert "_uuid" not in info.listable_columns
    assert "oblast" in info.listable_columns
    pii_columns = {"resp_name", "resp_phone", "gps_lat", "gps_lon", "enumerator", "comment"}
    assert pii_columns <= set(pii)
    assert not (pii_columns & info.listable_columns)

    period = Period("submission_date", date(2026, 7, 3), date(2026, 7, 3))
    _, narrow_info, _ = load_scoped(demo_workspace / "data", "beneficiaries", period)
    assert narrow_info.listable_columns == info.listable_columns


def test_load_scoped_read_error_text_is_not_echoed(tmp_path: Path, monkeypatch) -> None:
    _write_csv(tmp_path / "hh.csv", {"id": ["a"]})

    def fail(*args, **kwargs):
        raise ValueError("could not convert string to float: '+380 67 123 45 67'")

    monkeypatch.setattr(pd, "read_csv", fail)
    with pytest.raises(ReportError, match="Could not read hh.csv.*valid") as info:
        load_scoped(tmp_path, "hh", None)
    assert "+380" not in str(info.value)


def test_load_scoped_locked_file_keeps_the_os_reason(tmp_path: Path, monkeypatch) -> None:
    _write_csv(tmp_path / "hh.csv", {"id": ["a"]})

    def locked(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(pd, "read_csv", locked)
    with pytest.raises(ReportError, match="Permission denied.*close it"):
        load_scoped(tmp_path, "hh", None)


def test_load_scoped_refuses_pii_period_column(demo_workspace: Path) -> None:
    period = Period("resp_name", date(2026, 6, 1), date(2026, 8, 31))
    with pytest.raises(ReportError, match="'resp_name'.*personal data.*pick another"):
        load_scoped(demo_workspace / "data", "beneficiaries", period)


def test_copy_label() -> None:
    clean = SourceInfo("hh", Path("hh_clean.csv"), True, 1, 1, 0, 0)
    raw = SourceInfo("hh", Path("hh.csv"), False, 1, 1, 0, 0)
    assert clean.copy_label == "clean"
    assert raw.copy_label == "raw"


def test_load_scoped_stale_clean_copy_warns(tmp_path: Path) -> None:
    clean = _write_csv(tmp_path / "hh_clean.csv", {"id": ["a"]})
    raw = _write_csv(tmp_path / "hh.csv", {"id": ["a", "b"]})
    now = time.time()
    os.utime(clean, (now, now))
    os.utime(raw, (now + 86400, now + 86400))
    _, info, _ = load_scoped(tmp_path, "hh", None)
    assert info.used_clean is True
    assert info.raw_updated == date.fromtimestamp(now + 86400)
    assert info.stale_warning is not None
    assert "hh_clean.csv" in info.stale_warning
    assert info.raw_updated.isoformat() in info.stale_warning
    assert "re-run the cleaning" in info.stale_warning


def test_load_scoped_clean_copy_not_stale_when_newer(tmp_path: Path) -> None:
    clean = _write_csv(tmp_path / "hh_clean.csv", {"id": ["a"]})
    raw = _write_csv(tmp_path / "hh.csv", {"id": ["a", "b"]})
    now = time.time()
    os.utime(raw, (now, now))
    os.utime(clean, (now + 86400, now + 86400))
    _, info, _ = load_scoped(tmp_path, "hh", None)
    assert info.raw_updated is None
    assert info.stale_warning is None


def test_load_scoped_no_raw_file_no_stale_warning(tmp_path: Path) -> None:
    _write_csv(tmp_path / "hh_clean.csv", {"id": ["a"]})
    _, info, _ = load_scoped(tmp_path, "hh", None)
    assert info.raw_updated is None
    assert info.stale_warning is None


def test_load_scoped_numeric_period_column_is_a_friendly_error(tmp_path: Path) -> None:
    _write_csv(tmp_path / "hh.csv", {"id": ["a", "b"], "when": [20260601, 20260615]})
    period = Period("when", date(2026, 6, 1), date(2026, 8, 31))
    with pytest.raises(ReportError, match="'when' holds numbers, not dates"):
        load_scoped(tmp_path, "hh", period)


def test_load_scoped_all_empty_numeric_period_column_is_not_an_error(tmp_path: Path) -> None:
    _write_csv(tmp_path / "hh.csv", {"id": ["a", "b"], "when": [None, None]})
    period = Period("when", date(2026, 6, 1), date(2026, 8, 31))
    df, info, _ = load_scoped(tmp_path, "hh", period)
    assert len(df) == 0
    assert info.rows_bad_date == 2


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
