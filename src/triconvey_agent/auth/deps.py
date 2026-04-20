"""FastAPI dependencies for auth.

Usage in routes:

    from triconvey_agent.auth import require_auth, AuthContext

    @router.get("/me")
    async def me(ctx: AuthContext = Depends(require_auth)):
        return {"user_id": str(ctx.user.id), "client_id": str(ctx.client.id)}

Every API endpoint that touches tenant data MUST depend on `require_auth`
(or `require_admin`) so that `client_id` is enforced server-side.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from triconvey_agent.auth.security import hash_token, verify_token
from triconvey_agent.backend.runtime import ensure_runtime_dirs, get_runtime_paths
from triconvey_agent.db.models import Client, User
from triconvey_agent.db.repositories import ClientRepo, SessionRepo, UserRepo
from triconvey_agent.db.session import get_session


@dataclass(slots=True, frozen=True)
class AuthContext:
    user: User
    client: Client
    token: str
    token_hash: str


def _auth_debug(event: str, **fields: Any) -> None:
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


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header must be 'Bearer <token>'.",
        )
    return parts[1]


async def require_auth(
    request: Request,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> AuthContext:
    token = _extract_bearer(authorization)
    token_hash = hash_token(token)

    # 1. Signature + expiry
    try:
        claims = verify_token(token)
    except ValueError as exc:
        _auth_debug("require_auth_invalid_token", path=request.url.path, detail=str(exc))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    # 2. Revocation check (session row must still exist)
    row = await SessionRepo.get_by_hash(session, token_hash)
    if row is None:
        _auth_debug("require_auth_missing_session", path=request.url.path)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has been revoked or expired.",
        )

    # 3. Load user + tenant
    try:
        user_id = uuid.UUID(claims["sub"])
        client_id = uuid.UUID(claims["cid"])
    except (KeyError, ValueError) as exc:
        _auth_debug("require_auth_bad_claims", path=request.url.path)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bad token claims.") from exc

    user = await UserRepo.get_by_id(session, user_id)
    if user is None or not user.is_active:
        _auth_debug("require_auth_user_missing", path=request.url.path, user_id=user_id)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive.")
    if user.client_id != client_id:
        _auth_debug("require_auth_client_mismatch", path=request.url.path, user_id=user_id, token_client_id=client_id, actual_client_id=user.client_id)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Client mismatch on token.")

    client = await ClientRepo.get_by_id(session, client_id)
    if client is None or not client.is_active:
        _auth_debug("require_auth_inactive_client", path=request.url.path, client_id=client_id)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Client is inactive.")

    # Attach to request state for middleware/logging
    request.state.auth_user_id = str(user.id)
    request.state.auth_client_id = str(client.id)
    if request.url.path in {"/api/auth/whoami", "/api/settings"}:
        _auth_debug("require_auth_ok", path=request.url.path, email=user.email, client_slug=client.slug)

    return AuthContext(user=user, client=client, token=token, token_hash=token_hash)


async def require_admin(ctx: AuthContext = Depends(require_auth)) -> AuthContext:
    if ctx.user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required.")
    return ctx


async def require_client(ctx: AuthContext = Depends(require_auth)) -> Client:
    """Shortcut when a route only cares about the tenant, not the user."""
    return ctx.client
