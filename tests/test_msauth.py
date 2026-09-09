import time

import httpx
import pytest
import respx

from haa.connectors.base import ConnectorError
from haa.connectors.msauth import (
    LOGIN_BASE,
    AuthError,
    TokenSet,
    start_device_flow,
)

TENANT = "contoso.onmicrosoft.com"
DEVICECODE_URL = f"{LOGIN_BASE}/{TENANT}/oauth2/v2.0/devicecode"
TOKEN_URL = f"{LOGIN_BASE}/{TENANT}/oauth2/v2.0/token"


def test_token_set_json_roundtrip() -> None:
    ts = TokenSet(access_token="at", refresh_token="rt", expires_at=123.5)
    assert TokenSet.from_json(ts.to_json()) == ts


def test_token_set_from_garbage_is_friendly() -> None:
    with pytest.raises(AuthError, match="haa connect sharepoint"):
        TokenSet.from_json("plain-old-kobo-token")


def test_auth_error_is_connector_error() -> None:
    assert issubclass(AuthError, ConnectorError)


def test_is_expired_with_skew() -> None:
    assert TokenSet("a", "r", time.time() + 30).is_expired()  # inside 60s skew
    assert not TokenSet("a", "r", time.time() + 3600).is_expired()


@respx.mock
def test_start_device_flow() -> None:
    route = respx.post(DEVICECODE_URL).mock(
        return_value=httpx.Response(200, json={
            "device_code": "dc-1", "user_code": "ABC123",
            "verification_uri": "https://microsoft.com/devicelogin",
            "interval": 5, "expires_in": 900,
        })
    )
    flow = start_device_flow(TENANT, "cid-1")
    assert flow.user_code == "ABC123" and flow.device_code == "dc-1"
    assert flow.interval == 5 and flow.expires_at > time.time()
    body = route.calls[0].request.content.decode()
    assert "client_id=cid-1" in body and "Files.Read.All" in body


@respx.mock
def test_start_device_flow_consent_error_has_hint() -> None:
    respx.post(DEVICECODE_URL).mock(
        return_value=httpx.Response(400, json={
            "error": "invalid_client",
            "error_description": "AADSTS700016: Application not found in the directory.",
        })
    )
    with pytest.raises(AuthError, match="IT"):
        start_device_flow(TENANT, "cid-1")
