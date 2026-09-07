"""PreToolUse hook enforcing the PII boundary: no direct reads of workspace/data."""

from __future__ import annotations

import os
from collections.abc import Callable

from haa.config import HaaConfig

BLOCK_MESSAGE = (
    "Direct access to raw data files is blocked by the PII boundary. "
    "Use profile_dataset / run_analysis from the data toolset instead."
)

_FILE_TOOLS = {"Read", "Grep", "Glob", "Edit", "Write", "NotebookEdit"}
_PATH_KEYS = ("file_path", "path", "notebook_path")


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _under_data_dir(path: str, config: HaaConfig) -> bool:
    return _norm(path).startswith(_norm(str(config.data_dir)))


def deny_reason(tool_name: str, tool_input: dict, config: HaaConfig) -> str | None:
    if tool_name in _FILE_TOOLS:
        for key in _PATH_KEYS:
            value = tool_input.get(key)
            if isinstance(value, str) and value and _under_data_dir(value, config):
                return BLOCK_MESSAGE
    elif tool_name == "Bash":
        command = str(tool_input.get("command", ""))
        needle = os.path.normcase(str(config.data_dir))
        if needle in os.path.normcase(command):
            return BLOCK_MESSAGE
    return None


def make_pretooluse_hook(
    config: HaaConfig, on_block: Callable[[str, str], None] | None = None
):
    async def hook(input_data: dict, tool_use_id, context) -> dict:
        tool_name = str(input_data.get("tool_name", ""))
        tool_input = input_data.get("tool_input") or {}
        reason = deny_reason(tool_name, tool_input, config)
        if reason is None:
            return {}
        if on_block is not None:
            on_block(tool_name, reason)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    return hook
