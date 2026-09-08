"""Configuration for a HAA workspace session."""

from __future__ import annotations

import dataclasses
import tomllib
from dataclasses import dataclass
from pathlib import Path


class ConfigError(ValueError):
    """Raised for invalid workspace or config.toml contents."""


@dataclass(frozen=True)
class HaaConfig:
    workspace: Path
    orchestrator_model: str = "claude-opus-5"
    analyst_model: str = "claude-opus-5"
    cleaner_model: str = "claude-opus-5"
    designer_model: str = "claude-opus-5"
    max_budget_usd: float = 2.0
    sandbox_timeout_s: int = 60
    output_limit_bytes: int = 32768
    table_row_cap: int = 50

    @property
    def data_dir(self) -> Path:
        return self.workspace / "data"

    @property
    def docs_dir(self) -> Path:
        return self.workspace / "project_docs"

    @property
    def charts_dir(self) -> Path:
        return self.workspace / "charts"

    @property
    def logs_dir(self) -> Path:
        return self.workspace / "logs"

    @property
    def reports_dir(self) -> Path:
        return self.workspace / "reports"

    @property
    def forms_dir(self) -> Path:
        return self.workspace / "forms"


_TUNABLE = {f.name for f in dataclasses.fields(HaaConfig)} - {"workspace"}


def load_config(workspace: Path, config_file: Path | None = None) -> HaaConfig:
    workspace = workspace.resolve()
    if not workspace.is_dir():
        raise ConfigError(f"Workspace does not exist: {workspace}")

    values: dict[str, object] = {}
    path = config_file or workspace / "config.toml"
    if path.is_file():
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        unknown = set(data) - _TUNABLE
        if unknown:
            raise ConfigError(f"Unknown config keys: {', '.join(sorted(unknown))}")
        values = data

    cfg = HaaConfig(workspace=workspace, **values)  # type: ignore[arg-type]
    for d in (
        cfg.data_dir,
        cfg.docs_dir,
        cfg.charts_dir,
        cfg.logs_dir,
        cfg.reports_dir,
        cfg.forms_dir,
    ):
        d.mkdir(parents=True, exist_ok=True)
    return cfg
