# Indicators Registry + XLSForm Designer Implementation Plan (subproject 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A third agent role (`designer`) that curates a machine-checkable indicators registry extracted from project documentation, and designs bilingual XLSForm surveys as a validated model → deterministically rendered `.xlsx` that pyxform compiles.

**Architecture:** Pure-logic packages `haa/forms/` (model + validation, renderer, pyxform compiler, yaml storage) and `haa/indicators/` (registry schema + storage) — no LLM anywhere in them. A third in-process MCP server `forms` exposes five tools; the `designer` AgentDefinition owns them plus the docs/profile tools; the analyst gains `read_indicators`. The `.xlsx` is never edited in place — every save re-renders it from `*.form.yaml`.

**Tech Stack:** existing haa core (subprojects 1 + 2a) + `pyxform`, `PyYAML`; openpyxl (already present) for rendering and golden tests.

**Spec:** `docs/superpowers/specs/2026-09-08-indicators-formdesigner-design.md`

## Global Constraints

- Everything from earlier subprojects still binds: Python 3.11+, `src/haa/`, English code/comments, ruff (E,F,I,UP,B; line 100), TDD, commit per task, `uv run ...` (`python -m uv run ...` on this machine), never loop the full suite (~2 min).
- **No LLM inside `haa/forms/` and `haa/indicators/`** — they are deterministic libraries; every behavior there is unit-testable offline.
- **`.xlsx` is a build artifact**: written only by `render_xlsx`, always regenerated whole from the model. No code path edits an existing xlsx.
- **Bilingual by default**: every question/choice/group label carries `uk` and `en`; xlsx columns are exactly `label::Українська (uk)` and `label::English (en)` (same pattern for `hint::`).
- Friendly errors only (pattern from 2a): validation and compile failures return message strings/lists, never tracebacks to the SDK.
- New workspace dir `workspace/forms/` (`HaaConfig.forms_dir`), created by `load_config`. It is NOT under `data_dir` — the PII boundary and hook are untouched.
- Telemetry kinds added: `indicators_saved`, `form_saved`.
- Suite baseline before this plan: 153 offline tests green, 9 deselected (7 api + 2 remote).

---

## File Structure

```
src/haa/
  config.py                    # MODIFY: + designer_model, forms_dir property
  forms/
    __init__.py                # NEW (empty)
    model.py                   # NEW: LANGS, QUESTION_TYPES, validate_model, iter_questions
    renderer.py                # NEW: render_xlsx(model, path) -> Path
    compiler.py                # NEW: compile_check(xlsx_path) -> list[str]
    storage.py                 # NEW: form_path/load_form/save_form_model/list_forms
  indicators/
    __init__.py                # NEW (empty)
    registry.py                # NEW: AGGREGATIONS, validate_registry, load/save, summarize
  core/
    tools/formtools.py         # NEW: FormToolbox + build_forms_server + FORM_TOOL_NAMES
    agents/designer.py         # NEW: DESIGNER_PROMPT + build_designer
    agents/registry.py         # MODIFY: + designer
    agents/analyst.py          # MODIFY: + read_indicators tool
    agents/orchestrator.py     # MODIFY: routing for indicators/forms
    session.py                 # MODIFY: forms server wiring
pyproject.toml                 # MODIFY: + pyxform, PyYAML
tests/
  test_form_model.py           # NEW
  test_form_renderer.py        # NEW
  test_form_compiler.py        # NEW  (real pyxform, no LLM — the load-bearing test)
  test_form_storage.py         # NEW
  test_indicators_registry.py  # NEW
  test_formtools.py            # NEW
  test_designer_agent.py       # NEW
  test_config.py               # MODIFY: designer_model, forms_dir
  test_session.py              # MODIFY: forms server assertions
  test_agents.py               # MODIFY: registry has three roles
  test_smoke_api.py            # MODIFY: two new api evals
README.md                      # MODIFY: indicators + forms sections, roadmap
```

---

### Task 1: Dependencies + config (designer_model, forms_dir)

**Files:**
- Modify: `pyproject.toml`, `src/haa/config.py`
- Test: `tests/test_config.py` (append)

**Interfaces:**
- Produces: deps `pyxform>=2.0`, `PyYAML>=6.0`; `HaaConfig.designer_model: str = "claude-opus-5"` (auto-tunable — `_TUNABLE` derives from fields); property `forms_dir -> workspace/"forms"`, created by `load_config`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_config.py`:

```python
def test_designer_model_default_and_override(tmp_path: Path) -> None:
    assert load_config(tmp_path).designer_model == "claude-opus-5"
    (tmp_path / "config.toml").write_text('designer_model = "claude-sonnet-5"', encoding="utf-8")
    assert load_config(tmp_path).designer_model == "claude-sonnet-5"


