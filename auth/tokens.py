"""Firebase token handling for the desktop agent.

- Stores the Firebase refresh token securely using Windows Credential Manager (via keyring)
- Refreshes Firebase ID tokens on demand via Secure Token API

This is meant to support future backend API calls after the auth UI closes.
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

import keyring
import requests
from dotenv import load_dotenv

# Load .env
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_SERVICE = "zenno-desktop-agent"
_REFRESH_KEY = "firebase_refresh_token"

_API_KEY = os.getenv("FIREBASE_API_KEY", "")
_SECURETOKEN_URL = "https://securetoken.googleapis.com/v1/token"

# in-memory cache
_cached_id_token: str | None = None
_cached_id_token_exp: int | None = None


class TokenError(Exception):
    pass


def set_initial_tokens(*, id_token: str, refresh_token: str | None) -> None:
    """Called after sign-in to seed cache and persist refresh token."""
    global _cached_id_token, _cached_id_token_exp

    if id_token:
        _cached_id_token = id_token
        _cached_id_token_exp = _jwt_exp(id_token)

    if refresh_token:
        keyring.set_password(_SERVICE, _REFRESH_KEY, refresh_token)


def clear_tokens() -> None:
    """Clear cached and stored tokens (logout)."""
    global _cached_id_token, _cached_id_token_exp
    _cached_id_token = None
    _cached_id_token_exp = None
    try:
        keyring.delete_password(_SERVICE, _REFRESH_KEY)
    except keyring.errors.PasswordDeleteError:
        pass


def get_valid_id_token(*, min_validity_sec: int = 120) -> str:
    """Return a valid Firebase ID token, refreshing if needed.

    Args:
        min_validity_sec: ensures the returned token is valid for at least this many seconds.

    Raises:
        TokenError if refresh is not possible.
    """
    global _cached_id_token, _cached_id_token_exp

    now = int(time.time())
    if _cached_id_token and _cached_id_token_exp and _cached_id_token_exp > now + min_validity_sec:
        return _cached_id_token

    refresh_token = keyring.get_password(_SERVICE, _REFRESH_KEY)
    if not refresh_token:
        raise TokenError("No refresh token available. Please sign in again.")

    new_id_token, new_refresh_token = _refresh_with_securetoken(refresh_token)
    _cached_id_token = new_id_token
    _cached_id_token_exp = _jwt_exp(new_id_token)

    # Firebase may rotate refresh tokens
    if new_refresh_token and new_refresh_token != refresh_token:
        keyring.set_password(_SERVICE, _REFRESH_KEY, new_refresh_token)

    return new_id_token


def _refresh_with_securetoken(refresh_token: str) -> tuple[str, str | None]:
    if not _API_KEY:
        raise TokenError("FIREBASE_API_KEY missing in .env")

    url = f"{_SECURETOKEN_URL}?key={_API_KEY}"
    resp = requests.post(
        url,
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=15,
    )
    if resp.status_code != 200:
        raise TokenError(f"Token refresh failed ({resp.status_code}): {resp.text[:200]}")

    body = resp.json()
    id_token = body.get("id_token")
    new_refresh_token = body.get("refresh_token")

    if not id_token:
        raise TokenError("Token refresh response missing id_token")

    return id_token, new_refresh_token


def _jwt_exp(jwt_token: str) -> int | None:
    """Extract exp from JWT without verifying signature."""
    try:
        parts = jwt_token.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1]
        # Base64url padding
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = base64.urlsafe_b64decode(payload_b64.encode("utf-8"))
        data = json.loads(payload.decode("utf-8"))
        exp = data.get("exp")
        return int(exp) if exp is not None else None
    except Exception:
        return None
