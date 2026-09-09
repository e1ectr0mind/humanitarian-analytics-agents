import time

import httpx
import pytest
import respx

import haa.connectors.sharepoint as sp
from haa.connectors.base import ConnectorError
from haa.connectors.credentials import Connection
from haa.connectors.msauth import LOGIN_BASE, TokenSet
from haa.connectors.sharepoint import GRAPH, SharePointConnector

TENANT = "contoso.onmicrosoft.com"
TOKEN_URL = f"{LOGIN_BASE}/{TENANT}/oauth2/v2.0/token"
SITE_CONN = Connection(
    name="sp", kind="sharepoint",
    base_url="https://contoso.sharepoint.com/sites/MEAL",
    tenant=TENANT, client_id="cid",
)
OD_CONN = Connection(name="od", kind="sharepoint", base_url="onedrive",
                     tenant=TENANT, client_id="cid")


def _token_json(expires_in: float = 3600.0) -> str:
    return TokenSet("at-1", "rt-1", time.time() + expires_in).to_json()


def _mock_refresh(access: str = "at-2", refresh_tok: str = "rt-2") -> respx.Route:
    return respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={
            "access_token": access, "refresh_token": refresh_tok, "expires_in": 3600,
        })
    )


@respx.mock
def test_site_drive_resolution() -> None:
    respx.get(f"{GRAPH}/sites/contoso.sharepoint.com:/sites/MEAL").mock(
        return_value=httpx.Response(200, json={"id": "site-1"})
    )
    respx.get(f"{GRAPH}/sites/site-1/drive").mock(
        return_value=httpx.Response(200, json={"id": "drv-1"})
    )
    c = SharePointConnector(SITE_CONN, _token_json())
    assert c._drive() == f"{GRAPH}/drives/drv-1"
    assert c._drive() == f"{GRAPH}/drives/drv-1"  # cached: routes called once
    assert respx.calls.call_count == 2


@respx.mock
def test_onedrive_resolution() -> None:
    respx.get(f"{GRAPH}/me/drive").mock(return_value=httpx.Response(200, json={"id": "drv-me"}))
    assert SharePointConnector(OD_CONN, _token_json())._drive() == f"{GRAPH}/drives/drv-me"


@respx.mock
def test_expired_token_refreshes_and_persists_before_call() -> None:
    _mock_refresh()
    route = respx.get(f"{GRAPH}/me/drive").mock(
        return_value=httpx.Response(200, json={"id": "drv-me"})
    )
    saved: list[str] = []
    c = SharePointConnector(OD_CONN, _token_json(expires_in=-10.0),
                            on_tokens_updated=saved.append)
    c._drive()
    assert route.calls[0].request.headers["Authorization"] == "Bearer at-2"
    assert c.refresh_events == [True]
    assert saved and TokenSet.from_json(saved[0]).refresh_token == "rt-2"


@respx.mock
def test_401_triggers_one_refresh_and_retry() -> None:
    _mock_refresh()
    respx.get(f"{GRAPH}/me/drive").mock(side_effect=[
        httpx.Response(401),
        httpx.Response(200, json={"id": "drv-me"}),
    ])
    c = SharePointConnector(OD_CONN, _token_json())
    assert c._drive() == f"{GRAPH}/drives/drv-me"
    assert c.refresh_events == [True]


@respx.mock
def test_site_not_found_is_friendly() -> None:
    respx.get(f"{GRAPH}/sites/contoso.sharepoint.com:/sites/MEAL").mock(
        return_value=httpx.Response(404)
    )
    with pytest.raises(ConnectorError, match="Site not found"):
        SharePointConnector(SITE_CONN, _token_json())._drive()


def test_missing_tenant_rejected() -> None:
    bare = Connection(name="x", kind="sharepoint", base_url="onedrive")
    with pytest.raises(ConnectorError, match="haa connect sharepoint"):
        SharePointConnector(bare, _token_json())


def test_garbage_token_rejected() -> None:
    with pytest.raises(ConnectorError, match="haa connect sharepoint"):
        SharePointConnector(OD_CONN, "kobo-style-token")


def test_close_releases_client() -> None:
    c = SharePointConnector(OD_CONN, _token_json())
    c.close()
    assert c._client.is_closed


