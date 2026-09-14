import copy
import re
from datetime import date
from pathlib import Path

import openpyxl
import pandas as pd
import pytest
import yaml

from haa.config import load_config
from haa.core.telemetry import SessionTelemetry
from haa.core.tools.reporttools import PERIOD_SCHEMA, REPORT_TOOL_NAMES, ReportToolbox
from tests.conftest import DEMO_REGISTRY
from tests.test_reporting_mapping import VALID_MAPPING

TODAY = date(2026, 9, 14)
MAPPING_YAML = yaml.safe_dump(VALID_MAPPING, allow_unicode=True, sort_keys=False)


@pytest.fixture()
def box(report_workspace: Path) -> ReportToolbox:
    cfg = load_config(report_workspace)
    return ReportToolbox(cfg, SessionTelemetry(cfg.logs_dir / "t.jsonl"), today=lambda: TODAY)


def _log(box: ReportToolbox) -> str:
    path = box.telemetry.path
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_tool_names_constant() -> None:
    assert REPORT_TOOL_NAMES == [
        "mcp__reports__compute_indicators",
        "mcp__reports__build_indicator_report",
        "mcp__reports__read_5w_mapping",
        "mcp__reports__save_5w_mapping",
        "mcp__reports__build_5w",
    ]


def test_period_schema_makes_arguments_optional() -> None:
    assert PERIOD_SCHEMA["type"] == "object" and PERIOD_SCHEMA["required"] == []
    assert set(PERIOD_SCHEMA["properties"]) == {"date_field", "start", "end"}


def test_compute_indicators_summary(box: ReportToolbox) -> None:
    out = box.compute_indicators()
    assert "Period: all records" in out
    assert (
        "- 1.1 Охоплені домогосподарства / Households reached: actual 3000, "
        "target 2500 = 120% of target"
    ) in out
    assert "by oblast:" in out and "by head_sex:" in out
    assert "Not computable (1):" in out and "no measure block" in out
    assert not list(box.config.reports_dir.iterdir())  # preview only, no files


