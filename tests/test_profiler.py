import json
from pathlib import Path

import pandas as pd

from haa.core.tools.profiler import (
    MAX_CATEGORY_VALUES,
    detect_pii_columns,
    discover_datasets,
    listable_columns,
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


def test_listable_columns_demo(demo_workspace: Path) -> None:
    df = _df(demo_workspace)
    pii = detect_pii_columns(df)
    listable = listable_columns(df, pii)
    assert "oblast" in listable
    assert "_uuid" not in listable
    assert not (set(pii) & listable)


def test_listable_columns_boundary_is_inclusive() -> None:
    n = MAX_CATEGORY_VALUES + 1
    df = pd.DataFrame({"a": [*range(MAX_CATEGORY_VALUES), 0], "b": range(n)})
    listable = listable_columns(df, [])
    assert "a" in listable
    assert "b" not in listable
