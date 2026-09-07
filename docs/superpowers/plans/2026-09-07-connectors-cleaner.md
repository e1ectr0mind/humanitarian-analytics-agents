# Connectors + Cleaner Implementation Plan (slice 2a)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pull survey data from KoboToolbox/Ona into the workspace (CLI + chat), with per-user credentials in Windows Credential Manager, and add a second agent role — a data cleaner that produces a clean copy + cleaning report without touching raw files.

**Architecture:** Plain `httpx` connector clients behind an `ODKConnector` protocol (`connectors/`), credential profiles in `workspace/connections.toml` + tokens in `keyring`, a second in-process MCP server `sources` (summaries only — raw rows never reach the LLM), a `cleaner` AgentDefinition registered beside the analyst, `include_pii=True` flag in the sandbox loader (audited via a `pii_access` telemetry event), and CLI subcommands `connect`/`connections`/`pull`.

**Tech Stack:** existing haa core (subproject 1) + `httpx`, `keyring`, `tomli-w`; dev: `respx` (HTTP mocking).

**Spec:** `docs/superpowers/specs/2026-09-07-connectors-cleaner-design.md` (amends core spec §5 with the include_pii flag)

## Global Constraints

- Everything from subproject 1 still binds: Python 3.11+, `src/haa/`, English code/comments, ruff (E,F,I,UP,B; line 100), TDD, commit per task, run via `uv run ...` (`python -m uv run ...` on this machine).
- **Tokens never touch files or git**: keyring service `"haa"`, account = profile name; env fallback `HAA_TOKEN_<PROFILE>` (upper-cased, non-alnum → `_`). `workspace/connections.toml` holds metadata only (name, kind, base_url).
- **Unique record IDs (spec §2)**: pulled files keep `_uuid`/`_id` when present AND unique; otherwise gain `_haa_row_id` (sha1 of row content, 16 hex chars, `-N` suffix for exact duplicates).
- **PII boundary**: pull summaries to LLM contain file name / form / row count only. `load_dataset(name, include_pii=True)` is legal (spec §3 amendment) but every use is logged as telemetry kind `pii_access`.
- pytest markers: default addopts becomes `-m 'not api and not remote'`; new marker `remote` for live Kobo/Ona tests (manual, needs env `HAA_TEST_KOBO_URL`/`HAA_TEST_KOBO_TOKEN`, `HAA_TEST_ONA_URL`/`HAA_TEST_ONA_TOKEN`).
- Raw dataset files are never modified by the cleaner; clean copy is `<dataset>_clean.xlsx` in data_dir, report is `<dataset>_cleaning_report.md` in reports_dir.
- Suite baseline before this plan: 89 offline tests green.

---

## File Structure

```
src/haa/
  config.py                 # MODIFY: + cleaner_model field, + reports_dir property
  connectors/
    __init__.py             # NEW (empty)
    base.py                 # NEW: RemoteForm, PullResult, ConnectorError, ODKConnector,
                            #      ensure_row_ids, rows_to_dataframe, write_pull, get_json
    kobo.py                 # NEW: KoboConnector (Kobo API v2)
    ona.py                  # NEW: OnaConnector (Ona API v1)
    credentials.py          # NEW: Connection, save/load/list/delete, make_connector
  core/
    sandbox/executor.py     # MODIFY: include_pii param in prelude load_dataset; REPORTS_DIR
    tools/datatools.py      # MODIFY: mtime-based cache invalidation; pii_access logging
    tools/sourcetools.py    # NEW: SourceToolbox + build_sources_server + SOURCE_TOOL_NAMES
    agents/cleaner.py       # NEW: CLEANER_PROMPT + build_cleaner
    agents/registry.py      # MODIFY: + cleaner
    agents/orchestrator.py  # MODIFY: routing for pull & clean requests
    session.py              # MODIFY: sources server wiring, allowed_tools
  cli/app.py                # MODIFY: connect / connections / pull subcommands
pyproject.toml              # MODIFY: deps + markers + addopts
tests/
  test_connectors_base.py   # NEW
  test_credentials.py       # NEW
  test_kobo.py              # NEW (respx)
  test_ona.py               # NEW (respx)
  test_sourcetools.py       # NEW
  test_cleaner_agent.py     # NEW
  test_executor.py          # MODIFY: include_pii tests
  test_datatools.py         # MODIFY: mtime invalidation, pii_access
  test_session.py           # MODIFY: build_options assertions
  test_cli.py               # MODIFY: new subcommands
  test_remote_live.py       # NEW (marker remote)
  test_smoke_api.py         # MODIFY: cleaner eval
README.md                   # MODIFY: connect/pull quickstart, roadmap tick
```

---

### Task 1: Dependencies, markers, config extension

**Files:**
- Modify: `pyproject.toml`, `src/haa/config.py`
- Test: `tests/test_config.py` (append)

**Interfaces:**
- Consumes: existing `HaaConfig`.
- Produces: deps `httpx>=0.27`, `keyring>=25.0`, `tomli-w>=1.0`; dev dep `respx>=0.21`; markers `api` + `remote`; addopts `-m 'not api and not remote'`. `HaaConfig.cleaner_model: str = "claude-opus-5"` (tunable via config.toml automatically — `_TUNABLE` derives from fields); property `reports_dir -> workspace/"reports"`, created by `load_config`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_config.py`:

```python
def test_cleaner_model_default_and_override(tmp_path: Path) -> None:
    assert load_config(tmp_path).cleaner_model == "claude-opus-5"
    (tmp_path / "config.toml").write_text('cleaner_model = "claude-sonnet-5"', encoding="utf-8")
    assert load_config(tmp_path).cleaner_model == "claude-sonnet-5"


