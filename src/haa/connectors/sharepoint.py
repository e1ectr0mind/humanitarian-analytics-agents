"""SharePoint / OneDrive connector: tabular files via Microsoft Graph (read-only)."""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import quote, urlsplit

import httpx

from haa.connectors.base import (
    AuthRejectedError,
    ConnectorError,
    NotFoundError,
    RemoteForm,
    get_json,
)
from haa.connectors.credentials import Connection
from haa.connectors.msauth import TokenSet, refresh

GRAPH = "https://graph.microsoft.com/v1.0"
TABULAR_EXTENSIONS = (".xlsx", ".csv")
MAX_FILES = 200


class SharePointConnector:
    def __init__(
        self,
        conn: Connection,
        token_json: str,
        on_tokens_updated: Callable[[str], None] | None = None,
        timeout: float = 30.0,
    ):
        if not (conn.tenant and conn.client_id):
            raise ConnectorError(
                f"Connection {conn.name!r} is missing tenant/client_id. "
                "Re-run: haa connect sharepoint"
            )
        self.conn = conn
        self._tokens = TokenSet.from_json(token_json)
        self._on_tokens_updated = on_tokens_updated
        self._client = httpx.Client(timeout=timeout, follow_redirects=True)
        self._apply_token()
        self._drive_id: str | None = None
        self.refresh_events: list[bool] = []
        self.list_truncated = False

    def _apply_token(self) -> None:
        self._client.headers["Authorization"] = f"Bearer {self._tokens.access_token}"

    def _refresh_tokens(self) -> None:
        try:
            self._tokens = refresh(
                self.conn.tenant, self.conn.client_id, self._tokens.refresh_token
            )
        except ConnectorError:
            self.refresh_events.append(False)
            raise
        self.refresh_events.append(True)
        self._apply_token()
        if self._on_tokens_updated is not None:
            self._on_tokens_updated(self._tokens.to_json())

    def _get(self, url: str, *, params: dict | None = None) -> object:
        if self._tokens.is_expired():
            self._refresh_tokens()
        try:
            return get_json(self._client, url, params=params)
        except AuthRejectedError:
            self._refresh_tokens()
            return get_json(self._client, url, params=params)

    def _download(self, url: str) -> bytes:
        if self._tokens.is_expired():
            self._refresh_tokens()
        resp = self._client.get(url)
        if resp.status_code in (401, 403):
            self._refresh_tokens()
            resp = self._client.get(url)
        if resp.status_code in (401, 403):
            raise ConnectorError(
                f"Access denied by Microsoft Graph (HTTP {resp.status_code}). "
                "Re-run: haa connect sharepoint"
            )
        if resp.status_code == 404:
            raise NotFoundError(f"Not found (HTTP 404): {url}")
        if resp.status_code >= 400:
            raise ConnectorError(
                f"Download failed (HTTP {resp.status_code}). "
                "Try again; if it persists, contact IT."
            )
        return resp.content

    def _drive(self) -> str:
        if self._drive_id is None:
            if self.conn.base_url == "onedrive":
                info = self._get(f"{GRAPH}/me/drive")
            else:
                parts = urlsplit(self.conn.base_url)
                try:
                    site = self._get(f"{GRAPH}/sites/{parts.netloc}:{parts.path}")
                except NotFoundError:
                    raise ConnectorError(
                        f"Site not found: {self.conn.base_url}. Check the URL "
                        "(expected https://<tenant>.sharepoint.com/sites/<name>)."
                    ) from None
                info = self._get(f"{GRAPH}/sites/{site['id']}/drive")
            self._drive_id = info["id"]
        return f"{GRAPH}/drives/{self._drive_id}"

    def _children_url(self, drive: str, folder: str | None) -> str:
        if folder:
            return f"{drive}/root:/{quote(folder)}:/children"
        return f"{drive}/root/children"

    def list_forms(self) -> list[RemoteForm]:
        drive = self._drive()
        self.list_truncated = False
        files: list[RemoteForm] = []
        queue: list[tuple[str, str]] = [(self._children_url(drive, self.conn.folder), "")]
        while queue:
            url, prefix = queue.pop(0)
            page: str | None = url
            while page:
                try:
                    data = self._get(page)
                except NotFoundError:
                    raise ConnectorError(
                        f"Folder {self.conn.folder!r} not found on {self.conn.base_url}."
                    ) from None
                for item in data.get("value", []):
                    rel = f"{prefix}{item['name']}"
                    if "folder" in item:
                        queue.append((f"{drive}/items/{item['id']}/children", f"{rel}/"))
                    elif rel.lower().endswith(TABULAR_EXTENSIONS):
                        files.append(RemoteForm(uid=item["id"], name=rel, submissions=None))
                        if len(files) >= MAX_FILES:
                            self.list_truncated = True
                            return files
                page = data.get("@odata.nextLink")
        return files

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> SharePointConnector:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
