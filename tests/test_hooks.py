import sys
from pathlib import Path

import pytest

from haa.config import load_config
from haa.core.hooks import deny_reason, make_pretooluse_hook


@pytest.fixture()
def cfg(tmp_path: Path):
    return load_config(tmp_path)


def test_read_of_data_file_denied(cfg) -> None:
    reason = deny_reason("Read", {"file_path": str(cfg.data_dir / "beneficiaries.xlsx")}, cfg)
    assert reason is not None
    assert "profile_dataset" in reason


def test_grep_and_glob_in_data_denied(cfg) -> None:
    assert deny_reason("Grep", {"path": str(cfg.data_dir)}, cfg)
    assert deny_reason("Glob", {"path": str(cfg.data_dir), "pattern": "*.xlsx"}, cfg)


def test_bash_touching_data_dir_denied(cfg) -> None:
    assert deny_reason("Bash", {"command": f"cat {cfg.data_dir / 'x.csv'}"}, cfg)


def test_project_docs_allowed(cfg) -> None:
    assert deny_reason("Read", {"file_path": str(cfg.docs_dir / "logframe.md")}, cfg) is None


def test_unrelated_paths_allowed(cfg, tmp_path: Path) -> None:
    assert deny_reason("Read", {"file_path": str(tmp_path / "README.md")}, cfg) is None
    assert deny_reason("Bash", {"command": "echo hello"}, cfg) is None


def test_mcp_tools_allowed(cfg) -> None:
    assert deny_reason("mcp__data__run_analysis", {"code": "print(1)"}, cfg) is None


@pytest.mark.skipif(sys.platform != "win32", reason="normcase folds case only on Windows")
def test_case_insensitive_on_windows_style_paths(cfg) -> None:
    path = str(cfg.data_dir / "file.csv").upper()
    assert deny_reason("Read", {"file_path": path}, cfg) is not None


async def test_adapter_denies_with_sdk_shape(cfg) -> None:
    blocked: list[tuple[str, str]] = []
    hook = make_pretooluse_hook(cfg, on_block=lambda t, r: blocked.append((t, r)))
    out = await hook(
        {"tool_name": "Read", "tool_input": {"file_path": str(cfg.data_dir / "a.csv")}},
        None,
        None,
    )
    decision = out["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert blocked and blocked[0][0] == "Read"


async def test_adapter_allows_with_empty_dict(cfg) -> None:
    hook = make_pretooluse_hook(cfg)
    out = await hook({"tool_name": "Bash", "tool_input": {"command": "ls"}}, None, None)
    assert out == {}


def test_bash_relative_data_ref_denied(cfg) -> None:
    assert deny_reason("Bash", {"command": "cat data/x.csv"}, cfg) is not None


def test_bash_unrelated_data_word_allowed(cfg) -> None:
    cmd = "echo data analysis of database/report.txt"
    assert deny_reason("Bash", {"command": cmd}, cfg) is None


def test_glob_pattern_only_denied(cfg) -> None:
    assert deny_reason("Glob", {"pattern": "data/**/*.csv"}, cfg) is not None


def test_relative_file_path_denied(cfg) -> None:
    assert deny_reason("Read", {"file_path": "data/x.csv"}, cfg) is not None


def test_sibling_data_prefix_dir_allowed(cfg) -> None:
    path = str(cfg.workspace / "data_export" / "x.csv")
    assert deny_reason("Read", {"file_path": path}, cfg) is None
