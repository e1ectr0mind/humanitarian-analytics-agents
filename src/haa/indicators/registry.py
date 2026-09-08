"""Indicators registry: descriptive base plus an optional machine-readable measure."""

from __future__ import annotations

from pathlib import Path

import yaml

AGGREGATIONS = {"count", "count_unique", "sum", "percent"}
LANGS = ("uk", "en")
REGISTRY_FILE = "indicators.yaml"


class RegistryError(ValueError):
    """Friendly, user-facing registry failure."""


def registry_path(workspace: Path) -> Path:
    return workspace / REGISTRY_FILE


def parse_registry_yaml(text: str) -> dict:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RegistryError(f"Could not parse YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise RegistryError("Registry must be a YAML mapping with an 'indicators' list")
    return data


def _validate_measure(measure: object, where: str, errors: list[str]) -> None:
    if not isinstance(measure, dict):
        errors.append(f"{where}: expected a mapping")
        return
    if not isinstance(measure.get("dataset"), str) or not measure["dataset"]:
        errors.append(f"{where}.dataset: expected a dataset name")
    aggregation = measure.get("aggregation")
    if aggregation not in AGGREGATIONS:
        errors.append(
            f"{where}.aggregation: unknown {aggregation!r}; allowed: "
            f"{', '.join(sorted(AGGREGATIONS))}"
        )
    if aggregation == "percent":
        for part in ("numerator", "denominator"):
            block = measure.get(part)
            if not isinstance(block, dict):
                errors.append(f"{where}.{part}: percent needs a mapping with a 'field'")
                continue
            if not isinstance(block.get("field"), str) or not block["field"]:
                errors.append(f"{where}.{part}.field: expected a column name")
            if block.get("filter") is not None and not isinstance(block["filter"], str):
                errors.append(f"{where}.{part}.filter: expected a query string or null")
    elif aggregation in {"count_unique", "sum"}:
        if not isinstance(measure.get("field"), str) or not measure["field"]:
            errors.append(f"{where}.field: expected a column name for {aggregation!r}")
    if measure.get("filter") is not None and not isinstance(measure["filter"], str):
        errors.append(f"{where}.filter: expected a query string or null")


def validate_registry(data: object) -> list[str]:
    """Return a list of addressed, human-readable errors ([] means valid)."""
    if not isinstance(data, dict):
        return ["registry: expected a mapping with an 'indicators' list"]
    indicators = data.get("indicators")
    if not isinstance(indicators, list):
        return ["indicators: expected a list (may be empty)"]

    errors: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(indicators):
        where = f"indicators[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{where}: expected a mapping")
            continue
        code = item.get("code")
        if not isinstance(code, str) or not code.strip():
            errors.append(f"{where}.code: expected a non-empty string")
        else:
            if code in seen:
                errors.append(f"{where}.code: duplicate indicator code {code!r}")
            seen.add(code)
        name = item.get("name")
        if not isinstance(name, dict):
            errors.append(f"{where}.name: expected a mapping with {LANGS} labels")
        else:
            for lang in LANGS:
                if not isinstance(name.get(lang), str) or not name[lang].strip():
                    errors.append(f"{where}.name: missing or empty '{lang}' label")
        if not isinstance(item.get("definition"), str) or not item["definition"].strip():
            errors.append(f"{where}.definition: expected a non-empty string")
        target = item.get("target")
        if target is not None:
            if not isinstance(target, dict):
                errors.append(f"{where}.target: expected a mapping with 'value' and 'unit'")
            else:
                value = target.get("value")
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    errors.append(f"{where}.target.value: expected a number")
                if not isinstance(target.get("unit"), str) or not target["unit"]:
                    errors.append(f"{where}.target.unit: expected a unit name")
        disaggregation = item.get("disaggregation")
        if disaggregation is not None:
            if not isinstance(disaggregation, list) or not all(
                isinstance(d, str) for d in disaggregation
            ):
                errors.append(f"{where}.disaggregation: expected a list of strings")
        if item.get("source") is not None and not isinstance(item["source"], str):
            errors.append(f"{where}.source: expected a dataset name or null")
        if item.get("measure") is not None:
            _validate_measure(item["measure"], f"{where}.measure", errors)
    return errors


def save_registry(workspace: Path, data: dict) -> Path:
    path = registry_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return path


def load_registry(workspace: Path) -> dict:
    path = registry_path(workspace)
    if not path.is_file():
        return {"indicators": []}
    return parse_registry_yaml(path.read_text(encoding="utf-8"))


def summarize(data: dict) -> dict:
    indicators = data.get("indicators") or []
    return {
        "count": len(indicators),
        "with_target": sum(1 for i in indicators if isinstance(i, dict) and i.get("target")),
        "with_measure": sum(1 for i in indicators if isinstance(i, dict) and i.get("measure")),
    }
