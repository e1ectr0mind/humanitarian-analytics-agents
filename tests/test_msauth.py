import time

import httpx
import pytest
import respx

from haa.connectors.base import ConnectorError
from haa.connectors.msauth import (
    LOGIN_BASE,
    AuthError,
    DeviceFlow,
    TokenSet,
    poll_for_token,
    refresh,
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


@respx.mock
def test_non_consent_error_still_names_action() -> None:
    respx.post(DEVICECODE_URL).mock(
        return_value=httpx.Response(400, json={
            "error": "invalid_request",
            "error_description": "AADSTS90002: Tenant not found.",
        })
    )
    with pytest.raises(AuthError, match="IT/HQ"):
        start_device_flow(TENANT, "cid-1")


def _flow(expires_in: float = 60.0) -> DeviceFlow:
    return DeviceFlow(
        tenant=TENANT, client_id="cid-1", device_code="dc-1", user_code="ABC123",
        verification_uri="https://microsoft.com/devicelogin", interval=5,
        expires_at=time.time() + expires_in,
    )


@respx.mock
def test_poll_pending_then_slow_down_then_success() -> None:
    respx.post(TOKEN_URL).mock(side_effect=[
        httpx.Response(400, json={"error": "authorization_pending"}),
        httpx.Response(400, json={"error": "slow_down"}),
        httpx.Response(200, json={
            "access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600,
        }),
    ])
    naps: list[int] = []
    tokens = poll_for_token(_flow(), sleep=naps.append)
    assert tokens.access_token == "at-1" and tokens.refresh_token == "rt-1"
    assert naps == [5, 10]  # interval, then interval + 5 on slow_down


@respx.mock
def test_poll_declined_is_friendly() -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(400, json={
            "error": "authorization_declined",
            "error_description": "AADSTS65004: User declined to consent.",
        })
    )
    with pytest.raises(AuthError, match="declined"):
        poll_for_token(_flow(), sleep=lambda s: None)


def test_poll_code_expired_before_use() -> None:
    with pytest.raises(AuthError, match="expired"):
        poll_for_token(_flow(expires_in=-1.0), sleep=lambda s: None)


@respx.mock
def test_refresh_rotates_refresh_token() -> None:
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={
            "access_token": "at-2", "refresh_token": "rt-2", "expires_in": 3600,
        })
    )
    tokens = refresh(TENANT, "cid-1", "rt-1")
    assert tokens.access_token == "at-2" and tokens.refresh_token == "rt-2"
    body = route.calls[0].request.content.decode()
    assert "grant_type=refresh_token" in body and "rt-1" in body


@respx.mock
def test_refresh_keeps_old_token_when_none_returned() -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "at-2", "expires_in": 3600})
    )
    assert refresh(TENANT, "cid-1", "rt-1").refresh_token == "rt-1"


@respx.mock
def test_refresh_invalid_grant_is_friendly() -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(400, json={
            "error": "invalid_grant", "error_description": "AADSTS70000: expired.",
        })
    )
    with pytest.raises(AuthError, match="haa connect sharepoint"):
        refresh(TENANT, "cid-1", "rt-old")
