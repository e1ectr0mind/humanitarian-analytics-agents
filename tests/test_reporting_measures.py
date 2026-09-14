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