def test_compute_indicators_hides_high_cardinality_breakdowns(
    box: ReportToolbox, report_workspace: Path
) -> None:
    registry = copy.deepcopy(DEMO_REGISTRY)
    registry["indicators"][0]["disaggregation"] = ["_uuid", "oblast"]
    (report_workspace / "indicators.yaml").write_text(
        yaml.safe_dump(registry, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    raw = pd.read_excel(report_workspace / "data" / "beneficiaries.xlsx")
    out = box.compute_indicators()
    for value in raw["_uuid"].dropna().astype(str):
        assert value not in out
    categories = raw["_uuid"].nunique() + int(raw["_uuid"].isna().any())
    assert (
        f"by _uuid: {categories} categories — values not shown here "
        "(too many distinct values in the dataset); see the report file"
    ) in out
    oblast_line = next(line for line in out.splitlines() if "by oblast:" in line)
    for value in raw["oblast"].dropna().unique():
        assert str(value) in oblast_line


def test_compute_indicators_full_dataset_rule_applies_even_with_narrow_period(
    box: ReportToolbox, report_workspace: Path
) -> None:
    """A short period can leave <= 30 _uuid rows, but the dataset overall has 3000 —
    the rule looks at the full dataset, not the in-scope row count."""
    registry = copy.deepcopy(DEMO_REGISTRY)
    registry["indicators"][0]["disaggregation"] = ["_uuid", "oblast"]
    (report_workspace / "indicators.yaml").write_text(
        yaml.safe_dump(registry, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    raw = pd.read_excel(report_workspace / "data" / "beneficiaries.xlsx")
    counts = raw["submission_date"].value_counts()
    narrow_days = counts[(counts <= 30) & (counts.index != "2027-01-15")]
    assert not narrow_days.empty
    day = str(narrow_days.index[0])

    out = box.compute_indicators("submission_date", day, day)
    in_scope = raw.loc[raw["submission_date"] == day]
    for value in in_scope["_uuid"].dropna().astype(str):
        assert value not in out
    uuid_line = next(line for line in out.splitlines() if "by _uuid:" in line)
    assert (
        "categories — values not shown here (too many distinct values in the dataset); "
        "see the report file"
    ) in uuid_line
    oblast_line = next(line for line in out.splitlines() if "by oblast:" in line)
    for value in in_scope["oblast"].dropna().unique():
        assert str(value) in oblast_line


def test_compute_indicators_bare_column_filter_leaks_no_values(
    box: ReportToolbox, report_workspace: Path
) -> None:
    registry = copy.deepcopy(DEMO_REGISTRY)
    registry["indicators"][0]["measure"]["filter"] = "_uuid"
    (report_workspace / "indicators.yaml").write_text(
        yaml.safe_dump(registry, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    raw = pd.read_excel(report_workspace / "data" / "beneficiaries.xlsx")
    out = box.compute_indicators()
    for value in raw["_uuid"].dropna().astype(str):
        assert value not in out
    assert "filter '_uuid' uses syntax the report engine does not allow" in out


def test_compute_indicators_with_period(box: ReportToolbox) -> None:
    out = box.compute_indicators("submission_date", "2026-06-01", "2026-08-31")
    assert "Period: 2026-06-01 .. 2026-08-31 (by submission_date)" in out
    assert "excluded by period" in out


def test_compute_indicators_bad_period_is_friendly(box: ReportToolbox) -> None:
    out = box.compute_indicators("submission_date", "2026-06-01", None)
    assert "both" in out and "Traceback" not in out


def test_compute_indicators_empty_registry(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    box = ReportToolbox(cfg, SessionTelemetry(cfg.logs_dir / "t.jsonl"))
    assert "empty" in box.compute_indicators()


def test_compute_indicators_broken_registry_yaml(box: ReportToolbox) -> None:
    (box.config.workspace / "indicators.yaml").write_text("indicators: [\n", encoding="utf-8")
    assert "indicators.yaml could not be read" in box.compute_indicators()


def test_build_indicator_report_writes_files_and_logs(box: ReportToolbox) -> None:
    out = box.build_indicator_report()
    md = box.config.reports_dir / "indicators_2026-09-14.md"
    xlsx = box.config.reports_dir / "indicators_2026-09-14.xlsx"
    assert md.is_file() and xlsx.is_file()
    assert (
        "Report files: reports/indicators_2026-09-14.md, reports/indicators_2026-09-14.xlsx"
    ) in out
    first = next(openpyxl.load_workbook(xlsx)["Summary"].iter_rows(min_row=2, values_only=True))
    assert first[0] == "1.1" and first[5] == 3000
    log = _log(box)
    assert '"kind": "report_built"' in log and '"report": "indicators"' in log
    assert '"computed": 2' in log and '"not_computable": 1' in log


def test_build_indicator_report_write_failure_is_friendly(
    box: ReportToolbox, monkeypatch
) -> None:
    import haa.core.tools.reporttools as rt

    def boom(*args):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(rt, "render_indicator_report", boom)
    out = box.build_indicator_report()
    assert "close it" in out and "Traceback" not in out


def test_read_mapping_when_missing(box: ReportToolbox) -> None:
    assert "no 5W mapping yet" in box.read_5w_mapping()


def test_save_mapping_invalid_writes_nothing(box: ReportToolbox) -> None:
    out = box.save_5w_mapping(yaml.safe_dump(dict(VALID_MAPPING, sector="health")))
    assert "nothing was saved" in out and "sector" in out
    assert not (box.config.workspace / "5w.yaml").exists()


def test_save_mapping_bad_yaml(box: ReportToolbox) -> None:
    assert "Could not parse" in box.save_5w_mapping("where: [\n")


def test_save_read_and_replace_mapping(box: ReportToolbox) -> None:
    first = box.save_5w_mapping(MAPPING_YAML)
    assert "saved" in first and "replaced" not in first
    assert "oblast" in box.read_5w_mapping()
    assert "replaced the previous mapping" in box.save_5w_mapping(MAPPING_YAML)
    assert '"kind": "mapping_saved"' in _log(box)


def test_build_5w_without_mapping(box: ReportToolbox) -> None:
    assert "no 5W mapping yet" in box.build_5w()


def test_build_5w_invalid_mapping_file(box: ReportToolbox) -> None:
    (box.config.workspace / "5w.yaml").write_text("dataset: beneficiaries\n", encoding="utf-8")
    assert "5w.yaml is invalid" in box.build_5w()


def test_build_5w_writes_files_and_logs(box: ReportToolbox) -> None:
    box.save_5w_mapping(MAPPING_YAML)
    out = box.build_5w()
    assert "Unique reach (distinct _uuid): 3000" in out
    assert (box.config.reports_dir / "5w_2026-09-14.md").is_file()
    assert (box.config.reports_dir / "5w_2026-09-14.xlsx").is_file()
    assert '"report": "5w"' in _log(box)


def test_build_5w_period_defaults_to_when_field(box: ReportToolbox) -> None:
    box.save_5w_mapping(MAPPING_YAML)
    assert "by submission_date" in box.build_5w(None, "2026-06-01", "2026-08-31")


def test_build_5w_keeps_labels_for_listable_disaggregation(box: ReportToolbox) -> None:
    box.save_5w_mapping(MAPPING_YAML)  # whom.disaggregation: [head_sex]
    out = box.build_5w()
    columns_line = next(line for line in out.splitlines() if line.startswith("5W built:"))
    assert "head_sex=female" in columns_line
    assert "head_sex=male" in columns_line


def test_build_5w_collapses_labels_for_non_listable_disaggregation(
    box: ReportToolbox, report_workspace: Path
) -> None:
    mapping = copy.deepcopy(VALID_MAPPING)
    mapping["whom"]["disaggregation"] = ["_uuid"]
    box.save_5w_mapping(yaml.safe_dump(mapping, allow_unicode=True, sort_keys=False))
    raw = pd.read_excel(report_workspace / "data" / "beneficiaries.xlsx")
    out = box.build_5w()
    for value in raw["_uuid"].dropna().astype(str):
        assert value not in out
    columns_line = next(line for line in out.splitlines() if line.startswith("5W built:"))
    match = re.search(r"_uuid=(\d+) categories", columns_line)
    assert match is not None and int(match.group(1)) > 0


def test_tool_outputs_never_contain_pii(box: ReportToolbox, report_workspace: Path) -> None:
    raw = pd.read_excel(report_workspace / "data" / "beneficiaries.xlsx")
    box.save_5w_mapping(MAPPING_YAML)
    text = box.compute_indicators() + box.build_indicator_report() + box.build_5w()
    for value in (*raw["resp_phone"].astype(str), *raw["resp_name"].astype(str)):
        assert value not in text


def test_build_server_importable(box: ReportToolbox) -> None:
    from haa.core.tools.reporttools import build_reports_server

    assert build_reports_server(box) is not None
