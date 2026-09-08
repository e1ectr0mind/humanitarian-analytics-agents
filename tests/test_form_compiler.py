from pathlib import Path

import openpyxl

from haa.forms.compiler import compile_check
from haa.forms.renderer import render_xlsx
from tests.test_form_model import VALID


def test_valid_form_compiles(tmp_path: Path) -> None:
    path = render_xlsx(VALID, tmp_path / "ok.xlsx")
    assert compile_check(path) == []


def test_broken_relevant_is_reported(tmp_path: Path) -> None:
    path = render_xlsx(VALID, tmp_path / "bad.xlsx")
    wb = openpyxl.load_workbook(path)
    survey = wb["survey"]
    for row in survey.iter_rows(min_row=2):
        if row[1].value == "head_sex":
            row[7].value = "${nonexistent_question} = 'yes'"  # unknown reference
    wb.save(path)
    errors = compile_check(path)
    assert errors, "pyxform should reject an unknown ${reference}"
    assert any("nonexistent_question" in e for e in errors)


def test_missing_file_is_friendly(tmp_path: Path) -> None:
    errors = compile_check(tmp_path / "nope.xlsx")
    assert errors and all(isinstance(e, str) for e in errors)
