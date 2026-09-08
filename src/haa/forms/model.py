"""Form model schema: the source of truth an XLSForm is rendered from."""

from __future__ import annotations

import re
from collections.abc import Iterator

LANGS = ("uk", "en")
LANG_COLUMNS = {"uk": "Українська (uk)", "en": "English (en)"}
QUESTION_TYPES = {
    "text",
    "integer",
    "decimal",
    "date",
    "note",
    "geopoint",
    "select_one",
    "select_multiple",
}
SELECT_TYPES = {"select_one", "select_multiple"}
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,29}\Z")
QUESTION_KEYS = {"name", "type", "list", "label", "required", "relevant", "constraint", "hint"}
GROUP_KEYS = {"name", "label", "questions"}
TOP_KEYS = {"title", "form_id", "groups", "choices"}
CHOICE_KEYS = {"name", "label"}


def _check_label(value: object, where: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{where}: expected a mapping with {LANGS} labels")
        return
    for lang in LANGS:
        text = value.get(lang)
        if not isinstance(text, str) or not text.strip():
            errors.append(f"{where}: missing or empty '{lang}' label")


def _check_name(value: object, where: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not NAME_RE.match(value):
        errors.append(
            f"{where}: invalid name {value!r} — use lowercase letters, digits and "
            "underscores, starting with a letter (max 30 chars)"
        )


def iter_questions(model: dict) -> Iterator[tuple[dict, dict]]:
    for group in model.get("groups") or []:
        for question in group.get("questions") or []:
            yield group, question


def _validate_question(
    question: object, where: str, choices: dict, used_lists: set[str], errors: list[str]
) -> str | None:
    if not isinstance(question, dict):
        errors.append(f"{where}: expected a mapping")
        return None
    name = question.get("name")
    unknown = set(question.keys()) - QUESTION_KEYS
    if unknown:
        errors.append(
            f"{where} ({name!r}): unknown key(s) {sorted(unknown)}; supported: "
            f"{', '.join(sorted(QUESTION_KEYS))}"
        )
    _check_name(name, f"{where}.name", errors)
    qtype = question.get("type")
    if not isinstance(qtype, str) or qtype not in QUESTION_TYPES:
        errors.append(
            f"{where} ({name!r}): unknown type {qtype!r}; allowed: "
            f"{', '.join(sorted(QUESTION_TYPES))}"
        )
    _check_label(question.get("label"), f"{where}.label ({name!r})", errors)
    if isinstance(qtype, str) and qtype in SELECT_TYPES:
        list_name = question.get("list")
        if not isinstance(list_name, str) or not list_name:
            errors.append(f"{where} ({name!r}): select question needs a 'list' name")
        elif list_name not in choices:
            errors.append(f"{where} ({name!r}): choice list {list_name!r} is not defined")
        else:
            used_lists.add(list_name)
    for key in ("relevant", "constraint"):
        value = question.get(key)
        if value is not None and not isinstance(value, str):
            errors.append(f"{where} ({name!r}): '{key}' must be a string expression or null")
    if question.get("required") is not None and not isinstance(question["required"], bool):
        errors.append(f"{where} ({name!r}): 'required' must be true or false")
    if question.get("hint") is not None:
        _check_label(question["hint"], f"{where}.hint ({name!r})", errors)
    return name if isinstance(name, str) else None


def validate_model(model: object) -> list[str]:
    """Return a list of addressed, human-readable errors ([] means valid)."""
    if not isinstance(model, dict):
        return ["model: expected a mapping at the top level"]

    errors: list[str] = []
    unknown_top = set(model.keys()) - TOP_KEYS
    if unknown_top:
        errors.append(
            f"model: unknown key(s) {sorted(unknown_top)}; supported: "
            f"{', '.join(sorted(TOP_KEYS))}"
        )
    _check_label(model.get("title"), "title", errors)
    _check_name(model.get("form_id"), "form_id", errors)

    choices = model.get("choices")
    if choices is None:
        choices = {}
    if not isinstance(choices, dict):
        errors.append("choices: expected a mapping of list name -> options")
        choices = {}

    groups = model.get("groups")
    if not isinstance(groups, list) or not groups:
        errors.append("groups: expected a non-empty list of groups")
        groups = []

    used_lists: set[str] = set()
    seen_questions: set[str] = set()
    seen_groups: set[str] = set()
    for gi, group in enumerate(groups):
        where = f"groups[{gi}]"
        if not isinstance(group, dict):
            errors.append(f"{where}: expected a mapping")
            continue
        unknown_group = set(group.keys()) - GROUP_KEYS
        if unknown_group:
            errors.append(
                f"{where}: unknown key(s) {sorted(unknown_group)}; supported: "
                f"{', '.join(sorted(GROUP_KEYS))}"
            )
        _check_name(group.get("name"), f"{where}.name", errors)
        gname = group.get("name")
        if isinstance(gname, str):
            if gname in seen_groups:
                errors.append(f"{where}.name: duplicate group name {gname!r}")
            seen_groups.add(gname)
        _check_label(group.get("label"), f"{where}.label", errors)
        questions = group.get("questions")
        if not isinstance(questions, list) or not questions:
            errors.append(f"{where}.questions: expected a non-empty list")
            continue
        for qi, question in enumerate(questions):
            qname = _validate_question(
                question, f"{where}.questions[{qi}]", choices, used_lists, errors
            )
            if qname:
                if qname in seen_questions:
                    errors.append(
                        f"{where}.questions[{qi}]: duplicate question name {qname!r}"
                    )
                seen_questions.add(qname)

    for list_name, options in choices.items():
        where = f"choices.{list_name}"
        if not isinstance(options, list) or not options:
            errors.append(f"{where}: expected a non-empty list of options")
            continue
        seen_options: set[str] = set()
        for oi, option in enumerate(options):
            if not isinstance(option, dict):
                errors.append(f"{where}[{oi}]: expected a mapping")
                continue
            unknown_choice = set(option.keys()) - CHOICE_KEYS
            if unknown_choice:
                errors.append(
                    f"{where}[{oi}]: unknown key(s) {sorted(unknown_choice)}; supported: "
                    f"{', '.join(sorted(CHOICE_KEYS))}"
                )
            _check_name(option.get("name"), f"{where}[{oi}].name", errors)
            _check_label(option.get("label"), f"{where}[{oi}].label", errors)
            oname = option.get("name")
            if isinstance(oname, str):
                if oname in seen_options:
                    errors.append(f"{where}[{oi}]: duplicate option name {oname!r}")
                seen_options.add(oname)
        if list_name not in used_lists:
            errors.append(f"{where}: choice list is never used by any question")

    return errors
