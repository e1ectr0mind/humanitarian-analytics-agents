"""Microsoft Entra ID device-code flow and token refresh (raw httpx, no MSAL)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

import httpx

from haa.connectors.base import ConnectorError

LOGIN_BASE = "https://login.microsoftonline.com"
SCOPES = (
    "https://graph.microsoft.com/Files.Read.All "
    "https://graph.microsoft.com/Sites.Read.All offline_access"
)
# Microsoft Graph Command Line Tools — a public client pre-consented in many tenants.
DEFAULT_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"

_CONSENT_CODES = ("AADSTS65001", "AADSTS7000218", "AADSTS700016", "AADSTS90094", "AADSTS500113")


class AuthError(ConnectorError):
    """Friendly, user-facing Microsoft sign-in failure."""


@dataclass(frozen=True)
class DeviceFlow:
    tenant: str
    client_id: str
    device_code: str
    user_code: str
    verification_uri: str
    interval: int
    expires_at: float


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    refresh_token: str
    expires_at: float

    def to_json(self) -> str:
        return json.dumps({
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
        })

    @classmethod
    def from_json(cls, text: str) -> TokenSet:
        try:
            data = json.loads(text)
            return cls(
                access_token=str(data["access_token"]),
                refresh_token=str(data["refresh_token"]),
                expires_at=float(data["expires_at"]),
            )
        except (ValueError, TypeError, KeyError) as exc:
            raise AuthError(
                "The stored credential is not a Microsoft token set. "
                "Re-run: haa connect sharepoint"
            ) from exc

    def is_expired(self, skew: float = 60.0) -> bool:
        return time.time() >= self.expires_at - skew


def _token_url(tenant: str) -> str:
    return f"{LOGIN_BASE}/{tenant}/oauth2/v2.0/token"


def _post(url: str, data: dict, timeout: float) -> dict:
    try:
        resp = httpx.post(url, data=data, timeout=timeout)
    except Exception as exc:
        raise AuthError(
            f"Could not reach Microsoft sign-in: {exc}. Check your network and try again."
        ) from exc
    try:
        return resp.json()
    except Exception as exc:
        msg = (
            f"Microsoft sign-in returned a non-JSON response (HTTP {resp.status_code}). "
            "Try again; if it persists, contact IT."
        )
        raise AuthError(msg) from exc


def _friendly(data: dict) -> str:
    desc = str(data.get("error_description") or data.get("error") or "unknown error")
    message = f"Microsoft sign-in failed: {desc.splitlines()[0]}"
    if any(code in desc for code in _CONSENT_CODES):
        message += (
            " — the tenant does not allow this application. Agree a client_id "
            "with IT/HQ or try the pre-consented default one."
        )
    else:
        message += " Try again; if it persists, share this message with IT/HQ."
    return message


def _token_set(data: dict, *, fallback_refresh: str) -> TokenSet:
    return TokenSet(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token") or fallback_refresh,
        expires_at=time.time() + float(data.get("expires_in", 3600)),
    )


def start_device_flow(tenant: str, client_id: str, *, timeout: float = 30.0) -> DeviceFlow:
    data = _post(
        f"{LOGIN_BASE}/{tenant}/oauth2/v2.0/devicecode",
        {"client_id": client_id, "scope": SCOPES},
        timeout,
    )
    if "device_code" not in data:
        raise AuthError(_friendly(data))
    return DeviceFlow(
        tenant=tenant,
        client_id=client_id,
        device_code=data["device_code"],
        user_code=data["user_code"],
        verification_uri=data.get("verification_uri", "https://microsoft.com/devicelogin"),
        interval=int(data.get("interval", 5)),
        expires_at=time.time() + float(data.get("expires_in", 900)),
    )
