"""Authentication endpoints: login, logout, whoami, register (admin-only),
and OAuth 2.0 / OIDC callbacks for Google and Microsoft.

Mounted under `/api/auth` by the main app.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
import httpx
from typing import Any  # noqa: F401 — used in response annotations
from pathlib import Path
from datetime import datetime, UTC

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from triconvey_agent.app_meta import DEFAULT_CLOUD_BACKEND_URL
from triconvey_agent.auth.deps import AuthContext, require_admin, require_auth
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
    JWT_REFRESH_TTL_DAYS,
    JWT_TTL_MINUTES,
    create_access_token,
    hash_password,
    hash_token,
    verify_token,
    verify_password,
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


def _auth_state_file() -> Path:
    paths = ensure_runtime_dirs(get_runtime_paths())
    return paths.local_app_dir / "auth_state.json"


def _persist_auth_state(*, token: str, user: "WhoAmI", expires_in_seconds: int) -> None:
    expires_at = int(datetime.now(UTC).timestamp()) + max(0, int(expires_in_seconds))
    payload = {
        "access_token": token,
        "expires_at": expires_at,
        "user": user.model_dump(mode="json"),
    }
    _auth_state_file().write_text(
        json.dumps(payload, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


def _clear_auth_state() -> None:
    try:
        _auth_state_file().unlink(missing_ok=True)
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


class LoginRequest(BaseModel):
    client_slug: str = Field(default="", description="Firm slug — omit for single-tenant desktop installs.")
    email: EmailStr
    password: str = Field(..., min_length=8)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_seconds: int
    user: "WhoAmI"


class WhoAmI(BaseModel):
    user_id: str
    email: str
    name: str
    role: str
    client_id: str
    client_slug: str
    client_name: str


class RegisterRequest(BaseModel):
    email: EmailStr
    name: str = Field(..., min_length=1)
    password: str = Field(..., min_length=8)
    role: str = Field(default="reviewer", pattern=r"^(admin|reviewer|viewer)$")


class SelfRegisterRequest(BaseModel):
    """Public self-registration.

    Firm resolution priority:
      1. If `activation_key` is provided → use it (explicit license key)
      2. Otherwise → single active client on this install (desktop mode)
    """
    email: EmailStr
    name: str = Field(..., min_length=1)
    company_name: str = Field(..., min_length=1, description="Firm or company name used to create or resolve the workspace.")
    password: str = Field(..., min_length=8)
    activation_key: str = Field(default="", description="Firm's license/activation key. Omit for single-tenant desktop installs.")


class IssueTokenRequest(BaseModel):
    client_slug: str = Field(..., min_length=1)
    api_key: str = Field(..., min_length=1)


class IssueTokenResponse(BaseModel):
    token: str
    expires_in: int


class RemoteLoginRequest(BaseModel):
    remote_access_token: str = Field(..., min_length=1)


class PersistedAuthState(BaseModel):
    access_token: str
    expires_at: int
    user: "WhoAmI"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> LoginResponse:
    # Resolve client: by slug first, then by single active client (desktop/single-tenant mode)
    if body.client_slug.strip():
        client = await ClientRepo.get_by_slug(session, body.client_slug.strip())
    else:
        client = await ClientRepo.get_single_active(session)
    if client is None or not client.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials.")

    user = await UserRepo.get_by_email(session, client_id=client.id, email=body.email)
    if user is None or not user.is_active or not user.password_hash:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials.")

    if not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials.")

    token, expires_at = create_access_token(
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
    await UserRepo.mark_login(session, user.id)
    await AuditRepo.record(
        session,
        client_id=client.id,
        user_id=user.id,
        event_type="user_login",
        entity_type="user",
        entity_id=user.id,
        ip_address=request.client.host if request.client else None,
    )

    response = LoginResponse(
        access_token=token,
        expires_in_seconds=JWT_TTL_MINUTES * 60,
        user=WhoAmI(
            user_id=str(user.id),
            email=user.email,
            name=user.name,
            role=user.role,
            client_id=str(client.id),
            client_slug=client.slug,
            client_name=client.name,
        ),
    )
    _persist_auth_state(
        token=response.access_token,
        user=response.user,
        expires_in_seconds=response.expires_in_seconds,
    )
    return response


@router.get("/state", response_model=PersistedAuthState | None)
async def get_persisted_auth_state(
    session: AsyncSession = Depends(get_session),
) -> PersistedAuthState | None:
    path = _auth_state_file()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        token = str(payload.get("access_token") or "").strip()
        expires_at = int(payload.get("expires_at") or 0)
        if not token or expires_at <= int(datetime.now(UTC).timestamp()):
            _clear_auth_state()
            return None
        claims = verify_token(token)
        row = await SessionRepo.get_by_hash(session, hash_token(token))
        if row is None:
            _clear_auth_state()
            return None
        client = await ClientRepo.get_by_id(session, uuid.UUID(claims["cid"]))
        user = await UserRepo.get_by_id(session, uuid.UUID(claims["sub"]))
        if user is None or client is None or not user.is_active or not client.is_active:
            _clear_auth_state()
            return None
        return PersistedAuthState(
            access_token=token,
            expires_at=expires_at,
            user=WhoAmI(
                user_id=str(user.id),
                email=user.email,
                name=user.name,
                role=user.role,
                client_id=str(client.id),
                client_slug=client.slug,
                client_name=client.name,
            ),
        )
    except Exception:
        _clear_auth_state()
        return None


@router.post("/state", status_code=status.HTTP_204_NO_CONTENT)
async def save_persisted_auth_state(body: PersistedAuthState) -> None:
    _persist_auth_state(
        token=body.access_token,
        user=body.user,
        expires_in_seconds=max(0, body.expires_at - int(datetime.now(UTC).timestamp())),
    )


@router.delete("/state", status_code=status.HTTP_204_NO_CONTENT)
async def delete_persisted_auth_state() -> None:
    _clear_auth_state()


@router.post("/logout")
async def logout(
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    await SessionRepo.revoke(session, ctx.token_hash)
    _clear_auth_state()
    await AuditRepo.record(
        session,
        client_id=ctx.client.id,
        user_id=ctx.user.id,
        event_type="user_logout",
        entity_type="user",
        entity_id=ctx.user.id,
    )
    return {"ok": True}


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


@router.post("/users", response_model=WhoAmI, status_code=status.HTTP_201_CREATED)
async def register_user(
    body: RegisterRequest,
    ctx: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> WhoAmI:
    """Admin-only: create a new user within the caller's client."""
    existing = await UserRepo.get_by_email(session, client_id=ctx.client.id, email=body.email)
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already exists.")

    user = await UserRepo.create(
        session,
        client_id=ctx.client.id,
        email=body.email,
        name=body.name,
        password_hash=hash_password(body.password),
        role=body.role,
    )
    await AuditRepo.record(
        session,
        client_id=ctx.client.id,
        user_id=ctx.user.id,
        event_type="user_created",
        entity_type="user",
        entity_id=user.id,
        after={"email": user.email, "role": user.role, "name": user.name},
    )
    return WhoAmI(
        user_id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
        client_id=str(ctx.client.id),
        client_slug=ctx.client.slug,
        client_name=ctx.client.name,
    )


