import time

import httpx
import pytest
import respx

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
