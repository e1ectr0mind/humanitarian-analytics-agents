from pathlib import Path

import httpx
import pandas as pd
import pytest
import respx

from haa.connectors.base import (
    ConnectorError,
    ensure_row_ids,
    get_json,
    rows_to_dataframe,
    safe_filename,
    write_pull,
)


def test_unique_uuid_left_untouched() -> None:
    df = pd.DataFrame({"_uuid": ["a", "b", "c"], "v": [1, 2, 3]})
    out = ensure_row_ids(df)
    assert "_haa_row_id" not in out.columns
    assert out is df  # no copy when nothing to do


def test_missing_id_gets_haa_row_id() -> None:
    df = pd.DataFrame({"v": [1, 2, 3]})
    out = ensure_row_ids(df)
    assert out["_haa_row_id"].is_unique
    assert len(out["_haa_row_id"].iloc[0]) == 16


def test_duplicate_uuid_triggers_haa_row_id() -> None:
    df = pd.DataFrame({"_uuid": ["a", "a", "b"], "v": [1, 1, 2]})
    out = ensure_row_ids(df)
    assert out["_haa_row_id"].is_unique


def test_identical_rows_get_suffixed_ids() -> None:
    df = pd.DataFrame({"v": [1, 1, 1]})
    ids = ensure_row_ids(df)["_haa_row_id"].tolist()
    assert len(set(ids)) == 3
    assert ids[0] == ids[1].rsplit("-", 1)[0]  # same content hash, -1 suffix


def test_row_ids_deterministic() -> None:
    df = pd.DataFrame({"v": [1, 2], "w": ["x", "y"]})
    a = ensure_row_ids(df.copy())["_haa_row_id"].tolist()
    b = ensure_row_ids(df.copy())["_haa_row_id"].tolist()
    assert a == b


def test_row_ids_independent_of_column_order() -> None:
    a = ensure_row_ids(pd.DataFrame({"v": [1], "w": ["x"]}))["_haa_row_id"].iloc[0]
    b = ensure_row_ids(pd.DataFrame({"w": ["x"], "v": [1]}))["_haa_row_id"].iloc[0]
    assert a == b


def test_rows_to_dataframe_empty() -> None:
    assert rows_to_dataframe([]).empty


def test_safe_filename() -> None:
    assert safe_filename("Household Survey (v2)!") == "household_survey_v2"


def test_write_pull_creates_xlsx(tmp_path: Path) -> None:
    df = pd.DataFrame({"v": [1, 2]})
    path = write_pull(df, tmp_path, "My Form")
    assert path == tmp_path / "my_form.xlsx"
    back = pd.read_excel(path)
    assert "_haa_row_id" in back.columns and len(back) == 2


@respx.mock
def test_get_json_retries_then_succeeds() -> None:
    route = respx.get("https://x.test/api").mock(
        side_effect=[httpx.ConnectError("boom"), httpx.Response(200, json={"ok": 1})]
    )
    with httpx.Client() as client:
        assert get_json(client, "https://x.test/api") == {"ok": 1}
    assert route.call_count == 2


@respx.mock
def test_get_json_auth_error_no_retry() -> None:
    route = respx.get("https://x.test/api").mock(return_value=httpx.Response(401))
    with httpx.Client() as client, pytest.raises(ConnectorError, match="[Tt]oken"):
        get_json(client, "https://x.test/api")
    assert route.call_count == 1


@respx.mock
def test_get_json_gives_up_with_friendly_error() -> None:
    respx.get("https://x.test/api").mock(side_effect=httpx.ConnectError("down"))
    with httpx.Client() as client, pytest.raises(ConnectorError, match="3 attempts"):
        get_json(client, "https://x.test/api")
