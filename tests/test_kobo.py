from pathlib import Path

import httpx
import pandas as pd
import pytest
import respx

from haa.connectors.base import ConnectorError
from haa.connectors.kobo import KoboConnector

BASE = "https://kf.example.org"

ASSETS = {
    "results": [
        {"uid": "aXb1", "name": "Household Survey", "asset_type": "survey",
         "deployment__submission_count": 3},
        {"uid": "tmpl", "name": "A template", "asset_type": "template"},
    ]
}
PAGE1 = {
    "count": 3,
    "next": f"{BASE}/api/v2/assets/aXb1/data/?format=json&limit=2&start=2",
    "results": [
        {"_uuid": "u1", "oblast": "X", "hh_size": 3},
        {"_uuid": "u2", "oblast": "Y", "hh_size": 5},
    ],
}
PAGE2 = {"count": 3, "next": None, "results": [{"_uuid": "u3", "oblast": "X", "hh_size": 2}]}

ASSETS_PAGE1 = {
    "results": [
        {"uid": "s1", "name": "Survey One", "asset_type": "survey",
         "deployment__submission_count": 1},
    ],
    "next": f"{BASE}/api/v2/assets/?format=json&page=2",
}
ASSETS_PAGE2 = {
    "results": [
        {"uid": "s2", "name": "Survey Two", "asset_type": "survey",
         "deployment__submission_count": 2},
    ],
    "next": None,
}


@pytest.fixture()
def connector() -> KoboConnector:
    return KoboConnector(BASE, "tok", page_size=2)


@respx.mock
def test_list_forms_filters_surveys(connector: KoboConnector) -> None:
    route = respx.get(f"{BASE}/api/v2/assets/").mock(return_value=httpx.Response(200, json=ASSETS))
    forms = connector.list_forms()
    assert [(f.uid, f.name, f.submissions) for f in forms] == [("aXb1", "Household Survey", 3)]
    assert route.calls[0].request.headers["Authorization"] == "Token tok"


@respx.mock
def test_list_forms_paginates(connector: KoboConnector) -> None:
    # respx matches params as a SUBSET in insertion order — the page-2 route (which
    # carries the extra `page` param) must be registered FIRST, or page 1's request
    # (params={"format": "json"}, a subset of page 2's query too) would shadow it.
    respx.get(f"{BASE}/api/v2/assets/", params={"page": "2"}).mock(
        return_value=httpx.Response(200, json=ASSETS_PAGE2)
    )
    respx.get(f"{BASE}/api/v2/assets/", params={"format": "json"}).mock(
        return_value=httpx.Response(200, json=ASSETS_PAGE1)
    )
    forms = connector.list_forms()
    assert [(f.uid, f.name, f.submissions) for f in forms] == [
        ("s1", "Survey One", 1),
        ("s2", "Survey Two", 2),
    ]


@respx.mock
def test_pull_paginates_and_writes(connector: KoboConnector, tmp_path: Path) -> None:
    respx.get(f"{BASE}/api/v2/assets/").mock(return_value=httpx.Response(200, json=ASSETS))
    # respx matches params as a SUBSET in insertion order — the start-page route
    # must be registered FIRST, or page 2's request (which also carries
    # format=json&limit=2) would match the page-1 route and loop forever.
    respx.get(f"{BASE}/api/v2/assets/aXb1/data/", params={"limit": "2", "start": "2"}).mock(
        return_value=httpx.Response(200, json=PAGE2)
    )
    respx.get(f"{BASE}/api/v2/assets/aXb1/data/", params={"format": "json", "limit": "2"}).mock(
        return_value=httpx.Response(200, json=PAGE1)
    )
    result = connector.pull("Household Survey", tmp_path)
    assert result.rows == 3 and result.form.uid == "aXb1"
    df = pd.read_excel(result.path)
    assert len(df) == 3 and set(df["_uuid"]) == {"u1", "u2", "u3"}
    assert result.path.name == "household_survey.xlsx"


@respx.mock
def test_pull_by_uid(connector: KoboConnector, tmp_path: Path) -> None:
    respx.get(f"{BASE}/api/v2/assets/").mock(return_value=httpx.Response(200, json=ASSETS))
    respx.get(url__startswith=f"{BASE}/api/v2/assets/aXb1/data/").mock(
        return_value=httpx.Response(200, json=PAGE2)
    )
    assert connector.pull("aXb1", tmp_path).rows == 1


@respx.mock
def test_unknown_form_lists_available(connector: KoboConnector, tmp_path: Path) -> None:
    respx.get(f"{BASE}/api/v2/assets/").mock(return_value=httpx.Response(200, json=ASSETS))
    with pytest.raises(ConnectorError, match="Household Survey"):
        connector.pull("nope", tmp_path)


@respx.mock
def test_empty_form_no_file(connector: KoboConnector, tmp_path: Path) -> None:
    respx.get(f"{BASE}/api/v2/assets/").mock(return_value=httpx.Response(200, json=ASSETS))
    respx.get(url__startswith=f"{BASE}/api/v2/assets/aXb1/data/").mock(
        return_value=httpx.Response(200, json={"count": 0, "next": None, "results": []})
    )
    with pytest.raises(ConnectorError, match="0 submissions"):
        connector.pull("aXb1", tmp_path)
    assert not list(tmp_path.iterdir())


def test_close_releases_client(connector: KoboConnector) -> None:
    connector.close()
    assert connector._client.is_closed


@respx.mock
def test_context_manager() -> None:
    respx.get(f"{BASE}/api/v2/assets/").mock(return_value=httpx.Response(200, json=ASSETS))
    with KoboConnector(BASE, "tok") as c:
        c.list_forms()
    assert c._client.is_closed
