import copy
from pathlib import Path

import pytest

from haa.indicators.registry import (
    RegistryError,
    load_registry,
    parse_registry_yaml,
    save_registry,
    summarize,
    validate_registry,
)

VALID = {
    "indicators": [
        {
            "code": "1.1",
            "name": {"uk": "К-сть домогосподарств", "en": "# of households reached"},
            "definition": "Unique households with at least one service (by _uuid)",
            "target": {"value": 2500, "unit": "households"},
            "disaggregation": ["sex", "oblast"],
            "source": "beneficiaries",
            "measure": {
                "dataset": "beneficiaries",
                "aggregation": "count_unique",
                "field": "_uuid",
                "filter": None,
            },
        },
        {
            "code": "1.2",
            "name": {"uk": "Частка жінок-голів", "en": "% female-headed"},
            "definition": "Share of reached households headed by a woman",
            "target": {"value": 55, "unit": "percent"},
            "measure": {
                "dataset": "beneficiaries",
                "aggregation": "percent",
                "numerator": {"field": "_uuid", "filter": "head_sex == 'female'"},
                "denominator": {"field": "_uuid", "filter": None},
            },
        },
    ]
}


@pytest.fixture()
def data() -> dict:
    return copy.deepcopy(VALID)


def test_valid(data: dict) -> None:
    assert validate_registry(data) == []


def test_missing_indicators_key() -> None:
    assert any("indicators" in e for e in validate_registry({}))


def test_empty_list_is_valid() -> None:
    assert validate_registry({"indicators": []}) == []


def test_duplicate_codes(data: dict) -> None:
    data["indicators"][1]["code"] = "1.1"
    assert any("1.1" in e and "duplicate" in e.lower() for e in validate_registry(data))


def test_name_needs_both_languages(data: dict) -> None:
    del data["indicators"][0]["name"]["uk"]
    assert any("uk" in e for e in validate_registry(data))


def test_target_value_must_be_number(data: dict) -> None:
    data["indicators"][0]["target"]["value"] = "many"
    assert any("target.value" in e for e in validate_registry(data))


def test_unknown_aggregation(data: dict) -> None:
    data["indicators"][0]["measure"]["aggregation"] = "median"
    assert any("median" in e for e in validate_registry(data))


def test_percent_needs_numerator_and_denominator(data: dict) -> None:
    del data["indicators"][1]["measure"]["denominator"]
    assert any("denominator" in e for e in validate_registry(data))


def test_measure_is_optional(data: dict) -> None:
    del data["indicators"][0]["measure"]
    assert validate_registry(data) == []


def test_disaggregation_must_be_strings(data: dict) -> None:
    data["indicators"][0]["disaggregation"] = ["sex", 5]
    assert any("disaggregation" in e for e in validate_registry(data))


def test_round_trip(tmp_path: Path, data: dict) -> None:
    save_registry(tmp_path, data)
    assert load_registry(tmp_path) == data
    assert "К-сть" in (tmp_path / "indicators.yaml").read_text(encoding="utf-8")


def test_load_missing_returns_empty(tmp_path: Path) -> None:
    assert load_registry(tmp_path) == {"indicators": []}


def test_parse_bad_yaml() -> None:
    with pytest.raises(RegistryError):
        parse_registry_yaml("indicators: [\n")


def test_summarize(data: dict) -> None:
    assert summarize(data) == {"count": 2, "with_target": 2, "with_measure": 2}
