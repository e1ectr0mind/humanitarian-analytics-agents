import copy
from pathlib import Path

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
    with pytest.raises(NotComputable, match="personal data.*pick another column"):
        evaluate_measure(DF, {"aggregation": "count_unique", "field": "resp_phone"}, PII)


def test_pii_in_filter_refused() -> None:
    measure = {"aggregation": "count", "filter": "resp_phone == '+380 1'"}
    with pytest.raises(NotComputable, match="personal data.*pick another column"):
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


# -- filter allowlist ---------------------------------------------------------
FILTER_DF = pd.DataFrame(
    {
        "_uuid": ["uid-7f3a-01", "uid-7f3a-02", "uid-7f3a-03", "uid-7f3a-04"],
        "head_sex": ["female", "male", "female", None],
        "oblast": ["A", "B", "C", "A"],
        "age": [17, 18, 40, 70],
        "hh type": ["idp", "host", "idp", "host"],
        "resp_phone": ["+380 1", "+380 2", "+380 3", "+380 4"],
        "resp phone": ["+380 1", "+380 2", "+380 3", "+380 4"],
    }
)
FILTER_PII = ["resp_phone", "resp phone"]


def _count(query: str) -> float:
    return evaluate_measure(FILTER_DF, {"aggregation": "count", "filter": query}, FILTER_PII)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("head_sex == 'female'", 2),
        ("age >= 18 and oblast == 'A'", 1),
        ("oblast in ['A', 'B']", 3),
        ("oblast not in ('A',)", 2),
        ("oblast.str.contains('A')", 2),
        ("oblast.str.startswith('B', na=False)", 1),
        ("oblast.str.endswith('C')", 1),
        ("oblast.str.contains('c', case=False, regex=False)", 1),
        ("head_sex.isna()", 1),
        ("not head_sex.notna()", 1),
        ("oblast.isin(['A'])", 2),
        ("`hh type` == 'idp'", 2),
        ("(age > 17) & ~head_sex.isna()", 2),
        ("(age * 2 > 70) | (oblast == 'B')", 3),
        # pandas reads & and | as and/or, so this is (age >= 18) and (oblast == 'A')
        ("age >= 18 & oblast == 'A'", 1),
        ("age > -1", 4),
        ("  age >= 40  ", 2),
    ],
)
def test_allowed_filters_still_work(query: str, expected: int) -> None:
    assert _count(query) == expected


@pytest.mark.parametrize(
    "template",
    [
        "@df.to_csv('{path}')",
        "_uuid.to_csv('{path}')",
        "oblast == 'A' or _uuid.to_csv('{path}')",
        # backticks inside quotes must not hide the call from the validator
        "oblast == '`' or _uuid.to_csv('{path}') or '`'",
    ],
    ids=["engine-local-df", "series-method", "series-method-in-or", "backticks-in-quotes"],
)
def test_filter_cannot_write_files(tmp_path: Path, template: str) -> None:
    target = tmp_path / "leak.csv"
    with pytest.raises(NotComputable):
        _count(template.format(path=target.as_posix()))
    assert not target.exists()


@pytest.mark.parametrize(
    "query",
    [
        "age > @x",
        "age > @pii.__len__()",
        "age @ age",
        "oblast.__class__ == oblast.__class__",
        "oblast.values == 'A'",
        "oblast.str.len() > 1",
        "oblast.str.contains(oblast)",
        "oblast.str.contains('A', flags=2)",
        "oblast.isin(oblast)",
        "oblast.isin([oblast])",
        "Timestamp('2026-01-01') < age",
        "age ** 2 > 4",
        "oblast[0] == 'A'",
        "age if age else age",
        "(oblast == 'A'\n or age > 1)",
        "`oblast` == 'A' and `age`` > 1",
    ],
)
def test_disallowed_filter_syntax_is_refused(query: str) -> None:
    with pytest.raises(NotComputable) as info:
        _count(query)
    assert "Traceback" not in info.value.reason


UUIDS = FILTER_DF["_uuid"].tolist()


@pytest.mark.parametrize(
    "query",
    [
        "_uuid",
        "(_uuid)",
        "_uuid & oblast == 'A'",
        "oblast == 'A' or _uuid",
        "not _uuid",
        "~age",
        "age + 1",
        "'uid'",
        "['A']",
    ],
)
def test_filter_must_be_a_row_condition(query: str) -> None:
    with pytest.raises(NotComputable, match="does not allow") as info:
        _count(query)
    assert not any(value in info.value.reason for value in UUIDS)


def test_filter_evaluation_error_carries_no_cell_values() -> None:
    # passes the allowlist, fails in pandas: .str on a numeric column
    with pytest.raises(NotComputable, match="could not be evaluated.*profile_dataset") as info:
        _count("age.str.contains('9')")
    assert not any(str(value) in info.value.reason for value in FILTER_DF["age"])


def test_filter_evaluation_error_text_is_not_echoed(monkeypatch) -> None:
    def fail(self, *args, **kwargs):
        raise KeyError(f"None of [Index({UUIDS!r})] are in the [index]")

    monkeypatch.setattr(pd.DataFrame, "query", fail)
    with pytest.raises(NotComputable, match="could not be evaluated") as info:
        _count("oblast == 'A'")
    assert not any(value in info.value.reason for value in UUIDS)


def test_refused_filter_names_the_allowed_syntax() -> None:
    with pytest.raises(NotComputable, match="does not allow.*head_sex == 'female'"):
        _count("oblast.str.len() > 1")


def test_filter_names_must_be_dataset_columns() -> None:
    with pytest.raises(NotComputable, match="'sex'.*profile_dataset"):
        _count("sex == 'female'")


def test_filter_cannot_reach_pii_column_through_pandas_cleaned_name() -> None:
    # pandas resolves this identifier to the column "resp phone"
    with pytest.raises(NotComputable):
        _count("BACKTICK_QUOTED_STRING_resp_phone == '+380 1'")


def test_backticked_pii_column_refused() -> None:
    with pytest.raises(NotComputable, match="personal data.*pick another column"):
        _count("`resp phone` == '+380 1'")


def test_pii_column_spelled_with_lookalike_letters_refused() -> None:
    # Python (and so pandas) normalizes fullwidth letters in names to "resp_phone"
    with pytest.raises(NotComputable, match="personal data.*pick another column"):
        _count("ｒｅｓｐ_ｐｈｏｎｅ == '+380 1'")
