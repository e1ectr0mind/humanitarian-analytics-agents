"""CLI: `haa chat` (REPL over AnalyticsSession) and `haa demo` (generate data)."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown

from haa.config import ConfigError, HaaConfig, load_config
from haa.core.session import AnalyticsSession, BudgetExceeded

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
        with make_connector(conn, token) as connector:
            result = connector.pull(form, workspace / "data")
    except (ConnectorError, CredentialsError) as exc:
        return str(exc)
    return f"Pulled {result.rows} submissions of {result.form.name!r} into {result.path.name}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="haa", description="Humanitarian Analytics Agents")
    sub = parser.add_subparsers(dest="command", required=True)

    chat = sub.add_parser("chat", help="Interactive analytics chat")
    chat.add_argument("--workspace", type=Path, default=Path("workspace"))
    chat.add_argument("--config", type=Path, default=None)

    demo = sub.add_parser("demo", help="Generate the synthetic demo workspace")
    demo.add_argument("--workspace", type=Path, default=Path("workspace"))

    connect = sub.add_parser("connect", help="Configure a data-source connection")
    connect.add_argument("kind", choices=["kobo", "ona"])
    connect.add_argument("--workspace", type=Path, default=Path("workspace"))

    connections = sub.add_parser("connections", help="List configured connections")
    connections.add_argument("--workspace", type=Path, default=Path("workspace"))

    pull = sub.add_parser("pull", help="Pull a form's submissions into the workspace")
    pull.add_argument("profile")
    pull.add_argument("--form", required=True)
    pull.add_argument("--workspace", type=Path, default=Path("workspace"))

    return parser


def validate_workspace(cfg: HaaConfig) -> list[str]:
    from haa.core.tools.profiler import discover_datasets

    warnings: list[str] = []
    if not discover_datasets(cfg.data_dir):
        warnings.append(
            "No datasets in workspace/data/ — add .csv/.xlsx files, or run `haa demo` "
            "to generate synthetic demo data."
        )
    if not any(cfg.docs_dir.iterdir()):
        warnings.append("No project docs in workspace/project_docs/ (optional).")
    return warnings


async def _repl(cfg: HaaConfig, console: Console) -> None:
    async with AnalyticsSession(cfg) as session:
        console.print("[bold]haa[/bold] — ask about your data. /cost for spend, /quit to exit.")
        while True:
            try:
                question = console.input("[bold cyan]you>[/bold cyan] ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not question:
                continue
            if question in {"/quit", "/exit"}:
                break
            if question == "/cost":
                console.print(session.telemetry.summary())
                continue
            try:
                async for event in session.ask(question):
                    if event.kind == "text":
                        console.print(Markdown(event.text))
                    elif event.kind == "tool":
                        console.print(f"[dim]· {event.text}[/dim]")
                    elif event.kind == "error":
                        console.print(f"[red]{event.text}[/red]")
                    else:
                        console.print(f"[dim]{event.text}[/dim]")
            except BudgetExceeded as exc:
                console.print(f"[red]{exc}[/red]")
                break
            except KeyboardInterrupt:
                console.print("[dim]interrupted[/dim]")
                break
            except Exception as exc:  # noqa: BLE001 - REPL must survive transient API/network errors
                console.print(f"[red]Error during analysis: {exc}[/red]")
                console.print("[yellow]Session saved; retry or /quit.[/yellow]")
                continue
    console.print(f"Session log: {session.telemetry.path}")
    console.print(session.telemetry.summary())


def main() -> int:
    args = build_parser().parse_args()
    console = Console()

    if args.command == "connect":
        name = input(f"Profile name [{args.kind}]: ").strip() or args.kind
        default_url = DEFAULT_URLS[args.kind]
        base_url = input(f"Server URL [{default_url}]: ").strip() or default_url
        token = getpass.getpass("API token: ")
        console.print(run_connect(args.workspace, args.kind, name, base_url, token))
        return 0

    if args.command == "connections":
        from haa.connectors.credentials import list_connections

        conns = list_connections(args.workspace)
        if not conns:
            console.print("No connections configured. Run: haa connect <kobo|ona>")
            return 0
        for conn in conns:
            console.print(f"{conn.name:20} {conn.kind:10} {conn.base_url}")
        return 0

    if args.command == "pull":
        console.print(run_pull(args.workspace, args.profile, args.form))
        return 0

    if args.command == "demo":
        from haa.demo import generate

        args.workspace.mkdir(parents=True, exist_ok=True)
        path = generate(args.workspace)
        console.print(f"Demo data written to {path}")
        return 0

    try:
        cfg = load_config(args.workspace, args.config)
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1
    for warning in validate_workspace(cfg):
        console.print(f"[yellow]{warning}[/yellow]")
    try:
        asyncio.run(_repl(cfg, console))
    except KeyboardInterrupt:
        pass
    except Exception as exc:  # noqa: BLE001 - startup/session failures must not crash bare
        console.print(f"[red]Failed to start session: {exc}[/red]")
        console.print("[yellow]Check ANTHROPIC_API_KEY and network connectivity.[/yellow]")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
