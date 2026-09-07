"""In-process MCP server exposing the data toolset to agents.

DataToolbox holds the logic (plain, testable); the @tool wrappers only adapt
to the SDK's content-block format.
"""

from __future__ import annotations

import asyncio
import json

import pandas as pd

from haa.config import HaaConfig
from haa.core.sandbox.executor import run_code
from haa.core.sandbox.guard import filter_output
from haa.core.telemetry import SessionTelemetry
from haa.core.tools.docs import DocError, list_docs, read_doc
from haa.core.tools.profiler import (
    detect_pii_columns,
    discover_datasets,
    profile_dataframe,
)

DATA_TOOL_NAMES = [
    "mcp__data__list_datasets",
    "mcp__data__profile_dataset",
    "mcp__data__run_analysis",
    "mcp__data__list_project_docs",
    "mcp__data__read_project_doc",
]

MAX_CONSECUTIVE_FAILURES = 3
_FAILURE_STOP_MESSAGE = (
    f"Execution disabled after {MAX_CONSECUTIVE_FAILURES} consecutive failed runs "
    "(spec: 3-attempt limit lives in the core, not the prompt). Stop trying; "
    "report honestly to the user what was attempted and why it failed. "
    "Do not invent results."
)


class DataToolbox:
    def __init__(self, config: HaaConfig, telemetry: SessionTelemetry) -> None:
        self.config = config
        self.telemetry = telemetry
        self._pii_map: dict[str, list[str]] = {}
        self._profiles: dict[str, dict] = {}
        self._consecutive_failures = 0
        self._broken: dict[str, str] = {}
        self._mtimes: dict[str, float] = {}

    def reset_failures(self) -> None:
        self._consecutive_failures = 0

    # -- datasets ---------------------------------------------------------
    def list_datasets(self) -> str:
        found = discover_datasets(self.config.data_dir)
        if not found:
            return "No datasets found in workspace/data/ (supported: .csv, .xlsx, .xls)."
        lines = [
            f"- {name} ({path.name}, {path.stat().st_size // 1024} KB)"
            for name, path in found.items()
        ]
        return "Available datasets:\n" + "\n".join(lines)

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

    def profile_dataset(self, name: str) -> str:
        profile = self._ensure_profiled(name)
        if profile is None:
            if name in self._broken:
                return f"Failed to read dataset {name!r}: {self._broken[name]}"
            known = ", ".join(sorted(discover_datasets(self.config.data_dir))) or "(none)"
            return f"Unknown dataset {name!r}. Available: {known}"
        return json.dumps(profile, ensure_ascii=False, indent=1)

    def run_analysis(self, code: str) -> str:
        if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            return _FAILURE_STOP_MESSAGE
        # Profile every known dataset once so the PII map covers load_dataset calls.
        for ds in discover_datasets(self.config.data_dir):
            self._ensure_profiled(ds)
        if "include_pii=True" in code:
            self.telemetry.log(
                "pii_access", note="load_dataset(include_pii=True) in submitted code"
            )
        self.telemetry.log("code_executed", code=code)
        result = run_code(code, self.config, self._pii_map)
        failed = result.timed_out or result.returncode != 0
        self._consecutive_failures = self._consecutive_failures + 1 if failed else 0
        if result.timed_out:
            return f"Execution timed out after {self.config.sandbox_timeout_s}s. Simplify the code."
        filtered = filter_output(
            result.stdout,
            row_cap=self.config.table_row_cap,
            size_cap=self.config.output_limit_bytes,
        )
        parts = [filtered.text] if filtered.text.strip() else []
        parts.extend(filtered.notices)
        if result.returncode != 0:
            err = filter_output(
                result.stderr, row_cap=self.config.table_row_cap, size_cap=4096
            ).text
            parts.append(f"[error, exit code {result.returncode}]\n{err}")
        return "\n".join(parts) or "(no output — use print() to return results)"

    # -- project docs -----------------------------------------------------
    def list_project_docs(self) -> str:
        docs = list_docs(self.config.docs_dir)
        if not docs:
            return "No project documents in workspace/project_docs/."
        return "Project documents:\n" + "\n".join(
            f"- {d['name']} ({d['type']}, {d['size_kb']} KB)" for d in docs
        )

    def read_project_doc(self, name: str) -> str:
        try:
            return read_doc(self.config.docs_dir, name)
        except DocError as exc:
            return f"Document not found or unreadable: {exc}"


def build_data_server(box: DataToolbox):
    from claude_agent_sdk import create_sdk_mcp_server, tool

    def _text(result: str) -> dict:
        return {"content": [{"type": "text", "text": result}]}

    @tool("list_datasets", "List tabular datasets available in the workspace", {})
    async def list_datasets(args: dict) -> dict:
        return _text(box.list_datasets())

    @tool(
        "profile_dataset",
        "Safe profile of a dataset: columns, dtypes, null rates, category values. "
        "Never returns raw rows.",
        {"name": str},
    )
    async def profile_dataset(args: dict) -> dict:
        return _text(box.profile_dataset(str(args["name"])))

    @tool(
        "run_analysis",
        "Execute pandas code in the local sandbox. Use load_dataset(name) to load "
        "data (PII columns are stripped automatically); print() results; save "
        "charts into CHARTS_DIR. Raw rows never reach you — work with aggregates.",
        {"code": str},
    )
    async def run_analysis(args: dict) -> dict:
        return _text(await asyncio.to_thread(box.run_analysis, str(args["code"])))

    @tool("list_project_docs", "List project documentation files", {})
    async def list_project_docs(args: dict) -> dict:
        return _text(box.list_project_docs())

    @tool("read_project_doc", "Read one project document as text", {"name": str})
    async def read_project_doc(args: dict) -> dict:
        return _text(box.read_project_doc(str(args["name"])))

    return create_sdk_mcp_server(
        name="data",
        version="0.1.0",
        tools=[list_datasets, profile_dataset, run_analysis, list_project_docs, read_project_doc],
    )
