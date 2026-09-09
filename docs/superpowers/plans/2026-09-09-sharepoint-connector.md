# SharePoint Connector (slice 2b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A fourth connection kind `sharepoint` that pulls tabular files (.xlsx/.csv) from SharePoint document libraries and personal OneDrive into `workspace/data/` via Microsoft Graph, with a hand-rolled device-code OAuth flow.

**Architecture:** Two new modules in `src/haa/connectors/` — `msauth.py` (device-code flow + refresh, raw httpx, token set serialized to JSON and stored as the profile "token" in keyring) and `sharepoint.py` (`SharePointConnector` implementing the same `list_forms`/`pull` protocol as Kobo/Ona over Graph drives). `credentials.py` gains the kind, three optional `Connection` fields, and a rotation-persisting callback in `make_connector`. Sourcetools and CLI get thin extensions; no new MCP tools.

**Tech Stack:** Python 3.12, httpx (+respx for test mocks), keyring, argparse/rich CLI. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-08-sharepoint-connector-design.md` — the plan argues from the spec; executors read both.

## Global Constraints

- No new runtime dependencies: httpx, keyring, pandas already present; respx is the existing test mock.
- Every user-facing error is one friendly sentence that names the next action; never a traceback (spec §4).
- Tokens and secrets never appear in `connections.toml`, telemetry logs, or test assertions on disk artifacts; keyring (or env fallback) only (spec §2).
- Graph scopes exactly: `https://graph.microsoft.com/Files.Read.All https://graph.microsoft.com/Sites.Read.All offline_access` (spec §3).
- Listing cap: 200 found tabular files; pull writes bytes verbatim — no pandas round-trip (spec §1, §3).
- On this machine run tests as `.\.venv\Scripts\python.exe -m pytest` and lint as `.\.venv\Scripts\python.exe -m ruff check .` (`uv` is NOT on PATH).
- Commit после каждой задачи; commit messages in English, conventional style (`feat:`/`test:`/`docs:`).

---

### Task 1: Typed auth rejection and byte size in base.py

**Files:**
- Modify: `src/haa/connectors/base.py`
- Test: `tests/test_connectors_base.py`

**Interfaces:**
- Consumes: existing `get_json`, `ConnectorError`, `PullResult`.
- Produces: `class AuthRejectedError(ConnectorError)` — raised by `get_json` on HTTP 401/403 (message text unchanged); `PullResult` gains field `bytes: int | None = None`. Later tasks import `AuthRejectedError` and construct `PullResult(..., bytes=N)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_connectors_base.py` (it already imports `httpx`, `pytest`, `respx`, and `from haa.connectors.base import ...` — extend that import with `AuthRejectedError`, `PullResult`, `RemoteForm` as needed):

```python
@respx.mock
def test_get_json_auth_error_is_typed() -> None:
    respx.get("https://x.test/a").mock(return_value=httpx.Response(401))
    with pytest.raises(AuthRejectedError, match="haa connect"):
        get_json(httpx.Client(), "https://x.test/a")


def test_pull_result_bytes_optional(tmp_path: Path) -> None:
    form = RemoteForm(uid="u", name="n")
    assert PullResult(path=tmp_path / "x.xlsx", rows=1, form=form).bytes is None
    assert PullResult(path=tmp_path / "x.xlsx", rows=0, form=form, bytes=2048).bytes == 2048
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_connectors_base.py -q`
Expected: FAIL — `ImportError: cannot import name 'AuthRejectedError'`.

- [ ] **Step 3: Implement**

In `src/haa/connectors/base.py`, after `NotFoundError`:

```python
class AuthRejectedError(ConnectorError):
    """HTTP 401/403 — the server rejected our credentials."""
```

Add `bytes: int | None = None` as the last field of `PullResult`. In `get_json`, change the 401/403 branch to raise `AuthRejectedError` (same message string as today).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_connectors_base.py -q`
Expected: all PASS (including the pre-existing `test_get_json_auth_error_no_retry` — `AuthRejectedError` is a `ConnectorError`).

- [ ] **Step 5: Commit**

```bash
git add src/haa/connectors/base.py tests/test_connectors_base.py
git commit -m "feat: typed auth-rejection error and byte size in pull results"
```

---

### Task 2: msauth.py — TokenSet, DeviceFlow, start_device_flow

**Files:**
- Create: `src/haa/connectors/msauth.py`
- Create: `tests/test_msauth.py`

**Interfaces:**
- Consumes: `ConnectorError` from `haa.connectors.base`.
- Produces (later tasks rely on these exact names):
  - `LOGIN_BASE = "https://login.microsoftonline.com"`, `SCOPES` (string, see Global Constraints), `DEFAULT_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"` (Microsoft Graph Command Line Tools, widely pre-consented).
  - `class AuthError(ConnectorError)`.
  - `@dataclass(frozen=True) DeviceFlow(tenant, client_id, device_code, user_code, verification_uri, interval: int, expires_at: float)`.
  - `@dataclass(frozen=True) TokenSet(access_token: str, refresh_token: str, expires_at: float)` with `to_json() -> str`, `TokenSet.from_json(text) -> TokenSet` (AuthError on garbage), `is_expired(skew: float = 60.0) -> bool`.
  - `start_device_flow(tenant: str, client_id: str, *, timeout: float = 30.0) -> DeviceFlow`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_msauth.py`:

```python
import time

import httpx
import pytest
import respx

from haa.connectors.base import ConnectorError
from haa.connectors.msauth import (
    LOGIN_BASE,
    SCOPES,
    AuthError,
    TokenSet,
    start_device_flow,
)

TENANT = "contoso.onmicrosoft.com"
DEVICECODE_URL = f"{LOGIN_BASE}/{TENANT}/oauth2/v2.0/devicecode"
TOKEN_URL = f"{LOGIN_BASE}/{TENANT}/oauth2/v2.0/token"


def test_token_set_json_roundtrip() -> None:
    ts = TokenSet(access_token="at", refresh_token="rt", expires_at=123.5)
    assert TokenSet.from_json(ts.to_json()) == ts


def test_token_set_from_garbage_is_friendly() -> None:
    with pytest.raises(AuthError, match="haa connect sharepoint"):
        TokenSet.from_json("plain-old-kobo-token")


def test_auth_error_is_connector_error() -> None:
    assert issubclass(AuthError, ConnectorError)


def test_is_expired_with_skew() -> None:
    assert TokenSet("a", "r", time.time() + 30).is_expired()  # inside 60s skew
    assert not TokenSet("a", "r", time.time() + 3600).is_expired()


@respx.mock
def test_start_device_flow() -> None:
    route = respx.post(DEVICECODE_URL).mock(
        return_value=httpx.Response(200, json={
            "device_code": "dc-1", "user_code": "ABC123",
            "verification_uri": "https://microsoft.com/devicelogin",
            "interval": 5, "expires_in": 900,
        })
    )
    flow = start_device_flow(TENANT, "cid-1")
    assert flow.user_code == "ABC123" and flow.device_code == "dc-1"
    assert flow.interval == 5 and flow.expires_at > time.time()
    body = route.calls[0].request.content.decode()
    assert "client_id=cid-1" in body and "Files.Read.All" in body