@respx.mock
def test_download_server_error_is_friendly() -> None:
    respx.get(f"{GRAPH}/some/file/content").mock(return_value=httpx.Response(500))
    c = SharePointConnector(OD_CONN, _token_json())
    with pytest.raises(ConnectorError, match="Try again"):
        c._download(f"{GRAPH}/some/file/content")


def _drive_ready() -> SharePointConnector:
    """Connector against OneDrive with the drive already mocked (call inside respx.mock)."""
    respx.get(f"{GRAPH}/me/drive").mock(return_value=httpx.Response(200, json={"id": "drv-me"}))
    return SharePointConnector(OD_CONN, _token_json())


def _item(name: str, item_id: str, *, folder: bool = False) -> dict:
    node: dict = {"id": item_id, "name": name}
    node["folder" if folder else "file"] = {}
    return node


@respx.mock
def test_list_recurses_and_filters_tabular() -> None:
    c = _drive_ready()
    respx.get(f"{GRAPH}/drives/drv-me/root/children").mock(
        return_value=httpx.Response(200, json={"value": [
            _item("2026", "fld-1", folder=True),
            _item("june_5w.xlsx", "f-1"),
            _item("readme.txt", "f-2"),
        ]})
    )
    respx.get(f"{GRAPH}/drives/drv-me/items/fld-1/children").mock(
        return_value=httpx.Response(200, json={"value": [_item("archive.csv", "f-3")]})
    )
    forms = c.list_forms()
    assert [(f.uid, f.name) for f in forms] == [
        ("f-1", "june_5w.xlsx"), ("f-3", "2026/archive.csv"),
    ]
    assert c.list_truncated is False


@respx.mock
def test_list_follows_next_link() -> None:
    c = _drive_ready()
    next_url = f"{GRAPH}/drives/drv-me/root/children?$skiptoken=abc"
    respx.get(next_url).mock(
        return_value=httpx.Response(200, json={"value": [_item("b.csv", "f-2")]})
    )
    respx.get(f"{GRAPH}/drives/drv-me/root/children").mock(
        return_value=httpx.Response(200, json={
            "value": [_item("a.xlsx", "f-1")], "@odata.nextLink": next_url,
        })
    )
    assert [f.name for f in c.list_forms()] == ["a.xlsx", "b.csv"]


@respx.mock
def test_list_caps_and_flags_truncation(monkeypatch) -> None:
    monkeypatch.setattr(sp, "MAX_FILES", 2)
    c = _drive_ready()
    respx.get(f"{GRAPH}/drives/drv-me/root/children").mock(
        return_value=httpx.Response(200, json={"value": [
            _item("a.xlsx", "f-1"), _item("b.xlsx", "f-2"), _item("c.xlsx", "f-3"),
        ]})
    )
    forms = c.list_forms()
    assert len(forms) == 2 and c.list_truncated is True


@respx.mock
def test_list_pinned_folder_encodes_path() -> None:
    respx.get(f"{GRAPH}/sites/contoso.sharepoint.com:/sites/MEAL").mock(
        return_value=httpx.Response(200, json={"id": "site-1"})
    )
    respx.get(f"{GRAPH}/sites/site-1/drive").mock(
        return_value=httpx.Response(200, json={"id": "drv-1"})
    )
    route = respx.get(
        f"{GRAPH}/drives/drv-1/root:/Shared%20Documents/5W:/children"
    ).mock(return_value=httpx.Response(200, json={"value": [_item("x.csv", "f-1")]}))
    conn = Connection(name="sp", kind="sharepoint",
                      base_url="https://contoso.sharepoint.com/sites/MEAL",
                      tenant=TENANT, client_id="cid", folder="Shared Documents/5W")
    forms = SharePointConnector(conn, _token_json()).list_forms()
    assert route.called and [f.name for f in forms] == ["x.csv"]


@respx.mock
def test_list_missing_folder_is_friendly() -> None:
    respx.get(f"{GRAPH}/me/drive").mock(return_value=httpx.Response(200, json={"id": "drv-me"}))
    respx.get(f"{GRAPH}/drives/drv-me/root:/nope:/children").mock(
        return_value=httpx.Response(404)
    )
    conn = Connection(name="od", kind="sharepoint", base_url="onedrive",
                      tenant=TENANT, client_id="cid", folder="nope")
    with pytest.raises(ConnectorError, match="'nope' not found"):
        SharePointConnector(conn, _token_json()).list_forms()
