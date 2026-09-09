from pathlib import Path

from haa.cli.app import build_parser, run_connect, run_pull, validate_workspace
from haa.config import load_config


def test_parser_chat_defaults() -> None:
    args = build_parser().parse_args(["chat"])
    assert args.command == "chat"
    assert args.workspace == Path("workspace")


def test_parser_demo() -> None:
    args = build_parser().parse_args(["demo", "--workspace", "ws2"])
    assert args.command == "demo"
    assert args.workspace == Path("ws2")


def test_validate_empty_workspace_warns(tmp_path: Path) -> None:
    warnings = validate_workspace(load_config(tmp_path))
    assert any("dataset" in w.lower() for w in warnings)
    assert any("haa demo" in w for w in warnings)


def test_validate_demo_workspace_clean(demo_workspace: Path) -> None:
    assert validate_workspace(load_config(demo_workspace)) == []


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


def test_connect_sharepoint_keyring_failure_is_friendly(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    import time

    import keyring

    import haa.cli.app as app
    from haa.connectors.msauth import DeviceFlow, TokenSet

    monkeypatch.setattr("sys.argv", [
        "haa", "connect", "sharepoint", "--workspace", str(tmp_path),
        "--site", "onedrive", "--tenant", "t", "--client-id", "c",
    ])
    monkeypatch.setattr("builtins.input", lambda prompt="": "sp")
    flow = DeviceFlow(tenant="t", client_id="c", device_code="d", user_code="ABC",
                      verification_uri="https://microsoft.com/devicelogin", interval=1,
                      expires_at=time.time() + 60)
    monkeypatch.setattr("haa.connectors.msauth.start_device_flow", lambda t, c: flow)
    monkeypatch.setattr("haa.connectors.msauth.poll_for_token",
                        lambda f: TokenSet("a", "r", time.time() + 3600))

    def boom(*args):
        raise RuntimeError("vault locked")

    monkeypatch.setattr(keyring, "set_password", boom)
    assert app.main() == 1
    out = capsys.readouterr().out
    assert "HAA_TOKEN_SP" in out and "Traceback" not in out
