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
    # Specify dtype for resp_phone to preserve "+" prefix (pandas infers it as int64 otherwise)
    return pd.read_excel(ws / "data" / "beneficiaries.xlsx", dtype={"resp_phone": str})


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
