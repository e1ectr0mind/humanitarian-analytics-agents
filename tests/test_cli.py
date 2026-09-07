from pathlib import Path

from haa.cli.app import build_parser, validate_workspace
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
