"""Authentication: bcrypt passwords, JWT tokens, FastAPI dependencies."""

from triconvey_agent.auth.deps import (
    AuthContext,
    require_admin,
    require_auth,
    require_client,
)
from triconvey_agent.auth.security import (
    create_access_token,
    hash_password,
    hash_token,
    verify_password,
    verify_token,
)

__all__ = [
    "AuthContext",
    "require_auth",
    "require_admin",
    "require_client",
    "hash_password",
    "verify_password",
    "hash_token",
    "create_access_token",
    "verify_token",
]
