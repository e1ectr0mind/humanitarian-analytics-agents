"""Evaluate one indicator `measure` block against a data frame."""

from __future__ import annotations

import re

import pandas as pd

MISSING = "(missing)"


class NotComputable(Exception):
    """An indicator, or one breakdown category, cannot be computed; carries the reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def category_keys(series: pd.Series) -> pd.Series:
    """Stringify category values; missing values become MISSING."""
    return series.astype(str).mask(series.isna(), MISSING)


def ordered_categories(keys: pd.Series) -> list[str]:
    """Categories sorted by name, with MISSING last."""
    present = set(keys.unique())
    ordered = sorted(c for c in present if c != MISSING)
    if MISSING in present:
        ordered.append(MISSING)
    return ordered


def check_column(df: pd.DataFrame, column: object, pii: list[str]) -> str:
    if not isinstance(column, str) or not column:
        raise NotComputable("the measure does not name a column")
    if column in pii:
        raise NotComputable(
            f"column {column!r} is personal data — aggregating by it is not allowed"
        )
    if column not in df.columns:
        raise NotComputable(
            f"column {column!r} not found in the dataset — check profile_dataset"
        )
    return column


def _apply_filter(df: pd.DataFrame, query: object, pii: list[str]) -> pd.DataFrame:
    if query is None or query == "":
        return df
    text = str(query)
    for column in pii:
        if re.search(rf"(?<!\w){re.escape(str(column))}(?!\w)", text):
            raise NotComputable(
                f"filter {text!r} uses column {column!r}, which is personal data — not allowed"
            )
    try:
        return df.query(text)
    except Exception as exc:
        detail = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        raise NotComputable(f"filter {text!r} failed: {detail}") from exc


def _distinct(df: pd.DataFrame, column: str) -> int:
    return int(df[column].dropna().nunique())


def evaluate_measure(df: pd.DataFrame, measure: dict, pii: list[str]) -> float:
    aggregation = measure.get("aggregation")
    if aggregation == "percent":
        parts: list[int] = []
        for key in ("numerator", "denominator"):
            spec = measure.get(key)
            if not isinstance(spec, dict):
                raise NotComputable(f"percent needs a {key} block")
            column = check_column(df, spec.get("field"), pii)
            parts.append(_distinct(_apply_filter(df, spec.get("filter"), pii), column))
        numerator, denominator = parts
        if denominator == 0:
            raise NotComputable("the denominator is 0 — nothing to divide by")
        return 100.0 * numerator / denominator
    column = measure.get("field")
    if column is not None:
        column = check_column(df, column, pii)
    scoped = _apply_filter(df, measure.get("filter"), pii)
    if aggregation == "count":
        return float(scoped[column].notna().sum()) if column else float(len(scoped))
    if aggregation not in ("count_unique", "sum"):
        raise NotComputable(f"unknown aggregation {aggregation!r}")
    if not column:
        raise NotComputable(f"{aggregation} needs a field")
    if aggregation == "count_unique":
        return float(_distinct(scoped, column))
    values = scoped[column]
    numeric = pd.to_numeric(values, errors="coerce")
    non_numeric = int(numeric.isna().sum() - values.isna().sum())
    if non_numeric:
        raise NotComputable(
            f"column {column!r} has {non_numeric} non-numeric values — cannot sum"
        )
    return float(numeric.sum())
