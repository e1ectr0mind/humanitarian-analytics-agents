import copy
from pathlib import Path

import pytest

from haa.reporting.mapping import (
    MappingError,
    load_mapping,
    mapping_path,
    parse_mapping_yaml,
    save_mapping,
    validate_mapping,
)
from haa.reporting.sources import ReportError

VALID_MAPPING = {
    "dataset": "beneficiaries",
    "fixed": {"Organization": "IMC", "Project": "ABC-123"},
    "where": ["oblast", "raion", "hromada"],
    "when": {"field": "submission_date", "granularity": "month"},
    "what": {"field": "services_received", "split": " "},
    "whom": {"id_field": "_uuid", "disaggregation": ["head_sex"]},
}


@pytest.fixture()
def mapping() -> dict:
    return copy.deepcopy(VALID_MAPPING)


def test_valid(mapping: dict) -> None:
    assert validate_mapping(mapping) == []


def test_minimal_valid() -> None:
    minimal = {
        "dataset": "beneficiaries",
        "where": ["oblast"],
        "when": {"field": "submission_date"},
        "what": {"field": "services_received"},
        "whom": {"id_field": "_uuid"},
    }
    assert validate_mapping(minimal) == []


def test_not_a_mapping() -> None:
    assert validate_mapping(["oblast"]) != []


def test_unknown_top_key(mapping: dict) -> None:
    mapping["sector"] = "health"
    assert any("sector" in e and "unknown" in e for e in validate_mapping(mapping))


def test_unknown_nested_key(mapping: dict) -> None:
    mapping["when"]["format"] = "%Y"
    assert any(e.startswith("when:") and "format" in e for e in validate_mapping(mapping))


def test_non_string_unknown_keys_do_not_raise(mapping: dict) -> None:
    mapping[1] = "x"
    assert any("unknown" in e for e in validate_mapping(mapping))


def test_dataset_required(mapping: dict) -> None:
    del mapping["dataset"]
    assert any(e.startswith("dataset:") for e in validate_mapping(mapping))


def test_where_must_be_non_empty(mapping: dict) -> None:
    mapping["where"] = []
    assert any(e.startswith("where:") for e in validate_mapping(mapping))


def test_where_items_are_column_names(mapping: dict) -> None:
    mapping["where"] = ["oblast", 7]
    assert "where[1]: expected a column name" in validate_mapping(mapping)


def test_where_duplicates(mapping: dict) -> None:
    mapping["where"] = ["oblast", "oblast"]
    assert any("duplicate" in e for e in validate_mapping(mapping))


def test_where_reserved_name(mapping: dict) -> None:
    mapping["where"] = ["Activity"]
    assert any("reserved" in e for e in validate_mapping(mapping))


def test_bad_granularity(mapping: dict) -> None:
    mapping["when"]["granularity"] = "week"
    assert any("week" in e for e in validate_mapping(mapping))


def test_unhashable_granularity_does_not_raise(mapping: dict) -> None:
    mapping["when"]["granularity"] = ["month"]
    assert any("granularity" in e for e in validate_mapping(mapping))


def test_what_field_required(mapping: dict) -> None:
    del mapping["what"]["field"]
    assert any(e.startswith("what.field") for e in validate_mapping(mapping))


def test_empty_split_rejected(mapping: dict) -> None:
    mapping["what"]["split"] = ""
    assert any(e.startswith("what.split") for e in validate_mapping(mapping))


def test_whom_id_field_required(mapping: dict) -> None:
    del mapping["whom"]["id_field"]
    assert any(e.startswith("whom.id_field") for e in validate_mapping(mapping))


def test_whom_disaggregation_must_be_names(mapping: dict) -> None:
    mapping["whom"]["disaggregation"] = "head_sex"
    assert any(e.startswith("whom.disaggregation") for e in validate_mapping(mapping))


def test_fixed_collides_with_output_column(mapping: dict) -> None:
    mapping["fixed"]["Beneficiaries"] = "x"
    mapping["fixed"]["oblast"] = "y"
    errors = validate_mapping(mapping)
    assert "fixed.Beneficiaries: collides with an output column" in errors
    assert "fixed.oblast: collides with an output column" in errors


def test_fixed_value_must_be_scalar(mapping: dict) -> None:
    mapping["fixed"]["Donor"] = ["ECHO"]
    assert "fixed.Donor: expected a single value" in validate_mapping(mapping)


def test_storage_round_trip(tmp_path: Path, mapping: dict) -> None:
    mapping["fixed"]["Organization"] = "МКМ"
    save_mapping(tmp_path, mapping)
    assert load_mapping(tmp_path) == mapping
    assert "МКМ" in mapping_path(tmp_path).read_text(encoding="utf-8")


def test_load_missing_returns_none(tmp_path: Path) -> None:
    assert load_mapping(tmp_path) is None


def test_parse_bad_yaml() -> None:
    with pytest.raises(MappingError, match="parse"):
        parse_mapping_yaml("where: [\n")


def test_parse_non_mapping() -> None:
    with pytest.raises(MappingError, match="mapping"):
        parse_mapping_yaml("- oblast\n")


def test_mapping_error_is_report_error() -> None:
    assert issubclass(MappingError, ReportError)
