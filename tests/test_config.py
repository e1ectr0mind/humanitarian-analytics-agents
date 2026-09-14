import dataclasses
from pathlib import Path

import pytest

from haa.config import ConfigError, HaaConfig, load_config


def test_defaults(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    assert cfg.orchestrator_model == "claude-opus-5"
    assert cfg.analyst_model == "claude-opus-5"
    assert cfg.max_budget_usd == 2.0
    assert cfg.sandbox_timeout_s == 60
    assert cfg.output_limit_bytes == 32768
    assert cfg.table_row_cap == 50


def test_creates_workspace_subdirs(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    assert cfg.data_dir == tmp_path.resolve() / "data"
    assert cfg.docs_dir == tmp_path.resolve() / "project_docs"
    for d in (cfg.data_dir, cfg.docs_dir, cfg.charts_dir, cfg.logs_dir):
        assert d.is_dir()


def test_reads_config_toml(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text(
        'analyst_model = "claude-sonnet-5"\nmax_budget_usd = 0.5\n', encoding="utf-8"
    )
    cfg = load_config(tmp_path)
    assert cfg.analyst_model == "claude-sonnet-5"
    assert cfg.max_budget_usd == 0.5
    assert cfg.orchestrator_model == "claude-opus-5"  # untouched default


def test_unknown_key_rejected(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("tabel_row_cap = 10\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="tabel_row_cap"):
        load_config(tmp_path)


def test_missing_workspace_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="does not exist"):
        load_config(tmp_path / "nope")


def test_config_is_frozen(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    with pytest.raises((AttributeError, dataclasses.FrozenInstanceError)):
        cfg.max_budget_usd = 99  # type: ignore[misc]
    assert isinstance(cfg, HaaConfig)


def test_cleaner_model_default_and_override(tmp_path: Path) -> None:
    assert load_config(tmp_path).cleaner_model == "claude-opus-5"
    (tmp_path / "config.toml").write_text('cleaner_model = "claude-sonnet-5"', encoding="utf-8")
    assert load_config(tmp_path).cleaner_model == "claude-sonnet-5"


def test_reports_dir_created(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    assert cfg.reports_dir == tmp_path.resolve() / "reports"
    assert cfg.reports_dir.is_dir()


def test_designer_model_default_and_override(tmp_path: Path) -> None:
    assert load_config(tmp_path).designer_model == "claude-opus-5"
    (tmp_path / "config.toml").write_text('designer_model = "claude-sonnet-5"', encoding="utf-8")
    assert load_config(tmp_path).designer_model == "claude-sonnet-5"


def test_forms_dir_created(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    assert cfg.forms_dir == tmp_path.resolve() / "forms"
    assert cfg.forms_dir.is_dir()


def test_reporter_model_default_and_override(tmp_path: Path) -> None:
    assert load_config(tmp_path).reporter_model == "claude-opus-5"
    (tmp_path / "config.toml").write_text('reporter_model = "claude-sonnet-5"', encoding="utf-8")
    assert load_config(tmp_path).reporter_model == "claude-sonnet-5"
