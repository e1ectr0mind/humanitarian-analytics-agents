"""Dataset discovery and safe profiling: schema and stats only, never raw rows."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

TABULAR_SUFFIXES = {".csv", ".xlsx", ".xls"}
MAX_CATEGORY_VALUES = 30

PII_NAME_RE = re.compile(
    r"name|імʼя|ім'я|имя|прізвище|фамил|phone|тел|gps|lat|lon|coord|address|"
    r"адрес|email|comment|коммент|enumerator|інтерв",
    re.IGNORECASE,
)
PHONE_RE = re.compile(
    r"\+?38\s?0\d{2}[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}|"
    r"\+380[\s-]\d{2}[\s-]\d{3}[\s-]\d{2}[\s-]\d{2}|"
    r"\b0\d{9}\b|\+\d{11,14}"
)
GPS_PAIR_RE = re.compile(r"\b\d{2}\.\d{4,}\s*,\s*\d{2}\.\d{4,}\b")

_VALUE_SAMPLE = 50
_VALUE_HIT_THRESHOLD = 0.3


def discover_datasets(data_dir: Path) -> dict[str, Path]:
    if not data_dir.is_dir():
        return {}
    return {
        p.stem: p
        for p in sorted(data_dir.iterdir())
        if p.suffix.lower() in TABULAR_SUFFIXES and p.is_file()
    }


def detect_pii_columns(df: pd.DataFrame) -> list[str]:
    pii: list[str] = []
    for col in df.columns:
        if PII_NAME_RE.search(str(col)):
            pii.append(col)
            continue
        sample = df[col].dropna().astype(str).head(_VALUE_SAMPLE)
        if len(sample) == 0:
            continue
        hits = sum(bool(PHONE_RE.search(v) or GPS_PAIR_RE.search(v)) for v in sample)
        if hits / len(sample) >= _VALUE_HIT_THRESHOLD:
            pii.append(col)
    return pii


def profile_dataframe(df: pd.DataFrame, name: str, pii_columns: list[str]) -> dict:
    pii = set(pii_columns)
    columns: list[dict] = []
    for col in df.columns:
        series = df[col]
        info: dict = {
            "name": str(col),
            "dtype": str(series.dtype),
            "null_rate": round(float(series.isna().mean()), 4),
            "pii": col in pii,
        }
        if col not in pii:
            non_null = series.dropna()
            if non_null.nunique() <= MAX_CATEGORY_VALUES:
                info["values"] = sorted(str(v) for v in non_null.unique())
            elif pd.api.types.is_numeric_dtype(series):
                info["min"] = float(non_null.min()) if len(non_null) else None
                info["max"] = float(non_null.max()) if len(non_null) else None
        columns.append(info)
    return {"dataset": name, "rows": int(len(df)), "columns": columns}
