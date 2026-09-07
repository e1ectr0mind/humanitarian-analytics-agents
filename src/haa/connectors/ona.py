"""Ona connector (API v1)."""

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


class OnaConnector:
    def __init__(self, base_url: str, token: str, timeout: float = 30.0, page_size: int = 1000):
        self.base_url = base_url.rstrip("/")
        self.page_size = page_size
        self._client = httpx.Client(
            headers={"Authorization": f"Token {token}"}, timeout=timeout
        )
        self._titles: dict[str, str] = {}

    def list_forms(self) -> list[RemoteForm]:
        data = get_json(self._client, f"{self.base_url}/api/v1/forms")
        self._titles = {str(f["formid"]): f.get("title", "") for f in data}
        return [
            RemoteForm(
                uid=str(f["formid"]),
                name=f["id_string"],
                submissions=f.get("num_of_submissions"),
            )
            for f in data
        ]

    def _resolve(self, form: str) -> RemoteForm:
        forms = self.list_forms()
        needle = form.lower()
        for f in forms:
            title = self._titles.get(f.uid, "").lower()
            if form == f.uid or needle == f.name.lower() or needle == title:
                return f
        known = ", ".join(f.name for f in forms) or "(none)"
        raise ConnectorError(f"Form {form!r} not found. Available: {known}")

    def pull(self, form: str, dest_dir: Path) -> PullResult:
        target = self._resolve(form)
        rows: list[dict] = []
        page = 1
        while True:
            batch = get_json(
                self._client,
                f"{self.base_url}/api/v1/data/{target.uid}",
                params={"page": page, "page_size": self.page_size},
            )
            rows.extend(batch)
            if len(batch) < self.page_size:
                break
            page += 1
        if not rows:
            raise ConnectorError(f"Form {target.name!r} has 0 submissions — nothing to pull.")
        path = write_pull(rows_to_dataframe(rows), dest_dir, target.name)
        return PullResult(path=path, rows=len(rows), form=target)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OnaConnector:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
