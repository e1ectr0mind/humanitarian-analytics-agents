from pathlib import Path

import httpx
import pandas as pd
import pytest
import respx

from haa.connectors.base import ConnectorError
from haa.connectors.ona import OnaConnector

BASE = "https://api.ona.example"
FORMS = [
    {"formid": 101, "id_string": "hh_survey", "title": "Household Survey",
     "num_of_submissions": 3},
]
ROWS_P1 = [{"_uuid": "u1", "v": 1}, {"_uuid": "u2", "v": 2}]
ROWS_P2 = [{"_uuid": "u3", "v": 3}]


@pytest.fixture()
def connector() -> OnaConnector:
    return OnaConnector(BASE, "tok", page_size=2)


@respx.mock
def test_list_forms(connector: OnaConnector) -> None:
    route = respx.get(f"{BASE}/api/v1/forms").mock(return_value=httpx.Response(200, json=FORMS))
    forms = connector.list_forms()
    assert [(f.uid, f.name, f.submissions) for f in forms] == [("101", "hh_survey", 3)]
    assert route.calls[0].request.headers["Authorization"] == "Token tok"


@respx.mock
def test_pull_paginates(connector: OnaConnector, tmp_path: Path) -> None:
    respx.get(f"{BASE}/api/v1/forms").mock(return_value=httpx.Response(200, json=FORMS))
    respx.get(f"{BASE}/api/v1/data/101", params={"page": "1", "page_size": "2"}).mock(
        return_value=httpx.Response(200, json=ROWS_P1)
    )
    respx.get(f"{BASE}/api/v1/data/101", params={"page": "2", "page_size": "2"}).mock(
        return_value=httpx.Response(200, json=ROWS_P2)
    )
    result = connector.pull("hh_survey", tmp_path)
    assert result.rows == 3
    assert len(pd.read_excel(result.path)) == 3


@respx.mock
def test_pull_by_title(connector: OnaConnector, tmp_path: Path) -> None:
    respx.get(f"{BASE}/api/v1/forms").mock(return_value=httpx.Response(200, json=FORMS))
    respx.get(url__startswith=f"{BASE}/api/v1/data/101").mock(
        return_value=httpx.Response(200, json=ROWS_P2)
    )
    assert connector.pull("Household Survey", tmp_path).form.uid == "101"


@respx.mock
def test_empty_form(connector: OnaConnector, tmp_path: Path) -> None:
    respx.get(f"{BASE}/api/v1/forms").mock(return_value=httpx.Response(200, json=FORMS))
    respx.get(url__startswith=f"{BASE}/api/v1/data/101").mock(
        return_value=httpx.Response(200, json=[])
    )
    with pytest.raises(ConnectorError, match="0 submissions"):
        connector.pull("101", tmp_path)


def test_close_releases_client(connector: OnaConnector) -> None:
    connector.close()
    assert connector._client.is_closed


@respx.mock
def test_context_manager() -> None:
    respx.get(f"{BASE}/api/v1/forms").mock(return_value=httpx.Response(200, json=FORMS))
    with OnaConnector(BASE, "tok") as c:
        c.list_forms()
    assert c._client.is_closed
