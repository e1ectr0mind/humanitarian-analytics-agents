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
            return "No connections configured. Set one up with: haa connect kobo|ona|sharepoint"
        return "Configured connections:\n" + "\n".join(
            f"- {c.name} ({c.kind}, {c.base_url})" for c in conns
        )

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


def build_sources_server(box: SourceToolbox):
    import asyncio

    from claude_agent_sdk import create_sdk_mcp_server, tool

    def _text(result: str) -> dict:
        return {"content": [{"type": "text", "text": result}]}

    @tool("list_connections", "List configured data-source connections", {})
    async def list_connections_tool(args: dict) -> dict:
        return _text(box.list_connections())

    @tool(
        "list_remote_forms",
        "List forms available on a configured connection",
        {"connection": str},
    )
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
