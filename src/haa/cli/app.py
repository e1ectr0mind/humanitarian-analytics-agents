"""CLI: `haa chat` (REPL over AnalyticsSession) and `haa demo` (generate data)."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from collections.abc import Callable
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


def run_connect_sharepoint(
    workspace: Path,
    name: str,
    site: str,
    tenant: str,
    client_id: str,
    folder: str | None,
    say: Callable[[str], None],
) -> str:
    from haa.connectors import msauth
    from haa.connectors.credentials import Connection, save_connection

    flow = msauth.start_device_flow(tenant, client_id)
    say(f"Open {flow.verification_uri} and enter the code: {flow.user_code}")
    say("Waiting for the sign-in to finish (Ctrl+C to abort)...")
    tokens = msauth.poll_for_token(flow)
    workspace.mkdir(parents=True, exist_ok=True)
    save_connection(
        workspace,
        Connection(name=name, kind="sharepoint", base_url=site,
                   tenant=tenant, client_id=client_id, folder=folder),
        tokens.to_json(),
    )
    return f"Connection {name!r} (sharepoint) saved. Tokens stored in the OS credential store."


def run_pull(workspace: Path, profile: str, form: str) -> str:
    from datetime import UTC, datetime

    from haa.config import ConfigError, load_config
    from haa.core.telemetry import SessionTelemetry
    from haa.core.tools.sourcetools import SourceToolbox

    try:
        cfg = load_config(workspace)
    except ConfigError as exc:
        return str(exc)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    telemetry = SessionTelemetry(cfg.logs_dir / f"cli-pull-{stamp}.jsonl")
    return SourceToolbox(cfg, telemetry).pull_form(profile, form)


def run_report(
    workspace: Path, kind: str, period: str | None, date_field: str | None
) -> tuple[str, bool]:
    from datetime import UTC, datetime

    from haa.core.telemetry import SessionTelemetry
    from haa.core.tools.reporttools import ReportToolbox

    start = end = None
    if period:
        if ".." not in period:
            return "Use --period START..END, for example 2026-06-01..2026-08-31.", False
        start, end = (part.strip() for part in period.split("..", 1))
    try:
        cfg = load_config(workspace)
    except ConfigError as exc:
        return str(exc), False
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    box = ReportToolbox(cfg, SessionTelemetry(cfg.logs_dir / f"cli-report-{stamp}.jsonl"))
    if kind == "indicators":
        ok, text = box._build_indicator_report(date_field, start, end)
    else:
        ok, text = box._build_5w(date_field, start, end)
    return text, ok


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="haa", description="Humanitarian Analytics Agents")
    sub = parser.add_subparsers(dest="command", required=True)

    chat = sub.add_parser("chat", help="Interactive analytics chat")
    chat.add_argument("--workspace", type=Path, default=Path("workspace"))
    chat.add_argument("--config", type=Path, default=None)

    demo = sub.add_parser("demo", help="Generate the synthetic demo workspace")
    demo.add_argument("--workspace", type=Path, default=Path("workspace"))

    connect = sub.add_parser("connect", help="Configure a data-source connection")
    connect.add_argument("kind", choices=["kobo", "ona", "sharepoint"])
    connect.add_argument("--workspace", type=Path, default=Path("workspace"))
    connect.add_argument("--site", default=None,
                         help="SharePoint site URL, or 'onedrive' for the personal drive")
    connect.add_argument("--tenant", default=None,
                         help="Entra tenant, e.g. contoso.onmicrosoft.com")
    connect.add_argument("--client-id", dest="client_id", default=None)
    connect.add_argument("--folder", default=None,
                         help="Folder to pin, e.g. 'Shared Documents/5W'")

    connections = sub.add_parser("connections", help="List configured connections")
    connections.add_argument("--workspace", type=Path, default=Path("workspace"))

    pull = sub.add_parser("pull", help="Pull a form's submissions into the workspace")
    pull.add_argument("profile")
    pull.add_argument("--form", required=True)
    pull.add_argument("--workspace", type=Path, default=Path("workspace"))

    report = sub.add_parser("report", help="Build a report without the LLM (indicators or 5W)")
    report.add_argument("kind", choices=["indicators", "5w"])
    report.add_argument("--workspace", type=Path, default=Path("workspace"))
    report.add_argument(
        "--period", default=None, help="START..END, for example 2026-06-01..2026-08-31"
    )
    report.add_argument(
        "--date-field",
        dest="date_field",
        default=None,
        help="Date column for the period (the 5W defaults to its when.field)",
    )

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
        if args.kind == "sharepoint":
            from haa.connectors.base import ConnectorError
            from haa.connectors.credentials import CredentialsError
            from haa.connectors.msauth import DEFAULT_CLIENT_ID

            site = args.site or input("Site URL (or 'onedrive'): ").strip()
            tenant = args.tenant or input("Tenant (e.g. contoso.onmicrosoft.com): ").strip()
            client_id = args.client_id or DEFAULT_CLIENT_ID
            try:
                console.print(run_connect_sharepoint(
                    args.workspace, name, site, tenant, client_id,
                    args.folder, console.print,
                ))
            except KeyboardInterrupt:
                console.print("[red]Sign-in aborted.[/red]")
                return 1
            except (ConnectorError, CredentialsError) as exc:
                console.print(f"[red]{exc}[/red]")
                return 1
            return 0
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

    if args.command == "report":
        text, ok = run_report(args.workspace, args.kind, args.period, args.date_field)
        console.print(text, markup=False, highlight=False)
        return 0 if ok else 1

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