def test_reports_dir_created(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    assert cfg.reports_dir == tmp_path.resolve() / "reports"
    assert cfg.reports_dir.is_dir()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_config.py -v` — Expected: 2 FAIL (`cleaner_model`/`reports_dir` missing).

- [ ] **Step 3: Implement**

`pyproject.toml`: add to `dependencies`: `"httpx>=0.27"`, `"keyring>=25.0"`, `"tomli-w>=1.0"`; to dev group: `"respx>=0.21"`. Replace pytest options:

```toml
addopts = "-m 'not api and not remote'"
markers = [
    "api: tests that call the Claude API (cost real money; run manually)",
    "remote: tests that call live Kobo/Ona servers (need HAA_TEST_* env; run manually)",
]
```

`src/haa/config.py`: add field `cleaner_model: str = "claude-opus-5"` after `analyst_model`; add property:

```python
    @property
    def reports_dir(self) -> Path:
        return self.workspace / "reports"
```

and add `cfg.reports_dir` to the directory-creation loop in `load_config`.

- [ ] **Step 4: Verify**

Run: `uv sync --all-groups && uv run pytest tests/test_config.py -v` — all PASS; `uv run pytest -q` — 91 passed; `uv run ruff check .` clean.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/haa/config.py tests/test_config.py uv.lock
git commit -m "feat: connector deps, remote marker, cleaner_model and reports_dir config"
```

---

### Task 2: Connector base (contract, row IDs, download plumbing)

**Files:**
- Create: `src/haa/connectors/__init__.py` (empty), `src/haa/connectors/base.py`
- Test: `tests/test_connectors_base.py`

**Interfaces:**
- Produces (all consumed by Tasks 3–6, 10):
  - `RemoteForm` frozen dataclass: `uid: str`, `name: str`, `submissions: int | None = None`
  - `PullResult` frozen dataclass: `path: Path`, `rows: int`, `form: RemoteForm`
  - `ConnectorError(RuntimeError)`
  - `ODKConnector` Protocol: `list_forms(self) -> list[RemoteForm]`; `pull(self, form: str, dest_dir: Path) -> PullResult`
  - `ID_CANDIDATES = ("_uuid", "_id")`; `ensure_row_ids(df: pd.DataFrame) -> pd.DataFrame` (returns df unchanged when a candidate column exists, is fully non-null AND unique; otherwise returns a copy with `_haa_row_id`)
  - `rows_to_dataframe(rows: list[dict]) -> pd.DataFrame` (empty list → empty df)
  - `safe_filename(name: str) -> str` (lowercase, `[^\w-]+` → `_`, stripped)
  - `write_pull(df, dest_dir: Path, name: str) -> Path` (ensure_row_ids + to_excel, returns path)
  - `get_json(client: httpx.Client, url: str, *, params: dict | None = None) -> object` — 3 attempts, 0.5s/1.0s backoff, 401/403 → immediate `ConnectorError("Token rejected...")`, other failures after retries → `ConnectorError`

- [ ] **Step 1: Write the failing tests**

`tests/test_connectors_base.py`:

```python
from pathlib import Path

import httpx
import pandas as pd
import pytest
import respx

from haa.connectors.base import (
    ConnectorError,
    ensure_row_ids,
    get_json,
    rows_to_dataframe,
    safe_filename,
    write_pull,
)


def test_unique_uuid_left_untouched() -> None:
    df = pd.DataFrame({"_uuid": ["a", "b", "c"], "v": [1, 2, 3]})
    out = ensure_row_ids(df)
    assert "_haa_row_id" not in out.columns
    assert out is df  # no copy when nothing to do


def test_missing_id_gets_haa_row_id() -> None:
    df = pd.DataFrame({"v": [1, 2, 3]})
    out = ensure_row_ids(df)
    assert out["_haa_row_id"].is_unique
    assert len(out["_haa_row_id"].iloc[0]) == 16


def test_duplicate_uuid_triggers_haa_row_id() -> None:
    df = pd.DataFrame({"_uuid": ["a", "a", "b"], "v": [1, 1, 2]})
    out = ensure_row_ids(df)
    assert out["_haa_row_id"].is_unique


def test_identical_rows_get_suffixed_ids() -> None:
    df = pd.DataFrame({"v": [1, 1, 1]})
    ids = ensure_row_ids(df)["_haa_row_id"].tolist()
    assert len(set(ids)) == 3
    assert ids[0] == ids[1].rsplit("-", 1)[0]  # same content hash, -1 suffix


def test_row_ids_deterministic() -> None:
    df = pd.DataFrame({"v": [1, 2], "w": ["x", "y"]})
    a = ensure_row_ids(df.copy())["_haa_row_id"].tolist()
    b = ensure_row_ids(df.copy())["_haa_row_id"].tolist()
    assert a == b


def test_rows_to_dataframe_empty() -> None:
    assert rows_to_dataframe([]).empty


def test_safe_filename() -> None:
    assert safe_filename("Household Survey (v2)!") == "household_survey_v2"


def test_write_pull_creates_xlsx(tmp_path: Path) -> None:
    df = pd.DataFrame({"v": [1, 2]})
    path = write_pull(df, tmp_path, "My Form")
    assert path == tmp_path / "my_form.xlsx"
    back = pd.read_excel(path)
    assert "_haa_row_id" in back.columns and len(back) == 2


@respx.mock
def test_get_json_retries_then_succeeds() -> None:
    route = respx.get("https://x.test/api").mock(
        side_effect=[httpx.ConnectError("boom"), httpx.Response(200, json={"ok": 1})]
    )
    with httpx.Client() as client:
        assert get_json(client, "https://x.test/api") == {"ok": 1}
    assert route.call_count == 2


@respx.mock
def test_get_json_auth_error_no_retry() -> None:
    route = respx.get("https://x.test/api").mock(return_value=httpx.Response(401))
    with httpx.Client() as client, pytest.raises(ConnectorError, match="[Tt]oken"):
        get_json(client, "https://x.test/api")
    assert route.call_count == 1


@respx.mock
def test_get_json_gives_up_with_friendly_error() -> None:
    respx.get("https://x.test/api").mock(side_effect=httpx.ConnectError("down"))
    with httpx.Client() as client, pytest.raises(ConnectorError, match="3 attempts"):
        get_json(client, "https://x.test/api")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_connectors_base.py -v` — Expected: ImportError.

- [ ] **Step 3: Implement `src/haa/connectors/base.py`**

```python
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
    hashes = df.astype(str).apply(
        lambda row: hashlib.sha1("\x1f".join(row.values).encode()).hexdigest()[:16],
        axis=1,
    ) if len(df) else pd.Series([], dtype=str)
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
                raise ConnectorError(
                    f"Token rejected by the server (HTTP {resp.status_code}). "
                    "Refresh it with: haa connect <kind>"
                )
            resp.raise_for_status()
            return resp.json()
        except ConnectorError:
            raise
        except Exception as exc:  # network / 5xx / JSON errors — retry
            last = exc
            if attempt < _RETRIES - 1:
                time.sleep(0.5 * (attempt + 1))
    raise ConnectorError(f"Request failed after {_RETRIES} attempts: {last}") from last
```

Plus empty `src/haa/connectors/__init__.py`.

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/test_connectors_base.py -v` — all PASS; ruff clean. (Retry test sleeps 0.5s — acceptable.)

- [ ] **Step 5: Commit**

```bash
git add src/haa/connectors tests/test_connectors_base.py
git commit -m "feat: ODK connector contract with explicit row IDs and retrying HTTP"
```

---

### Task 3: Credential profiles (keyring + env fallback)

**Files:**
- Create: `src/haa/connectors/credentials.py`
- Test: `tests/test_credentials.py`

**Interfaces:**
- Produces: `Connection` frozen dataclass (`name: str`, `kind: str` in {"kobo","ona"}, `base_url: str`); `CredentialsError(ValueError)`; `save_connection(workspace: Path, conn: Connection, token: str) -> None`; `load_connection(workspace: Path, name: str) -> tuple[Connection, str]` (token resolution: keyring → env `HAA_TOKEN_<NAME>` → CredentialsError); `list_connections(workspace: Path) -> list[Connection]`; `delete_connection(workspace: Path, name: str) -> None`; `env_var_name(profile: str) -> str`; `make_connector(conn: Connection, token: str) -> ODKConnector` (imports Kobo/Ona lazily — Tasks 4–5). Metadata file: `workspace/connections.toml` (`tomli_w` to write, `tomllib` to read). `KEYRING_SERVICE = "haa"`.

- [ ] **Step 1: Write the failing tests**

`tests/test_credentials.py`:

```python
from pathlib import Path

import keyring
import pytest

from haa.connectors.credentials import (
    Connection,
    CredentialsError,
    delete_connection,
    env_var_name,
    list_connections,
    load_connection,
    save_connection,
)


class MemoryKeyring:
    """Plain in-memory stand-in; keyring's module functions are monkeypatched to it."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def get_password(self, service, username):
        return self.store.get((service, username))

    def delete_password(self, service, username):
        self.store.pop((service, username), None)


@pytest.fixture(autouse=True)
def mem_keyring(monkeypatch):
    backend = MemoryKeyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    monkeypatch.setattr(keyring, "set_password", backend.set_password)
    monkeypatch.setattr(keyring, "get_password", backend.get_password)
    monkeypatch.setattr(keyring, "delete_password", backend.delete_password)
    return backend


CONN = Connection(name="imc-kobo", kind="kobo", base_url="https://kf.kobotoolbox.org")


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    save_connection(tmp_path, CONN, "secret-token")
    conn, token = load_connection(tmp_path, "imc-kobo")
    assert conn == CONN and token == "secret-token"


def test_token_never_written_to_disk(tmp_path: Path) -> None:
    save_connection(tmp_path, CONN, "secret-token")
    assert "secret-token" not in (tmp_path / "connections.toml").read_text(encoding="utf-8")


def test_env_fallback(tmp_path: Path, mem_keyring, monkeypatch) -> None:
    save_connection(tmp_path, CONN, "secret")
    mem_keyring.store.clear()  # keyring lost the token
    monkeypatch.setenv("HAA_TOKEN_IMC_KOBO", "env-token")
    _, token = load_connection(tmp_path, "imc-kobo")
    assert token == "env-token"


def test_missing_token_raises_friendly(tmp_path: Path, mem_keyring) -> None:
    save_connection(tmp_path, CONN, "secret")
    mem_keyring.store.clear()
    with pytest.raises(CredentialsError, match="HAA_TOKEN_IMC_KOBO"):
        load_connection(tmp_path, "imc-kobo")


def test_unknown_profile_lists_available(tmp_path: Path) -> None:
    save_connection(tmp_path, CONN, "t")
    with pytest.raises(CredentialsError, match="imc-kobo"):
        load_connection(tmp_path, "nope")


def test_list_and_delete(tmp_path: Path, mem_keyring) -> None:
    save_connection(tmp_path, CONN, "t")
    save_connection(tmp_path, Connection("my-ona", "ona", "https://api.ona.io"), "t2")
    assert {c.name for c in list_connections(tmp_path)} == {"imc-kobo", "my-ona"}
    delete_connection(tmp_path, "imc-kobo")
    assert {c.name for c in list_connections(tmp_path)} == {"my-ona"}
    assert ("haa", "imc-kobo") not in mem_keyring.store


def test_invalid_kind_rejected(tmp_path: Path) -> None:
    with pytest.raises(CredentialsError, match="kind"):
        save_connection(tmp_path, Connection("x", "sharepoint", "https://x"), "t")


def test_env_var_name() -> None:
    assert env_var_name("imc-kobo") == "HAA_TOKEN_IMC_KOBO"
    assert env_var_name("my.test 1") == "HAA_TOKEN_MY_TEST_1"


def test_no_connections_file(tmp_path: Path) -> None:
    assert list_connections(tmp_path) == []
```

- [ ] **Step 2: Run to verify failure** — ImportError expected.

- [ ] **Step 3: Implement `src/haa/connectors/credentials.py`**

```python
"""Connection profiles: metadata on disk, tokens in the OS credential store."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

import keyring
import tomli_w

KEYRING_SERVICE = "haa"
VALID_KINDS = ("kobo", "ona")


class CredentialsError(ValueError):
    """Friendly, user-facing credentials failure."""


@dataclass(frozen=True)
class Connection:
    name: str
    kind: str
    base_url: str


def _toml_path(workspace: Path) -> Path:
    return workspace / "connections.toml"


def _read_all(workspace: Path) -> dict[str, dict]:
    path = _toml_path(workspace)
    if not path.is_file():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8")).get("connections", {})


def _write_all(workspace: Path, data: dict[str, dict]) -> None:
    _toml_path(workspace).write_text(
        tomli_w.dumps({"connections": data}), encoding="utf-8"
    )


def env_var_name(profile: str) -> str:
    return "HAA_TOKEN_" + re.sub(r"[^A-Za-z0-9]+", "_", profile).strip("_").upper()


def save_connection(workspace: Path, conn: Connection, token: str) -> None:
    if conn.kind not in VALID_KINDS:
        raise CredentialsError(f"Unknown connection kind {conn.kind!r}; use one of {VALID_KINDS}")
    data = _read_all(workspace)
    data[conn.name] = {"kind": conn.kind, "base_url": conn.base_url.rstrip("/")}
    _write_all(workspace, data)
    keyring.set_password(KEYRING_SERVICE, conn.name, token)


def list_connections(workspace: Path) -> list[Connection]:
    return [
        Connection(name=name, kind=meta["kind"], base_url=meta["base_url"])
        for name, meta in sorted(_read_all(workspace).items())
    ]


def load_connection(workspace: Path, name: str) -> tuple[Connection, str]:
    data = _read_all(workspace)
    if name not in data:
        known = ", ".join(sorted(data)) or "(none)"
        raise CredentialsError(f"Unknown connection {name!r}. Known: {known}")
    conn = Connection(name=name, kind=data[name]["kind"], base_url=data[name]["base_url"])
    token = keyring.get_password(KEYRING_SERVICE, name) or os.environ.get(env_var_name(name))
    if not token:
        raise CredentialsError(
            f"No token for {name!r}: not in the OS credential store and "
            f"{env_var_name(name)} is not set. Re-run: haa connect {conn.kind}"
        )
    return conn, token


def delete_connection(workspace: Path, name: str) -> None:
    data = _read_all(workspace)
    data.pop(name, None)
    _write_all(workspace, data)
    try:
        keyring.delete_password(KEYRING_SERVICE, name)
    except Exception:  # backend may not have it — deletion is best-effort
        pass


def make_connector(conn: Connection, token: str):
    if conn.kind == "kobo":
        from haa.connectors.kobo import KoboConnector

        return KoboConnector(conn.base_url, token)
    from haa.connectors.ona import OnaConnector

    return OnaConnector(conn.base_url, token)
```

- [ ] **Step 4: Verify** — `uv run pytest tests/test_credentials.py -v` all PASS; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/haa/connectors/credentials.py tests/test_credentials.py
git commit -m "feat: connection profiles with keyring tokens and env fallback"
```

---

### Task 4: KoboConnector

**Files:**
- Create: `src/haa/connectors/kobo.py`
- Test: `tests/test_kobo.py`

**Interfaces:**
- Consumes: `base.get_json/rows_to_dataframe/write_pull/RemoteForm/PullResult/ConnectorError`.
- Produces: `KoboConnector(base_url: str, token: str, timeout: float = 30.0, page_size: int = 1000)` implementing `ODKConnector`. Kobo API v2: header `Authorization: Token <t>`; `GET {base}/api/v2/assets/?format=json` → `{"results": [{"uid", "name", "asset_type", "deployment__submission_count"}...]}` filtered to `asset_type == "survey"`; data `GET {base}/api/v2/assets/{uid}/data/?format=json&limit=<page_size>` → `{"count", "next", "results": [...]}`, follow `next` URLs until null. `pull(form, dest_dir)`: `form` matches by uid exact or name case-insensitive; unknown → `ConnectorError` listing available names; file named after form name.

- [ ] **Step 1: Write the failing tests**

`tests/test_kobo.py`:

```python
from pathlib import Path

import httpx
import pandas as pd
import pytest
import respx

from haa.connectors.base import ConnectorError
from haa.connectors.kobo import KoboConnector

BASE = "https://kf.example.org"

ASSETS = {
    "results": [
        {"uid": "aXb1", "name": "Household Survey", "asset_type": "survey",
         "deployment__submission_count": 3},
        {"uid": "tmpl", "name": "A template", "asset_type": "template"},
    ]
}
PAGE1 = {
    "count": 3,
    "next": f"{BASE}/api/v2/assets/aXb1/data/?format=json&limit=2&start=2",
    "results": [
        {"_uuid": "u1", "oblast": "X", "hh_size": 3},
        {"_uuid": "u2", "oblast": "Y", "hh_size": 5},
    ],
}
PAGE2 = {"count": 3, "next": None, "results": [{"_uuid": "u3", "oblast": "X", "hh_size": 2}]}


@pytest.fixture()
def connector() -> KoboConnector:
    return KoboConnector(BASE, "tok", page_size=2)


@respx.mock
def test_list_forms_filters_surveys(connector: KoboConnector) -> None:
    route = respx.get(f"{BASE}/api/v2/assets/").mock(return_value=httpx.Response(200, json=ASSETS))
    forms = connector.list_forms()
    assert [(f.uid, f.name, f.submissions) for f in forms] == [("aXb1", "Household Survey", 3)]
    assert route.calls[0].request.headers["Authorization"] == "Token tok"


@respx.mock
def test_pull_paginates_and_writes(connector: KoboConnector, tmp_path: Path) -> None:
    respx.get(f"{BASE}/api/v2/assets/").mock(return_value=httpx.Response(200, json=ASSETS))
    # respx matches params as a SUBSET in insertion order — the start-page route
    # must be registered FIRST, or page 2's request (which also carries
    # format=json&limit=2) would match the page-1 route and loop forever.
    respx.get(f"{BASE}/api/v2/assets/aXb1/data/", params={"limit": "2", "start": "2"}).mock(
        return_value=httpx.Response(200, json=PAGE2)
    )
    respx.get(f"{BASE}/api/v2/assets/aXb1/data/", params={"format": "json", "limit": "2"}).mock(
        return_value=httpx.Response(200, json=PAGE1)
    )
    result = connector.pull("Household Survey", tmp_path)
    assert result.rows == 3 and result.form.uid == "aXb1"
    df = pd.read_excel(result.path)
    assert len(df) == 3 and set(df["_uuid"]) == {"u1", "u2", "u3"}
    assert result.path.name == "household_survey.xlsx"


@respx.mock
def test_pull_by_uid(connector: KoboConnector, tmp_path: Path) -> None:
    respx.get(f"{BASE}/api/v2/assets/").mock(return_value=httpx.Response(200, json=ASSETS))
    respx.get(url__startswith=f"{BASE}/api/v2/assets/aXb1/data/").mock(
        return_value=httpx.Response(200, json=PAGE2)
    )
    assert connector.pull("aXb1", tmp_path).rows == 1


@respx.mock
def test_unknown_form_lists_available(connector: KoboConnector, tmp_path: Path) -> None:
    respx.get(f"{BASE}/api/v2/assets/").mock(return_value=httpx.Response(200, json=ASSETS))
    with pytest.raises(ConnectorError, match="Household Survey"):
        connector.pull("nope", tmp_path)


@respx.mock
def test_empty_form_no_file(connector: KoboConnector, tmp_path: Path) -> None:
    respx.get(f"{BASE}/api/v2/assets/").mock(return_value=httpx.Response(200, json=ASSETS))
    respx.get(url__startswith=f"{BASE}/api/v2/assets/aXb1/data/").mock(
        return_value=httpx.Response(200, json={"count": 0, "next": None, "results": []})
    )
    with pytest.raises(ConnectorError, match="0 submissions"):
        connector.pull("aXb1", tmp_path)
    assert not list(tmp_path.iterdir())
```

- [ ] **Step 2: Run to verify failure** — ImportError expected.

- [ ] **Step 3: Implement `src/haa/connectors/kobo.py`**

```python
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


class KoboConnector:
    def __init__(self, base_url: str, token: str, timeout: float = 30.0, page_size: int = 1000):
        self.base_url = base_url.rstrip("/")
        self.page_size = page_size
        self._client = httpx.Client(
            headers={"Authorization": f"Token {token}"}, timeout=timeout
        )

    def list_forms(self) -> list[RemoteForm]:
        data = get_json(self._client, f"{self.base_url}/api/v2/assets/", params={"format": "json"})
        return [
            RemoteForm(
                uid=a["uid"],
                name=a["name"],
                submissions=a.get("deployment__submission_count"),
            )
            for a in data.get("results", [])
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
        while url:
            page = get_json(self._client, url, params=params)
            rows.extend(page.get("results", []))
            url, params = page.get("next"), None  # `next` is a full URL
        if not rows:
            raise ConnectorError(f"Form {target.name!r} has 0 submissions — nothing to pull.")
        path = write_pull(rows_to_dataframe(rows), dest_dir, target.name)
        return PullResult(path=path, rows=len(rows), form=target)
```

- [ ] **Step 4: Verify** — `uv run pytest tests/test_kobo.py -v` all PASS; ruff clean. If a live smoke later disagrees with these endpoint shapes, the fixtures define the unit contract — adjust the connector AND fixtures together against current Kobo docs.

- [ ] **Step 5: Commit**

```bash
git add src/haa/connectors/kobo.py tests/test_kobo.py
git commit -m "feat: KoboToolbox connector with pagination and form resolution"
```

---

### Task 5: OnaConnector

**Files:**
- Create: `src/haa/connectors/ona.py`
- Test: `tests/test_ona.py`

**Interfaces:**
- Produces: `OnaConnector(base_url, token, timeout=30.0, page_size=1000)` implementing `ODKConnector`. Ona API v1: header `Authorization: Token <t>`; forms `GET {base}/api/v1/forms` → list of `{"formid", "id_string", "title", "num_of_submissions"}` → `RemoteForm(uid=str(formid), name=id_string, submissions=num_of_submissions)`; data `GET {base}/api/v1/data/{formid}?page=N&page_size=<page_size>` (N from 1) → JSON array; stop when the page is shorter than page_size or empty. Resolution: uid (formid as string) exact, or name (id_string) case-insensitive, or title case-insensitive. Empty form → `ConnectorError("0 submissions")`.

- [ ] **Step 1: Write the failing tests**

`tests/test_ona.py`:

```python
from pathlib import Path

import httpx
import pandas as pd
import pytest
import respx

from haa.connectors.base import ConnectorError
from haa.connectors.ona import OnaConnector

BASE = "https://api.ona.example"
FORMS = [
    {"formid": 101, "id_string": "hh_survey", "title": "Household Survey",
     "num_of_submissions": 3},
]
ROWS_P1 = [{"_uuid": "u1", "v": 1}, {"_uuid": "u2", "v": 2}]
ROWS_P2 = [{"_uuid": "u3", "v": 3}]


@pytest.fixture()
def connector() -> OnaConnector:
    return OnaConnector(BASE, "tok", page_size=2)


@respx.mock
def test_list_forms(connector: OnaConnector) -> None:
    route = respx.get(f"{BASE}/api/v1/forms").mock(return_value=httpx.Response(200, json=FORMS))
    forms = connector.list_forms()
    assert [(f.uid, f.name, f.submissions) for f in forms] == [("101", "hh_survey", 3)]
    assert route.calls[0].request.headers["Authorization"] == "Token tok"


@respx.mock
def test_pull_paginates(connector: OnaConnector, tmp_path: Path) -> None:
    respx.get(f"{BASE}/api/v1/forms").mock(return_value=httpx.Response(200, json=FORMS))
    respx.get(f"{BASE}/api/v1/data/101", params={"page": "1", "page_size": "2"}).mock(
        return_value=httpx.Response(200, json=ROWS_P1)
    )
    respx.get(f"{BASE}/api/v1/data/101", params={"page": "2", "page_size": "2"}).mock(
        return_value=httpx.Response(200, json=ROWS_P2)
    )
    result = connector.pull("hh_survey", tmp_path)
    assert result.rows == 3
    assert len(pd.read_excel(result.path)) == 3


@respx.mock
def test_pull_by_title(connector: OnaConnector, tmp_path: Path) -> None:
    respx.get(f"{BASE}/api/v1/forms").mock(return_value=httpx.Response(200, json=FORMS))
    respx.get(url__startswith=f"{BASE}/api/v1/data/101").mock(
        return_value=httpx.Response(200, json=ROWS_P2)
    )
    assert connector.pull("Household Survey", tmp_path).form.uid == "101"


@respx.mock
def test_empty_form(connector: OnaConnector, tmp_path: Path) -> None:
    respx.get(f"{BASE}/api/v1/forms").mock(return_value=httpx.Response(200, json=FORMS))
    respx.get(url__startswith=f"{BASE}/api/v1/data/101").mock(
        return_value=httpx.Response(200, json=[])
    )
    with pytest.raises(ConnectorError, match="0 submissions"):
        connector.pull("101", tmp_path)
```

- [ ] **Step 2: Run to verify failure** — ImportError expected.

- [ ] **Step 3: Implement `src/haa/connectors/ona.py`**

```python
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
            if form == f.uid or needle == f.name.lower() or needle == self._titles.get(f.uid, "").lower():
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
```

- [ ] **Step 4: Verify** — tests PASS; ruff clean; full suite once.

- [ ] **Step 5: Commit**

```bash
git add src/haa/connectors/ona.py tests/test_ona.py
git commit -m "feat: Ona connector with page-based pagination"
```

---

### Task 6: SourceToolbox + `sources` MCP server

**Files:**
- Create: `src/haa/core/tools/sourcetools.py`
- Test: `tests/test_sourcetools.py`

**Interfaces:**
- Consumes: credentials (Task 3), connectors (Tasks 4–5), `SessionTelemetry`, `HaaConfig`.
- Produces: `SOURCE_TOOL_NAMES = ["mcp__sources__list_connections", "mcp__sources__list_remote_forms", "mcp__sources__pull_form"]`; `SourceToolbox(config, telemetry)` with sync methods `list_connections() -> str`, `list_remote_forms(connection: str) -> str`, `pull_form(connection: str, form: str) -> str` (downloads into `config.data_dir`, logs telemetry kind `pull` with connection/form/rows/seconds, returns a SUMMARY string only); `build_sources_server(box)` (thin async @tool wrappers; `pull_form` and `list_remote_forms` via `asyncio.to_thread`). All `ConnectorError`/`CredentialsError` → friendly strings, never raised to the SDK.

- [ ] **Step 1: Write the failing tests**

`tests/test_sourcetools.py`:

```python
from pathlib import Path

import pytest

from haa.config import load_config
from haa.connectors.base import ConnectorError, PullResult, RemoteForm
from haa.connectors.credentials import Connection
from haa.core.telemetry import SessionTelemetry
from haa.core.tools.sourcetools import SOURCE_TOOL_NAMES, SourceToolbox


class StubConnector:
    def list_forms(self):
        return [RemoteForm(uid="a1", name="hh_survey", submissions=42)]

    def pull(self, form: str, dest_dir: Path) -> PullResult:
        path = dest_dir / "hh_survey.xlsx"
        path.write_bytes(b"fake")
        return PullResult(path=path, rows=42, form=RemoteForm("a1", "hh_survey", 42))


@pytest.fixture()
def box(tmp_path: Path, monkeypatch) -> SourceToolbox:
    cfg = load_config(tmp_path)
    telemetry = SessionTelemetry(cfg.logs_dir / "t.jsonl")
    import haa.core.tools.sourcetools as st

    monkeypatch.setattr(
        st, "load_connection",
        lambda ws, name: (Connection(name, "kobo", "https://x"), "tok"),
    )
    monkeypatch.setattr(st, "make_connector", lambda conn, token: StubConnector())
    monkeypatch.setattr(
        st, "list_connections",
        lambda ws: [Connection("imc-kobo", "kobo", "https://x")],
    )
    return SourceToolbox(cfg, telemetry)


def test_tool_names_constant() -> None:
    assert SOURCE_TOOL_NAMES == [
        "mcp__sources__list_connections",
        "mcp__sources__list_remote_forms",
        "mcp__sources__pull_form",
    ]


def test_list_connections(box: SourceToolbox) -> None:
    out = box.list_connections()
    assert "imc-kobo" in out and "kobo" in out


def test_list_remote_forms(box: SourceToolbox) -> None:
    out = box.list_remote_forms("imc-kobo")
    assert "hh_survey" in out and "42" in out


def test_pull_form_summary_only(box: SourceToolbox) -> None:
    out = box.pull_form("imc-kobo", "hh_survey")
    assert "hh_survey.xlsx" in out and "42" in out
    assert "fake" not in out  # file content never leaks into the summary
    assert (box.config.data_dir / "hh_survey.xlsx").exists()


def test_pull_logs_telemetry(box: SourceToolbox) -> None:
    box.pull_form("imc-kobo", "hh_survey")
    log = box.telemetry.path.read_text(encoding="utf-8")
    assert '"kind": "pull"' in log and "hh_survey" in log


def test_connector_error_is_friendly(box: SourceToolbox, monkeypatch) -> None:
    import haa.core.tools.sourcetools as st

    def boom(conn, token):
        raise ConnectorError("Form 'x' not found. Available: hh_survey")

    monkeypatch.setattr(st, "make_connector", boom)
    out = box.pull_form("imc-kobo", "x")
    assert "not found" in out and "Traceback" not in out


def test_unknown_connection_friendly(box: SourceToolbox, monkeypatch) -> None:
    import haa.core.tools.sourcetools as st
    from haa.connectors.credentials import CredentialsError

    def missing(ws, name):
        raise CredentialsError("Unknown connection 'zzz'. Known: imc-kobo")

    monkeypatch.setattr(st, "load_connection", missing)
    assert "Unknown connection" in box.pull_form("zzz", "hh_survey")


def test_build_server_importable(box: SourceToolbox) -> None:
    from haa.core.tools.sourcetools import build_sources_server

    assert build_sources_server(box) is not None
```

- [ ] **Step 2: Run to verify failure** — ImportError expected.

- [ ] **Step 3: Implement `src/haa/core/tools/sourcetools.py`**

```python
"""In-process MCP server exposing source connectors to agents (summaries only)."""

from __future__ import annotations

import time

from haa.config import HaaConfig
from haa.connectors.base import ConnectorError
from haa.connectors.credentials import (
    CredentialsError,
    list_connections,
    load_connection,
    make_connector,
)
from haa.core.telemetry import SessionTelemetry

SOURCE_TOOL_NAMES = [
    "mcp__sources__list_connections",
    "mcp__sources__list_remote_forms",
    "mcp__sources__pull_form",
]


class SourceToolbox:
    def __init__(self, config: HaaConfig, telemetry: SessionTelemetry) -> None:
        self.config = config
        self.telemetry = telemetry

    def list_connections(self) -> str:
        conns = list_connections(self.config.workspace)
        if not conns:
            return "No connections configured. Set one up with: haa connect kobo|ona"
        return "Configured connections:\n" + "\n".join(
            f"- {c.name} ({c.kind}, {c.base_url})" for c in conns
        )

    def list_remote_forms(self, connection: str) -> str:
        try:
            conn, token = load_connection(self.config.workspace, connection)
            forms = make_connector(conn, token).list_forms()
        except (ConnectorError, CredentialsError) as exc:
            return str(exc)
        if not forms:
            return f"No forms found on {connection!r}."
        return f"Forms on {connection!r}:\n" + "\n".join(
            f"- {f.name} (uid {f.uid}, {f.submissions if f.submissions is not None else '?'} submissions)"
            for f in forms
        )

    def pull_form(self, connection: str, form: str) -> str:
        started = time.monotonic()
        try:
            conn, token = load_connection(self.config.workspace, connection)
            result = make_connector(conn, token).pull(form, self.config.data_dir)
        except (ConnectorError, CredentialsError) as exc:
            return str(exc)
        seconds = round(time.monotonic() - started, 1)
        self.telemetry.log(
            "pull",
            connection=connection,
            form=result.form.name,
            rows=result.rows,
            seconds=seconds,
        )
        return (
            f"Pulled {result.rows} submissions of {result.form.name!r} "
            f"into {result.path.name} ({seconds}s). "
            "The dataset is now available to profile_dataset / run_analysis."
        )


def build_sources_server(box: SourceToolbox):
    import asyncio

    from claude_agent_sdk import create_sdk_mcp_server, tool

    def _text(result: str) -> dict:
        return {"content": [{"type": "text", "text": result}]}

    @tool("list_connections", "List configured data-source connections", {})
    async def list_connections_tool(args: dict) -> dict:
        return _text(box.list_connections())

    @tool("list_remote_forms", "List forms available on a configured connection", {"connection": str})
    async def list_remote_forms_tool(args: dict) -> dict:
        return _text(await asyncio.to_thread(box.list_remote_forms, str(args["connection"])))

    @tool(
        "pull_form",
        "Download all submissions of a form into the local workspace. Returns a "
        "summary only; the data itself stays local (PII boundary).",
        {"connection": str, "form": str},
    )
    async def pull_form_tool(args: dict) -> dict:
        return _text(
            await asyncio.to_thread(box.pull_form, str(args["connection"]), str(args["form"]))
        )

    return create_sdk_mcp_server(
        name="sources",
        version="0.1.0",
        tools=[list_connections_tool, list_remote_forms_tool, pull_form_tool],
    )
```

- [ ] **Step 4: Verify** — tests PASS; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/haa/core/tools/sourcetools.py tests/test_sourcetools.py
git commit -m "feat: sources MCP server with summary-only pull tools"
```

---

### Task 7: Sandbox `include_pii` + cache invalidation + `pii_access` audit

**Files:**
- Modify: `src/haa/core/sandbox/executor.py`, `src/haa/core/tools/datatools.py`
- Test: `tests/test_executor.py`, `tests/test_datatools.py` (append)

**Interfaces:**
- `load_dataset(name, include_pii=False)` in the sandbox prelude: with `include_pii=True` NO columns are dropped. Prelude also exports `REPORTS_DIR` (string, from new cfg key `reports_dir`).
- `DataToolbox._ensure_profiled` re-profiles when the file's mtime changed (stores `self._mtimes: dict[str, float]`); a changed file also clears its `_broken` entry.
- `DataToolbox.run_analysis` logs telemetry kind `pii_access` (payload: `note`) BEFORE execution whenever the literal `include_pii=True` occurs in the submitted code.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_executor.py`:

```python
def test_load_dataset_include_pii(demo_workspace: Path) -> None:
    code = 'df = load_dataset("beneficiaries", include_pii=True)\nprint(sorted(df.columns))'
    res = run_code(code, _cfg(demo_workspace), PII)
    assert res.returncode == 0, res.stderr
    assert "resp_phone" in res.stdout


def test_reports_dir_exposed(demo_workspace: Path) -> None:
    res = run_code("print(REPORTS_DIR)", _cfg(demo_workspace), {})
    assert res.returncode == 0 and "reports" in res.stdout
```

Append to `tests/test_datatools.py`:

```python
def test_profile_cache_invalidated_on_mtime(demo_workspace: Path, tmp_path: Path) -> None:
    import shutil

    ws = tmp_path
    (ws / "data").mkdir()
    (ws / "project_docs").mkdir()
    shutil.copy(demo_workspace / "data" / "beneficiaries.xlsx", ws / "data" / "beneficiaries.xlsx")
    tb = _toolbox(ws)
    first = tb.profile_dataset("beneficiaries")
    df = pd.read_excel(ws / "data" / "beneficiaries.xlsx").head(10)
    df.to_excel(ws / "data" / "beneficiaries.xlsx", index=False)
    second = tb.profile_dataset("beneficiaries")
    assert '"rows": 10' in second and second != first


def test_pii_access_logged(demo_workspace: Path) -> None:
    cfg = load_config(demo_workspace)
    tele = SessionTelemetry(cfg.logs_dir / "pii.jsonl")
    DataToolbox(cfg, tele).run_analysis(
        'df = load_dataset("beneficiaries", include_pii=True)\nprint(len(df))'
    )
    log = tele.path.read_text(encoding="utf-8")
    assert '"kind": "pii_access"' in log


def test_no_pii_access_event_without_flag(demo_workspace: Path) -> None:
    cfg = load_config(demo_workspace)
    tele = SessionTelemetry(cfg.logs_dir / "nopii.jsonl")
    DataToolbox(cfg, tele).run_analysis("print(1)")
    assert "pii_access" not in tele.path.read_text(encoding="utf-8")
```

(`test_datatools.py` needs `import pandas as pd` at top if absent.)

- [ ] **Step 2: Run to verify failure** — new tests FAIL (TypeError on include_pii / NameError REPORTS_DIR / cache returns stale / no pii_access event).

- [ ] **Step 3: Implement**

`executor.py` prelude — replace the `load_dataset` definition and add `REPORTS_DIR`:

```python
REPORTS_DIR = _CFG["reports_dir"]


def load_dataset(name: str, include_pii: bool = False) -> "pd.DataFrame":
    if name not in _DATASETS:
        raise KeyError(f"unknown dataset {{name!r}}; available: {{sorted(_DATASETS)}}")
    path = _DATASETS[name]
    df = pd.read_csv(path) if path.lower().endswith(".csv") else pd.read_excel(path)
    if include_pii:
        return df
    drop = [c for c in _PII.get(name, []) if c in df.columns]
    return df.drop(columns=drop)
```

(remember the brace-doubling rule inside `_PRELUDE`), and in `run_code`'s cfg dict add `"reports_dir": str(config.reports_dir)`.

`datatools.py`:
- `__init__`: add `self._mtimes: dict[str, float] = {}`.
- `_ensure_profiled`: before the `name in self._profiles` shortcut, compute `mtime = found[name].stat().st_mtime` when the file exists and drop caches (`_profiles`, `_pii_map`, `_broken`, `_mtimes`) for `name` when `self._mtimes.get(name) != mtime`; store the new mtime after a successful profile. Restructure:

```python
    def _ensure_profiled(self, name: str) -> dict | None:
        found = discover_datasets(self.config.data_dir)
        if name not in found:
            return None
        mtime = found[name].stat().st_mtime
        if self._mtimes.get(name) != mtime:
            self._profiles.pop(name, None)
            self._pii_map.pop(name, None)
            self._broken.pop(name, None)
        if name in self._broken:
            return None
        if name in self._profiles:
            return self._profiles[name]
        path = found[name]
        try:
            df = pd.read_csv(path) if path.suffix.lower() == ".csv" else pd.read_excel(path)
        except Exception as exc:
            self._broken[name] = str(exc)
            self._mtimes[name] = mtime
            return None
        pii = detect_pii_columns(df)
        self._pii_map[name] = pii
        self._profiles[name] = profile_dataframe(df, name, pii)
        self._mtimes[name] = mtime
        return self._profiles[name]
```

- `run_analysis`: after the failure-limit check, before executing:

```python
        if "include_pii=True" in code:
            self.telemetry.log("pii_access", note="load_dataset(include_pii=True) in submitted code")
```

- [ ] **Step 4: Verify** — `uv run pytest tests/test_executor.py tests/test_datatools.py -v` all PASS; full suite once; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/haa/core/sandbox/executor.py src/haa/core/tools/datatools.py tests/test_executor.py tests/test_datatools.py
git commit -m "feat: include_pii loader flag with audit event and mtime cache invalidation"
```

---

### Task 8: Cleaner role + registry + orchestrator routing

**Files:**
- Create: `src/haa/core/agents/cleaner.py`
- Modify: `src/haa/core/agents/registry.py`, `src/haa/core/agents/orchestrator.py`
- Test: `tests/test_cleaner_agent.py`, `tests/test_agents.py` (adjust registry test)

**Interfaces:**
- `CLEANER_PROMPT: str`; `build_cleaner(config) -> AgentDefinition(description=..., prompt=CLEANER_PROMPT, tools=DATA_TOOL_NAMES, model=config.cleaner_model, mcpServers=["data"])`.
- `build_agents(config)` → `{"analyst": ..., "cleaner": ...}`.
- Orchestrator prompt gains routing: pull/sync requests → use the sources tools directly; cleaning requests → delegate to the `cleaner` subagent.

- [ ] **Step 1: Write the failing tests**

`tests/test_cleaner_agent.py`:

```python
from pathlib import Path

from haa.config import load_config
from haa.core.agents.cleaner import CLEANER_PROMPT, build_cleaner
from haa.core.agents.registry import build_agents
from haa.core.tools.datatools import DATA_TOOL_NAMES


def _cfg(tmp_path: Path):
    return load_config(tmp_path)


def test_cleaner_definition(tmp_path: Path) -> None:
    agent = build_cleaner(_cfg(tmp_path))
    assert agent.model == "claude-opus-5"
    assert set(agent.tools) == set(DATA_TOOL_NAMES)
    assert agent.prompt == CLEANER_PROMPT


def test_cleaner_model_configurable(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text('cleaner_model = "claude-sonnet-5"', encoding="utf-8")
    assert build_cleaner(_cfg(tmp_path)).model == "claude-sonnet-5"


def test_registry_has_both_roles(tmp_path: Path) -> None:
    assert set(build_agents(_cfg(tmp_path))) == {"analyst", "cleaner"}


def test_cleaner_prompt_discipline() -> None:
    for needle in (
        "_clean.xlsx", "cleaning_report", "REPORTS_DIR", "include_pii=True",
        "never print", "unique", "raw file", "never fabricate",
    ):
        assert needle.lower() in CLEANER_PROMPT.lower(), needle
```

Adjust `tests/test_agents.py::test_registry`: expected set becomes `{"analyst", "cleaner"}`. Add to `test_prompts_carry_discipline` orchestrator needles: `"cleaner"`, `"pull"`.

- [ ] **Step 2: Run to verify failure** — ImportError + registry test FAIL.

- [ ] **Step 3: Implement**

`src/haa/core/agents/cleaner.py`:

```python
"""Cleaner subagent: MEAL-style data cleaning — clean copy + report, raw untouched."""

from claude_agent_sdk import AgentDefinition

from haa.config import HaaConfig
from haa.core.tools.datatools import DATA_TOOL_NAMES

CLEANER_PROMPT = """\
You are a data-cleaning specialist for humanitarian survey data (MEAL discipline).
You produce TWO artifacts per dataset and NEVER modify the raw file:
1. A clean copy saved next to the raw one: `<dataset>_clean.xlsx` (in the same
   data directory the raw file lives in — write it via run_analysis code using
   df.to_excel(f"data/{name}_clean.xlsx", index=False) relative to the workspace).
2. A human-readable report `<dataset>_cleaning_report.md` written into REPORTS_DIR
   (available in the sandbox), with before/after numbers for every action.

Cleaning checklist — work through it in order and report each item:
1. Unique record ID: verify one exists (_uuid, _id or _haa_row_id). If none is
   unique and non-null, CREATE `_haa_row_id` in the clean copy and say so.
2. Exact duplicates (same record ID): drop, keep first, report the count.
3. Impossible values (ages like 999, negative sizes): set to missing, report counts.
4. Dates in the future or before the plausible reporting period: flag in the
   report; set clearly impossible ones to missing.
5. Category spelling variants (e.g. oblast names with extra suffixes): normalize
   to the dominant spelling, list every mapping you applied.
6. Missing values in key fields: do NOT invent values; report rates only.

Rules (non-negotiable):
- The clean copy must keep ALL columns, including personal data — load with
  load_dataset("<name>", include_pii=True). NEVER print PII values (names,
  phones, GPS) to stdout — print counts and column names only.
- Never fabricate a number: every figure in the report must come from executed
  code output.
- The raw file is read-only; if asked to overwrite it, refuse and explain.
- Finish by reporting: rows before/after, duplicates removed, values fixed,
  values flagged, and the two artifact paths.
"""


def build_cleaner(config: HaaConfig) -> AgentDefinition:
    return AgentDefinition(
        description=(
            "Data cleaning and validation: duplicates, impossible values, category "
            "normalization. Produces a clean copy + cleaning report. Delegate any "
            "'clean this dataset' request here."
        ),
        prompt=CLEANER_PROMPT,
        tools=list(DATA_TOOL_NAMES),
        model=config.cleaner_model,
        mcpServers=["data"],
    )
```

`registry.py`: import `build_cleaner`, return `{"analyst": build_analyst(config), "cleaner": build_cleaner(config)}`.

`orchestrator.py` — extend the routing rules block:

```
- Requests to fetch/refresh data from a connected source (Kobo, Ona): use the
  sources tools yourself (list_connections, list_remote_forms, pull_form) —
  do not delegate. Report the pull summary to the user.
- Requests to clean or validate a dataset: delegate to the `cleaner` subagent.
```

- [ ] **Step 4: Verify** — `uv run pytest tests/test_cleaner_agent.py tests/test_agents.py -v` all PASS; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/haa/core/agents tests/test_cleaner_agent.py tests/test_agents.py
git commit -m "feat: cleaner agent role with MEAL checklist and orchestrator routing"
```

---

### Task 9: Session wiring

**Files:**
- Modify: `src/haa/core/session.py`
- Test: `tests/test_session.py` (extend `test_build_options`)

**Interfaces:**
- `AnalyticsSession` also owns `SourceToolbox` + `build_sources_server`; `build_options()` returns `mcp_servers={"data": ..., "sources": ...}` and `allowed_tools=["Agent", "Task", *DATA_TOOL_NAMES, *SOURCE_TOOL_NAMES]`. Everything else unchanged.

- [ ] **Step 1: Extend the test** — in `tests/test_session.py::test_build_options` add:

```python
    from haa.core.tools.sourcetools import SOURCE_TOOL_NAMES

    assert "sources" in opts.mcp_servers
    assert set(SOURCE_TOOL_NAMES) <= set(opts.allowed_tools)
```

- [ ] **Step 2: Run to verify failure** — FAIL on both assertions.

- [ ] **Step 3: Implement** — in `session.py`:

```python
from haa.core.tools.sourcetools import SOURCE_TOOL_NAMES, SourceToolbox, build_sources_server
```

In `__init__`: `self._source_toolbox = SourceToolbox(config, self.telemetry)` and `self._sources_server = build_sources_server(self._source_toolbox)`. In `build_options`: `mcp_servers={"data": self._server, "sources": self._sources_server}`, `allowed_tools=["Agent", "Task", *DATA_TOOL_NAMES, *SOURCE_TOOL_NAMES]`.

- [ ] **Step 4: Verify** — `uv run pytest tests/test_session.py -v` all PASS; full suite once; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/haa/core/session.py tests/test_session.py
git commit -m "feat: wire sources server and tools into the session"
```

---

### Task 10: CLI — connect / connections / pull

**Files:**
- Modify: `src/haa/cli/app.py`
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Subcommands: `haa connect <kobo|ona> [--workspace]` (interactive: profile name [default `<kind>`], base URL [defaults: kobo → `https://kf.kobotoolbox.org`, ona → `https://api.ona.io`], token via `getpass.getpass`); `haa connections [--workspace]`; `haa pull <profile> --form <id|name> [--workspace]`.
- Testable core kept separate from interactivity: `run_connect(workspace, kind, name, base_url, token) -> str`, `run_pull(workspace, profile, form) -> str` — pure functions returning the message to print; the argparse handlers are thin wrappers around them.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_cli.py`:

```python
from haa.cli.app import run_connect, run_pull


def test_parser_connect_and_pull() -> None:
    p = build_parser()
    a = p.parse_args(["connect", "kobo"])
    assert a.command == "connect" and a.kind == "kobo"
    b = p.parse_args(["pull", "imc-kobo", "--form", "hh"])
    assert b.command == "pull" and b.profile == "imc-kobo" and b.form == "hh"


def test_run_connect_saves_profile(tmp_path: Path, monkeypatch) -> None:
    import keyring

    store: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(keyring, "set_password", lambda s, u, p: store.__setitem__((s, u), p))
    msg = run_connect(tmp_path, "kobo", "test-kobo", "https://kf.example.org", "tok")
    assert "test-kobo" in msg
    assert ("haa", "test-kobo") in store
    assert "tok" not in (tmp_path / "connections.toml").read_text(encoding="utf-8")


def test_run_pull_friendly_on_missing_profile(tmp_path: Path) -> None:
    out = run_pull(tmp_path, "ghost", "form")
    assert "Unknown connection" in out
```

- [ ] **Step 2: Run to verify failure** — ImportError / parser errors.

- [ ] **Step 3: Implement** — in `cli/app.py` add subparsers:

```python
    connect = sub.add_parser("connect", help="Configure a data-source connection")
    connect.add_argument("kind", choices=["kobo", "ona"])
    connect.add_argument("--workspace", type=Path, default=Path("workspace"))

    connections = sub.add_parser("connections", help="List configured connections")
    connections.add_argument("--workspace", type=Path, default=Path("workspace"))

    pull = sub.add_parser("pull", help="Pull a form's submissions into the workspace")
    pull.add_argument("profile")
    pull.add_argument("--form", required=True)
    pull.add_argument("--workspace", type=Path, default=Path("workspace"))
```

Core functions:

```python
DEFAULT_URLS = {"kobo": "https://kf.kobotoolbox.org", "ona": "https://api.ona.io"}


def run_connect(workspace: Path, kind: str, name: str, base_url: str, token: str) -> str:
    from haa.connectors.credentials import Connection, save_connection

    workspace.mkdir(parents=True, exist_ok=True)
    save_connection(workspace, Connection(name=name, kind=kind, base_url=base_url), token)
    return f"Connection {name!r} ({kind}) saved. Token stored in the OS credential store."


def run_pull(workspace: Path, profile: str, form: str) -> str:
    from haa.connectors.base import ConnectorError
    from haa.connectors.credentials import CredentialsError, load_connection, make_connector

    try:
        conn, token = load_connection(workspace, profile)
        result = make_connector(conn, token).pull(form, workspace / "data")
    except (ConnectorError, CredentialsError) as exc:
        return str(exc)
    return f"Pulled {result.rows} submissions of {result.form.name!r} into {result.path.name}"
```

`main()` dispatch: `connect` → prompt (`input` for name with default `args.kind`, `input` for URL with `DEFAULT_URLS[args.kind]` default, `getpass.getpass("API token: ")`), then `console.print(run_connect(...))`; `connections` → print `list_connections` table (name, kind, base_url); `pull` → `console.print(run_pull(args.workspace, args.profile, args.form))`.

- [ ] **Step 4: Verify** — CLI tests PASS; full suite once; ruff clean. Manual: `uv run haa connections --workspace workspace` prints the empty-state hint.

- [ ] **Step 5: Commit**

```bash
git add src/haa/cli/app.py tests/test_cli.py
git commit -m "feat: haa connect/connections/pull CLI subcommands"
```

---

### Task 11: Live remote tests + README

**Files:**
- Create: `tests/test_remote_live.py`
- Modify: `README.md`

**Interfaces:**
- Marker `remote`; env contract: `HAA_TEST_KOBO_URL`+`HAA_TEST_KOBO_TOKEN`, `HAA_TEST_ONA_URL`+`HAA_TEST_ONA_TOKEN`. Each test skips when its pair is absent.

- [ ] **Step 1: Write the tests**

`tests/test_remote_live.py`:

```python
"""Live smoke tests against real Kobo/Ona servers.

Run manually: uv run pytest -m remote -v
Set HAA_TEST_KOBO_URL / HAA_TEST_KOBO_TOKEN (and the ONA pair) first.
"""

import os
from pathlib import Path

import pytest

from haa.connectors.kobo import KoboConnector
from haa.connectors.ona import OnaConnector

pytestmark = pytest.mark.remote


def _env(*names: str) -> list[str]:
    values = [os.environ.get(n, "") for n in names]
    if not all(values):
        pytest.skip(f"env not set: {', '.join(names)}")
    return values


def test_kobo_live_roundtrip(tmp_path: Path) -> None:
    url, token = _env("HAA_TEST_KOBO_URL", "HAA_TEST_KOBO_TOKEN")
    forms = KoboConnector(url, token).list_forms()
    assert isinstance(forms, list)
    with_data = [f for f in forms if (f.submissions or 0) > 0]
    if not with_data:
        pytest.skip("no forms with submissions on this account")
    result = KoboConnector(url, token).pull(with_data[0].uid, tmp_path)
    assert result.rows > 0 and result.path.exists()


def test_ona_live_roundtrip(tmp_path: Path) -> None:
    url, token = _env("HAA_TEST_ONA_URL", "HAA_TEST_ONA_TOKEN")
    forms = OnaConnector(url, token).list_forms()
    assert isinstance(forms, list)
    with_data = [f for f in forms if (f.submissions or 0) > 0]
    if not with_data:
        pytest.skip("no forms with submissions on this account")
    result = OnaConnector(url, token).pull(with_data[0].uid, tmp_path)
    assert result.rows > 0 and result.path.exists()
```

- [ ] **Step 2: Verify gating** — `uv run pytest -q` shows the 2 tests deselected; `uv run pytest -m remote -v` shows 2 skipped (env not set); ruff clean.

- [ ] **Step 3: Update README** — add after the Quickstart section:

```markdown
## Connecting live sources (KoboToolbox / Ona)

    uv run haa connect kobo        # asks for server URL and API token once;
                                   # the token goes into the OS credential store
    uv run haa pull kobo --form "Household Survey"

Or just ask in the chat: *"Pull fresh submissions of Household Survey from Kobo"*.
Pulled files land in `workspace/data/` and are cleaned/analyzed like any local
export. Only a summary (form name, row count) ever reaches the model.

## Cleaning a dataset

Ask: *"Clean the beneficiaries dataset."* The cleaner agent produces
`beneficiaries_clean.xlsx` plus `workspace/reports/beneficiaries_cleaning_report.md`
(duplicates, impossible values, category normalization — with before/after
numbers). The raw file is never modified.
```

And in Roadmap: mark connectors/cleaner as done, next = indicators registry + XLSForm designer, SharePoint connector.

- [ ] **Step 4: Commit**

```bash
git add tests/test_remote_live.py README.md
git commit -m "test: live remote smoke tests; docs: connect/pull/clean quickstart"
```

---

### Task 12: Cleaner smoke eval (API, manual)

**Files:**
- Modify: `tests/test_smoke_api.py`

**Interfaces:**
- One new api-marked eval; expected values computed independently from the demo dataset (3030 rows, 3000 unique `_uuid`, 15 ages 999). Budget raised for this heavier session via `dataclasses.replace(cfg, max_budget_usd=4.0)`.

- [ ] **Step 1: Write the eval** — append to `tests/test_smoke_api.py`:

```python
async def test_cleaner_produces_clean_copy(demo_workspace: Path, df: pd.DataFrame) -> None:
    import dataclasses

    cfg = dataclasses.replace(load_config(demo_workspace), max_budget_usd=4.0)
    async with AnalyticsSession(cfg) as session:
        async for _ in session.ask(
            "Почисти датасет beneficiaries: дубли, невозможные значения, даты, "
            "разнобой категорий. Сырой файл не трогай."
        ):
            pass

    clean_path = cfg.data_dir / "beneficiaries_clean.xlsx"
    assert clean_path.exists(), "clean copy not created"
    clean = pd.read_excel(clean_path)
    assert len(clean) == df["_uuid"].nunique()          # 3000: duplicates dropped
    assert clean["_uuid"].is_unique
    assert not (clean["head_age"] == 999).any()          # impossible ages gone
    assert "resp_phone" in clean.columns                 # PII columns preserved

    reports = list(cfg.reports_dir.glob("*cleaning_report*.md"))
    assert reports, "cleaning report not created"
    report_text = reports[0].read_text(encoding="utf-8")
    assert str(len(df) - df["_uuid"].nunique()) in report_text  # 30 duplicates reported

    raw_again = pd.read_excel(demo_workspace / "data" / "beneficiaries.xlsx")
    pd.testing.assert_frame_equal(raw_again, df)         # raw byte-identical
```

- [ ] **Step 2: Verify gating** — `uv run pytest -q`: eval deselected, offline suite green; `uv run pytest -m api --collect-only -q`: collects (now 7 api tests); ruff clean.

- [ ] **Step 3: Run manually when a key is available** — `uv run pytest -m api -v -k cleaner`. Formatting failures → strengthen the test; substance failures (wrong counts, raw file touched) → real bug, check telemetry.

- [ ] **Step 4: Commit**

```bash
git add tests/test_smoke_api.py
git commit -m "test: cleaner smoke eval with independently computed expectations"
```

---

## Acceptance Checklist (spec §7)

- [ ] `haa connect kobo` → profile saved, token in Credential Manager, no token on disk (Tasks 3, 10).
- [ ] `haa pull <profile> --form <name>` downloads a real form with a unique ID column (Tasks 2, 4, 5, 10; live proof via `pytest -m remote`).
- [ ] Chat pull works through the orchestrator with a `pull` telemetry event and no raw rows in LLM context (Tasks 6, 9).
- [ ] Chat clean produces `X_clean.xlsx` + report; raw untouched; demo clean copy = 3000 rows (Tasks 7, 8, 12).
- [ ] `pii_access` telemetry on every `include_pii=True` (Task 7).
- [ ] Unit tests + ruff green in CI; `remote`/`api` suites pass locally (all tasks).
