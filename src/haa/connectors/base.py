"""Shared contract and plumbing for ODK-family source connectors."""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx
import pandas as pd


class ConnectorError(RuntimeError):
    """Friendly, user-facing connector failure."""


class NotFoundError(ConnectorError):
    """HTTP 404 — the resource (or a page past the end of data) does not exist."""


class AuthRejectedError(ConnectorError):
    """HTTP 401/403 — the server rejected our credentials."""


@dataclass(frozen=True)
class RemoteForm:
    uid: str
    name: str
    submissions: int | None = None


@dataclass(frozen=True)
class PullResult:
    path: Path
    rows: int
    form: RemoteForm
    bytes: int | None = None


class ODKConnector(Protocol):
    def list_forms(self) -> list[RemoteForm]: ...

    def pull(self, form: str, dest_dir: Path) -> PullResult: ...


ID_CANDIDATES = ("_uuid", "_id")
_RETRIES = 3


def ensure_row_ids(df: pd.DataFrame) -> pd.DataFrame:
    """Guarantee an explicit unique per-record identifier (spec 2a §2)."""
    for col in ID_CANDIDATES:
        if col in df.columns and df[col].notna().all() and df[col].astype(str).is_unique:
            return df
    stringified = df.astype(str)
    stringified.columns = stringified.columns.astype(str)
    ordered = sorted(stringified.columns)
    hashes = (
        stringified.apply(
            lambda row: hashlib.sha1(
                "\x1f".join(f"{col}\x1e{row[col]}" for col in ordered).encode()
            ).hexdigest()[:16],
            axis=1,
        )
        if len(df)
        else pd.Series([], dtype=str)
    )
    occurrence = hashes.groupby(hashes).cumcount() if len(df) else hashes
    out = df.copy()
    out["_haa_row_id"] = [
        f"{h}-{n}" if n else h for h, n in zip(hashes, occurrence, strict=True)
    ]
    return out


def rows_to_dataframe(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def safe_filename(name: str) -> str:
    return re.sub(r"[^\w-]+", "_", name).strip("_").lower()


def write_pull(df: pd.DataFrame, dest_dir: Path, name: str) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"{safe_filename(name)}.xlsx"
    ensure_row_ids(df).to_excel(path, index=False)
    return path


def get_json(client: httpx.Client, url: str, *, params: dict | None = None) -> object:
    last: Exception | None = None
    for attempt in range(_RETRIES):
        try:
            resp = client.get(url, params=params)
            if resp.status_code in (401, 403):
                raise AuthRejectedError(
                    f"Token rejected by the server (HTTP {resp.status_code}). "
                    "Refresh it with: haa connect <kind>"
                )
            if resp.status_code == 404:
                raise NotFoundError(f"Not found (HTTP 404): {url}")
            resp.raise_for_status()
            return resp.json()
        except ConnectorError:
            raise
        except Exception as exc:  # network / 5xx / JSON errors — retry
            last = exc
            if attempt < _RETRIES - 1:
                time.sleep(0.5 * (attempt + 1))
    raise ConnectorError(f"Request failed after {_RETRIES} attempts: {last}") from last
