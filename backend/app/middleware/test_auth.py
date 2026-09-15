"""Test-only middleware to pre-set request.state.user from JWT when using ASGITransport.

In production, jvspatial's AuthenticationMiddleware sets request.state.user after
validating the JWT. When running in-process tests with httpx ASGITransport, that
validation can fail. This middleware runs first, decodes the JWT directly, and
pre-sets request.state.user so the auth middleware accepts it and skips validation.

Only active when TESTING=1 or PYTEST_CURRENT_TEST. See backend/README.md#authentication.
"""

import os
from datetime import datetime, timezone
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


async def _test_auth_bypass(request: Request, call_next: Callable) -> Response:
    """When TESTING=1, decode JWT and set request.state.user for auth middleware."""
    if not (os.getenv("TESTING") or os.getenv("PYTEST_CURRENT_TEST")):
        return await call_next(request)

    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return await call_next(request)

    token = auth_header[7:].strip()
    if not token:
        return await call_next(request)

    try:
        import jwt

        from app.config import settings

        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            options={"verify_exp": True},
        )
        user_id = payload.get("user_id") or payload.get("sub")
        if not user_id:
            return await call_next(request)

        # Create minimal UserResponse from payload - no DB lookup needed
        from jvspatial.api.auth.models import UserResponse

        created_at = payload.get("created_at")
        if isinstance(created_at, (int, float)):
            created_at = datetime.fromtimestamp(created_at, tz=timezone.utc)
        elif not created_at:
            created_at = datetime.now(timezone.utc)

        request.state.user = UserResponse(
            id=user_id,
            email=payload.get("email", ""),
            name=payload.get("name", ""),
            created_at=created_at,
            is_active=True,
            roles=payload.get("roles") or ["user"],
            permissions=list(payload.get("permissions") or []),
        )
    except Exception:
        pass

    return await call_next(request)


class TestAuthBypassMiddleware(BaseHTTPMiddleware):
    """Middleware that pre-sets request.state.user from JWT when TESTING=1."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        return await _test_auth_bypass(request, call_next)
