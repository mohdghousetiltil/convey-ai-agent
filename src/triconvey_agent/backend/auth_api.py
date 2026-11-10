"""Cloud-facing auth endpoints: whoami, sync tokens, and OAuth 2.0 / OIDC
callbacks for Google and Microsoft. The desktop app itself has no sign-in.

Mounted under `/api/auth` by the main app.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any  # noqa: F401 — used in response annotations
from datetime import datetime, UTC

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from triconvey_agent.auth.deps import AuthContext, require_auth
from triconvey_agent.auth.oauth import (
    OAuthUserInfo,
    build_auth_url,
    consume_oauth_result,
    exchange_code,
    is_provider_configured,
    popup_error_html,
    popup_success_html,
    store_oauth_result,
)
from triconvey_agent.auth.security import (
    JWT_TTL_MINUTES,
    create_access_token,
    hash_token,
)
from triconvey_agent.db.repositories import (
    AuditRepo,
    ClientRepo,
    SessionRepo,
    UserRepo,
)
from triconvey_agent.db.session import get_session
from triconvey_agent.backend.runtime import ensure_runtime_dirs, get_runtime_paths

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth_debug(event: str, **fields: Any) -> None:
    """Append a compact auth trace for packaged desktop debugging."""
    try:
        paths = ensure_runtime_dirs(get_runtime_paths())
        log_file = paths.local_app_dir / "auth_debug.log"
        timestamp = datetime.now(UTC).isoformat()
        parts = [f"ts={timestamp}", f"event={event}"]
        for key, value in fields.items():
            text = str(value).replace("\n", " ").replace("\r", " ")
            parts.append(f"{key}={text}")
        with log_file.open("a", encoding="utf-8") as fh:
            fh.write(" | ".join(parts) + "\n")
    except Exception:
        pass


async def _mint_session(
    session: AsyncSession,
    *,
    request: Request,
    user: Any,
    client: Any,
) -> str:
    """Create DB session row + return signed JWT string."""
    token, _ = create_access_token(
        user_id=user.id, client_id=client.id, role=user.role
    )
    await SessionRepo.create(
        session,
        user_id=user.id,
        token_hash=hash_token(token),
        ttl=timedelta(minutes=JWT_TTL_MINUTES),
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return token

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class WhoAmI(BaseModel):
    user_id: str
    email: str
    name: str
    role: str
    client_id: str
    client_slug: str
    client_name: str


class IssueTokenRequest(BaseModel):
    client_slug: str = Field(..., min_length=1)
    api_key: str = Field(..., min_length=1)


class IssueTokenResponse(BaseModel):
    token: str
    expires_in: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/whoami", response_model=WhoAmI)
async def whoami(ctx: AuthContext = Depends(require_auth)) -> WhoAmI:
    return WhoAmI(
        user_id=str(ctx.user.id),
        email=ctx.user.email,
        name=ctx.user.name,
        role=ctx.user.role,
        client_id=str(ctx.client.id),
        client_slug=ctx.client.slug,
        client_name=ctx.client.name,
    )


@router.post("/issue-token", response_model=IssueTokenResponse)
async def issue_sync_token(
    body: IssueTokenRequest,
    session: AsyncSession = Depends(get_session),
) -> IssueTokenResponse:
    """Issue a sync JWT for desktop clients posting to /api/sync/ingest.

    The per-client secret is stored in clients.policy_json.sync_api_key_hash.
    For bootstrap/legacy deployments, plaintext clients.policy_json.sync_api_key
    is also accepted and compared after hashing.
    """
    client = await ClientRepo.get_by_slug(session, body.client_slug.strip())
    if client is None or not client.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid credentials")

    policy = client.policy_json or {}
    stored_hash = str(policy.get("sync_api_key_hash") or "").strip()
    legacy_plain = str(policy.get("sync_api_key") or "").strip()
    if not stored_hash and legacy_plain:
        stored_hash = hash_token(legacy_plain)
    if not stored_hash and client.license_key:
        stored_hash = hash_token(client.license_key)

    if not stored_hash or stored_hash != hash_token(body.api_key):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid credentials")

    ttl = timedelta(hours=24)
    sync_user_id = uuid.uuid4()
    token, _ = create_access_token(
        user_id=sync_user_id,
        client_id=client.id,
        role="sync_client",
        ttl=ttl,
    )
    return IssueTokenResponse(token=token, expires_in=int(ttl.total_seconds()))


# ---------------------------------------------------------------------------
# OAuth 2.0 / OIDC — Google and Microsoft
# ---------------------------------------------------------------------------


@router.get("/oauth/providers")
async def list_providers() -> dict[str, dict[str, bool]]:
    """Tell the UI which OAuth providers are configured on this install."""
    return {
        "google": {"configured": is_provider_configured("google")},
        "microsoft": {"configured": is_provider_configured("microsoft")},
    }


@router.get("/oauth/{provider}/start")
async def oauth_start(
    provider: str,
    client_slug: str = Query(default="", description="Tenant slug — omit for single-tenant desktop installs."),
    opener_origin: str = Query(default="", description="Origin of the UI opener window."),
) -> dict[str, str]:
    """Returns the authorization URL and a poll_key for the desktop polling flow."""
    try:
        auth_url, poll_key = build_auth_url(provider, client_slug, opener_origin)
    except ValueError as exc:
        _auth_debug("oauth_start_error", provider=provider, error=str(exc))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    _auth_debug("oauth_start", provider=provider, poll_key=poll_key, client_slug=client_slug or "-", opener_origin=opener_origin or "-")
    return {"auth_url": auth_url, "provider": provider, "poll_key": poll_key}


@router.get("/oauth/{provider}/poll")
async def oauth_poll(
    provider: str,
    key: str = Query(..., description="poll_key returned by /start"),
) -> dict[str, Any]:
    """Desktop polling endpoint. Returns {ready: false} until the OAuth callback
    completes, then returns {ready: true, token, user} once (one-time consume).
    """
    result = consume_oauth_result(key)
    if result is None:
        _auth_debug("oauth_poll_pending", provider=provider, poll_key=key)
        return {"ready": False}
    _auth_debug("oauth_poll_ready", provider=provider, poll_key=key, user_email=result.user.get("email", "-"))
    return {"ready": True, "token": result.token, "user": result.user}


@router.get("/oauth/{provider}/callback", response_class=HTMLResponse)
async def oauth_callback(
    provider: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
) -> HTMLResponse:
    """Provider redirects here after user approves.

    Exchanges the code, creates a session JWT, then returns an HTML page
    that calls window.opener.postMessage(token) and closes the popup.
    """
    if error:
        msg = error_description or error
        _auth_debug("oauth_callback_error", provider=provider, error=msg)
        return HTMLResponse(content=popup_error_html(msg), status_code=200)

    if not code or not state:
        _auth_debug("oauth_callback_error", provider=provider, error="missing_code_or_state")
        return HTMLResponse(content=popup_error_html("Missing code or state parameter."))

    try:
        info: OAuthUserInfo = await exchange_code(provider, code, state)
    except ValueError as exc:
        _auth_debug("oauth_callback_error", provider=provider, error=str(exc))
        return HTMLResponse(content=popup_error_html(str(exc)))

    # ── Resolve the firm (client row) ────────────────────────────────────────
    # Priority order:
    #   1. Microsoft tenant ID (tid) → auto-provision firm on first login
    #   2. Explicit slug in state (legacy / multi-tenant)
    #   3. Single active client (desktop single-tenant mode)
    is_new_client = False
    client = None

    if info.tenant_id:
        # Microsoft: stable per-org identifier; store in license_key column
        _PERSONAL_MS_TID = "9188040d-6c67-4c5b-b112-36a304b66dad"
        if info.tenant_id == _PERSONAL_MS_TID:
            # Personal MS accounts (outlook.com etc.) fall through to slug/single-tenant
            pass
        else:
            email_domain = info.email.split("@")[-1] if "@" in info.email else info.email
            client, is_new_client = await ClientRepo.provision_from_oauth_tenant(
                session,
                tenant_id=info.tenant_id,
                email_domain=email_domain,
            )

    if client is None:
        if info.client_slug.strip():
            client = await ClientRepo.get_by_slug(session, info.client_slug.strip())
        else:
            client = await ClientRepo.get_single_active(session)

    if client is None or not client.is_active:
        msg = (
            f"Tenant '{info.client_slug}' not found. Contact your administrator."
            if info.client_slug.strip()
            else "No active firm found for this Microsoft account. Contact your administrator."
        )
        _auth_debug("oauth_callback_no_client", provider=provider, email=info.email, tenant_id=info.tenant_id or "-")
        return HTMLResponse(content=popup_error_html(msg, info.opener_origin))

    # ── Determine role: first user in a new org becomes admin ────────────────
    user_count = await UserRepo.count_for_client(session, client.id)
    default_role = "admin" if (is_new_client or user_count == 0) else "reviewer"

    # Upsert the user (find-or-create by provider+subject, fallback to email)
    user = await UserRepo.upsert_oauth_user(
        session,
        client_id=client.id,
        oauth_provider=info.provider,
        oauth_subject=info.subject,
        email=info.email,
        name=info.name,
        default_role=default_role,
    )
    if not user.is_active:
        _auth_debug("oauth_callback_user_inactive", provider=provider, email=info.email)
        return HTMLResponse(content=popup_error_html("Account is deactivated.", info.opener_origin))

    token = await _mint_session(session, request=request, user=user, client=client)
    await UserRepo.mark_login(session, user.id)
    await AuditRepo.record(
        session,
        client_id=client.id,
        user_id=user.id,
        event_type="oauth_login",
        entity_type="user",
        entity_id=user.id,
        after={"provider": provider},
        ip_address=request.client.host if request.client else None,
    )

    # For desktop OAuth, the frontend polls immediately after the browser
    # callback succeeds. Make the DB-backed session durable before exposing the
    # token via the polling endpoint; otherwise /api/auth/whoami can observe a
    # valid JWT whose session row is still uncommitted and reject it forever.
    try:
        await session.commit()
        _auth_debug("oauth_callback_commit_ok", provider=provider, email=user.email)
    except Exception as exc:
        await session.rollback()
        _auth_debug("oauth_callback_commit_error", provider=provider, email=user.email, error=str(exc))
        return HTMLResponse(content=popup_error_html("Could not finalize sign-in. Please try again.", info.opener_origin))

    user_payload = {
        "user_id": str(user.id),
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "client_id": str(client.id),
        "client_slug": client.slug,
        "client_name": client.name,
    }

    # Store result for the desktop polling flow. poll_key is empty for
    # browser-popup logins; populated for pywebview desktop logins.
    if info.poll_key:
        store_oauth_result(info.poll_key, token, user_payload)
    _auth_debug(
        "oauth_callback_success",
        provider=provider,
        poll_key=info.poll_key or "-",
        email=user.email,
        client_slug=client.slug,
        has_opener=bool(info.opener_origin),
    )

    return HTMLResponse(content=popup_success_html(token, user_payload, info.opener_origin))
