"""The 5W mapping file (workspace/5w.yaml): strict schema validation and storage."""

from __future__ import annotations

from pathlib import Path

import yaml

from haa.reporting.sources import ReportError

MAPPING_FILE = "5w.yaml"
TOP_KEYS = {"dataset", "fixed", "where", "when", "what", "whom"}
WHEN_KEYS = {"field", "granularity"}
WHAT_KEYS = {"field", "split"}
WHOM_KEYS = {"id_field", "disaggregation"}
GRANULARITIES = ("month", "none")
RESERVED_COLUMNS = ("Period", "Activity", "Beneficiaries")


class MappingError(ReportError):
    """The 5W mapping file cannot be parsed."""


def _is_name(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _unknown_keys(block: dict, allowed: set[str], label: str, errors: list[str]) -> None:
    unknown = sorted(str(key) for key in block if key not in allowed)
    if unknown:
        errors.append(
            f"{label}: unknown key(s) {unknown}; supported: {', '.join(sorted(allowed))}"
        )


def _name_list(value: object, label: str, errors: list[str], required: bool) -> list[str]:
    if value is None and not required:
        return []
    if not isinstance(value, list) or (required and not value):
        kind = "a non-empty list" if required else "a list"
        errors.append(f"{label}: expected {kind} of column names")
        return []
    names: list[str] = []
    for index, item in enumerate(value):
        if _is_name(item):
            names.append(item)
        else:
            errors.append(f"{label}[{index}]: expected a column name")
    if len(set(names)) != len(names):
        errors.append(f"{label}: duplicate column names")
    return names


def validate_mapping(data: object) -> list[str]:
    """Return addressed, human-readable errors for a 5w.yaml mapping ([] means valid)."""
    if not isinstance(data, dict):
        return ["5w.yaml: expected a mapping with dataset, where, when, what and whom"]
    errors: list[str] = []
    _unknown_keys(data, TOP_KEYS, "5w.yaml", errors)
    if not _is_name(data.get("dataset")):
        errors.append("dataset: expected a dataset name")
    where = _name_list(data.get("where"), "where", errors, required=True)
    for name in where:
        if name in RESERVED_COLUMNS:
            errors.append(f"where: {name!r} is a reserved output column name")

    when = data.get("when")
    if not isinstance(when, dict):
        errors.append("when: expected a mapping with 'field' and optional 'granularity'")
    else:
        _unknown_keys(when, WHEN_KEYS, "when", errors)
        if not _is_name(when.get("field")):
            errors.append("when.field: expected a date column name")
        granularity = when.get("granularity", "month")
        if not isinstance(granularity, str) or granularity not in GRANULARITIES:
            errors.append(f"when.granularity: unknown {granularity!r}; allowed: month, none")

    what = data.get("what")
    if not isinstance(what, dict):
        errors.append("what: expected a mapping with 'field' and optional 'split'")
    else:
        _unknown_keys(what, WHAT_KEYS, "what", errors)
        if not _is_name(what.get("field")):
            errors.append("what.field: expected the activity/service column name")
        split = what.get("split")
        if split is not None and (not isinstance(split, str) or split == ""):
            errors.append("what.split: expected a separator string such as ' '")

    whom = data.get("whom")
    if not isinstance(whom, dict):
        errors.append("whom: expected a mapping with 'id_field' and optional 'disaggregation'")
    else:
        _unknown_keys(whom, WHOM_KEYS, "whom", errors)
        if not _is_name(whom.get("id_field")):
            errors.append("whom.id_field: expected the unique beneficiary ID column name")
        _name_list(whom.get("disaggregation"), "whom.disaggregation", errors, required=False)

    fixed = data.get("fixed")
    if fixed is not None:
        if not isinstance(fixed, dict):
            errors.append("fixed: expected a mapping of output column to value")
        else:
            taken = set(RESERVED_COLUMNS) | set(where)
            for key, value in fixed.items():
                if not _is_name(key):
                    errors.append(f"fixed: column names must be non-empty strings (got {key!r})")
                elif key in taken:
                    errors.append(f"fixed.{key}: collides with an output column")
                elif value is None or isinstance(value, (dict, list)):
                    errors.append(f"fixed.{key}: expected a single value")
    return errors


def mapping_path(workspace: Path) -> Path:
    return workspace / MAPPING_FILE


def parse_mapping_yaml(text: str) -> dict:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise MappingError(f"Could not parse 5w.yaml: {exc}") from exc
    if not isinstance(data, dict):
        raise MappingError("5w.yaml must be a YAML mapping with dataset, where, when, what, whom")
    return data


def load_mapping(workspace: Path) -> dict | None:
    path = mapping_path(workspace)
    if not path.is_file():
        return None
    return parse_mapping_yaml(path.read_text(encoding="utf-8"))


def save_mapping(workspace: Path, data: dict) -> Path:
    path = mapping_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path
