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
