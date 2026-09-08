"""Read and write form models as human-editable YAML."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

SAFE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,29}\Z")


class FormStorageError(ValueError):
    """Friendly, user-facing storage failure."""


def form_paths(forms_dir: Path, name: str) -> tuple[Path, Path]:
    if not SAFE_NAME_RE.match(name or ""):
        raise FormStorageError(
            f"Invalid form name {name!r} — use lowercase letters, digits and underscores"
        )
    return forms_dir / f"{name}.form.yaml", forms_dir / f"{name}.xlsx"


def parse_model_yaml(text: str) -> dict:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise FormStorageError(f"Could not parse YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise FormStorageError("Form model must be a YAML mapping")
    return data


def save_model(forms_dir: Path, name: str, model: dict) -> Path:
    yaml_path, _ = form_paths(forms_dir, name)
    forms_dir.mkdir(parents=True, exist_ok=True)
    yaml_path.write_text(
        yaml.safe_dump(model, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return yaml_path


def load_model(forms_dir: Path, name: str) -> dict:
    yaml_path, _ = form_paths(forms_dir, name)
    if not yaml_path.is_file():
        known = ", ".join(list_forms(forms_dir)) or "(none)"
        raise FormStorageError(f"Form {name!r} not found. Local forms: {known}")
    return parse_model_yaml(yaml_path.read_text(encoding="utf-8"))


def list_forms(forms_dir: Path) -> list[str]:
    if not forms_dir.is_dir():
        return []
    return sorted(p.name[: -len(".form.yaml")] for p in forms_dir.glob("*.form.yaml"))