def test_forms_dir_created(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    assert cfg.forms_dir == tmp_path.resolve() / "forms"
    assert cfg.forms_dir.is_dir()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_config.py -v` — Expected: 2 FAIL (attribute/property missing).

- [ ] **Step 3: Implement**

`pyproject.toml` — add to `dependencies`: `"pyxform>=2.0"`, `"PyYAML>=6.0"`.
NOTE: these two lines are ALREADY present on the branch (added while probing
pyxform during plan self-review, together with the resulting `uv.lock`). Verify
they are there and move on — do not duplicate them; the commit for this task
then covers only config.py, the tests, and any lock refresh.

`src/haa/config.py` — add field after `cleaner_model`:

```python
    designer_model: str = "claude-opus-5"
```

add property next to `reports_dir`:

```python
    @property
    def forms_dir(self) -> Path:
        return self.workspace / "forms"
```

and add `cfg.forms_dir` to the directory-creation tuple in `load_config`.

- [ ] **Step 4: Verify**

Run: `uv sync --all-groups && uv run pytest tests/test_config.py -v` — all PASS.
Run: `uv run pytest -q` — expect 155 passed, 9 deselected. Run: `uv run ruff check .` — clean.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/haa/config.py tests/test_config.py uv.lock
git commit -m "feat: pyxform/PyYAML deps, designer_model and forms_dir config"
```

---

### Task 2: Form model and validation

**Files:**
- Create: `src/haa/forms/__init__.py` (empty), `src/haa/forms/model.py`
- Test: `tests/test_form_model.py`

**Interfaces:**
- Produces (consumed by Tasks 3–6, 8):
  - `LANGS = ("uk", "en")`; `LANG_COLUMNS = {"uk": "Українська (uk)", "en": "English (en)"}`
  - `QUESTION_TYPES = {"text", "integer", "decimal", "date", "note", "geopoint", "select_one", "select_multiple"}`
  - `SELECT_TYPES = {"select_one", "select_multiple"}`
  - `NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,29}$")`
  - `validate_model(model: dict) -> list[str]` — returns a list of human-readable, addressed errors (empty list = valid). Checks: top-level `title` bilingual, `form_id` matches NAME_RE, `groups` non-empty list; each group has NAME_RE `name`, bilingual `label`, non-empty `questions`; each question has NAME_RE `name`, `type` in QUESTION_TYPES, bilingual `label`, optional `required` bool, optional `relevant`/`constraint` strings, optional bilingual `hint`; select questions carry `list` that exists in `choices`; every choice list is a non-empty list of `{name, label{uk,en}}` with NAME_RE names; question names unique across the whole form; group names unique; every declared choice list is referenced by at least one question (unused list = error).
  - `iter_questions(model) -> Iterator[tuple[dict, dict]]` — yields `(group, question)` pairs in document order.

- [ ] **Step 1: Write the failing tests**

`tests/test_form_model.py`:

```python
import copy

import pytest

from haa.forms.model import QUESTION_TYPES, iter_questions, validate_model

VALID = {
    "title": {"uk": "Опитування", "en": "Survey"},
    "form_id": "pdm_2026",
    "groups": [
        {
            "name": "consent",
            "label": {"uk": "Згода", "en": "Consent"},
            "questions": [
                {
                    "name": "consent_given",
                    "type": "select_one",
                    "list": "yesno",
                    "label": {"uk": "Чи згодні ви?", "en": "Do you consent?"},
                    "required": True,
                }
            ],
        },
        {
            "name": "demographics",
            "label": {"uk": "Демографія", "en": "Demographics"},
            "questions": [
                {
                    "name": "head_sex",
                    "type": "select_one",
                    "list": "sex",
                    "label": {"uk": "Стать", "en": "Sex"},
                    "relevant": "${consent_given} = 'yes'",
                },
                {
                    "name": "head_age",
                    "type": "integer",
                    "label": {"uk": "Вік", "en": "Age"},
                    "constraint": ". >= 0 and . <= 120",
                    "hint": {"uk": "Повних років", "en": "Full years"},
                },
            ],
        },
    ],
    "choices": {
        "yesno": [
            {"name": "yes", "label": {"uk": "Так", "en": "Yes"}},
            {"name": "no", "label": {"uk": "Ні", "en": "No"}},
        ],
        "sex": [
            {"name": "female", "label": {"uk": "Жіноча", "en": "Female"}},
            {"name": "male", "label": {"uk": "Чоловіча", "en": "Male"}},
        ],
    },
}


@pytest.fixture()
def model() -> dict:
    return copy.deepcopy(VALID)


def test_valid_model_has_no_errors(model: dict) -> None:
    assert validate_model(model) == []


def test_missing_title_language(model: dict) -> None:
    del model["title"]["en"]
    assert any("title" in e and "en" in e for e in validate_model(model))


def test_bad_form_id(model: dict) -> None:
    model["form_id"] = "PDM 2026"
    assert any("form_id" in e for e in validate_model(model))


def test_unknown_question_type(model: dict) -> None:
    model["groups"][1]["questions"][1]["type"] = "rating"
    errors = validate_model(model)
    assert any("rating" in e for e in errors)
    assert any("head_age" in e for e in errors)


def test_select_without_list(model: dict) -> None:
    del model["groups"][0]["questions"][0]["list"]
    assert any("list" in e and "consent_given" in e for e in validate_model(model))


def test_select_references_missing_list(model: dict) -> None:
    model["groups"][0]["questions"][0]["list"] = "nope"
    assert any("nope" in e for e in validate_model(model))


def test_duplicate_question_names(model: dict) -> None:
    model["groups"][1]["questions"][1]["name"] = "head_sex"
    assert any("head_sex" in e and "duplicate" in e.lower() for e in validate_model(model))


def test_bad_question_name(model: dict) -> None:
    model["groups"][1]["questions"][1]["name"] = "Head Age"
    assert any("Head Age" in e for e in validate_model(model))


def test_choice_missing_label_language(model: dict) -> None:
    del model["choices"]["sex"][0]["label"]["uk"]
    errors = validate_model(model)
    assert any("sex" in e and "uk" in e for e in errors)


def test_empty_choice_list(model: dict) -> None:
    model["choices"]["sex"] = []
    assert any("sex" in e for e in validate_model(model))


def test_unused_choice_list(model: dict) -> None:
    model["choices"]["extra"] = [{"name": "a", "label": {"uk": "А", "en": "A"}}]
    assert any("extra" in e for e in validate_model(model))


def test_no_groups(model: dict) -> None:
    model["groups"] = []
    assert any("groups" in e for e in validate_model(model))


def test_not_a_dict() -> None:
    assert validate_model([]) == ["model: expected a mapping at the top level"]


def test_iter_questions_order(model: dict) -> None:
    pairs = list(iter_questions(model))
    assert [q["name"] for _, q in pairs] == ["consent_given", "head_sex", "head_age"]
    assert pairs[0][0]["name"] == "consent"


def test_question_types_constant() -> None:
    assert {"text", "integer", "select_one", "select_multiple", "note"} <= QUESTION_TYPES
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_form_model.py -v` — Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement `src/haa/forms/model.py`**

```python
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
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,29}$")


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
    _check_name(name, f"{where}.name", errors)
    qtype = question.get("type")
    if qtype not in QUESTION_TYPES:
        errors.append(
            f"{where} ({name!r}): unknown type {qtype!r}; allowed: "
            f"{', '.join(sorted(QUESTION_TYPES))}"
        )
    _check_label(question.get("label"), f"{where}.label ({name!r})", errors)
    if qtype in SELECT_TYPES:
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_form_model.py -v` — Expected: all 15 PASS. Run: `uv run ruff check .` — clean.

- [ ] **Step 5: Commit**

```bash
git add src/haa/forms tests/test_form_model.py
git commit -m "feat: form model schema with addressed validation errors"
```

---

### Task 3: XLSForm renderer

**Files:**
- Create: `src/haa/forms/renderer.py`
- Test: `tests/test_form_renderer.py`

**Interfaces:**
- Consumes: `model.LANG_COLUMNS`, `SELECT_TYPES`, `iter_questions`.
- Produces: `render_xlsx(model: dict, path: Path) -> Path`. Sheets and columns exactly:
  - `survey`: `type`, `name`, `label::Українська (uk)`, `label::English (en)`, `hint::Українська (uk)`, `hint::English (en)`, `required`, `relevant`, `constraint`. Groups wrap their questions in `begin_group`/`end_group` rows (group row carries the group's labels, `name`, empty logic cells). Select questions render type as `select_one <list>` / `select_multiple <list>`. `required` is `"yes"` or empty. Empty optional cells are `""`.
  - `choices`: `list_name`, `name`, `label::Українська (uk)`, `label::English (en)` — lists in the order they appear in `model["choices"]`.
  - `settings`: `form_title` (uses the `en` title), `form_id`, `version`, `default_language` (`Українська (uk)`) — one header row + one data row; `version` = `datetime.now(UTC).strftime("%Y%m%d%H%M")`.

- [ ] **Step 1: Write the failing tests**

`tests/test_form_renderer.py`:

```python
from pathlib import Path

import openpyxl
import pytest

from haa.forms.renderer import render_xlsx
from tests.test_form_model import VALID


@pytest.fixture()
def rendered(tmp_path: Path) -> Path:
    return render_xlsx(VALID, tmp_path / "pdm.xlsx")


def _rows(path: Path, sheet: str) -> list[list]:
    wb = openpyxl.load_workbook(path)
    return [list(r) for r in wb[sheet].iter_rows(values_only=True)]


def test_sheets_present(rendered: Path) -> None:
    assert set(openpyxl.load_workbook(rendered).sheetnames) == {"survey", "choices", "settings"}


def test_survey_header_exact(rendered: Path) -> None:
    assert _rows(rendered, "survey")[0] == [
        "type",
        "name",
        "label::Українська (uk)",
        "label::English (en)",
        "hint::Українська (uk)",
        "hint::English (en)",
        "required",
        "relevant",
        "constraint",
    ]


def test_groups_wrap_questions(rendered: Path) -> None:
    types = [r[0] for r in _rows(rendered, "survey")[1:]]
    assert types == [
        "begin_group",
        "select_one yesno",
        "end_group",
        "begin_group",
        "select_one sex",
        "integer",
        "end_group",
    ]


def test_question_row_content(rendered: Path) -> None:
    rows = {r[1]: r for r in _rows(rendered, "survey")[1:]}
    consent = rows["consent_given"]
    assert consent[2] == "Чи згодні ви?" and consent[3] == "Do you consent?"
    assert consent[6] == "yes"  # required
    age = rows["head_age"]
    assert age[4] == "Повних років" and age[5] == "Full years"
    assert age[6] == ""  # not required
    assert age[8] == ". >= 0 and . <= 120"
    assert rows["head_sex"][7] == "${consent_given} = 'yes'"


def test_choices_sheet(rendered: Path) -> None:
    rows = _rows(rendered, "choices")
    assert rows[0] == ["list_name", "name", "label::Українська (uk)", "label::English (en)"]
    assert ["yesno", "yes", "Так", "Yes"] in [list(r) for r in rows[1:]]
    assert ["sex", "female", "Жіноча", "Female"] in [list(r) for r in rows[1:]]


def test_settings_sheet(rendered: Path) -> None:
    header, data = _rows(rendered, "settings")[:2]
    assert header == ["form_title", "form_id", "version", "default_language"]
    assert data[0] == "Survey" and data[1] == "pdm_2026"
    assert len(str(data[2])) == 12 and str(data[2]).isdigit()  # YYYYMMDDHHMM
    assert data[3] == "Українська (uk)"


def test_render_is_overwrite_not_append(tmp_path: Path) -> None:
    path = tmp_path / "pdm.xlsx"
    render_xlsx(VALID, path)
    render_xlsx(VALID, path)
    assert len(_rows(path, "survey")) == 8  # header + 7 rows, not doubled
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_form_renderer.py -v` — Expected: ImportError.

- [ ] **Step 3: Implement `src/haa/forms/renderer.py`**

```python
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
```

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/test_form_renderer.py -v` — all PASS; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/haa/forms/renderer.py tests/test_form_renderer.py
git commit -m "feat: deterministic bilingual XLSForm renderer"
```

---

### Task 4: pyxform compile check

**Files:**
- Create: `src/haa/forms/compiler.py`
- Test: `tests/test_form_compiler.py`

**Interfaces:**
- Produces: `compile_check(xlsx_path: Path) -> list[str]` — `[]` when pyxform converts the workbook successfully; otherwise a list of message lines (pyxform's own text, split on newlines, blanks dropped). Never raises for a bad form; a genuinely broken environment (pyxform missing) returns a single explanatory line.

- [ ] **Step 1: Write the failing tests**

`tests/test_form_compiler.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_form_compiler.py -v` — Expected: ImportError.

- [ ] **Step 3: Implement `src/haa/forms/compiler.py`**

```python
"""Compile a rendered XLSForm with pyxform — the authoritative syntax check."""

from __future__ import annotations

from pathlib import Path


def compile_check(xlsx_path: Path) -> list[str]:
    """Return [] when pyxform accepts the workbook, else its error lines."""
    try:
        from pyxform.xls2xform import convert
    except Exception as exc:  # pragma: no cover - environment problem
        return [f"pyxform is unavailable: {exc}"]

    try:
        convert(str(xlsx_path), warnings=[])
    except Exception as exc:
        text = str(exc).strip() or exc.__class__.__name__
        return [line.strip() for line in text.splitlines() if line.strip()]
    return []
```

**Verified against the installed pyxform 4.5.0** (probed while writing this
plan): `convert(xlsform, warnings=None, validate=False, pretty_print=False,
enketo=False, form_name=None, default_language=None, file_type=None) ->
ConvertResult` — there is NO `outpath` parameter; the XML comes back on
`result.xform`, and a broken form raises `PyXFormError` with an addressed
message, e.g. `[row : 6] On the 'survey' sheet, the 'relevant' value is
invalid. … Could not find the name 'nonexistent'.` Passing `warnings=[]`
keeps warnings out of stdout. `compile_check` deliberately discards the XML —
its only job is the verdict.

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/test_form_compiler.py -v` — all PASS (the broken-form
test takes a second: pyxform parses the workbook twice).
The call above matches pyxform 4.5.0 as installed. If a future version changes
it, the contract to preserve is: no exception → `[]`, exception → its message
lines; record any adaptation in your report. Run: `uv run ruff check .` — clean.

- [ ] **Step 5: Commit**

```bash
git add src/haa/forms/compiler.py tests/test_form_compiler.py
git commit -m "feat: pyxform compile check for rendered forms"
```

---

### Task 5: Form storage (yaml round-trip)

**Files:**
- Create: `src/haa/forms/storage.py`
- Test: `tests/test_form_storage.py`

**Interfaces:**
- Produces: `FormStorageError(ValueError)`; `SAFE_NAME_RE` (same shape as model NAME_RE); `form_paths(forms_dir: Path, name: str) -> tuple[Path, Path]` (yaml, xlsx — rejects unsafe names); `save_model(forms_dir: Path, name: str, model: dict) -> Path` (writes yaml with `allow_unicode=True`, `sort_keys=False`); `load_model(forms_dir: Path, name: str) -> dict` (raises FormStorageError for missing/broken yaml, message names the file); `list_forms(forms_dir: Path) -> list[str]` (stems of `*.form.yaml`, sorted); `parse_model_yaml(text: str) -> dict` (raises FormStorageError with the yaml error line for bad input).

- [ ] **Step 1: Write the failing tests**

`tests/test_form_storage.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure** — ImportError expected.

- [ ] **Step 3: Implement `src/haa/forms/storage.py`**

```python
"""Read and write form models as human-editable YAML."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

SAFE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,29}$")


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
```

- [ ] **Step 4: Verify** — `uv run pytest tests/test_form_storage.py -v` all PASS; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/haa/forms/storage.py tests/test_form_storage.py
git commit -m "feat: form model YAML storage with safe names"
```

---

### Task 6: Indicators registry

**Files:**
- Create: `src/haa/indicators/__init__.py` (empty), `src/haa/indicators/registry.py`
- Test: `tests/test_indicators_registry.py`

**Interfaces:**
- Produces: `AGGREGATIONS = {"count", "count_unique", "sum", "percent"}`; `RegistryError(ValueError)`; `registry_path(workspace: Path) -> Path` (`workspace/indicators.yaml`); `validate_registry(data: object) -> list[str]`; `parse_registry_yaml(text) -> dict` (RegistryError on bad yaml); `save_registry(workspace, data) -> Path`; `load_registry(workspace) -> dict` (returns `{"indicators": []}` when the file does not exist); `summarize(data) -> dict` with keys `count`, `with_target`, `with_measure`.
- Validation rules: top-level mapping with `indicators` list (may be empty); each item: `code` non-empty string (unique), `name` bilingual, `definition` non-empty string, optional `target` `{value: number, unit: str}`, optional `disaggregation` list of strings, optional `source` string, optional `measure`: `dataset` str, `aggregation` in AGGREGATIONS, `filter` str|null; for `percent` — `numerator` and `denominator` sub-mappings each with `field` str and optional `filter`; for the other aggregations — `field` str (`count` may omit it).

- [ ] **Step 1: Write the failing tests**

`tests/test_indicators_registry.py`:

```python
import copy
from pathlib import Path

import pytest

from haa.indicators.registry import (
    RegistryError,
    load_registry,
    parse_registry_yaml,
    save_registry,
    summarize,
    validate_registry,
)

VALID = {
    "indicators": [
        {
            "code": "1.1",
            "name": {"uk": "К-сть домогосподарств", "en": "# of households reached"},
            "definition": "Unique households with at least one service (by _uuid)",
            "target": {"value": 2500, "unit": "households"},
            "disaggregation": ["sex", "oblast"],
            "source": "beneficiaries",
            "measure": {
                "dataset": "beneficiaries",
                "aggregation": "count_unique",
                "field": "_uuid",
                "filter": None,
            },
        },
        {
            "code": "1.2",
            "name": {"uk": "Частка жінок-голів", "en": "% female-headed"},
            "definition": "Share of reached households headed by a woman",
            "target": {"value": 55, "unit": "percent"},
            "measure": {
                "dataset": "beneficiaries",
                "aggregation": "percent",
                "numerator": {"field": "_uuid", "filter": "head_sex == 'female'"},
                "denominator": {"field": "_uuid", "filter": None},
            },
        },
    ]
}


@pytest.fixture()
def data() -> dict:
    return copy.deepcopy(VALID)


def test_valid(data: dict) -> None:
    assert validate_registry(data) == []


def test_missing_indicators_key() -> None:
    assert any("indicators" in e for e in validate_registry({}))


def test_empty_list_is_valid() -> None:
    assert validate_registry({"indicators": []}) == []


def test_duplicate_codes(data: dict) -> None:
    data["indicators"][1]["code"] = "1.1"
    assert any("1.1" in e and "duplicate" in e.lower() for e in validate_registry(data))


def test_name_needs_both_languages(data: dict) -> None:
    del data["indicators"][0]["name"]["uk"]
    assert any("uk" in e for e in validate_registry(data))


def test_target_value_must_be_number(data: dict) -> None:
    data["indicators"][0]["target"]["value"] = "many"
    assert any("target.value" in e for e in validate_registry(data))


def test_unknown_aggregation(data: dict) -> None:
    data["indicators"][0]["measure"]["aggregation"] = "median"
    assert any("median" in e for e in validate_registry(data))


def test_percent_needs_numerator_and_denominator(data: dict) -> None:
    del data["indicators"][1]["measure"]["denominator"]
    assert any("denominator" in e for e in validate_registry(data))


def test_measure_is_optional(data: dict) -> None:
    del data["indicators"][0]["measure"]
    assert validate_registry(data) == []


def test_disaggregation_must_be_strings(data: dict) -> None:
    data["indicators"][0]["disaggregation"] = ["sex", 5]
    assert any("disaggregation" in e for e in validate_registry(data))


def test_round_trip(tmp_path: Path, data: dict) -> None:
    save_registry(tmp_path, data)
    assert load_registry(tmp_path) == data
    assert "К-сть" in (tmp_path / "indicators.yaml").read_text(encoding="utf-8")


def test_load_missing_returns_empty(tmp_path: Path) -> None:
    assert load_registry(tmp_path) == {"indicators": []}


def test_parse_bad_yaml() -> None:
    with pytest.raises(RegistryError):
        parse_registry_yaml("indicators: [\n")


def test_summarize(data: dict) -> None:
    assert summarize(data) == {"count": 2, "with_target": 2, "with_measure": 2}
```

- [ ] **Step 2: Run to verify failure** — ImportError expected.

- [ ] **Step 3: Implement `src/haa/indicators/registry.py`**

```python
"""Indicators registry: descriptive base plus an optional machine-readable measure."""

from __future__ import annotations

from pathlib import Path

import yaml

AGGREGATIONS = {"count", "count_unique", "sum", "percent"}
LANGS = ("uk", "en")
REGISTRY_FILE = "indicators.yaml"


class RegistryError(ValueError):
    """Friendly, user-facing registry failure."""


def registry_path(workspace: Path) -> Path:
    return workspace / REGISTRY_FILE


def parse_registry_yaml(text: str) -> dict:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RegistryError(f"Could not parse YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise RegistryError("Registry must be a YAML mapping with an 'indicators' list")
    return data


def _validate_measure(measure: object, where: str, errors: list[str]) -> None:
    if not isinstance(measure, dict):
        errors.append(f"{where}: expected a mapping")
        return
    if not isinstance(measure.get("dataset"), str) or not measure["dataset"]:
        errors.append(f"{where}.dataset: expected a dataset name")
    aggregation = measure.get("aggregation")
    if aggregation not in AGGREGATIONS:
        errors.append(
            f"{where}.aggregation: unknown {aggregation!r}; allowed: "
            f"{', '.join(sorted(AGGREGATIONS))}"
        )
    if aggregation == "percent":
        for part in ("numerator", "denominator"):
            block = measure.get(part)
            if not isinstance(block, dict):
                errors.append(f"{where}.{part}: percent needs a mapping with a 'field'")
                continue
            if not isinstance(block.get("field"), str) or not block["field"]:
                errors.append(f"{where}.{part}.field: expected a column name")
            if block.get("filter") is not None and not isinstance(block["filter"], str):
                errors.append(f"{where}.{part}.filter: expected a query string or null")
    elif aggregation in {"count_unique", "sum"}:
        if not isinstance(measure.get("field"), str) or not measure["field"]:
            errors.append(f"{where}.field: expected a column name for {aggregation!r}")
    if measure.get("filter") is not None and not isinstance(measure["filter"], str):
        errors.append(f"{where}.filter: expected a query string or null")


def validate_registry(data: object) -> list[str]:
    """Return a list of addressed, human-readable errors ([] means valid)."""
    if not isinstance(data, dict):
        return ["registry: expected a mapping with an 'indicators' list"]
    indicators = data.get("indicators")
    if not isinstance(indicators, list):
        return ["indicators: expected a list (may be empty)"]

    errors: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(indicators):
        where = f"indicators[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{where}: expected a mapping")
            continue
        code = item.get("code")
        if not isinstance(code, str) or not code.strip():
            errors.append(f"{where}.code: expected a non-empty string")
        else:
            if code in seen:
                errors.append(f"{where}.code: duplicate indicator code {code!r}")
            seen.add(code)
        name = item.get("name")
        if not isinstance(name, dict):
            errors.append(f"{where}.name: expected a mapping with {LANGS} labels")
        else:
            for lang in LANGS:
                if not isinstance(name.get(lang), str) or not name[lang].strip():
                    errors.append(f"{where}.name: missing or empty '{lang}' label")
        if not isinstance(item.get("definition"), str) or not item["definition"].strip():
            errors.append(f"{where}.definition: expected a non-empty string")
        target = item.get("target")
        if target is not None:
            if not isinstance(target, dict):
                errors.append(f"{where}.target: expected a mapping with 'value' and 'unit'")
            else:
                value = target.get("value")
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    errors.append(f"{where}.target.value: expected a number")
                if not isinstance(target.get("unit"), str) or not target["unit"]:
                    errors.append(f"{where}.target.unit: expected a unit name")
        disaggregation = item.get("disaggregation")
        if disaggregation is not None:
            if not isinstance(disaggregation, list) or not all(
                isinstance(d, str) for d in disaggregation
            ):
                errors.append(f"{where}.disaggregation: expected a list of strings")
        if item.get("source") is not None and not isinstance(item["source"], str):
            errors.append(f"{where}.source: expected a dataset name or null")
        if item.get("measure") is not None:
            _validate_measure(item["measure"], f"{where}.measure", errors)
    return errors


def save_registry(workspace: Path, data: dict) -> Path:
    path = registry_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return path


def load_registry(workspace: Path) -> dict:
    path = registry_path(workspace)
    if not path.is_file():
        return {"indicators": []}
    return parse_registry_yaml(path.read_text(encoding="utf-8"))


def summarize(data: dict) -> dict:
    indicators = data.get("indicators") or []
    return {
        "count": len(indicators),
        "with_target": sum(1 for i in indicators if isinstance(i, dict) and i.get("target")),
        "with_measure": sum(1 for i in indicators if isinstance(i, dict) and i.get("measure")),
    }
```

- [ ] **Step 4: Verify** — `uv run pytest tests/test_indicators_registry.py -v` all PASS; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/haa/indicators tests/test_indicators_registry.py
git commit -m "feat: indicators registry schema with optional measure block"
```

---

### Task 7: `forms` MCP server (FormToolbox)

**Files:**
- Create: `src/haa/core/tools/formtools.py`
- Test: `tests/test_formtools.py`

**Interfaces:**
- Consumes: forms model/renderer/compiler/storage (Tasks 2–5), indicators registry (Task 6), `SessionTelemetry`, `HaaConfig`.
- Produces: `FORM_TOOL_NAMES = ["mcp__forms__list_local_forms", "mcp__forms__load_form", "mcp__forms__save_form", "mcp__forms__read_indicators", "mcp__forms__save_indicators"]`; `FormToolbox(config, telemetry)` with sync methods returning strings: `list_local_forms()`, `load_form(name)` (yaml text or friendly error), `save_form(name, model_yaml)` (parse → validate_model → save yaml → render xlsx → compile_check → verdict; on validation/compile failure nothing is written and the errors are returned; telemetry `form_saved` on success with `questions` and `attempts_failed` omitted — see below), `read_indicators()` (yaml text, or the empty-registry hint), `save_indicators(yaml_text)` (parse → validate_registry → save → summary; telemetry `indicators_saved`); `build_forms_server(box)` — thin async @tool wrappers, `asyncio.to_thread` for `save_form` (pyxform is CPU-bound).
- Failure bookkeeping: `FormToolbox` counts consecutive failed `save_form` calls per name in `self._failed_saves: dict[str, int]` and includes the count in the success telemetry (`attempts_failed`), then resets it.

- [ ] **Step 1: Write the failing tests**

`tests/test_formtools.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure** — ImportError expected.

- [ ] **Step 3: Implement `src/haa/core/tools/formtools.py`**

```python
"""In-process MCP server for the indicators registry and form design."""

from __future__ import annotations

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

        render_xlsx(model, xlsx_path)
        compile_errors = compile_check(xlsx_path)
        if compile_errors:
            xlsx_path.unlink(missing_ok=True)
            self._failed_saves[name] = self._failed_saves.get(name, 0) + 1
            return "pyxform rejected the form:\n" + "\n".join(f"- {e}" for e in compile_errors)

        save_model(self.config.forms_dir, name, model)
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
        save_registry(self.config.workspace, data)
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
```

- [ ] **Step 4: Verify** — `uv run pytest tests/test_formtools.py -v` all PASS; full suite once; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/haa/core/tools/formtools.py tests/test_formtools.py
git commit -m "feat: forms MCP server for registry and form design"
```

---

### Task 8: Designer role, registry, routing, analyst tool

**Files:**
- Create: `src/haa/core/agents/designer.py`
- Modify: `src/haa/core/agents/registry.py`, `src/haa/core/agents/analyst.py`, `src/haa/core/agents/orchestrator.py`
- Test: `tests/test_designer_agent.py`, `tests/test_agents.py` (adjust)

**Interfaces:**
- `DESIGNER_PROMPT: str`; `build_designer(config) -> AgentDefinition` with `tools = FORM_TOOL_NAMES + ["mcp__data__list_project_docs", "mcp__data__read_project_doc", "mcp__data__list_datasets", "mcp__data__profile_dataset"]`, `model=config.designer_model`, `mcpServers=["forms", "data"]`.
- `build_agents(config)` → `{"analyst", "cleaner", "designer"}`.
- Analyst gains `"mcp__forms__read_indicators"` in `tools` and `"forms"` in `mcpServers`; its prompt gains one workflow line about consulting the registry for target/definition questions.
- Orchestrator prompt gains: indicator-registry work and survey-form design/editing → delegate to `designer`.

- [ ] **Step 1: Write the failing tests**

`tests/test_designer_agent.py`:

```python
from pathlib import Path

from haa.config import load_config
from haa.core.agents.analyst import build_analyst
from haa.core.agents.designer import DESIGNER_PROMPT, build_designer
from haa.core.agents.registry import build_agents
from haa.core.tools.formtools import FORM_TOOL_NAMES


def _cfg(tmp_path: Path):
    return load_config(tmp_path)


def test_designer_definition(tmp_path: Path) -> None:
    agent = build_designer(_cfg(tmp_path))
    assert agent.model == "claude-opus-5"
    assert set(FORM_TOOL_NAMES) <= set(agent.tools)
    assert "mcp__data__read_project_doc" in agent.tools
    assert "mcp__data__run_analysis" not in agent.tools
    assert set(agent.mcpServers) == {"forms", "data"}
    assert agent.prompt == DESIGNER_PROMPT


def test_designer_model_configurable(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text('designer_model = "claude-sonnet-5"', encoding="utf-8")
    assert build_designer(_cfg(tmp_path)).model == "claude-sonnet-5"


def test_registry_has_three_roles(tmp_path: Path) -> None:
    assert set(build_agents(_cfg(tmp_path))) == {"analyst", "cleaner", "designer"}


def test_analyst_can_read_indicators(tmp_path: Path) -> None:
    agent = build_analyst(_cfg(tmp_path))
    assert "mcp__forms__read_indicators" in agent.tools
    assert "forms" in agent.mcpServers


def test_designer_prompt_discipline() -> None:
    for needle in (
        "consent", "sadd", "uk", "en", "save_form", "pyxform",
        "read_indicators", "never fabricate", "upload",
    ):
        assert needle.lower() in DESIGNER_PROMPT.lower(), needle
```

In `tests/test_agents.py`: update `test_registry` to expect the three roles, and add `"designer"` to the orchestrator prompt needles in `test_prompts_carry_discipline`.

- [ ] **Step 2: Run to verify failure** — ImportError + registry assertion FAIL.

- [ ] **Step 3: Implement**

`src/haa/core/agents/designer.py`:

```python
"""Designer subagent: indicators registry curation and XLSForm design."""

from claude_agent_sdk import AgentDefinition

from haa.config import HaaConfig
from haa.core.tools.formtools import FORM_TOOL_NAMES

DOC_TOOLS = [
    "mcp__data__list_project_docs",
    "mcp__data__read_project_doc",
    "mcp__data__list_datasets",
    "mcp__data__profile_dataset",
]

DESIGNER_PROMPT = """\
You maintain the indicators registry and design survey forms for humanitarian
programmes (KoboToolbox / Ona). You never invent programme facts: indicators
come from the project documentation, and form questions serve those indicators.

Indicators registry:
- Extract indicators from project docs (list_project_docs / read_project_doc).
  For each: code, bilingual name, definition, target (value + unit),
  disaggregation, source dataset.
- Add the optional machine-readable `measure` block only when the documentation
  and the dataset make the calculation unambiguous — check real column names
  with profile_dataset first. Never fabricate a column name.
- Save with save_indicators; fix every reported validation error and retry.

Form design:
- Build the form as a model (YAML) and save it with save_form. The tool
  validates the model, renders the XLSForm and compiles it with pyxform;
  read the errors it returns and fix them, then save again.
- Editing an existing form: load_form, change the model, save_form. Never ask
  the user to edit the .xlsx — it is regenerated from the model every time.
- Every label needs both languages: uk (Ukrainian, for enumerators) and en
  (English, for donors/HQ).
- Humanitarian conventions: informed consent question first, with the rest of
  the form relevant on it; SADD questions (sex, age, disability of the
  respondent or household head) whenever the indicators are disaggregated that
  way; stable snake_case names and choice list names; no personal data beyond
  what the programme genuinely needs.
- When done, tell the user the file name and that they upload it to Kobo/Ona
  themselves — this tool does not deploy forms.

Honesty: never fabricate an indicator, a target or a column name. If the
documentation does not state something, say so instead of guessing.
"""


def build_designer(config: HaaConfig) -> AgentDefinition:
    return AgentDefinition(
        description=(
            "Indicators registry and survey form design: extracts indicators from "
            "project documentation, designs and edits XLSForm surveys. Delegate "
            "indicator-registry and form-design requests here."
        ),
        prompt=DESIGNER_PROMPT,
        tools=[*FORM_TOOL_NAMES, *DOC_TOOLS],
        model=config.designer_model,
        mcpServers=["forms", "data"],
    )
```

`registry.py`: import `build_designer`, add `"designer": build_designer(config)`.

`analyst.py`: `tools=[*DATA_TOOL_NAMES, "mcp__forms__read_indicators"]`, `mcpServers=["data", "forms"]`, and add to the workflow section of `ANALYST_PROMPT`:

```
   If the question mentions an indicator, a target or programme progress, call
   read_indicators first — the registry holds the definition, the target and
   (sometimes) a machine-readable measure telling you exactly how to compute it.
```

`orchestrator.py` routing block — add:

```
- Requests about the indicators registry (extract indicators from documentation,
  update or review them) or about designing/editing a survey form (XLSForm for
  Kobo/Ona): delegate to the `designer` subagent.
```

- [ ] **Step 4: Verify** — `uv run pytest tests/test_designer_agent.py tests/test_agents.py -v` all PASS; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/haa/core/agents tests/test_designer_agent.py tests/test_agents.py
git commit -m "feat: designer agent role, registry access for the analyst, routing"
```

---

### Task 9: Session wiring

**Files:**
- Modify: `src/haa/core/session.py`
- Test: `tests/test_session.py` (extend `test_build_options`)

**Interfaces:**
- Session owns `FormToolbox` + `build_forms_server`; `build_options()` → `mcp_servers` gains `"forms"`, `allowed_tools` becomes `["Agent", "Task", *DATA_TOOL_NAMES, *SOURCE_TOOL_NAMES, *FORM_TOOL_NAMES]`. Everything else untouched.

- [ ] **Step 1: Extend the test** — in `tests/test_session.py::test_build_options` add:

```python
    from haa.core.tools.formtools import FORM_TOOL_NAMES

    assert "forms" in opts.mcp_servers
    assert set(FORM_TOOL_NAMES) <= set(opts.allowed_tools)
```

- [ ] **Step 2: Run to verify failure** — both assertions FAIL.

- [ ] **Step 3: Implement** — in `session.py`:

```python
from haa.core.tools.formtools import FORM_TOOL_NAMES, FormToolbox, build_forms_server
```

`__init__`: `self._form_toolbox = FormToolbox(config, self.telemetry)`; `self._forms_server = build_forms_server(self._form_toolbox)`.
`build_options`: `mcp_servers={"data": self._server, "sources": self._sources_server, "forms": self._forms_server}` and `allowed_tools=["Agent", "Task", *DATA_TOOL_NAMES, *SOURCE_TOOL_NAMES, *FORM_TOOL_NAMES]`.

- [ ] **Step 4: Verify** — `uv run pytest tests/test_session.py -v` all PASS; full suite once; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/haa/core/session.py tests/test_session.py
git commit -m "feat: wire the forms server into the session"
```

---

### Task 10: README + api evals

**Files:**
- Modify: `README.md`, `tests/test_smoke_api.py`

**Interfaces:**
- Two new api-marked evals with independently computed expectations; README gains an "Indicators and forms" section and an updated roadmap.

- [ ] **Step 1: Write the evals** — append to `tests/test_smoke_api.py`:

```python
async def test_indicator_extraction_builds_registry(demo_workspace: Path) -> None:
    import yaml

    from haa.indicators.registry import registry_path, validate_registry

    cfg = load_config(demo_workspace)
    registry_path(cfg.workspace).unlink(missing_ok=True)
    async with AnalyticsSession(cfg) as session:
        async for _ in session.ask(
            "Витягни індикатори з логфрейму проєкту у реєстр індикаторів."
        ):
            pass

    data = yaml.safe_load(registry_path(cfg.workspace).read_text(encoding="utf-8"))
    assert validate_registry(data) == []
    codes = {str(i["code"]) for i in data["indicators"]}
    assert "1.1" in codes
    target = next(i for i in data["indicators"] if str(i["code"]) == "1.1")["target"]
    assert target["value"] == 2500  # the value stated in demo logframe.md


async def test_designer_produces_compiling_form(demo_workspace: Path) -> None:
    from haa.forms.compiler import compile_check

    cfg = load_config(demo_workspace)
    async with AnalyticsSession(cfg) as session:
        async for _ in session.ask(
            "Створи форму пост-дистрибуційного моніторингу 'pdm': згода, стать і вік "
            "голови домогосподарства, отримані послуги, задоволеність."
        ):
            pass

    xlsx = cfg.forms_dir / "pdm.xlsx"
    assert xlsx.exists(), "designer did not produce the XLSForm"
    assert (cfg.forms_dir / "pdm.form.yaml").exists()
    assert compile_check(xlsx) == []

    import openpyxl

    wb = openpyxl.load_workbook(xlsx)
    header = [c.value for c in wb["survey"][1]]
    assert "label::Українська (uk)" in header and "label::English (en)" in header
    names = [row[1] for row in wb["survey"].iter_rows(min_row=2, values_only=True)]
    joined = " ".join(str(n) for n in names if n)
    assert "consent" in joined.lower()
    assert any(k in joined.lower() for k in ("sex", "gender", "stat"))
```

- [ ] **Step 2: Verify gating**

Run: `uv run pytest -q` — new evals deselected; offline suite green (expect 190+ passed, 11 deselected).
Run: `uv run pytest -m api --collect-only -q` — 9 api tests collected, no errors. Run: `uv run ruff check .` — clean.

- [ ] **Step 3: Update README** — add after the "Cleaning a dataset" section:

```markdown
## Indicators and forms

Ask: *"Extract the indicators from the logframe"* — the designer agent reads
`workspace/project_docs/`, builds `workspace/indicators.yaml` (code, bilingual
name, definition, target, disaggregation, and — when unambiguous — a
machine-readable `measure` block), and validates it against a schema. The
analyst consults that registry when you ask about progress toward a target.

Ask: *"Design a post-distribution monitoring form"* — you get
`workspace/forms/<name>.form.yaml` (the editable source of truth) and
`workspace/forms/<name>.xlsx` (an XLSForm, Ukrainian + English, compiled with
pyxform before it is saved). Upload the .xlsx to Kobo or Ona yourself — this
tool does not deploy forms. Editing works the same way: *"make the phone
question optional in pdm"* re-renders the workbook from the updated model.
```

Update the Roadmap: mark indicators/forms done; remaining — SharePoint connector, reporting agent (5W/MEAL), Power BI engineer, web UI.

- [ ] **Step 4: Commit**

```bash
git add README.md tests/test_smoke_api.py
git commit -m "test: designer api evals; docs: indicators and forms sections"
```

- [ ] **Step 5: Run the evals manually when a key is available**

Run: `uv run pytest -m api -v -k "indicator or designer"`. A formatting-only failure means the assertion needs normalizing; a substantive failure (registry invalid, form does not compile, missing consent/SADD) is a real defect — check `workspace/logs/` for the tool calls.

---

## Acceptance Checklist (spec §7)

- [ ] Indicator extraction on demo data produces a schema-valid `indicators.yaml` containing 1.1 with target 2500 (Tasks 6, 7, 10).
- [ ] Analyst answers progress questions via `read_indicators` (Tasks 7, 8; visible in telemetry).
- [ ] Form design produces `<name>.form.yaml` + `<name>.xlsx` that pyxform compiles, bilingual, with consent and SADD questions (Tasks 2–5, 7, 8, 10).
- [ ] Editing an existing local form re-renders the workbook (Tasks 5, 7).
- [ ] `indicators_saved` / `form_saved` telemetry events (Task 7).
- [ ] Offline tests and ruff green in CI; api evals pass locally (all tasks).
