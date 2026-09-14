"""In-process MCP server for deterministic reports: indicator progress and the 5W matrix."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import yaml

from haa.config import HaaConfig
from haa.core.telemetry import SessionTelemetry
from haa.indicators.registry import RegistryError, load_registry
from haa.reporting import fivew
from haa.reporting.engine import ReportRun, evaluate_registry
from haa.reporting.mapping import (
    load_mapping,
    mapping_path,
    parse_mapping_yaml,
    save_mapping,
    validate_mapping,
)
from haa.reporting.render import (
    format_value,
    period_label,
    render_5w,
    render_indicator_report,
    report_paths,
)
from haa.reporting.sources import ReportError, load_scoped, parse_period

REPORT_TOOL_NAMES = [
    "mcp__reports__compute_indicators",
    "mcp__reports__build_indicator_report",
    "mcp__reports__read_5w_mapping",
    "mcp__reports__save_5w_mapping",
    "mcp__reports__build_5w",
]

# A dict of name -> type makes the SDK mark every argument required; the period
# arguments are optional, so these tools declare a full JSON Schema instead.
PERIOD_SCHEMA = {
    "type": "object",
    "properties": {
        "date_field": {
            "type": "string",
            "description": "Dataset column holding the date (the 5W defaults to when.field)",
        },
        "start": {"type": "string", "description": "Period start, YYYY-MM-DD"},
        "end": {"type": "string", "description": "Period end, YYYY-MM-DD (inclusive)"},
    },
    "required": [],
}

_WRITE_FAILED = (
    "Could not write the report files: {detail}. "
    "If a report .xlsx is open in Excel, close it and try again."
)


def _name(names: dict) -> str:
    return " / ".join(part for part in (names.get("uk"), names.get("en")) if part)


def _five_w_display_columns(
    columns: list[str], dimensions: list[str], listable: frozenset[str]
) -> list[str]:
    """`table.columns` for display: `<dimension>=<category>` labels of a dimension the
    profiler would not list are collapsed into one `<dimension>=<N categories>` token.
    """
    prefixes = {f"{dimension}=": dimension for dimension in dimensions if dimension not in listable}
    counts: dict[str, int] = {}
    for column in columns:
        for prefix, dimension in prefixes.items():
            if column.startswith(prefix):
                counts[dimension] = counts.get(dimension, 0) + 1
                break
    display: list[str] = []
    collapsed: set[str] = set()
    for column in columns:
        dimension = next((d for p, d in prefixes.items() if column.startswith(p)), None)
        if dimension is None:
            display.append(column)
        elif dimension not in collapsed:
            display.append(f"{dimension}={counts[dimension]} categories")
            collapsed.add(dimension)
    return display


def run_summary(run: ReportRun) -> str:
    """Plain-text digest of a report run: aggregates only, never rows."""
    lines = [f"Period: {period_label(run.period)}"]
    for source in run.sources.values():
        bad_date = (
            f", {source.rows_bad_date} without a valid date" if run.period is not None else ""
        )
        lines.append(
            f"Source {source.dataset}: {source.path.name} ({source.copy_label} copy), "
            f"{source.rows_in_scope} rows in scope, "
            f"{source.rows_excluded_by_period} excluded by period{bad_date}"
        )
        if source.rows_in_scope == 0:
            lines.append(
                f"Warning: no rows in scope for {source.dataset} — its indicators are 0 "
                "or not computable."
            )
        if source.stale_warning:
            lines.append(f"Warning: {source.stale_warning}")
    lines.append(f"Computed ({len(run.computed)}):")
    for r in run.computed:
        target = f", target {format_value(r.target, r.unit)}" if r.target is not None else ""
        progress = (
            f" = {format_value(r.progress_pct, 'percent')} of target"
            if r.progress_pct is not None
            else ""
        )
        lines.append(
            f"- {r.code} {_name(r.name)}: actual {format_value(r.actual, r.unit)}"
            f"{target}{progress}"
        )
        result_source = run.sources.get(r.dataset)
        listable = result_source.listable_columns if result_source is not None else frozenset()
        for dimension, rows in r.breakdowns.items():
            # Same rule as profile_dataset: category values of a high-cardinality
            # column (IDs, dates, free text) never reach the LLM.
            if dimension not in listable:
                lines.append(
                    f"    by {dimension}: {len(rows)} categories — values not shown here "
                    "(too many distinct values in the dataset); see the report file"
                )
                continue
            parts = [
                f"{row.category} {format_value(row.value, r.unit)}"
                if row.reason is None
                else f"{row.category} n/a ({row.reason})"
                for row in rows
            ]
            lines.append(f"    by {dimension}: {'; '.join(parts)}")
        for dimension, reason in r.breakdown_errors.items():
            lines.append(f"    by {dimension}: not available — {reason}")
    lines.append(f"Not computable ({len(run.not_computable)}):")
    lines += [f"- {r.code} {_name(r.name)}: {r.reason}" for r in run.not_computable]
    return "\n".join(lines)


class ReportToolbox:
    def __init__(
        self,
        config: HaaConfig,
        telemetry: SessionTelemetry,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.config = config
        self.telemetry = telemetry
        self._today = today

    # -- indicators -------------------------------------------------------
    def _run(self, date_field: str | None, start: str | None, end: str | None) -> ReportRun:
        period = parse_period(date_field, start, end)
        try:
            registry = load_registry(self.config.workspace)
        except RegistryError as exc:
            raise ReportError(f"indicators.yaml could not be read — {exc}") from exc
        return evaluate_registry(registry, self.config.data_dir, period)

    def compute_indicators(
        self, date_field: str | None = None, start: str | None = None, end: str | None = None
    ) -> str:
        try:
            run = self._run(date_field, start, end)
        except ReportError as exc:
            return str(exc)
        return run_summary(run)

    def _build_indicator_report(
        self, date_field: str | None = None, start: str | None = None, end: str | None = None
    ) -> tuple[bool, str]:
        try:
            run = self._run(date_field, start, end)
        except ReportError as exc:
            return False, str(exc)
        today = self._today()
        md_path, xlsx_path = report_paths(self.config.reports_dir, "indicators", today)
        try:
            render_indicator_report(run, md_path, xlsx_path, today)
        except OSError as exc:
            return False, _WRITE_FAILED.format(detail=exc.strerror or exc)
        self.telemetry.log(
            "report_built",
            report="indicators",
            computed=len(run.computed),
            not_computable=len(run.not_computable),
            rows=sum(s.rows_in_scope for s in run.sources.values()),
            paths=[md_path.name, xlsx_path.name],
        )
        return True, (
            f"{run_summary(run)}\n"
            f"Report files: reports/{md_path.name}, reports/{xlsx_path.name}"
        )

    def build_indicator_report(
        self, date_field: str | None = None, start: str | None = None, end: str | None = None
    ) -> str:
        _, text = self._build_indicator_report(date_field, start, end)
        return text

    # -- 5W ---------------------------------------------------------------
    def read_5w_mapping(self) -> str:
        try:
            mapping = load_mapping(self.config.workspace)
        except ReportError as exc:
            return str(exc)
        if mapping is None:
            return (
                "There is no 5W mapping yet (workspace/5w.yaml). Profile the dataset, "
                "then propose one with save_5w_mapping."
            )
        return yaml.safe_dump(mapping, allow_unicode=True, sort_keys=False)

    def save_5w_mapping(self, yaml_text: str) -> str:
        try:
            mapping = parse_mapping_yaml(yaml_text)
        except ReportError as exc:
            return str(exc)
        errors = validate_mapping(mapping)
        if errors:
            return "5W mapping validation failed — nothing was saved:\n" + "\n".join(
                f"- {e}" for e in errors
            )
        replaced = mapping_path(self.config.workspace).is_file()
        try:
            save_mapping(self.config.workspace, mapping)
        except OSError as exc:
            return f"Could not write 5w.yaml: {exc.strerror or exc}."
        self.telemetry.log(
            "mapping_saved", dataset=mapping["dataset"], columns=list(mapping["where"])
        )
        note = " (replaced the previous mapping)" if replaced else ""
        return (
            f"5W mapping saved to 5w.yaml{note}: dataset {mapping['dataset']!r}, "
            f"where = {', '.join(mapping['where'])}. Build the table with build_5w."
        )

    def _build_5w(
        self, date_field: str | None = None, start: str | None = None, end: str | None = None
    ) -> tuple[bool, str]:
        try:
            mapping = load_mapping(self.config.workspace)
            if mapping is None:
                return False, (
                    "There is no 5W mapping yet (workspace/5w.yaml) — ask the reporter "
                    "to propose one."
                )
            errors = validate_mapping(mapping)
            if errors:
                text = "5w.yaml is invalid — fix it by hand or save a corrected mapping:\n" + (
                    "\n".join(f"- {e}" for e in errors)
                )
                return False, text
            period = parse_period(date_field or mapping["when"]["field"], start, end)
            df, source, pii = load_scoped(self.config.data_dir, mapping["dataset"], period)
            table = fivew.build_5w(df, mapping, pii)
        except ReportError as exc:
            return False, str(exc)
        today = self._today()
        md_path, xlsx_path = report_paths(self.config.reports_dir, "5w", today)
        try:
            render_5w(table, source, period, md_path, xlsx_path, today)
        except OSError as exc:
            return False, _WRITE_FAILED.format(detail=exc.strerror or exc)
        self.telemetry.log(
            "report_built",
            report="5w",
            rows=len(table.rows),
            unique_reach=table.unique_reach,
            paths=[md_path.name, xlsx_path.name],
        )
        dimensions = list(mapping["whom"].get("disaggregation") or [])
        display_columns = _five_w_display_columns(
            table.columns, dimensions, source.listable_columns
        )
        bad_date = f", {source.rows_bad_date} without a valid date" if period is not None else ""
        lines = [
            f"5W built: {len(table.rows)} rows; columns: {', '.join(display_columns)}.",
            f"Unique reach (distinct {table.id_field}): {table.unique_reach}; "
            f"records without any activity: {table.rows_without_activity}.",
            f"Period: {period_label(period)}; source {source.path.name} "
            f"({source.copy_label} copy), {source.rows_in_scope} rows in scope{bad_date}.",
        ]
        if not table.rows:
            lines.append("Warning: no activity rows in scope — the table is empty.")
        if source.stale_warning:
            lines.append(f"Warning: {source.stale_warning}")
        lines.append(f"Report files: reports/{md_path.name}, reports/{xlsx_path.name}")
        return True, "\n".join(lines)

    def build_5w(
        self, date_field: str | None = None, start: str | None = None, end: str | None = None
    ) -> str:
        _, text = self._build_5w(date_field, start, end)
        return text


def build_reports_server(box: ReportToolbox):
    import asyncio

    from claude_agent_sdk import create_sdk_mcp_server, tool

    def _text(result: str) -> dict:
        return {"content": [{"type": "text", "text": result}]}

    def _period(args: dict) -> tuple[str | None, str | None, str | None]:
        date_field, start, end = (
            str(args[key]) if args.get(key) else None for key in ("date_field", "start", "end")
        )
        return date_field, start, end

    @tool(
        "compute_indicators",
        "Compute every registry indicator that has a machine-readable measure: actual, "
        "target, % progress and disaggregation. Deterministic — quote these numbers. "
        "Optional period: date_field + start + end (YYYY-MM-DD).",
        PERIOD_SCHEMA,
    )
    async def compute_indicators_tool(args: dict) -> dict:
        return _text(await asyncio.to_thread(box.compute_indicators, *_period(args)))

    @tool(
        "build_indicator_report",
        "Write the indicator progress report (reports/indicators_<date>.md and .xlsx) and "
        "return its summary. Optional period: date_field + start + end (YYYY-MM-DD).",
        PERIOD_SCHEMA,
    )
    async def build_indicator_report_tool(args: dict) -> dict:
        return _text(await asyncio.to_thread(box.build_indicator_report, *_period(args)))

    @tool("read_5w_mapping", "Read the 5W mapping (workspace/5w.yaml) as YAML", {})
    async def read_5w_mapping_tool(args: dict) -> dict:
        return _text(box.read_5w_mapping())

    @tool(
        "save_5w_mapping",
        "Validate and save the 5W mapping (YAML). Returns validation errors instead of "
        "saving when the mapping is wrong.",
        {"yaml_text": str},
    )
    async def save_5w_mapping_tool(args: dict) -> dict:
        return _text(box.save_5w_mapping(str(args["yaml_text"])))

    @tool(
        "build_5w",
        "Build the 5W matrix from workspace/5w.yaml (reports/5w_<date>.md and .xlsx) and "
        "return its summary. Optional period: start + end (YYYY-MM-DD); date_field "
        "defaults to the mapping's when.field.",
        PERIOD_SCHEMA,
    )
    async def build_5w_tool(args: dict) -> dict:
        return _text(await asyncio.to_thread(box.build_5w, *_period(args)))

    return create_sdk_mcp_server(
        name="reports",
        version="0.1.0",
        tools=[
            compute_indicators_tool,
            build_indicator_report_tool,
            read_5w_mapping_tool,
            save_5w_mapping_tool,
            build_5w_tool,
        ],
    )
