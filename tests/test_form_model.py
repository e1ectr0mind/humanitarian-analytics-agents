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
