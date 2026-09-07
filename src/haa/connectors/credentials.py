"""Connection profiles: metadata on disk, tokens in the OS credential store."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

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
    parts = urlsplit(conn.base_url)
    if parts.username or parts.password:
        host = parts.hostname or ""
        if parts.port:
            host += f":{parts.port}"
        conn = Connection(
            conn.name,
            conn.kind,
            urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment)),
        )
    try:
        keyring.set_password(KEYRING_SERVICE, conn.name, token)
    except Exception as exc:
        raise CredentialsError(
            f"Could not store the token in the OS credential store ({exc}). "
            f"Set {env_var_name(conn.name)} instead, then re-run haa connect."
        ) from exc
    data = _read_all(workspace)
    data[conn.name] = {"kind": conn.kind, "base_url": conn.base_url.rstrip("/")}
    _write_all(workspace, data)


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
    try:
        token = keyring.get_password(KEYRING_SERVICE, name)
    except Exception:  # no backend / locked vault / access denied
        token = None
    token = token or os.environ.get(env_var_name(name))
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
