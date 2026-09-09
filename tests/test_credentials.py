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
        save_connection(tmp_path, Connection("x", "dropbox", "https://x"), "t")


def test_env_var_name() -> None:
    assert env_var_name("imc-kobo") == "HAA_TOKEN_IMC_KOBO"
    assert env_var_name("my.test 1") == "HAA_TOKEN_MY_TEST_1"


def test_no_connections_file(tmp_path: Path) -> None:
    assert list_connections(tmp_path) == []


def test_env_fallback_when_keyring_raises(tmp_path: Path, mem_keyring, monkeypatch) -> None:
    save_connection(tmp_path, CONN, "secret")

    def _raise(service, username):
        raise RuntimeError("no backend available")

    monkeypatch.setattr(keyring, "get_password", _raise)
    monkeypatch.setenv("HAA_TOKEN_IMC_KOBO", "env-token")
    _, token = load_connection(tmp_path, "imc-kobo")
    assert token == "env-token"


def test_save_failure_leaves_no_profile(tmp_path: Path, mem_keyring, monkeypatch) -> None:
    def _raise(service, username, password):
        raise RuntimeError("vault locked")

    monkeypatch.setattr(keyring, "set_password", _raise)
    with pytest.raises(CredentialsError, match="HAA_TOKEN_IMC_KOBO"):
        save_connection(tmp_path, CONN, "secret")
    toml_path = tmp_path / "connections.toml"
    assert not toml_path.is_file() or "imc-kobo" not in toml_path.read_text(encoding="utf-8")


def test_base_url_userinfo_stripped(tmp_path: Path) -> None:
    conn = Connection(name="imc-kobo2", kind="kobo", base_url="https://secret-tok@kf.example.org")
    save_connection(tmp_path, conn, "secret-token")
    text = (tmp_path / "connections.toml").read_text(encoding="utf-8")
    assert "secret-tok" not in text
    loaded, _ = load_connection(tmp_path, "imc-kobo2")
    assert loaded.base_url == "https://kf.example.org"


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
