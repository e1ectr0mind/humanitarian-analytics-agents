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
    with pytest.raises(ReportError, match="personal data.*pick another column"):
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