@router.post("/register", response_model=LoginResponse, status_code=status.HTTP_201_CREATED)
async def self_register(
    body: SelfRegisterRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> LoginResponse:
    """Public self-registration using an activation key or firm/company name.

    Anyone with a valid activation key can create their own account.
    Accounts are created with the 'reviewer' role; admins can promote later.
    """
    # Resolve client: by activation key, single active client, or company name.
    if body.activation_key.strip():
        client = await ClientRepo.get_by_license(session, body.activation_key.strip())
        if client is None or not client.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid activation key. Contact your administrator.",
            )
    else:
        client = await ClientRepo.get_single_active(session)
        if client is None:
            try:
                client, _ = await ClientRepo.provision_from_company_name(
                    session,
                    company_name=body.company_name,
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(exc),
                ) from exc

    # Check for duplicate email
    existing = await UserRepo.get_by_email(session, client_id=client.id, email=body.email)
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered.")

    user_count = await UserRepo.count_for_client(session, client.id)
    role = "admin" if user_count == 0 else "reviewer"
    user = await UserRepo.create(
        session,
        client_id=client.id,
        email=body.email,
        name=body.name,
        password_hash=hash_password(body.password),
        role=role,
    )
    await AuditRepo.record(
        session,
        client_id=client.id,
        user_id=user.id,
        event_type="user_self_registered",
        entity_type="user",
        entity_id=user.id,
        after={"email": user.email, "name": user.name, "company_name": body.company_name},
    )

    token, expires_at = create_access_token(
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
    response = LoginResponse(
        access_token=token,
        expires_in_seconds=JWT_TTL_MINUTES * 60,
        user=WhoAmI(
            user_id=str(user.id),
            email=user.email,
            name=user.name,
            role=user.role,
            client_id=str(client.id),
            client_slug=client.slug,
            client_name=client.name,
        ),
    )
    _persist_auth_state(
        token=response.access_token,
        user=response.user,
        expires_in_seconds=response.expires_in_seconds,
    )
    return response


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
    sync_user_id = uuid4()
    token, _ = create_access_token(
        user_id=sync_user_id,
        client_id=client.id,
        role="sync_client",
        ttl=ttl,
    )
    return IssueTokenResponse(token=token, expires_in=int(ttl.total_seconds()))


@router.post("/remote-login", response_model=LoginResponse)
async def remote_login(
    body: RemoteLoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> LoginResponse:
    """Validate a Railway auth token, then create a local desktop session."""
    _auth_debug("remote_login_start")
    remote_whoami_url = f"{DEFAULT_CLOUD_BACKEND_URL}/api/auth/whoami"
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.get(
                remote_whoami_url,
                headers={"Authorization": f"Bearer {body.remote_access_token}"},
            )
            response.raise_for_status()
            remote_user = response.json()
    except Exception as exc:
        _auth_debug("remote_login_remote_whoami_error", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Remote login could not be validated: {exc}",
        ) from exc

    client_slug = str(remote_user.get("client_slug") or "").strip()
    client_name = str(remote_user.get("client_name") or client_slug or "Convey Client").strip()
    email = str(remote_user.get("email") or "").strip().lower()
    name = str(remote_user.get("name") or email or "User").strip()
    role = str(remote_user.get("role") or "reviewer").strip()
    remote_user_id = str(remote_user.get("user_id") or email).strip()

    if not client_slug or not email:
        _auth_debug("remote_login_incomplete_remote_user", remote_user=str(remote_user))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Remote login response was incomplete.")

    try:
        local_client = await ClientRepo.get_by_slug(session, client_slug)
        if local_client is None:
            local_client = await ClientRepo.ensure_local(
                session,
                slug=client_slug,
                name=client_name,
            )

        local_user = await UserRepo.upsert_oauth_user(
            session,
            client_id=local_client.id,
            oauth_provider="railway",
            oauth_subject=remote_user_id,
            email=email,
            name=name,
            default_role=role if role in {"admin", "reviewer", "viewer"} else "reviewer",
        )
        if role in {"admin", "reviewer", "viewer"} and local_user.role != role:
            local_user.role = role
            local_user.updated_at = datetime.now(UTC)
            await session.flush()

        token = await _mint_session(session, request=request, user=local_user, client=local_client)
        await UserRepo.mark_login(session, local_user.id)
        await session.commit()
        _auth_debug(
            "remote_login_commit_ok",
            email=local_user.email,
            client_slug=local_client.slug,
        )
    except Exception as exc:
        await session.rollback()
        _auth_debug(
            "remote_login_local_error",
            email=email,
            client_slug=client_slug,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not finalize local sign-in.",
        ) from exc

    response = LoginResponse(
        access_token=token,
        expires_in_seconds=JWT_TTL_MINUTES * 60,
        user=WhoAmI(
            user_id=str(local_user.id),
            email=local_user.email,
            name=local_user.name,
            role=local_user.role,
            client_id=str(local_client.id),
            client_slug=local_client.slug,
            client_name=local_client.name,
        ),
    )
    _persist_auth_state(
        token=response.access_token,
        user=response.user,
        expires_in_seconds=response.expires_in_seconds,
    )
    return response


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
