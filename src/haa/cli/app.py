"""CLI: `haa chat` (REPL over AnalyticsSession) and `haa demo` (generate data)."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown

from haa.config import ConfigError, HaaConfig, load_config
from haa.core.session import AnalyticsSession, BudgetExceeded


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="haa", description="Humanitarian Analytics Agents")
    sub = parser.add_subparsers(dest="command", required=True)

    chat = sub.add_parser("chat", help="Interactive analytics chat")
    chat.add_argument("--workspace", type=Path, default=Path("workspace"))
    chat.add_argument("--config", type=Path, default=None)

    demo = sub.add_parser("demo", help="Generate the synthetic demo workspace")
    demo.add_argument("--workspace", type=Path, default=Path("workspace"))
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
                    else:
                        console.print(f"[dim]{event.text}[/dim]")
            except BudgetExceeded as exc:
                console.print(f"[red]{exc}[/red]")
                break
    console.print(f"Session log: {session.telemetry.path}")
    console.print(session.telemetry.summary())


def main() -> int:
    args = build_parser().parse_args()
    console = Console()

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
