import copy
from pathlib import Path

import openpyxl
import pytest
import yaml

from haa.config import load_config
from haa.core.telemetry import SessionTelemetry
from haa.core.tools.formtools import FORM_TOOL_NAMES, FormToolbox
from tests.test_form_model import VALID as VALID_FORM
from tests.test_indicators_registry import VALID as VALID_REGISTRY


@pytest.fixture()
def box(tmp_path: Path) -> FormToolbox:
    cfg = load_config(tmp_path)
    return FormToolbox(cfg, SessionTelemetry(cfg.logs_dir / "t.jsonl"))


def _yaml(data: dict) -> str:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def test_tool_names_constant() -> None:
    assert FORM_TOOL_NAMES == [
        "mcp__forms__list_local_forms",
        "mcp__forms__load_form",
        "mcp__forms__save_form",
        "mcp__forms__read_indicators",
        "mcp__forms__save_indicators",
    ]


def test_save_form_writes_both_artifacts(box: FormToolbox) -> None:
    out = box.save_form("pdm", _yaml(VALID_FORM))
    assert "pdm.xlsx" in out and "compil" in out.lower()
    assert (box.config.forms_dir / "pdm.form.yaml").is_file()
    xlsx = box.config.forms_dir / "pdm.xlsx"
    assert xlsx.is_file()
    assert set(openpyxl.load_workbook(xlsx).sheetnames) == {"survey", "choices", "settings"}


def test_save_form_reports_validation_errors_and_writes_nothing(box: FormToolbox) -> None:
    broken = copy.deepcopy(VALID_FORM)
    broken["groups"][0]["questions"][0]["type"] = "rating"
    out = box.save_form("bad", _yaml(broken))
    assert "rating" in out
    assert not (box.config.forms_dir / "bad.form.yaml").exists()
    assert not (box.config.forms_dir / "bad.xlsx").exists()


def _breaking_edit() -> dict:
    broken = copy.deepcopy(VALID_FORM)
    broken["groups"][1]["questions"][0]["relevant"] = "${totally_bogus_field} = 'yes'"
    return broken


def test_compile_failure_is_reported_and_writes_nothing(box: FormToolbox) -> None:
    out = box.save_form("fresh", _yaml(_breaking_edit()))
    assert "pyxform" in out.lower() and "totally_bogus_field" in out
    assert not (box.config.forms_dir / "fresh.form.yaml").exists()
    assert not (box.config.forms_dir / "fresh.xlsx").exists()
    assert not list(box.config.forms_dir.glob("*.xlsx"))  # no temp file left behind


def test_failed_resave_keeps_previous_artifacts(box: FormToolbox) -> None:
    assert "saved" in box.save_form("pdm", _yaml(VALID_FORM)).lower()
    xlsx = box.config.forms_dir / "pdm.xlsx"
    before = xlsx.read_bytes()

    out = box.save_form("pdm", _yaml(_breaking_edit()))
    assert "pyxform" in out.lower()
    assert xlsx.exists(), "a rejected edit must not destroy the working XLSForm"
    assert xlsx.read_bytes() == before
    assert (box.config.forms_dir / "pdm.form.yaml").exists()
    assert len(list(box.config.forms_dir.glob("*.xlsx"))) == 1  # no temp leftovers


def test_save_form_bad_yaml_is_friendly(box: FormToolbox) -> None:
    out = box.save_form("pdm", "title: {uk: 'x'\n")
    assert "yaml" in out.lower() and "Traceback" not in out


def test_save_form_bad_name(box: FormToolbox) -> None:
    assert "Invalid form name" in box.save_form("../evil", _yaml(VALID_FORM))


def test_load_and_list(box: FormToolbox) -> None:
    box.save_form("pdm", _yaml(VALID_FORM))
    assert "pdm" in box.list_local_forms()
    loaded = box.load_form("pdm")
    assert "consent_given" in loaded
    assert yaml.safe_load(loaded)["form_id"] == "pdm_2026"


def test_load_missing_form_lists_known(box: FormToolbox) -> None:
    box.save_form("pdm", _yaml(VALID_FORM))
    out = box.load_form("ghost")
    assert "pdm" in out and "not found" in out.lower()


def test_form_saved_telemetry(box: FormToolbox) -> None:
    box.save_form("pdm", _yaml(VALID_FORM))
    log = box.telemetry.path.read_text(encoding="utf-8")
    assert '"kind": "form_saved"' in log and '"questions": 3' in log


def test_read_indicators_empty_hint(box: FormToolbox) -> None:
    assert "empty" in box.read_indicators().lower()


def test_save_and_read_indicators(box: FormToolbox) -> None:
    out = box.save_indicators(_yaml(VALID_REGISTRY))
    assert "2" in out  # count reported
    text = box.read_indicators()
    assert "1.1" in text and "2500" in text
    log = box.telemetry.path.read_text(encoding="utf-8")
    assert '"kind": "indicators_saved"' in log


def test_save_indicators_validation_error(box: FormToolbox) -> None:
    broken = copy.deepcopy(VALID_REGISTRY)
    broken["indicators"][0]["target"]["value"] = "many"
    out = box.save_indicators(_yaml(broken))
    assert "target.value" in out
    assert not (box.config.workspace / "indicators.yaml").exists()


def test_build_forms_server_importable(box: FormToolbox) -> None:
    from haa.core.tools.formtools import build_forms_server

    assert build_forms_server(box) is not None
