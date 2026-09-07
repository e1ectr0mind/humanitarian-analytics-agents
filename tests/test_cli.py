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
