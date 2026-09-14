"""Build the 5W matrix from a validated mapping."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from haa.core.tools.profiler import MAX_CATEGORY_VALUES
from haa.reporting.measures import MISSING, category_keys, ordered_categories
from haa.reporting.sources import ReportError, check_not_numeric_date, parse_dates


@dataclass(frozen=True)
class FiveWTable:
    columns: list[str]
    rows: list[dict]
    id_field: str
    unique_reach: int
    rows_without_activity: int


def _activities(value: object, split: str | None) -> list[str]:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return []
    text = str(value).strip()
    if not text:
        return []
    if split:
        return [part.strip() for part in text.split(split) if part.strip()]
    return [text]


def _plain(value: object) -> object:
    return value.item() if hasattr(value, "item") else value


def _unusable_columns_error(
    pii_columns: list[str], missing_columns: list[str], crowded_columns: list[str]
) -> str:
    reasons = [f"{c!r} (looks like personal data)" for c in pii_columns]
    reasons += [f"{c!r} (not in the dataset)" for c in missing_columns]
    reasons += [f"{c!r} (too many distinct values to disaggregate by)" for c in crowded_columns]
    fixes = []
    if pii_columns:
        fixes.append(
            "pick another column for personal data, or rename it in the clean copy if it "
            "is not personal data"
        )
    if missing_columns:
        fixes.append("check profile_dataset for column names")
    if crowded_columns:
        fixes.append(
            "disaggregate only by low-cardinality columns such as sex or age group "
            f"(at most {MAX_CATEGORY_VALUES} distinct values)"
        )
    return (
        "5w.yaml uses columns a report cannot use: "
        + ", ".join(reasons)
        + " — fix 5w.yaml: "
        + "; ".join(fixes)
        + "."
    )


def _missing_last(series: pd.Series) -> pd.Series:
    """Sort key for 5W key columns: MISSING sorts after every real value."""
    return series.map(lambda value: (value == MISSING, value))


def build_5w(
    df: pd.DataFrame, mapping: dict, pii: list[str], listable: frozenset[str]
) -> FiveWTable:
    """Group rows into 5W lines counting distinct beneficiaries per activity.

    `listable` holds the columns with few enough distinct values in the full dataset
    (`SourceInfo.listable_columns`); only those may be disaggregation dimensions — an
    ID-like dimension would pivot into rows x distinct-values cells and exhaust memory.
    """
    where = list(mapping["where"])
    when = mapping["when"]
    what = mapping["what"]
    whom = mapping["whom"]
    id_field = whom["id_field"]
    dimensions = list(whom.get("disaggregation") or [])
    fixed = dict(mapping.get("fixed") or {})

    pii_columns: list[str] = []
    missing_columns: list[str] = []
    for column in [*where, when["field"], what["field"], id_field, *dimensions]:
        if column in pii:
            if column not in pii_columns:
                pii_columns.append(column)
        elif column not in df.columns and column not in missing_columns:
            missing_columns.append(column)
    crowded_columns: list[str] = []
    for column in dimensions:
        usable = column not in pii_columns and column not in missing_columns
        if usable and column not in listable and column not in crowded_columns:
            crowded_columns.append(column)
    if pii_columns or missing_columns or crowded_columns:
        raise ReportError(
            _unusable_columns_error(pii_columns, missing_columns, crowded_columns)
        )

    work = pd.DataFrame(index=df.index)
    for column in where:
        work[column] = category_keys(df[column])
    keys = list(where)
    if when.get("granularity", "month") == "month":
        check_not_numeric_date(df[when["field"]], when["field"])
        dates = parse_dates(df[when["field"]])
        work["Period"] = category_keys(dates.dt.strftime("%Y-%m"))
        keys.append("Period")
    split = what.get("split")
    work["Activity"] = pd.Series(
        [_activities(value, split) for value in df[what["field"]].tolist()],
        index=df.index,
        dtype=object,
    )
    keys.append("Activity")
    work["__id"] = df[id_field]
    for dimension in dimensions:
        work[f"__dim_{dimension}"] = category_keys(df[dimension])

    rows_without_activity = int((work["Activity"].map(len) == 0).sum())
    exploded = work.explode("Activity")
    exploded = exploded[exploded["Activity"].notna()]

    base_columns = [*fixed, *keys, "Beneficiaries"]
    if exploded.empty:
        return FiveWTable(
            columns=base_columns,
            rows=[],
            id_field=id_field,
            unique_reach=0,
            rows_without_activity=rows_without_activity,
        )

    table = (
        exploded.groupby(keys, sort=True)["__id"].nunique().rename("Beneficiaries").reset_index()
    )
    table = table.sort_values(by=keys, key=_missing_last, kind="stable", ignore_index=True)
    extra_columns: list[str] = []
    for dimension in dimensions:
        column = f"__dim_{dimension}"
        categories = ordered_categories(exploded[column])
        counts = (
            exploded.groupby([*keys, column], sort=True)["__id"]
            .nunique()
            .unstack(column, fill_value=0)
            .reindex(columns=categories, fill_value=0)
        )
        labels = [f"{dimension}={category}" for category in categories]
        counts.columns = labels
        table = table.merge(counts.reset_index(), on=keys, how="left")
        extra_columns.extend(labels)
    for position, (name, value) in enumerate(fixed.items()):
        table.insert(position, name, value)

    columns = [*base_columns, *extra_columns]
    rows = [
        {column: _plain(record[column]) for column in columns}
        for record in table[columns].to_dict(orient="records")
    ]
    return FiveWTable(
        columns=columns,
        rows=rows,
        id_field=id_field,
        unique_reach=int(exploded["__id"].nunique()),
        rows_without_activity=rows_without_activity,
    )
