"""Build the 5W matrix from a validated mapping."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from haa.reporting.measures import category_keys, ordered_categories
from haa.reporting.sources import ReportError, parse_dates


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


def build_5w(df: pd.DataFrame, mapping: dict, pii: list[str]) -> FiveWTable:
    """Group rows into 5W lines counting distinct beneficiaries per activity."""
    where = list(mapping["where"])
    when = mapping["when"]
    what = mapping["what"]
    whom = mapping["whom"]
    id_field = whom["id_field"]
    dimensions = list(whom.get("disaggregation") or [])
    fixed = dict(mapping.get("fixed") or {})

    for column in [*where, when["field"], what["field"], id_field, *dimensions]:
        if column in pii:
            raise ReportError(f"column {column!r} is personal data — it cannot be used in a 5W")
        if column not in df.columns:
            raise ReportError(
                f"column {column!r} from 5w.yaml not found in the dataset — check profile_dataset"
            )

    work = pd.DataFrame(index=df.index)
    for column in where:
        work[column] = category_keys(df[column])
    keys = list(where)
    if when.get("granularity", "month") == "month":
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
