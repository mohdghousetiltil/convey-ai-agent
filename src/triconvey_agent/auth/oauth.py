"""OAuth 2.0 / OIDC provider support — Google and Microsoft.

No extra dependencies: uses httpx (already a dep) for token exchange
and python-jose (already a dep) for ID token verification.

Flow (PKCE, popup-based):
  1. Frontend calls POST /api/auth/oauth/{provider}/start  → gets {auth_url}
  2. Frontend opens auth_url in popup window
  3. Provider redirects to GET /api/auth/oauth/{provider}/callback?code=…&state=…
  4. Backend exchanges code, verifies ID token, upserts user, mints JWT
  5. Backend returns HTML page that calls window.opener.postMessage(token) + closes
  6. Frontend message listener captures token and stores it

Required env vars (per provider):
  GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
  MICROSOFT_CLIENT_ID, MICROSOFT_CLIENT_SECRET
  MICROSOFT_TENANT_ID  (use 'common' for multi-tenant / personal accounts)
  OAUTH_REDIRECT_BASE  (default: http://127.0.0.1:8765)
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from jose import jwt as jose_jwt
from jose.exceptions import JWTError

LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


REDIRECT_BASE = _env("OAUTH_REDIRECT_BASE", "http://127.0.0.1:8765")

_PROVIDERS: dict[str, dict[str, str]] = {
    "google": {
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "jwks_url": "https://www.googleapis.com/oauth2/v3/certs",
        "issuer": "https://accounts.google.com",
        "client_id_env": "GOOGLE_CLIENT_ID",
        "client_secret_env": "GOOGLE_CLIENT_SECRET",
        "scope": "openid email profile",
    },
    "microsoft": {
        "auth_url": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        "jwks_url": "https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys",
        "issuer": "https://login.microsoftonline.com/{tenant}/v2.0",
        "client_id_env": "MICROSOFT_CLIENT_ID",
        "client_secret_env": "MICROSOFT_CLIENT_SECRET",
        "scope": "openid email profile",
    },
}


def _resolve(template: str, tenant: str) -> str:
    return template.replace("{tenant}", tenant)


def _provider_cfg(provider: str) -> dict[str, str]:
    if provider not in _PROVIDERS:
        raise ValueError(f"Unknown OAuth provider: {provider!r}. Use 'google' or 'microsoft'.")
    cfg = dict(_PROVIDERS[provider])
    tenant = _env("MICROSOFT_TENANT_ID", "common")
    return {k: _resolve(v, tenant) for k, v in cfg.items()}


def is_provider_configured(provider: str) -> bool:
    try:
        cfg = _provider_cfg(provider)
    except ValueError:
        return False
    return bool(_env(cfg["client_id_env"]) and _env(cfg["client_secret_env"]))


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------


def _code_verifier() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()


def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


# ---------------------------------------------------------------------------
# In-process state store (short-lived; 10 min TTL)
# Sufficient for local desktop app where only one user is logging in at a time.
# ---------------------------------------------------------------------------

_STATE_TTL = 600  # 10 minutes

@dataclass
class _OAuthState:
    provider: str
    client_slug: str
    verifier: str
    created: float


_pending: dict[str, _OAuthState] = {}


def _create_state(provider: str, client_slug: str) -> tuple[str, str]:
    """Returns (state_token, code_verifier)."""
    _evict_old()
    state = secrets.token_urlsafe(24)
    verifier = _code_verifier()
    _pending[state] = _OAuthState(
        provider=provider,
        client_slug=client_slug,
        verifier=verifier,
        created=time.monotonic(),
    )
    return state, verifier


def _consume_state(state: str) -> _OAuthState:
    _evict_old()
    entry = _pending.pop(state, None)
    if entry is None:
        raise ValueError("OAuth state not found or expired. Please try logging in again.")
    return entry


def _evict_old() -> None:
    cutoff = time.monotonic() - _STATE_TTL
    expired = [k for k, v in _pending.items() if v.created < cutoff]
    for k in expired:
        _pending.pop(k, None)


# ---------------------------------------------------------------------------
# JWKs cache
# ---------------------------------------------------------------------------

_jwks_cache: dict[str, tuple[list[dict[str, Any]], float]] = {}
_JWKS_TTL = 3600  # 1 hour


async def _fetch_jwks(url: str) -> list[dict[str, Any]]:
    cached = _jwks_cache.get(url)
    if cached and time.monotonic() - cached[1] < _JWKS_TTL:
        return cached[0]
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        keys = resp.json().get("keys", [])
    _jwks_cache[url] = (keys, time.monotonic())
    return keys


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OAuthUserInfo:
    provider: str
    subject: str        # provider's unique user id (sub claim)
    email: str
    name: str
    client_slug: str


def build_auth_url(provider: str, client_slug: str) -> str:
    """Generate the URL the frontend should open in a popup."""
    cfg = _provider_cfg(provider)
    client_id = _env(cfg["client_id_env"])
    if not client_id:
        raise ValueError(
            f"OAuth provider '{provider}' is not configured. "
            f"Set {cfg['client_id_env']} in .env."
        )

    state, verifier = _create_state(provider, client_slug)
    challenge = _code_challenge(verifier)
    redirect_uri = f"{REDIRECT_BASE}/api/auth/oauth/{provider}/callback"

    params: dict[str, str] = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": cfg["scope"],
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    # Microsoft also needs nonce for ID token
    if provider == "microsoft":
        params["nonce"] = secrets.token_urlsafe(16)

    return cfg["auth_url"] + "?" + urlencode(params)


async def exchange_code(provider: str, code: str, state: str) -> OAuthUserInfo:
    """Exchange auth code → ID token → OAuthUserInfo.

    Also consumes and validates the PKCE state.
    """
    entry = _consume_state(state)
    if entry.provider != provider:
        raise ValueError("State/provider mismatch.")

    cfg = _provider_cfg(provider)
    client_id = _env(cfg["client_id_env"])
    client_secret = _env(cfg["client_secret_env"])
    redirect_uri = f"{REDIRECT_BASE}/api/auth/oauth/{provider}/callback"

    # 1. Exchange code for tokens
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            cfg["token_url"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
                "code_verifier": entry.verifier,
            },
        )
        if resp.status_code != 200:
            raise ValueError(f"Token exchange failed: {resp.text[:400]}")
        token_resp = resp.json()

    id_token = token_resp.get("id_token")
    if not id_token:
        raise ValueError("Provider did not return an id_token.")

    # 2. Verify ID token signature using provider's JWKs
    keys = await _fetch_jwks(cfg["jwks_url"])
    claims = _verify_id_token(id_token, keys, client_id, cfg["issuer"])

    email: str = claims.get("email", "")
    if not email:
        raise ValueError("ID token missing email claim. Ensure 'email' scope is approved.")

    name: str = (
        claims.get("name")
        or f"{claims.get('given_name', '')} {claims.get('family_name', '')}".strip()
        or email.split("@")[0]
    )

    return OAuthUserInfo(
        provider=provider,
        subject=claims["sub"],
        email=email.lower(),
        name=name,
        client_slug=entry.client_slug,
    )


def _verify_id_token(
    id_token: str,
    keys: list[dict[str, Any]],
    audience: str,
    issuer: str,
) -> dict[str, Any]:
    """Verify JWT signature + standard claims using python-jose."""
    header = jose_jwt.get_unverified_header(id_token)
    kid = header.get("kid")

    # Pick the matching key
    key = next((k for k in keys if k.get("kid") == kid), None)
    if key is None and keys:
        key = keys[0]  # fallback for single-key providers
    if key is None:
        raise JWTError("No matching JWK found.")

    try:
        claims = jose_jwt.decode(
            id_token,
            key,
            algorithms=[header.get("alg", "RS256")],
            audience=audience,
            issuer=issuer,
            options={"verify_at_hash": False},
        )
    except JWTError as exc:
        raise ValueError(f"ID token verification failed: {exc}") from exc

    return claims


# ---------------------------------------------------------------------------
# HTML response for popup close
# ---------------------------------------------------------------------------

def popup_success_html(token: str, user: dict[str, Any]) -> str:
    """Returns an HTML page that posts the token to the opener and closes."""
    payload_json = json.dumps({"type": "oauth_success", "token": token, "user": user})
    return f"""<!doctype html>
<html>
<head><title>Login successful</title></head>
<body>
<p style="font-family:sans-serif;color:#334155;padding:2rem">
  Login successful — closing this window…
</p>
<script>
  try {{
    var payload = {payload_json};
    if (window.opener) {{
      window.opener.postMessage(payload, window.location.origin);
    }}
  }} catch(e) {{}}
  setTimeout(function(){{ window.close(); }}, 800);
</script>
</body>
</html>"""


def popup_error_html(message: str) -> str:
    payload_json = json.dumps({"type": "oauth_error", "error": message})
    return f"""<!doctype html>
<html>
<head><title>Login failed</title></head>
<body>
<p style="font-family:sans-serif;color:#dc2626;padding:2rem">
  Login failed: {message}<br>You may close this window.
</p>
<script>
  try {{
    if (window.opener) {{
      window.opener.postMessage({payload_json}, window.location.origin);
    }}
  }} catch(e) {{}}
</script>
</body>
</html>"""
