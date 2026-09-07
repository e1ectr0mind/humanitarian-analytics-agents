"""KoboToolbox connector (KPI API v2)."""

from __future__ import annotations

from pathlib import Path

import httpx

from haa.connectors.base import (
    ConnectorError,
    PullResult,
    RemoteForm,
    get_json,
    rows_to_dataframe,
    write_pull,
)

_MAX_PAGES = 1000


class KoboConnector:
    def __init__(self, base_url: str, token: str, timeout: float = 30.0, page_size: int = 1000):
        self.base_url = base_url.rstrip("/")
        self.page_size = page_size
        self._client = httpx.Client(
            headers={"Authorization": f"Token {token}"}, timeout=timeout
        )

    def list_forms(self) -> list[RemoteForm]:
        results: list[dict] = []
        url: str | None = f"{self.base_url}/api/v2/assets/"
        params: dict | None = {"format": "json"}
        for _ in range(_MAX_PAGES):
            if url is None:
                break
            data = get_json(self._client, url, params=params)
            results.extend(data.get("results", []))
            url, params = data.get("next"), None  # `next` is a full URL
        else:
            raise ConnectorError(f"Pagination did not terminate after {_MAX_PAGES} pages")
        return [
            RemoteForm(
                uid=a["uid"],
                name=a["name"],
                submissions=a.get("deployment__submission_count"),
            )
            for a in results
            if a.get("asset_type") == "survey"
        ]

    def _resolve(self, form: str) -> RemoteForm:
        forms = self.list_forms()
        for f in forms:
            if form == f.uid or form.lower() == f.name.lower():
                return f
        known = ", ".join(f.name for f in forms) or "(none)"
        raise ConnectorError(f"Form {form!r} not found. Available: {known}")

    def pull(self, form: str, dest_dir: Path) -> PullResult:
        target = self._resolve(form)
        rows: list[dict] = []
        url: str | None = f"{self.base_url}/api/v2/assets/{target.uid}/data/"
        params: dict | None = {"format": "json", "limit": self.page_size}
        for _ in range(_MAX_PAGES):
            if url is None:
                break
            page = get_json(self._client, url, params=params)
            rows.extend(page.get("results", []))
            url, params = page.get("next"), None  # `next` is a full URL
        else:
            raise ConnectorError(f"Pagination did not terminate after {_MAX_PAGES} pages")
        if not rows:
            raise ConnectorError(f"Form {target.name!r} has 0 submissions — nothing to pull.")
        path = write_pull(rows_to_dataframe(rows), dest_dir, target.name)
        return PullResult(path=path, rows=len(rows), form=target)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> KoboConnector:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
