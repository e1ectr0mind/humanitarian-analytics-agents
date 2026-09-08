"""Deterministic XLSForm renderer: the model is the source of truth."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import openpyxl

from haa.forms.model import LANG_COLUMNS, SELECT_TYPES

SURVEY_HEADER = [
    "type",
    "name",
    f"label::{LANG_COLUMNS['uk']}",
    f"label::{LANG_COLUMNS['en']}",
    f"hint::{LANG_COLUMNS['uk']}",
    f"hint::{LANG_COLUMNS['en']}",
    "required",
    "relevant",
    "constraint",
]
CHOICES_HEADER = [
    "list_name",
    "name",
    f"label::{LANG_COLUMNS['uk']}",
    f"label::{LANG_COLUMNS['en']}",
]
SETTINGS_HEADER = ["form_title", "form_id", "version", "default_language"]


def _label(container: dict | None, lang: str) -> str:
    if not isinstance(container, dict):
        return ""
    value = container.get(lang)
    return value if isinstance(value, str) else ""


def _question_row(question: dict) -> list[str]:
    qtype = question["type"]
    if qtype in SELECT_TYPES:
        qtype = f"{qtype} {question['list']}"
    return [
        qtype,
        question["name"],
        _label(question.get("label"), "uk"),
        _label(question.get("label"), "en"),
        _label(question.get("hint"), "uk"),
        _label(question.get("hint"), "en"),
        "yes" if question.get("required") else "",
        question.get("relevant") or "",
        question.get("constraint") or "",
    ]


def render_xlsx(model: dict, path: Path) -> Path:
    """Render the model into a fresh XLSForm workbook (overwrites `path`)."""
    workbook = openpyxl.Workbook()
    survey = workbook.active
    survey.title = "survey"
    survey.append(SURVEY_HEADER)
    for group in model.get("groups") or []:
        survey.append(
            [
                "begin_group",
                group["name"],
                _label(group.get("label"), "uk"),
                _label(group.get("label"), "en"),
                "",
                "",
                "",
                "",
                "",
            ]
        )
        for question in group.get("questions") or []:
            survey.append(_question_row(question))
        survey.append(["end_group", "", "", "", "", "", "", "", ""])

    choices = workbook.create_sheet("choices")
    choices.append(CHOICES_HEADER)
    for list_name, options in (model.get("choices") or {}).items():
        for option in options:
            choices.append(
                [
                    list_name,
                    option["name"],
                    _label(option.get("label"), "uk"),
                    _label(option.get("label"), "en"),
                ]
            )

    settings = workbook.create_sheet("settings")
    settings.append(SETTINGS_HEADER)
    settings.append(
        [
            _label(model.get("title"), "en") or _label(model.get("title"), "uk"),
            model["form_id"],
            datetime.now(UTC).strftime("%Y%m%d%H%M"),
            LANG_COLUMNS["uk"],
        ]
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return path