@respx.mock
def test_start_device_flow_consent_error_has_hint() -> None:
    respx.post(DEVICECODE_URL).mock(
        return_value=httpx.Response(400, json={
            "error": "invalid_client",
            "error_description": "AADSTS700016: Application not found in the directory.",
        })
    )
    with pytest.raises(AuthError, match="IT"):
        start_device_flow(TENANT, "cid-1")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_msauth.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'haa.connectors.msauth'`.

- [ ] **Step 3: Implement**

Create `src/haa/connectors/msauth.py`:

```python
"""Microsoft Entra ID device-code flow and token refresh (raw httpx, no MSAL)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

import httpx

from haa.connectors.base import ConnectorError

LOGIN_BASE = "https://login.microsoftonline.com"
SCOPES = (
    "https://graph.microsoft.com/Files.Read.All "
    "https://graph.microsoft.com/Sites.Read.All offline_access"
)
# Microsoft Graph Command Line Tools — a public client pre-consented in many tenants.
DEFAULT_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"

_CONSENT_CODES = ("AADSTS65001", "AADSTS7000218", "AADSTS700016", "AADSTS90094", "AADSTS500113")


class AuthError(ConnectorError):
    """Friendly, user-facing Microsoft sign-in failure."""


@dataclass(frozen=True)
class DeviceFlow:
    tenant: str
    client_id: str
    device_code: str
    user_code: str
    verification_uri: str
    interval: int
    expires_at: float


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    refresh_token: str
    expires_at: float

    def to_json(self) -> str:
        return json.dumps({
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
        })

    @classmethod
    def from_json(cls, text: str) -> TokenSet:
        try:
            data = json.loads(text)
            return cls(
                access_token=str(data["access_token"]),
                refresh_token=str(data["refresh_token"]),
                expires_at=float(data["expires_at"]),
            )
        except (ValueError, TypeError, KeyError) as exc:
            raise AuthError(
                "The stored credential is not a Microsoft token set. "
                "Re-run: haa connect sharepoint"
            ) from exc

    def is_expired(self, skew: float = 60.0) -> bool:
        return time.time() >= self.expires_at - skew


def _token_url(tenant: str) -> str:
    return f"{LOGIN_BASE}/{tenant}/oauth2/v2.0/token"


def _post(url: str, data: dict, timeout: float) -> dict:
    try:
        resp = httpx.post(url, data=data, timeout=timeout)
    except Exception as exc:
        raise AuthError(f"Could not reach Microsoft sign-in: {exc}") from exc
    try:
        return resp.json()
    except Exception as exc:
        raise AuthError(
            f"Microsoft sign-in returned a non-JSON response (HTTP {resp.status_code})."
        ) from exc


def _friendly(data: dict) -> str:
    desc = str(data.get("error_description") or data.get("error") or "unknown error")
    message = f"Microsoft sign-in failed: {desc.splitlines()[0]}"
    if any(code in desc for code in _CONSENT_CODES):
        message += (
            " — the tenant does not allow this application. Agree a client_id "
            "with IT/HQ or try the pre-consented default one."
        )
    return message


def _token_set(data: dict, *, fallback_refresh: str) -> TokenSet:
    return TokenSet(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token") or fallback_refresh,
        expires_at=time.time() + float(data.get("expires_in", 3600)),
    )


def start_device_flow(tenant: str, client_id: str, *, timeout: float = 30.0) -> DeviceFlow:
    data = _post(
        f"{LOGIN_BASE}/{tenant}/oauth2/v2.0/devicecode",
        {"client_id": client_id, "scope": SCOPES},
        timeout,
    )
    if "device_code" not in data:
        raise AuthError(_friendly(data))
    return DeviceFlow(
        tenant=tenant,
        client_id=client_id,
        device_code=data["device_code"],
        user_code=data["user_code"],
        verification_uri=data.get("verification_uri", "https://microsoft.com/devicelogin"),
        interval=int(data.get("interval", 5)),
        expires_at=time.time() + float(data.get("expires_in", 900)),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_msauth.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/haa/connectors/msauth.py tests/test_msauth.py
git commit -m "feat: Microsoft device-code flow scaffolding (TokenSet, start_device_flow)"
```

---

### Task 3: msauth.py — poll_for_token

**Files:**
- Modify: `src/haa/connectors/msauth.py`
- Test: `tests/test_msauth.py`

**Interfaces:**
- Consumes: `DeviceFlow`, `_post`, `_friendly`, `_token_set`, `_token_url` from Task 2.
- Produces: `poll_for_token(flow: DeviceFlow, *, timeout: float = 30.0, sleep=time.sleep) -> TokenSet`. The `sleep` parameter exists so tests never really sleep.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_msauth.py` (extend the msauth import with `DeviceFlow`, `poll_for_token`):

```python
def _flow(expires_in: float = 60.0) -> DeviceFlow:
    return DeviceFlow(
        tenant=TENANT, client_id="cid-1", device_code="dc-1", user_code="ABC123",
        verification_uri="https://microsoft.com/devicelogin", interval=5,
        expires_at=time.time() + expires_in,
    )


@respx.mock
def test_poll_pending_then_slow_down_then_success() -> None:
    respx.post(TOKEN_URL).mock(side_effect=[
        httpx.Response(400, json={"error": "authorization_pending"}),
        httpx.Response(400, json={"error": "slow_down"}),
        httpx.Response(200, json={
            "access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600,
        }),
    ])
    naps: list[int] = []
    tokens = poll_for_token(_flow(), sleep=naps.append)
    assert tokens.access_token == "at-1" and tokens.refresh_token == "rt-1"
    assert naps == [5, 10]  # interval, then interval + 5 on slow_down


@respx.mock
def test_poll_declined_is_friendly() -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(400, json={
            "error": "authorization_declined",
            "error_description": "AADSTS65004: User declined to consent.",
        })
    )
    with pytest.raises(AuthError, match="declined"):
        poll_for_token(_flow(), sleep=lambda s: None)


def test_poll_code_expired_before_use() -> None:
    with pytest.raises(AuthError, match="expired"):
        poll_for_token(_flow(expires_in=-1.0), sleep=lambda s: None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_msauth.py -q`
Expected: FAIL — `ImportError: cannot import name 'poll_for_token'`.

- [ ] **Step 3: Implement**

Append to `src/haa/connectors/msauth.py`:

```python
def poll_for_token(flow: DeviceFlow, *, timeout: float = 30.0, sleep=time.sleep) -> TokenSet:
    payload = {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "client_id": flow.client_id,
        "device_code": flow.device_code,
    }
    while time.time() < flow.expires_at:
        data = _post(_token_url(flow.tenant), payload, timeout)
        if "access_token" in data:
            return _token_set(data, fallback_refresh="")
        error = data.get("error", "")
        if error == "authorization_pending":
            sleep(flow.interval)
        elif error == "slow_down":
            sleep(flow.interval + 5)
        else:
            raise AuthError(_friendly(data))
    raise AuthError("The sign-in code expired before it was used. Run: haa connect sharepoint")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_msauth.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/haa/connectors/msauth.py tests/test_msauth.py
git commit -m "feat: device-code polling with pending/slow_down/expiry handling"
```

---

### Task 4: msauth.py — refresh with rotation

**Files:**
- Modify: `src/haa/connectors/msauth.py`
- Test: `tests/test_msauth.py`

**Interfaces:**
- Consumes: helpers from Task 2.
- Produces: `refresh(tenant: str, client_id: str, refresh_token: str, *, timeout: float = 30.0) -> TokenSet`. Keeps the OLD refresh token when Microsoft's response omits a new one.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_msauth.py` (extend the msauth import with `refresh`):

```python
@respx.mock
def test_refresh_rotates_refresh_token() -> None:
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={
            "access_token": "at-2", "refresh_token": "rt-2", "expires_in": 3600,
        })
    )
    tokens = refresh(TENANT, "cid-1", "rt-1")
    assert tokens.access_token == "at-2" and tokens.refresh_token == "rt-2"
    body = route.calls[0].request.content.decode()
    assert "grant_type=refresh_token" in body and "rt-1" in body


@respx.mock
def test_refresh_keeps_old_token_when_none_returned() -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "at-2", "expires_in": 3600})
    )
    assert refresh(TENANT, "cid-1", "rt-1").refresh_token == "rt-1"


@respx.mock
def test_refresh_invalid_grant_is_friendly() -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(400, json={
            "error": "invalid_grant", "error_description": "AADSTS70000: expired.",
        })
    )
    with pytest.raises(AuthError, match="haa connect sharepoint"):
        refresh(TENANT, "cid-1", "rt-old")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_msauth.py -q`
Expected: FAIL — `ImportError: cannot import name 'refresh'`.

- [ ] **Step 3: Implement**

Append to `src/haa/connectors/msauth.py`:

```python
def refresh(tenant: str, client_id: str, refresh_token: str, *, timeout: float = 30.0) -> TokenSet:
    data = _post(
        _token_url(tenant),
        {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": refresh_token,
            "scope": SCOPES,
        },
        timeout,
    )
    if "access_token" not in data:
        if data.get("error") == "invalid_grant":
            raise AuthError(
                "The Microsoft session has expired or was revoked. "
                "Re-run: haa connect sharepoint"
            )
        raise AuthError(_friendly(data))
    return _token_set(data, fallback_refresh=refresh_token)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_msauth.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/haa/connectors/msauth.py tests/test_msauth.py
git commit -m "feat: token refresh with rotation persistence semantics"
```

---

### Task 5: credentials.py — sharepoint kind and Connection fields

**Files:**
- Modify: `src/haa/connectors/credentials.py`
- Test: `tests/test_credentials.py`

**Interfaces:**
- Consumes: existing `Connection`, `save_connection`, `load_connection`, `list_connections`.
- Produces: `VALID_KINDS = ("kobo", "ona", "sharepoint")`; `Connection` gains `tenant: str | None = None`, `client_id: str | None = None`, `folder: str | None = None` (persisted in `connections.toml` when set, restored on load); `save_connection` raises `CredentialsError` for a sharepoint connection missing tenant or client_id. `make_connector` is NOT touched here (Task 9).

- [ ] **Step 1: Update the existing invalid-kind test and write the failing tests**

In `tests/test_credentials.py`, `test_invalid_kind_rejected` currently uses `"sharepoint"` as the invalid kind — change it to `"dropbox"`:

```python
def test_invalid_kind_rejected(tmp_path: Path) -> None:
    with pytest.raises(CredentialsError, match="kind"):
        save_connection(tmp_path, Connection("x", "dropbox", "https://x"), "t")
```

Then append:

```python
SP = Connection(
    name="imc-sp", kind="sharepoint",
    base_url="https://contoso.sharepoint.com/sites/MEAL",
    tenant="contoso.onmicrosoft.com", client_id="cid-123",
    folder="Shared Documents/5W",
)


def test_sharepoint_roundtrip_with_extra_fields(tmp_path: Path) -> None:
    save_connection(tmp_path, SP, '{"access_token": "a"}')
    conn, token = load_connection(tmp_path, "imc-sp")
    assert conn == SP and token == '{"access_token": "a"}'
    text = (tmp_path / "connections.toml").read_text(encoding="utf-8")
    assert "contoso.onmicrosoft.com" in text and "access_token" not in text


def test_sharepoint_requires_tenant_and_client_id(tmp_path: Path) -> None:
    with pytest.raises(CredentialsError, match="tenant"):
        save_connection(tmp_path, Connection("x", "sharepoint", "onedrive"), "t")


def test_onedrive_base_url_survives(tmp_path: Path) -> None:
    conn = Connection("od", "sharepoint", "onedrive", tenant="t", client_id="c")
    save_connection(tmp_path, conn, "tok")
    loaded, _ = load_connection(tmp_path, "od")
    assert loaded.base_url == "onedrive" and loaded.folder is None


def test_kobo_connection_unaffected_by_new_fields(tmp_path: Path) -> None:
    save_connection(tmp_path, CONN, "t")
    loaded, _ = load_connection(tmp_path, "imc-kobo")
    assert loaded.tenant is None and loaded.client_id is None and loaded.folder is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_credentials.py -q`
Expected: FAIL — `TypeError: Connection.__init__() got an unexpected keyword argument 'tenant'`.

- [ ] **Step 3: Implement**

In `src/haa/connectors/credentials.py`:

```python
VALID_KINDS = ("kobo", "ona", "sharepoint")
_EXTRA_FIELDS = ("tenant", "client_id", "folder")


@dataclass(frozen=True)
class Connection:
    name: str
    kind: str
    base_url: str
    tenant: str | None = None
    client_id: str | None = None
    folder: str | None = None
```

In `save_connection`, after the kind check add:

```python
    if conn.kind == "sharepoint" and not (conn.tenant and conn.client_id):
        raise CredentialsError(
            "A sharepoint connection needs both tenant and client_id. "
            "Re-run: haa connect sharepoint"
        )
```

and replace the metadata write with:

```python
    meta: dict[str, str] = {"kind": conn.kind, "base_url": conn.base_url.rstrip("/")}
    for key in _EXTRA_FIELDS:
        value = getattr(conn, key)
        if value:
            meta[key] = value
    data[conn.name] = meta
```

In `list_connections` and `load_connection`, build the dataclass with the extra fields:

```python
        Connection(
            name=name, kind=meta["kind"], base_url=meta["base_url"],
            tenant=meta.get("tenant"), client_id=meta.get("client_id"),
            folder=meta.get("folder"),
        )
```

(`load_connection` uses `data[name]` as `meta`; keep the rest of both functions unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_credentials.py tests/test_cli.py tests/test_sourcetools.py -q`
Expected: PASS (CLI/sourcetools construct `Connection` positionally with 3 args — new fields default to None).

- [ ] **Step 5: Commit**

```bash
git add src/haa/connectors/credentials.py tests/test_credentials.py
git commit -m "feat: sharepoint connection kind with tenant/client_id/folder metadata"
```

---

### Task 6: sharepoint.py — construction, drive resolution, refresh-and-retry

**Files:**
- Create: `src/haa/connectors/sharepoint.py`
- Create: `tests/test_sharepoint.py`

**Interfaces:**
- Consumes: `AuthRejectedError`, `ConnectorError`, `NotFoundError`, `get_json` (Task 1/base); `TokenSet`, `refresh` (Tasks 2/4); `Connection` (Task 5).
- Produces: `class SharePointConnector` with `__init__(conn: Connection, token_json: str, on_tokens_updated: Callable[[str], None] | None = None, timeout: float = 30.0)`, attributes `refresh_events: list[bool]` and `list_truncated: bool`, internals `_drive() -> str` (returns `f"{GRAPH}/drives/{id}"`), `_get(url, *, params=None)`, `_download(url) -> bytes`, plus `close()` and context-manager methods. Module constants `GRAPH = "https://graph.microsoft.com/v1.0"`, `TABULAR_EXTENSIONS = (".xlsx", ".csv")`, `MAX_FILES = 200`. Tasks 7–8 add `list_forms`/`pull` to this class; Task 9 wires it into `make_connector`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sharepoint.py`:

```python
import time
from pathlib import Path

import httpx
import pytest
import respx

from haa.connectors.base import ConnectorError
from haa.connectors.credentials import Connection
from haa.connectors.msauth import LOGIN_BASE, TokenSet
from haa.connectors.sharepoint import GRAPH, SharePointConnector

TENANT = "contoso.onmicrosoft.com"
TOKEN_URL = f"{LOGIN_BASE}/{TENANT}/oauth2/v2.0/token"
SITE_CONN = Connection(
    name="sp", kind="sharepoint",
    base_url="https://contoso.sharepoint.com/sites/MEAL",
    tenant=TENANT, client_id="cid",
)
OD_CONN = Connection(name="od", kind="sharepoint", base_url="onedrive",
                     tenant=TENANT, client_id="cid")


def _token_json(expires_in: float = 3600.0) -> str:
    return TokenSet("at-1", "rt-1", time.time() + expires_in).to_json()


def _mock_refresh(access: str = "at-2", refresh_tok: str = "rt-2") -> respx.Route:
    return respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={
            "access_token": access, "refresh_token": refresh_tok, "expires_in": 3600,
        })
    )


@respx.mock
def test_site_drive_resolution() -> None:
    respx.get(f"{GRAPH}/sites/contoso.sharepoint.com:/sites/MEAL").mock(
        return_value=httpx.Response(200, json={"id": "site-1"})
    )
    respx.get(f"{GRAPH}/sites/site-1/drive").mock(
        return_value=httpx.Response(200, json={"id": "drv-1"})
    )
    c = SharePointConnector(SITE_CONN, _token_json())
    assert c._drive() == f"{GRAPH}/drives/drv-1"
    assert c._drive() == f"{GRAPH}/drives/drv-1"  # cached: routes called once
    assert respx.calls.call_count == 2


@respx.mock
def test_onedrive_resolution() -> None:
    respx.get(f"{GRAPH}/me/drive").mock(return_value=httpx.Response(200, json={"id": "drv-me"}))
    assert SharePointConnector(OD_CONN, _token_json())._drive() == f"{GRAPH}/drives/drv-me"


@respx.mock
def test_expired_token_refreshes_and_persists_before_call() -> None:
    _mock_refresh()
    route = respx.get(f"{GRAPH}/me/drive").mock(
        return_value=httpx.Response(200, json={"id": "drv-me"})
    )
    saved: list[str] = []
    c = SharePointConnector(OD_CONN, _token_json(expires_in=-10.0),
                            on_tokens_updated=saved.append)
    c._drive()
    assert route.calls[0].request.headers["Authorization"] == "Bearer at-2"
    assert c.refresh_events == [True]
    assert saved and TokenSet.from_json(saved[0]).refresh_token == "rt-2"


@respx.mock
def test_401_triggers_one_refresh_and_retry() -> None:
    _mock_refresh()
    respx.get(f"{GRAPH}/me/drive").mock(side_effect=[
        httpx.Response(401),
        httpx.Response(200, json={"id": "drv-me"}),
    ])
    c = SharePointConnector(OD_CONN, _token_json())
    assert c._drive() == f"{GRAPH}/drives/drv-me"
    assert c.refresh_events == [True]


@respx.mock
def test_site_not_found_is_friendly() -> None:
    respx.get(f"{GRAPH}/sites/contoso.sharepoint.com:/sites/MEAL").mock(
        return_value=httpx.Response(404)
    )
    with pytest.raises(ConnectorError, match="Site not found"):
        SharePointConnector(SITE_CONN, _token_json())._drive()


def test_missing_tenant_rejected() -> None:
    bare = Connection(name="x", kind="sharepoint", base_url="onedrive")
    with pytest.raises(ConnectorError, match="haa connect sharepoint"):
        SharePointConnector(bare, _token_json())


def test_garbage_token_rejected() -> None:
    with pytest.raises(ConnectorError, match="haa connect sharepoint"):
        SharePointConnector(OD_CONN, "kobo-style-token")


def test_close_releases_client() -> None:
    c = SharePointConnector(OD_CONN, _token_json())
    c.close()
    assert c._client.is_closed
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_sharepoint.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'haa.connectors.sharepoint'`.

- [ ] **Step 3: Implement**

Create `src/haa/connectors/sharepoint.py`:

```python
"""SharePoint / OneDrive connector: tabular files via Microsoft Graph (read-only)."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Callable
from urllib.parse import quote, urlsplit

