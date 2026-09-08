"""In-process MCP server for the indicators registry and form design."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import yaml

from haa.config import HaaConfig
from haa.core.telemetry import SessionTelemetry
from haa.forms.compiler import compile_check
from haa.forms.model import iter_questions, validate_model
from haa.forms.renderer import render_xlsx
from haa.forms.storage import (
    FormStorageError,
    form_paths,
    list_forms,
    load_model,
    parse_model_yaml,
    save_model,
)
from haa.indicators.registry import (
    RegistryError,
    load_registry,
    parse_registry_yaml,
    save_registry,
    summarize,
    validate_registry,
)

FORM_TOOL_NAMES = [
    "mcp__forms__list_local_forms",
    "mcp__forms__load_form",
    "mcp__forms__save_form",
    "mcp__forms__read_indicators",
    "mcp__forms__save_indicators",
]


class FormToolbox:
    def __init__(self, config: HaaConfig, telemetry: SessionTelemetry) -> None:
        self.config = config
        self.telemetry = telemetry
        self._failed_saves: dict[str, int] = {}

    # -- forms ------------------------------------------------------------
    def list_local_forms(self) -> str:
        names = list_forms(self.config.forms_dir)
        if not names:
            return "No local forms yet. Create one with save_form."
        return "Local forms:\n" + "\n".join(f"- {n}" for n in names)

    def load_form(self, name: str) -> str:
        try:
            model = load_model(self.config.forms_dir, name)
        except FormStorageError as exc:
            return str(exc)
        return yaml.safe_dump(model, allow_unicode=True, sort_keys=False)

    def save_form(self, name: str, model_yaml: str) -> str:
        try:
            _, xlsx_path = form_paths(self.config.forms_dir, name)
            model = parse_model_yaml(model_yaml)
        except FormStorageError as exc:
            self._failed_saves[name] = self._failed_saves.get(name, 0) + 1
            return str(exc)

        errors = validate_model(model)
        if errors:
            self._failed_saves[name] = self._failed_saves.get(name, 0) + 1
            return "Model validation failed:\n" + "\n".join(f"- {e}" for e in errors)

        forms_dir = self.config.forms_dir
        forms_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(suffix=".xlsx", dir=str(forms_dir))
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            render_xlsx(model, tmp_path)
            compile_errors = compile_check(tmp_path)
            if compile_errors:
                self._failed_saves[name] = self._failed_saves.get(name, 0) + 1
                return "pyxform rejected the form:\n" + "\n".join(
                    f"- {e}" for e in compile_errors
                )
            # Write the model file before swapping the xlsx into place, so a
            # write failure can never pair a new workbook with a stale model.
            save_model(self.config.forms_dir, name, model)
            os.replace(tmp_path, xlsx_path)  # atomic swap on the same filesystem
        except OSError as exc:
            self._failed_saves[name] = self._failed_saves.get(name, 0) + 1
            return (
                f"Could not write the form files: {exc.strerror or exc}. "
                "If the .xlsx is open in Excel, close it and try again."
            )
        finally:
            tmp_path.unlink(missing_ok=True)  # no-op after a successful replace

        questions = sum(1 for _ in iter_questions(model))
        groups = len(model.get("groups") or [])
        attempts_failed = self._failed_saves.pop(name, 0)
        self.telemetry.log(
            "form_saved", form=name, questions=questions, attempts_failed=attempts_failed
        )
        return (
            f"Form {name!r} saved and compiled successfully: {groups} groups, "
            f"{questions} questions, languages uk + en.\n"
            f"Model: {name}.form.yaml — XLSForm: {xlsx_path.name} "
            "(upload it to Kobo/Ona manually)."
        )

    # -- indicators -------------------------------------------------------
    def read_indicators(self) -> str:
        try:
            data = load_registry(self.config.workspace)
        except RegistryError as exc:
            return str(exc)
        if not (data.get("indicators") or []):
            return (
                "The indicators registry is empty. Ask to extract indicators from the "
                "project documentation first."
            )
        return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)

    def save_indicators(self, yaml_text: str) -> str:
        try:
            data = parse_registry_yaml(yaml_text)
        except RegistryError as exc:
            return str(exc)
        errors = validate_registry(data)
        if errors:
            return "Registry validation failed:\n" + "\n".join(f"- {e}" for e in errors)
        try:
            save_registry(self.config.workspace, data)
        except OSError as exc:
            return f"Could not write the registry: {exc.strerror or exc}."
        stats = summarize(data)
        self.telemetry.log("indicators_saved", **stats)
        return (
            f"Registry saved: {stats['count']} indicators, {stats['with_target']} with a "
            f"target, {stats['with_measure']} with a machine-readable measure."
        )


def build_forms_server(box: FormToolbox):
    import asyncio

    from claude_agent_sdk import create_sdk_mcp_server, tool

    def _text(result: str) -> dict:
        return {"content": [{"type": "text", "text": result}]}

    @tool("list_local_forms", "List form models stored in the workspace", {})
    async def list_local_forms_tool(args: dict) -> dict:
        return _text(box.list_local_forms())

    @tool("load_form", "Load a local form model as YAML", {"name": str})
    async def load_form_tool(args: dict) -> dict:
        return _text(box.load_form(str(args["name"])))

    @tool(
        "save_form",
        "Validate a form model (YAML), render it to XLSForm and compile it with "
        "pyxform. Returns validation or compile errors instead of saving when the "
        "model is wrong.",
        {"name": str, "model_yaml": str},
    )
    async def save_form_tool(args: dict) -> dict:
        return _text(
            await asyncio.to_thread(box.save_form, str(args["name"]), str(args["model_yaml"]))
        )

    @tool("read_indicators", "Read the indicators registry as YAML", {})
    async def read_indicators_tool(args: dict) -> dict:
        return _text(box.read_indicators())

    @tool(
        "save_indicators",
        "Validate and save the indicators registry (YAML with an 'indicators' list)",
        {"yaml_text": str},
    )
    async def save_indicators_tool(args: dict) -> dict:
        return _text(box.save_indicators(str(args["yaml_text"])))

    return create_sdk_mcp_server(
        name="forms",
        version="0.1.0",
        tools=[
            list_local_forms_tool,
            load_form_tool,
            save_form_tool,
            read_indicators_tool,
            save_indicators_tool,
        ],
    )
