from pathlib import Path

import pytest

from haa.forms.storage import (
    FormStorageError,
    form_paths,
    list_forms,
    load_model,
    parse_model_yaml,
    save_model,
)
from tests.test_form_model import VALID


def test_round_trip(tmp_path: Path) -> None:
    save_model(tmp_path, "pdm", VALID)
    assert load_model(tmp_path, "pdm") == VALID


def test_yaml_is_readable_unicode(tmp_path: Path) -> None:
    save_model(tmp_path, "pdm", VALID)
    text = (tmp_path / "pdm.form.yaml").read_text(encoding="utf-8")
    assert "Згода" in text  # not \u-escaped


def test_form_paths(tmp_path: Path) -> None:
    yaml_path, xlsx_path = form_paths(tmp_path, "pdm")
    assert yaml_path.name == "pdm.form.yaml" and xlsx_path.name == "pdm.xlsx"


def test_unsafe_name_rejected(tmp_path: Path) -> None:
    with pytest.raises(FormStorageError):
        form_paths(tmp_path, "../escape")


def test_list_forms(tmp_path: Path) -> None:
    save_model(tmp_path, "b_form", VALID)
    save_model(tmp_path, "a_form", VALID)
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    assert list_forms(tmp_path) == ["a_form", "b_form"]


def test_load_missing_is_friendly(tmp_path: Path) -> None:
    with pytest.raises(FormStorageError, match="pdm"):
        load_model(tmp_path, "pdm")


def test_broken_yaml_is_friendly(tmp_path: Path) -> None:
    (tmp_path / "bad.form.yaml").write_text("title: {uk: 'x'\n", encoding="utf-8")
    with pytest.raises(FormStorageError):
        load_model(tmp_path, "bad")


def test_parse_model_yaml_ok() -> None:
    assert parse_model_yaml("form_id: pdm\ntitle:\n  uk: А\n  en: A\n")["form_id"] == "pdm"


def test_parse_model_yaml_bad() -> None:
    with pytest.raises(FormStorageError):
        parse_model_yaml("a: [1, 2\n")


def test_trailing_newline_name_rejected(tmp_path: Path) -> None:
    with pytest.raises(FormStorageError):
        form_paths(tmp_path, "pdm\n")
