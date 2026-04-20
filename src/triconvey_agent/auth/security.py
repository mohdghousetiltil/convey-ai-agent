"""Password hashing + JWT token signing/verification.

Tokens are opaque to the UI — we store a SHA-256 hash of the token in the
`sessions` table so we can revoke (logout).
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext
from passlib.hash import bcrypt as passlib_bcrypt
from passlib.hash import bcrypt_sha256 as passlib_bcrypt_sha256

try:
    import bcrypt as _bcrypt_mod
except Exception:  # pragma: no cover - optional at import time
    _bcrypt_mod = None

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

JWT_SECRET = os.getenv("CONVEY_JWT_SECRET")
JWT_ALG = os.getenv("CONVEY_JWT_ALGORITHM", "HS256")
JWT_TTL_MINUTES = int(os.getenv("CONVEY_JWT_ACCESS_TTL_MINUTES", "43200"))   # 30 days — desktop app stays logged in
JWT_REFRESH_TTL_DAYS = int(os.getenv("CONVEY_JWT_REFRESH_TTL_DAYS", "365"))
LOG = logging.getLogger(__name__)


def _shim_bcrypt_about() -> None:
    """Passlib 1.7 expects bcrypt.__about__.__version__.

    bcrypt 4.1+ removed that module attribute, which triggers a trapped backend
    warning during password verification and can break login flows on some
    installs. Provide the tiny compatibility object that Passlib expects.
    """
    if _bcrypt_mod is None or hasattr(_bcrypt_mod, "__about__"):
        return

    version = getattr(_bcrypt_mod, "__version__", None)
    if not version:
        return

    class _About:
        __version__ = version

    _bcrypt_mod.__about__ = _About()


_shim_bcrypt_about()


def _secret() -> str:
    if not JWT_SECRET:
        raise RuntimeError(
            "CONVEY_JWT_SECRET is not configured. Set it in .env before starting the server."
        )
    return JWT_SECRET


# ---------------------------------------------------------------------------
# Passwords (bcrypt)
# ---------------------------------------------------------------------------

_pwd_ctx = CryptContext(
    schemes=["bcrypt_sha256", "bcrypt"],
    deprecated="auto",
)


def _coerce_password(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("Password must be a string.")
    return value


def hash_password(plain: str) -> str:
    plain = _coerce_password(plain)
    if not plain or len(plain) < 8:
        raise ValueError("Password must be at least 8 characters.")
    return _pwd_ctx.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    plain = _coerce_password(plain)
    hashed = (hashed or "").strip()
    if not hashed:
        return False
    try:
        return _pwd_ctx.verify(plain, hashed)
    except Exception as exc:
        LOG.warning("Primary password verification failed; trying explicit hash handlers: %s", exc)

    try:
        if hashed.startswith("$bcrypt-sha256$"):
            return passlib_bcrypt_sha256.verify(plain, hashed)
        if hashed.startswith("$2"):
            return passlib_bcrypt.verify(plain, hashed)
    except Exception as exc:
        LOG.warning("Explicit password verification failed: %s", exc)
    return False


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


def hash_token(token: str) -> str:
    """Stored in sessions.token_hash — used to look up / revoke a JWT."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_access_token(
    *,
    user_id: uuid.UUID,
    client_id: uuid.UUID,
    role: str,
    ttl: timedelta | None = None,
) -> tuple[str, datetime]:
    """Returns (jwt_string, expires_at). jti is included so logout can revoke it."""
    now = datetime.now(UTC)
    expires_at = now + (ttl or timedelta(minutes=JWT_TTL_MINUTES))
    claims: dict[str, Any] = {
        "sub": str(user_id),
        "cid": str(client_id),
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": secrets.token_urlsafe(16),
    }
    token = jwt.encode(claims, _secret(), algorithm=JWT_ALG)
    return token, expires_at


def verify_token(token: str) -> dict[str, Any]:
    """Decode and validate. Raises ValueError on any failure."""
    try:
        claims = jwt.decode(token, _secret(), algorithms=[JWT_ALG])
    except JWTError as exc:
        raise ValueError(f"Invalid token: {exc}") from exc
    if "sub" not in claims or "cid" not in claims:
        raise ValueError("Token missing required claims.")
    return claims
