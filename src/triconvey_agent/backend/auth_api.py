"""Authentication endpoints: login, logout, whoami, register (admin-only),
and OAuth 2.0 / OIDC callbacks for Google and Microsoft.

Mounted under `/api/auth` by the main app.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from triconvey_agent.auth.deps import AuthContext, require_admin, require_auth
from triconvey_agent.auth.oauth import (
    OAuthUserInfo,
    build_auth_url,
    exchange_code,
    is_provider_configured,
    popup_error_html,
    popup_success_html,
)
from triconvey_agent.auth.security import (
    JWT_REFRESH_TTL_DAYS,
    JWT_TTL_MINUTES,
    create_access_token,
    hash_password,
    hash_token,
    verify_password,
)
from triconvey_agent.db.repositories import (
    AuditRepo,
    ClientRepo,
    SessionRepo,
    UserRepo,
)
from triconvey_agent.db.session import get_session

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    client_slug: str = Field(..., min_length=1, description="Which firm/tenant.")
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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> LoginResponse:
    client = await ClientRepo.get_by_slug(session, body.client_slug)
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

    return LoginResponse(
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


@router.post("/logout")
async def logout(
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    await SessionRepo.revoke(session, ctx.token_hash)
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
    client_slug: str = Query(..., description="Tenant slug for this install"),
) -> dict[str, str]:
    """Returns the authorization URL the frontend should open in a popup."""
    try:
        auth_url = build_auth_url(provider, client_slug)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"auth_url": auth_url, "provider": provider}


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
        return HTMLResponse(content=popup_error_html(msg), status_code=200)

    if not code or not state:
        return HTMLResponse(content=popup_error_html("Missing code or state parameter."))

    try:
        info: OAuthUserInfo = await exchange_code(provider, code, state)
    except ValueError as exc:
        return HTMLResponse(content=popup_error_html(str(exc)))

    # Resolve the tenant
    client = await ClientRepo.get_by_slug(session, info.client_slug)
    if client is None or not client.is_active:
        return HTMLResponse(content=popup_error_html(
            f"Tenant '{info.client_slug}' not found. Contact your administrator."
        ))

    # Upsert the user (find-or-create by provider+subject, fallback to email)
    user = await UserRepo.upsert_oauth_user(
        session,
        client_id=client.id,
        oauth_provider=info.provider,
        oauth_subject=info.subject,
        email=info.email,
        name=info.name,
    )
    if not user.is_active:
        return HTMLResponse(content=popup_error_html("Account is deactivated."))

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

    user_payload = {
        "user_id": str(user.id),
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "client_id": str(client.id),
        "client_slug": client.slug,
        "client_name": client.name,
    }
    return HTMLResponse(content=popup_success_html(token, user_payload))
