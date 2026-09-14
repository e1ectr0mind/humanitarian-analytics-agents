import copy
from pathlib import Path

import pandas as pd
import pytest

from haa.core.tools.profiler import MAX_CATEGORY_VALUES, listable_columns
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
LISTABLE = listable_columns(DF, PII)
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
    table = build_5w(DF, MAPPING, PII, LISTABLE)
    assert table.columns == [
        "Organization", "oblast", "Period", "Activity", "Beneficiaries",
        "head_sex=female", "head_sex=male", f"head_sex={MISSING}",
    ]


def test_rows_count_distinct_beneficiaries() -> None:
    rows = _by_key(build_5w(DF, MAPPING, PII, LISTABLE))
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
    table = build_5w(DF, MAPPING, PII, LISTABLE)
    assert sum(r["Beneficiaries"] for r in table.rows) == 5
    assert table.unique_reach == 4
    assert table.rows_without_activity == 1
    assert table.id_field == "_uuid"


def test_without_split_the_activity_is_the_whole_value() -> None:
    mapping = copy.deepcopy(MAPPING)
    del mapping["what"]["split"]
    activities = {r["Activity"] for r in build_5w(DF, mapping, PII, LISTABLE).rows}
    assert "cash health" in activities and "health" in activities


def test_granularity_none_drops_period() -> None:
    mapping = copy.deepcopy(MAPPING)
    mapping["when"]["granularity"] = "none"
    table = build_5w(DF, mapping, PII, LISTABLE)
    assert "Period" not in table.columns
    assert len(table.rows) == 4


def test_when_field_numeric_is_a_friendly_error() -> None:
    df = DF.copy()
    df["submission_date"] = [20260603, 20260620, 20260701, 20260702, 20260715]
    with pytest.raises(ReportError, match="'submission_date' holds numbers, not dates"):
        build_5w(df, MAPPING, PII, listable_columns(df, PII))


def test_missing_column_raises() -> None:
    mapping = copy.deepcopy(MAPPING)
    mapping["where"] = ["raion"]
    with pytest.raises(ReportError, match="'raion'.*profile_dataset"):
        build_5w(DF, mapping, PII, LISTABLE)


def test_pii_column_refused() -> None:
    mapping = copy.deepcopy(MAPPING)
    mapping["whom"]["disaggregation"] = ["resp_phone"]
    with pytest.raises(ReportError, match="personal data.*pick another column"):
        build_5w(DF, mapping, PII, LISTABLE)


def test_multiple_bad_columns_raise_one_error() -> None:
    mapping = copy.deepcopy(MAPPING)
    mapping["where"] = ["foo"]
    mapping["whom"]["disaggregation"] = ["resp_phone"]
    with pytest.raises(ReportError) as excinfo:
        build_5w(DF, mapping, PII, LISTABLE)
    message = str(excinfo.value)
    assert "'foo'" in message
    assert "'resp_phone'" in message
    assert "personal data" in message and "pick another column" in message
    assert message.index("personal data") < message.index("pick another column")
    assert "profile_dataset" in message


def _many_ids_frame() -> pd.DataFrame:
    """A frame whose _uuid has one value more than a listable column may have."""
    n = MAX_CATEGORY_VALUES + 1
    return pd.DataFrame(
        {
            "_uuid": [f"hh-{i:03d}" for i in range(n)],
            "oblast": ["X"] * n,
            "submission_date": ["2026-06-03"] * n,
            "services_received": ["cash"] * n,
            "head_sex": ["female", "male"] * (n // 2) + ["female"] * (n % 2),
            "resp_phone": [f"+380 {i}" for i in range(n)],
        }
    )


def test_disaggregation_by_a_column_with_too_many_values_is_refused() -> None:
    df = _many_ids_frame()
    mapping = copy.deepcopy(MAPPING)
    mapping["whom"]["disaggregation"] = ["head_sex", "_uuid"]
    listable = listable_columns(df, PII)
    assert "head_sex" in listable and "_uuid" not in listable
    with pytest.raises(ReportError) as excinfo:
        build_5w(df, mapping, PII, listable)
    message = str(excinfo.value)
    assert message == (
        "5w.yaml uses columns a report cannot use: '_uuid' (too many distinct values to "
        "disaggregate by) — fix 5w.yaml: disaggregate only by low-cardinality columns such "
        "as sex or age group (at most 30 distinct values)."
    )


def test_too_many_values_joins_the_combined_unusable_columns_error() -> None:
    df = _many_ids_frame()
    mapping = copy.deepcopy(MAPPING)
    mapping["where"] = ["raion"]
    mapping["whom"]["disaggregation"] = ["resp_phone", "_uuid"]
    with pytest.raises(ReportError) as excinfo:
        build_5w(df, mapping, PII, listable_columns(df, PII))
    message = str(excinfo.value)
    assert message == (
        "5w.yaml uses columns a report cannot use: 'resp_phone' (looks like personal data), "
        "'raion' (not in the dataset), '_uuid' (too many distinct values to disaggregate by) "
        "— fix 5w.yaml: pick another column for personal data, or rename it in the clean "
        "copy if it is not personal data; check profile_dataset for column names; "
        "disaggregate only by low-cardinality columns such as sex or age group (at most 30 "
        "distinct values)."
    )
    assert "hh-0" not in message and "+380" not in message


def test_missing_sorts_last_for_where_and_period() -> None:
    df = pd.DataFrame(
        {
            "_uuid": ["a", "b", "c", "d", "e"],
            "oblast": ["B", "A", None, "A", "B"],
            "submission_date": [
                "2026-07-01", "2026-06-01", "2026-06-01", "bad-date", "2026-06-01",
            ],
            "services_received": ["cash"] * 5,
            "head_sex": ["female"] * 5,
            "resp_phone": ["1", "2", "3", "4", "5"],
        }
    )
    mapping = copy.deepcopy(MAPPING)
    del mapping["whom"]["disaggregation"]
    table = build_5w(df, mapping, PII, listable_columns(df, PII))
    assert [(r["oblast"], r["Period"]) for r in table.rows] == [
        ("A", "2026-06"),
        ("A", MISSING),
        ("B", "2026-06"),
        ("B", "2026-07"),
        (MISSING, "2026-06"),
    ]


def test_empty_frame() -> None:
    table = build_5w(DF.iloc[0:0], MAPPING, PII, LISTABLE)
    assert table.rows == [] and table.unique_reach == 0
    assert table.columns == ["Organization", "oblast", "Period", "Activity", "Beneficiaries"]


def test_row_values_are_plain_python() -> None:
    row = build_5w(DF, MAPPING, PII, LISTABLE).rows[0]
    assert type(row["Beneficiaries"]) is int
    assert type(row["oblast"]) is str


def test_demo_unique_reach_matches_independent_pandas(demo_workspace: Path) -> None:
    df, source, pii = load_scoped(demo_workspace / "data", "beneficiaries", None)
    mapping = copy.deepcopy(MAPPING)
    mapping["dataset"] = "beneficiaries"
    mapping["where"] = ["oblast", "raion"]
    table = build_5w(df, mapping, pii, source.listable_columns)
    assert table.unique_reach == df["_uuid"].nunique() == 3000
    assert table.rows and all(r["Beneficiaries"] > 0 for r in table.rows)
    assert table.rows_without_activity == 0