import httpx

from haa.connectors.base import (
    AuthRejectedError,
    ConnectorError,
    NotFoundError,
    PullResult,
    RemoteForm,
    get_json,
    safe_filename,
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
            raise ConnectorError(f"Download failed (HTTP {resp.status_code}).")
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

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> SharePointConnector:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
```

(`base64`, `quote`, `Path`, `PullResult`, `RemoteForm`, `safe_filename`, `MAX_FILES` and `TABULAR_EXTENSIONS` are used by Tasks 7–8; importing them now keeps later diffs additive. If ruff flags unused imports at this stage, add them in the task that uses them instead.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_sharepoint.py -q; .\.venv\Scripts\python.exe -m ruff check .`
Expected: tests PASS, ruff clean (drop any unused-import complaints per the note above).

- [ ] **Step 5: Commit**

```bash
git add src/haa/connectors/sharepoint.py tests/test_sharepoint.py
git commit -m "feat: SharePoint connector core - drive resolution and token refresh-retry"
```

---

### Task 7: sharepoint.py — list_forms

**Files:**
- Modify: `src/haa/connectors/sharepoint.py`
- Test: `tests/test_sharepoint.py`

**Interfaces:**
- Consumes: `_drive()`, `_get()`, `MAX_FILES`, `TABULAR_EXTENSIONS`, `list_truncated` from Task 6.
- Produces: `list_forms() -> list[RemoteForm]` — recursive walk of the pinned folder (BFS via Graph children API, `@odata.nextLink` pagination), `uid` = driveItem id, `name` = path relative to the pinned folder, `submissions=None`; sets `self.list_truncated = True` and returns early at `MAX_FILES` found tabular files. Helper `_children_url(drive: str, folder: str | None) -> str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sharepoint.py` (test fixtures `SITE_CONN`, `OD_CONN`, `_token_json`, `TOKEN_URL` from Task 6 are already in this file; extend imports with `RemoteForm` from `haa.connectors.base` and `import haa.connectors.sharepoint as sp`):

```python
def _drive_ready() -> SharePointConnector:
    """Connector against OneDrive with the drive already mocked (call inside respx.mock)."""
    respx.get(f"{GRAPH}/me/drive").mock(return_value=httpx.Response(200, json={"id": "drv-me"}))
    return SharePointConnector(OD_CONN, _token_json())


def _item(name: str, item_id: str, *, folder: bool = False) -> dict:
    node: dict = {"id": item_id, "name": name}
    node["folder" if folder else "file"] = {}
    return node


@respx.mock
def test_list_recurses_and_filters_tabular() -> None:
    c = _drive_ready()
    respx.get(f"{GRAPH}/drives/drv-me/root/children").mock(
        return_value=httpx.Response(200, json={"value": [
            _item("2026", "fld-1", folder=True),
            _item("june_5w.xlsx", "f-1"),
            _item("readme.txt", "f-2"),
        ]})
    )
    respx.get(f"{GRAPH}/drives/drv-me/items/fld-1/children").mock(
        return_value=httpx.Response(200, json={"value": [_item("archive.csv", "f-3")]})
    )
    forms = c.list_forms()
    assert [(f.uid, f.name) for f in forms] == [
        ("f-1", "june_5w.xlsx"), ("f-3", "2026/archive.csv"),
    ]
    assert c.list_truncated is False


@respx.mock
def test_list_follows_next_link() -> None:
    c = _drive_ready()
    next_url = f"{GRAPH}/drives/drv-me/root/children?$skiptoken=abc"
    respx.get(next_url).mock(
        return_value=httpx.Response(200, json={"value": [_item("b.csv", "f-2")]})
    )
    respx.get(f"{GRAPH}/drives/drv-me/root/children").mock(
        return_value=httpx.Response(200, json={
            "value": [_item("a.xlsx", "f-1")], "@odata.nextLink": next_url,
        })
    )
    assert [f.name for f in c.list_forms()] == ["a.xlsx", "b.csv"]


@respx.mock
def test_list_caps_and_flags_truncation(monkeypatch) -> None:
    monkeypatch.setattr(sp, "MAX_FILES", 2)
    c = _drive_ready()
    respx.get(f"{GRAPH}/drives/drv-me/root/children").mock(
        return_value=httpx.Response(200, json={"value": [
            _item("a.xlsx", "f-1"), _item("b.xlsx", "f-2"), _item("c.xlsx", "f-3"),
        ]})
    )
    forms = c.list_forms()
    assert len(forms) == 2 and c.list_truncated is True


@respx.mock
def test_list_pinned_folder_encodes_path() -> None:
    respx.get(f"{GRAPH}/sites/contoso.sharepoint.com:/sites/MEAL").mock(
        return_value=httpx.Response(200, json={"id": "site-1"})
    )
    respx.get(f"{GRAPH}/sites/site-1/drive").mock(
        return_value=httpx.Response(200, json={"id": "drv-1"})
    )
    route = respx.get(
        f"{GRAPH}/drives/drv-1/root:/Shared%20Documents/5W:/children"
    ).mock(return_value=httpx.Response(200, json={"value": [_item("x.csv", "f-1")]}))
    conn = Connection(name="sp", kind="sharepoint",
                      base_url="https://contoso.sharepoint.com/sites/MEAL",
                      tenant=TENANT, client_id="cid", folder="Shared Documents/5W")
    forms = SharePointConnector(conn, _token_json()).list_forms()
    assert route.called and [f.name for f in forms] == ["x.csv"]


@respx.mock
def test_list_missing_folder_is_friendly() -> None:
    respx.get(f"{GRAPH}/me/drive").mock(return_value=httpx.Response(200, json={"id": "drv-me"}))
    respx.get(f"{GRAPH}/drives/drv-me/root:/nope:/children").mock(
        return_value=httpx.Response(404)
    )
    conn = Connection(name="od", kind="sharepoint", base_url="onedrive",
                      tenant=TENANT, client_id="cid", folder="nope")
    with pytest.raises(ConnectorError, match="'nope' not found"):
        SharePointConnector(conn, _token_json()).list_forms()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_sharepoint.py -q`
Expected: FAIL — `AttributeError: 'SharePointConnector' object has no attribute 'list_forms'`.

- [ ] **Step 3: Implement**

Add to `SharePointConnector`:

```python
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
```

Note: the cap check must read the module-global `MAX_FILES` (not a copied local) so tests can monkeypatch it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_sharepoint.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/haa/connectors/sharepoint.py tests/test_sharepoint.py
git commit -m "feat: recursive tabular-file listing with cap and truncation flag"
```

---

### Task 8: sharepoint.py — pull (path, item id, https link)

**Files:**
- Modify: `src/haa/connectors/sharepoint.py`
- Test: `tests/test_sharepoint.py`

**Interfaces:**
- Consumes: `_drive()`, `_get()`, `_download()`, `list_forms()`; `PullResult(bytes=...)` from Task 1.
- Produces: `pull(form: str, dest_dir: Path) -> PullResult` with `rows=0`, `bytes=len(content)`, `form=RemoteForm(uid=item_id, name=original_filename)`; file written verbatim as `safe_filename(stem) + ext`. Reference resolution: `https://` prefix → Graph shares API (`u!` + base64url, `=` padding stripped); contains `/` or ends with a tabular extension → path under the pinned folder; otherwise → item id.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sharepoint.py` (extend imports with `base64`):

```python
PAYLOAD = b"PK\x03\x04 not a real xlsx but bytes that must survive verbatim \xd0\xaf"


@respx.mock
def test_pull_by_path_writes_bytes_verbatim(tmp_path: Path) -> None:
    c = _drive_ready()
    respx.get(f"{GRAPH}/drives/drv-me/root:/2026/june_5w.xlsx").mock(
        return_value=httpx.Response(200, json=_item("june_5w.xlsx", "f-1"))
    )
    respx.get(f"{GRAPH}/drives/drv-me/items/f-1/content").mock(
        return_value=httpx.Response(200, content=PAYLOAD)
    )
    result = c.pull("2026/june_5w.xlsx", tmp_path)
    assert result.path.read_bytes() == PAYLOAD
    assert result.path.name == "june_5w.xlsx"
    assert result.bytes == len(PAYLOAD) and result.rows == 0
    assert result.form.uid == "f-1" and result.form.name == "june_5w.xlsx"


@respx.mock
def test_pull_by_item_id(tmp_path: Path) -> None:
    c = _drive_ready()
    respx.get(f"{GRAPH}/drives/drv-me/items/f9").mock(
        return_value=httpx.Response(200, json=_item("data.csv", "f9"))
    )
    respx.get(f"{GRAPH}/drives/drv-me/items/f9/content").mock(
        return_value=httpx.Response(200, content=b"a,b\n1,2\n")
    )
    assert c.pull("f9", tmp_path).path.name == "data.csv"


@respx.mock
def test_pull_by_link_uses_shares_api(tmp_path: Path) -> None:
    c = _drive_ready()
    link = "https://contoso.sharepoint.com/:x:/s/MEAL/EbCdE?e=abc"
    encoded = "u!" + base64.urlsafe_b64encode(link.encode()).decode().rstrip("=")
    item = _item("ext.csv", "f-x")
    item["parentReference"] = {"driveId": "drv-other"}
    respx.get(f"{GRAPH}/shares/{encoded}/driveItem").mock(
        return_value=httpx.Response(200, json=item)
    )
    respx.get(f"{GRAPH}/drives/drv-other/items/f-x/content").mock(
        return_value=httpx.Response(200, content=b"x")
    )
    result = c.pull(link, tmp_path)
    assert result.path.read_bytes() == b"x" and result.form.name == "ext.csv"


@respx.mock
def test_pull_bad_link_is_friendly(tmp_path: Path) -> None:
    c = _drive_ready()
    respx.get(url__startswith=f"{GRAPH}/shares/").mock(return_value=httpx.Response(404))
    with pytest.raises(ConnectorError, match="did not resolve"):
        c.pull("https://contoso.sharepoint.com/:x:/s/MEAL/broken", tmp_path)


@respx.mock
def test_pull_link_to_folder_rejected(tmp_path: Path) -> None:
    c = _drive_ready()
    respx.get(url__startswith=f"{GRAPH}/shares/").mock(
        return_value=httpx.Response(200, json=_item("Reports", "fld-9", folder=True))
    )
    with pytest.raises(ConnectorError, match="folder, not a file"):
        c.pull("https://contoso.sharepoint.com/:f:/s/MEAL/folderlink", tmp_path)


@respx.mock
def test_pull_non_tabular_rejected(tmp_path: Path) -> None:
    c = _drive_ready()
    respx.get(f"{GRAPH}/drives/drv-me/root:/report.docx").mock(
        return_value=httpx.Response(200, json=_item("report.docx", "f-d"))
    )
    with pytest.raises(ConnectorError, match=".xlsx, .csv"):
        c.pull("report.docx", tmp_path)
    assert not list(tmp_path.iterdir())


@respx.mock
def test_pull_missing_path_lists_available(tmp_path: Path) -> None:
    c = _drive_ready()
    respx.get(f"{GRAPH}/drives/drv-me/root:/missing.xlsx").mock(
        return_value=httpx.Response(404)
    )
    respx.get(f"{GRAPH}/drives/drv-me/root/children").mock(
        return_value=httpx.Response(200, json={"value": [_item("real.xlsx", "f-1")]})
    )
    with pytest.raises(ConnectorError, match="real.xlsx"):
        c.pull("missing.xlsx", tmp_path)


@respx.mock
def test_pull_path_prefixed_with_pinned_folder(tmp_path: Path) -> None:
    respx.get(f"{GRAPH}/me/drive").mock(return_value=httpx.Response(200, json={"id": "drv-me"}))
    route = respx.get(f"{GRAPH}/drives/drv-me/root:/5W/june.csv").mock(
        return_value=httpx.Response(200, json=_item("june.csv", "f-1"))
    )
    respx.get(f"{GRAPH}/drives/drv-me/items/f-1/content").mock(
        return_value=httpx.Response(200, content=b"x")
    )
    conn = Connection(name="od", kind="sharepoint", base_url="onedrive",
                      tenant=TENANT, client_id="cid", folder="5W")
    SharePointConnector(conn, _token_json()).pull("june.csv", tmp_path)
    assert route.called
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_sharepoint.py -q`
Expected: FAIL — `AttributeError: ... no attribute 'pull'`.

- [ ] **Step 3: Implement**

Add to `SharePointConnector`:

```python
    def _resolve(self, ref: str) -> tuple[dict, str]:
        """Return (item metadata, content URL) for a path, item id, or https link."""
        if ref.startswith("https://"):
            encoded = "u!" + base64.urlsafe_b64encode(ref.encode()).decode().rstrip("=")
            try:
                item = self._get(f"{GRAPH}/shares/{encoded}/driveItem")
            except NotFoundError:
                raise ConnectorError(
                    "The link did not resolve to a file — check that you have "
                    "access and that it points to a file, not a folder."
                ) from None
            if "folder" in item:
                raise ConnectorError("The link points to a folder, not a file.")
            drive_id = item["parentReference"]["driveId"]
            return item, f"{GRAPH}/drives/{drive_id}/items/{item['id']}/content"
        drive = self._drive()
        if "/" in ref or ref.lower().endswith(TABULAR_EXTENSIONS):
            full = f"{self.conn.folder}/{ref}" if self.conn.folder else ref
            url = f"{drive}/root:/{quote(full)}"
        else:
            url = f"{drive}/items/{ref}"
        try:
            item = self._get(url)
        except NotFoundError:
            known = ", ".join(f.name for f in self.list_forms()) or "(none)"
            raise ConnectorError(f"File {ref!r} not found. Available: {known}") from None
        return item, f"{drive}/items/{item['id']}/content"

    def pull(self, form: str, dest_dir: Path) -> PullResult:
        item, content_url = self._resolve(form)
        name = str(item["name"])
        ext = Path(name).suffix.lower()
        if ext not in TABULAR_EXTENSIONS:
            raise ConnectorError(
                f"{name!r} is not a tabular file; supported: .xlsx, .csv"
            )
        content = self._download(content_url)
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / f"{safe_filename(Path(name).stem)}{ext}"
        path.write_bytes(content)
        remote = RemoteForm(uid=str(item["id"]), name=name, submissions=None)
        return PullResult(path=path, rows=0, form=remote, bytes=len(content))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_sharepoint.py -q; .\.venv\Scripts\python.exe -m ruff check .`
Expected: PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/haa/connectors/sharepoint.py tests/test_sharepoint.py
git commit -m "feat: verbatim file pull by path, item id, or shared link"
```

---

### Task 9: credentials.py — make_connector dispatch with rotation persistence

**Files:**
- Modify: `src/haa/connectors/credentials.py`
- Test: `tests/test_credentials.py`

**Interfaces:**
- Consumes: `SharePointConnector` (Task 6), `Connection` with sharepoint fields (Task 5), module-level `keyring` and `KEYRING_SERVICE` already imported in credentials.py.
- Produces: `make_connector(conn, token)` returns `SharePointConnector` for kind `"sharepoint"`, passing `on_tokens_updated` that writes the JSON back to keyring under the profile name (best-effort: a failing keyring must not break the pull).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_credentials.py` (extend imports with `make_connector`; `SP` constant and `mem_keyring` fixture are already in this file):

```python
def test_make_connector_sharepoint(tmp_path: Path, mem_keyring) -> None:
    import time

    from haa.connectors.msauth import TokenSet
    from haa.connectors.sharepoint import SharePointConnector

    save_connection(tmp_path, SP, TokenSet("at", "rt", time.time() + 3600).to_json())
    conn, token = load_connection(tmp_path, "imc-sp")
    connector = make_connector(conn, token)
    assert isinstance(connector, SharePointConnector)
    connector._on_tokens_updated('{"rotated": "tokens"}')
    assert mem_keyring.store[("haa", "imc-sp")] == '{"rotated": "tokens"}'


def test_make_connector_sharepoint_survives_keyring_failure(
    tmp_path: Path, mem_keyring, monkeypatch
) -> None:
    import time

    import keyring

    from haa.connectors.msauth import TokenSet

    save_connection(tmp_path, SP, TokenSet("at", "rt", time.time() + 3600).to_json())
    conn, token = load_connection(tmp_path, "imc-sp")
    connector = make_connector(conn, token)
    monkeypatch.setattr(
        keyring, "set_password",
        lambda *a: (_ for _ in ()).throw(RuntimeError("vault locked")),
    )
    connector._on_tokens_updated('{"rotated": "tokens"}')  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_credentials.py -q`
Expected: FAIL — `make_connector` currently returns `OnaConnector` for anything non-kobo (`AssertionError` on isinstance).

- [ ] **Step 3: Implement**

Replace `make_connector` in `src/haa/connectors/credentials.py`:

```python
def make_connector(conn: Connection, token: str):
    if conn.kind == "kobo":
        from haa.connectors.kobo import KoboConnector

        return KoboConnector(conn.base_url, token)
    if conn.kind == "ona":
        from haa.connectors.ona import OnaConnector

        return OnaConnector(conn.base_url, token)
    from haa.connectors.sharepoint import SharePointConnector

    def _persist(tokens_json: str) -> None:
        try:
            keyring.set_password(KEYRING_SERVICE, conn.name, tokens_json)
        except Exception:  # no backend / locked vault — rotation then lives in memory only
            pass

    return SharePointConnector(conn, token, on_tokens_updated=_persist)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_credentials.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/haa/connectors/credentials.py tests/test_credentials.py
git commit -m "feat: make_connector builds SharePointConnector with keyring rotation persist"
```

---

### Task 10: sourcetools — sharepoint wording, byte summary, msauth_refresh telemetry

**Files:**
- Modify: `src/haa/core/tools/sourcetools.py`
- Test: `tests/test_sourcetools.py`

**Interfaces:**
- Consumes: `refresh_events` / `list_truncated` connector attributes (Task 6/7), `PullResult.bytes` (Task 1).
- Produces: `list_remote_forms` says "Files on …" with `- <relative path>` lines and a truncation note for sharepoint connections; `pull_form` reports "(N KB)" instead of submissions when `result.bytes` is set and logs `bytes` in the `pull` event; both log a `msauth_refresh` telemetry event (`connection`, `results: list[bool]`) whenever the connector performed refreshes, even when the operation then failed. `list_connections` hint becomes `haa connect kobo|ona|sharepoint`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sourcetools.py`:

```python
class StubSharePoint:
    def __init__(self) -> None:
        self.refresh_events = [True]
        self.list_truncated = True

    def list_forms(self):
        return [RemoteForm(uid="i1", name="2026/june.xlsx", submissions=None)]

    def pull(self, form: str, dest_dir: Path) -> PullResult:
        path = dest_dir / "june.xlsx"
        path.write_bytes(b"x" * 2048)
        return PullResult(
            path=path, rows=0,
            form=RemoteForm("i1", "2026/june.xlsx", None), bytes=2048,
        )

    def close(self) -> None:
        pass


@pytest.fixture()
def sp_box(tmp_path: Path, monkeypatch) -> SourceToolbox:
    cfg = load_config(tmp_path)
    telemetry = SessionTelemetry(cfg.logs_dir / "t.jsonl")
    import haa.core.tools.sourcetools as st

    conn = Connection("imc-sp", "sharepoint", "https://x.sharepoint.com/sites/M",
                      tenant="t", client_id="c")
    monkeypatch.setattr(st, "load_connection", lambda ws, name: (conn, "tok"))
    monkeypatch.setattr(st, "make_connector", lambda conn, token: StubSharePoint())
    return SourceToolbox(cfg, telemetry)


def test_sharepoint_listing_wording_and_truncation(sp_box: SourceToolbox) -> None:
    out = sp_box.list_remote_forms("imc-sp")
    assert "Files on 'imc-sp'" in out and "- 2026/june.xlsx" in out
    assert "first 200" in out
    assert "submissions" not in out


def test_sharepoint_pull_reports_size_not_rows(sp_box: SourceToolbox) -> None:
    out = sp_box.pull_form("imc-sp", "2026/june.xlsx")
    assert "june.xlsx" in out and "2 KB" in out
    assert "submissions" not in out


def test_msauth_refresh_telemetry_logged(sp_box: SourceToolbox) -> None:
    sp_box.list_remote_forms("imc-sp")
    sp_box.pull_form("imc-sp", "2026/june.xlsx")
    log = sp_box.telemetry.path.read_text(encoding="utf-8")
    assert log.count('"kind": "msauth_refresh"') == 2


def test_connections_hint_mentions_sharepoint(tmp_path: Path, monkeypatch) -> None:
    cfg = load_config(tmp_path)
    import haa.core.tools.sourcetools as st

    monkeypatch.setattr(st, "list_connections", lambda ws: [])
    box = SourceToolbox(cfg, SessionTelemetry(cfg.logs_dir / "t.jsonl"))
    assert "sharepoint" in box.list_connections()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_sourcetools.py -q`
Expected: FAIL — "Forms on" wording, no size summary, no msauth_refresh events.

- [ ] **Step 3: Implement**

In `src/haa/core/tools/sourcetools.py`, replace `list_remote_forms` and `pull_form`, add `_log_refreshes`, and update the `list_connections` hint string to `"No connections configured. Set one up with: haa connect kobo|ona|sharepoint"`:

```python
    def _log_refreshes(self, connection: str, connector: object) -> None:
        events = getattr(connector, "refresh_events", [])
        if events:
            self.telemetry.log("msauth_refresh", connection=connection, results=list(events))

    def list_remote_forms(self, connection: str) -> str:
        try:
            conn, token = load_connection(self.config.workspace, connection)
            connector = make_connector(conn, token)
            try:
                forms = connector.list_forms()
            finally:
                self._log_refreshes(connection, connector)
                connector.close()
        except (ConnectorError, CredentialsError) as exc:
            return str(exc)
        noun = "files" if conn.kind == "sharepoint" else "forms"
        if not forms:
            return f"No {noun} found on {connection!r}."
        if conn.kind == "sharepoint":
            note = (
                "\n(showing the first 200 tabular files — narrow --folder to see others)"
                if getattr(connector, "list_truncated", False)
                else ""
            )
            return f"Files on {connection!r}:\n" + "\n".join(
                f"- {f.name}" for f in forms
            ) + note
        return f"Forms on {connection!r}:\n" + "\n".join(
            f"- {f.name} (uid {f.uid}, "
            f"{f.submissions if f.submissions is not None else '?'} submissions)"
            for f in forms
        )

    def pull_form(self, connection: str, form: str) -> str:
        started = time.monotonic()
        try:
            conn, token = load_connection(self.config.workspace, connection)
            connector = make_connector(conn, token)
            try:
                result = connector.pull(form, self.config.data_dir)
            finally:
                self._log_refreshes(connection, connector)
                connector.close()
        except (ConnectorError, CredentialsError) as exc:
            return str(exc)
        seconds = round(time.monotonic() - started, 1)
        payload = {"connection": connection, "form": result.form.name,
                   "rows": result.rows, "seconds": seconds}
        if result.bytes is not None:
            payload["bytes"] = result.bytes
        self.telemetry.log("pull", **payload)
        if result.bytes is not None:
            size_kb = max(1, round(result.bytes / 1024))
            return (
                f"Pulled {result.form.name!r} ({size_kb} KB) into {result.path.name} "
                f"({seconds}s). "
                "The dataset is now available to profile_dataset / run_analysis."
            )
        return (
            f"Pulled {result.rows} submissions of {result.form.name!r} "
            f"into {result.path.name} ({seconds}s). "
            "The dataset is now available to profile_dataset / run_analysis."
        )
```

Note the deliberate shape: `connector` is created outside `with` so `_log_refreshes` runs in `finally` even when the operation raised (the friendly message is still returned by the except branch).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_sourcetools.py -q`
Expected: PASS, including all pre-existing kobo-stub tests.

- [ ] **Step 5: Commit**

```bash
git add src/haa/core/tools/sourcetools.py tests/test_sourcetools.py
git commit -m "feat: sharepoint-aware source tools - file wording, size summary, refresh telemetry"
```

---

### Task 11: CLI — connect sharepoint via device flow

**Files:**
- Modify: `src/haa/cli/app.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `start_device_flow`, `poll_for_token`, `DEFAULT_CLIENT_ID` (msauth), `Connection`/`save_connection` (Task 5).
- Produces: parser accepts `connect sharepoint --site --tenant --client-id --folder`; `run_connect_sharepoint(workspace: Path, name: str, site: str, tenant: str, client_id: str, folder: str | None, say: Callable[[str], None]) -> str` runs the flow and saves the profile with `TokenSet.to_json()` as the token; `main()` wires it with interactive prompts for missing name/site/tenant, defaults client_id to `DEFAULT_CLIENT_ID`, prints friendly `ConnectorError` in red and returns 1.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def test_parser_connect_sharepoint() -> None:
    a = build_parser().parse_args([
        "connect", "sharepoint",
        "--site", "https://x.sharepoint.com/sites/M",
        "--tenant", "x.onmicrosoft.com",
        "--client-id", "cid-1",
        "--folder", "Shared Documents/5W",
    ])
    assert a.kind == "sharepoint" and a.site == "https://x.sharepoint.com/sites/M"
    assert a.tenant == "x.onmicrosoft.com" and a.client_id == "cid-1"
    assert a.folder == "Shared Documents/5W"


def test_run_connect_sharepoint_saves_tokens(tmp_path: Path, monkeypatch) -> None:
    import time

    import keyring

    from haa.cli.app import run_connect_sharepoint
    from haa.connectors.msauth import DeviceFlow, TokenSet

    store: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(keyring, "set_password", lambda s, u, p: store.__setitem__((s, u), p))
    flow = DeviceFlow(tenant="t", client_id="c", device_code="dc", user_code="ABC123",
                      verification_uri="https://microsoft.com/devicelogin", interval=1,
                      expires_at=time.time() + 60)
    monkeypatch.setattr("haa.connectors.msauth.start_device_flow", lambda t, c: flow)
    monkeypatch.setattr(
        "haa.connectors.msauth.poll_for_token",
        lambda f: TokenSet("access-SECRET1", "refresh-SECRET2", time.time() + 3600),
    )
    said: list[str] = []
    msg = run_connect_sharepoint(
        tmp_path, "imc-sp", "onedrive", "t", "c", None, said.append
    )
    assert "imc-sp" in msg
    assert any("ABC123" in line for line in said)
    assert "refresh-SECRET2" in store[("haa", "imc-sp")]
    toml_text = (tmp_path / "connections.toml").read_text(encoding="utf-8")
    assert "sharepoint" in toml_text and "SECRET" not in toml_text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_cli.py -q`
Expected: FAIL — argparse rejects `sharepoint` choice; `ImportError` for `run_connect_sharepoint`.

- [ ] **Step 3: Implement**

In `src/haa/cli/app.py`:

1. Parser — replace the connect subparser block:

```python
    connect = sub.add_parser("connect", help="Configure a data-source connection")
    connect.add_argument("kind", choices=["kobo", "ona", "sharepoint"])
    connect.add_argument("--workspace", type=Path, default=Path("workspace"))
    connect.add_argument("--site", default=None,
                         help="SharePoint site URL, or 'onedrive' for the personal drive")
    connect.add_argument("--tenant", default=None,
                         help="Entra tenant, e.g. contoso.onmicrosoft.com")
    connect.add_argument("--client-id", dest="client_id", default=None)
    connect.add_argument("--folder", default=None,
                         help="Folder to pin, e.g. 'Shared Documents/5W'")
```

2. New function next to `run_connect` (import `Callable` from `typing` at module top):

```python
def run_connect_sharepoint(
    workspace: Path,
    name: str,
    site: str,
    tenant: str,
    client_id: str,
    folder: str | None,
    say: Callable[[str], None],
) -> str:
    from haa.connectors import msauth
    from haa.connectors.credentials import Connection, save_connection

    flow = msauth.start_device_flow(tenant, client_id)
    say(f"Open {flow.verification_uri} and enter the code: {flow.user_code}")
    say("Waiting for the sign-in to finish (Ctrl+C to abort)...")
    tokens = msauth.poll_for_token(flow)
    workspace.mkdir(parents=True, exist_ok=True)
    save_connection(
        workspace,
        Connection(name=name, kind="sharepoint", base_url=site,
                   tenant=tenant, client_id=client_id, folder=folder),
        tokens.to_json(),
    )
    return f"Connection {name!r} (sharepoint) saved. Tokens stored in the OS credential store."
```

(The `from haa.connectors import msauth` + attribute-access form is what makes the test's `monkeypatch.setattr("haa.connectors.msauth.start_device_flow", ...)` effective.)

3. In `main()`, at the top of the existing `if args.command == "connect":` branch, insert the sharepoint path before the token prompt:

```python
    if args.command == "connect":
        name = input(f"Profile name [{args.kind}]: ").strip() or args.kind
        if args.kind == "sharepoint":
            from haa.connectors.base import ConnectorError
            from haa.connectors.msauth import DEFAULT_CLIENT_ID

            site = args.site or input("Site URL (or 'onedrive'): ").strip()
            tenant = args.tenant or input("Tenant (e.g. contoso.onmicrosoft.com): ").strip()
            client_id = args.client_id or DEFAULT_CLIENT_ID
            try:
                console.print(run_connect_sharepoint(
                    args.workspace, name, site, tenant, client_id,
                    args.folder, console.print,
                ))
            except KeyboardInterrupt:
                console.print("[red]Sign-in aborted.[/red]")
                return 1
            except ConnectorError as exc:
                console.print(f"[red]{exc}[/red]")
                return 1
            return 0
        default_url = DEFAULT_URLS[args.kind]
        ...  # existing kobo/ona prompt flow unchanged
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_cli.py -q; .\.venv\Scripts\python.exe -m ruff check .`
Expected: PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/haa/cli/app.py tests/test_cli.py
git commit -m "feat: haa connect sharepoint with interactive device-code sign-in"
```

---

### Task 12: Live probe test (remote marker)

**Files:**
- Modify: `tests/test_remote_live.py`

**Interfaces:**
- Consumes: `load_connection`/`make_connector` (Task 9), a real profile created manually with `haa connect sharepoint`.
- Produces: `test_sharepoint_live_roundtrip` — runs only when `HAA_TEST_SP_WORKSPACE` (path to a workspace with a saved profile) and `HAA_TEST_SP_PROFILE` are set; exercises list + pull + byte-size match against the real tenant. This test IS the spec's feasibility probe; its outcome (pass, or a consent-policy AuthError) is recorded in the spec afterwards by the user/reviewer.

- [ ] **Step 1: Write the test** (no offline failure cycle — it skips without env; verify it skips)

Append to `tests/test_remote_live.py`:

```python
def test_sharepoint_live_roundtrip(tmp_path: Path) -> None:
    workspace, profile = _env("HAA_TEST_SP_WORKSPACE", "HAA_TEST_SP_PROFILE")
    from haa.connectors.credentials import load_connection, make_connector

    conn, token = load_connection(Path(workspace), profile)
    with make_connector(conn, token) as connector:
        files = connector.list_forms()
        assert isinstance(files, list)
        if not files:
            pytest.skip("no tabular files visible to this profile")
        result = connector.pull(files[0].name, tmp_path)
    assert result.path.exists()
    assert result.path.stat().st_size == result.bytes > 0
```

- [ ] **Step 2: Verify it skips cleanly offline**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_remote_live.py -m remote -v`
Expected: all three remote tests SKIPPED with "env not set" reasons.

- [ ] **Step 3: Commit**

```bash
git add tests/test_remote_live.py
git commit -m "test: live SharePoint roundtrip probe behind remote marker"
```

---

### Task 13: README, full verification, wrap-up

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update README**

In the connectors/sources section of `README.md` (near the existing `haa connect` description), add:

```markdown
### SharePoint / OneDrive

    haa connect sharepoint --site https://<tenant>.sharepoint.com/sites/<name> \
        --tenant <tenant>.onmicrosoft.com --folder "Shared Documents/5W"

Sign-in is the standard Microsoft device-code flow: the CLI prints a code, you
enter it at microsoft.com/devicelogin and log in with your normal work account
(password and MFA stay on Microsoft's page — the tool only receives tokens,
stored in the OS credential store). Use `--site onedrive` for the personal
drive. Pulls download `.xlsx`/`.csv` files byte-for-byte into
`workspace/data/`; a pasted SharePoint file link also works as the pull target.
If the tenant blocks the default client id, pass your own with `--client-id`
(ask IT/HQ to approve one — read-only Files/Sites scopes).
```

In the Roadmap section: mark the SharePoint connector as done; remaining — reporting agent (5W/MEAL), Power BI engineer, web UI.

- [ ] **Step 2: Full offline suite and lint**

Run: `.\.venv\Scripts\python.exe -m pytest -q; .\.venv\Scripts\python.exe -m ruff check .`
Expected: ALL tests pass (≈265+), ruff clean. Fix anything that fails before committing.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: SharePoint connector usage and roadmap update"
```

- [ ] **Step 4: Manual live probe (user-run, outside CI)**

The user runs, in their own terminal (API key/tenant credentials never enter agent shells):

```
.\.venv\Scripts\python.exe -m haa.cli.app connect sharepoint  (or: haa connect sharepoint)
$env:HAA_TEST_SP_WORKSPACE = "<workspace path>"; $env:HAA_TEST_SP_PROFILE = "<profile>"
.\.venv\Scripts\python.exe -m pytest -m remote -k sharepoint -v
```

Record the outcome (works / blocked by tenant policy + which AADSTS code) in `docs/superpowers/specs/2026-09-08-sharepoint-connector-design.md` as a dated postscript. Both outcomes close the slice's feasibility question (spec §1).
