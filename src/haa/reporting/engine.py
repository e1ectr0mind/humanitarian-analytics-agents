"""Registry-level indicator evaluation: result model, breakdowns, dataset scoping."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from haa.indicators.registry import validate_registry
from haa.reporting.measures import (
    NotComputable,
    category_keys,
    check_column,
    evaluate_measure,
    ordered_categories,
)
from haa.reporting.sources import DatasetNotFound, Period, ReportError, SourceInfo, load_scoped


@dataclass(frozen=True)
class BreakdownRow:
    category: str
    value: float | None
    reason: str | None = None


@dataclass(frozen=True)
class IndicatorResult:
    code: str
    name: dict
    dataset: str | None
    target: float | None
    unit: str | None
    actual: float | None
    progress_pct: float | None
    status: str  # "computed" | "not_computable"
    reason: str | None = None
    breakdowns: dict[str, list[BreakdownRow]] = field(default_factory=dict)
    breakdown_errors: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ReportRun:
    period: Period | None
    sources: dict[str, SourceInfo]
    results: list[IndicatorResult]

    @property
    def computed(self) -> list[IndicatorResult]:
        return [r for r in self.results if r.status == "computed"]

    @property
    def not_computable(self) -> list[IndicatorResult]:
        return [r for r in self.results if r.status == "not_computable"]


def _breakdown(
    df: pd.DataFrame, measure: dict, dimension: str, pii: list[str]
) -> list[BreakdownRow]:
    column = check_column(df, dimension, pii)
    keys = category_keys(df[column])
    rows: list[BreakdownRow] = []
    for category in ordered_categories(keys):
        subset = df.loc[keys == category]
        try:
            rows.append(BreakdownRow(category, evaluate_measure(subset, measure, pii)))
        except NotComputable as exc:
            rows.append(BreakdownRow(category, None, exc.reason))
    return rows


def _target(item: dict) -> tuple[float | None, str | None]:
    target = item.get("target") or {}
    value = target.get("value")
    return (float(value) if value is not None else None), target.get("unit")


def _progress(actual: float, target: float | None) -> float | None:
    if target is None or target == 0:
        return None
    return round(100.0 * actual / target, 1)


def _not_computable(item: dict, reason: str, dataset: str | None) -> IndicatorResult:
    target, unit = _target(item)
    return IndicatorResult(
        code=str(item.get("code")),
        name=dict(item.get("name") or {}),
        dataset=dataset,
        target=target,
        unit=unit,
        actual=None,
        progress_pct=None,
        status="not_computable",
        reason=reason,
    )


def evaluate_indicator(
    item: dict, df: pd.DataFrame, pii: list[str], dataset: str
) -> IndicatorResult:
    measure = item["measure"]
    try:
        actual = evaluate_measure(df, measure, pii)
    except NotComputable as exc:
        return _not_computable(item, exc.reason, dataset)
    breakdowns: dict[str, list[BreakdownRow]] = {}
    breakdown_errors: dict[str, str] = {}
    for dimension in item.get("disaggregation") or []:
        try:
            breakdowns[dimension] = _breakdown(df, measure, dimension, pii)
        except NotComputable as exc:
            breakdown_errors[dimension] = exc.reason
    target, unit = _target(item)
    return IndicatorResult(
        code=str(item["code"]),
        name=dict(item.get("name") or {}),
        dataset=dataset,
        target=target,
        unit=unit,
        actual=actual,
        progress_pct=_progress(actual, target),
        status="computed",
        breakdowns=breakdowns,
        breakdown_errors=breakdown_errors,
    )


def evaluate_registry(registry: dict, data_dir: Path, period: Period | None) -> ReportRun:
    errors = validate_registry(registry)
    if errors:
        raise ReportError(
            "indicators.yaml is invalid — fix it or ask the designer:\n"
            + "\n".join(f"- {e}" for e in errors)
        )
    indicators = registry.get("indicators") or []
    if not indicators:
        raise ReportError(
            "The indicators registry is empty — ask to extract indicators from the "
            "project logframe first."
        )
    loaded: dict[str, tuple[pd.DataFrame, SourceInfo, list[str]] | str] = {}
    results: list[IndicatorResult] = []
    for item in indicators:
        measure = item.get("measure")
        if not measure:
            results.append(
                _not_computable(
                    item, "no measure block — add one to the registry (designer)", None
                )
            )
            continue
        name = measure["dataset"]
        if name not in loaded:
            try:
                loaded[name] = load_scoped(data_dir, name, period)
            except DatasetNotFound as exc:
                loaded[name] = str(exc)
        entry = loaded[name]
        if isinstance(entry, str):
            results.append(_not_computable(item, entry, name))
            continue
        df, _, pii = entry
        results.append(evaluate_indicator(item, df, pii, name))
    sources = {
        name: entry[1] for name, entry in loaded.items() if not isinstance(entry, str)
    }
    return ReportRun(period=period, sources=sources, results=results)
