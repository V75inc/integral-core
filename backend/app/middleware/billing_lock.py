"""Hosted base-plan lock. Reads stay open. The browser is not the gate.

Inactive unless ``INTEGRAL_HOSTED`` is set. Sign-in, billing, entitlement
projection, profile, and workspace create stay available so a customer can
pay. Everything else that writes is refused when the base plan is locked.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import settings

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Prefixes that must work while the workspace is billing-locked.
_ALLOW_PREFIXES = (
    "/api/auth",
    "/api/billing",
    "/api/entitlements",
    "/api/users",
    "/api/meta",
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
)


def request_requires_subscription(method: str, path: str) -> bool:
    """True when a hosted deployment must check the base plan."""
    verb = (method or "").upper()
    if verb not in _WRITE_METHODS:
        return False
    clean = (path or "").split("?", 1)[0]
    if len(clean) > 1:
        clean = clean.rstrip("/")
    for prefix in _ALLOW_PREFIXES:
        if clean == prefix or clean.startswith(prefix + "/"):
            return False
    # The billing subject has to exist before Checkout.
    if verb == "POST" and clean == "/api/workspaces":
        return False
    return True


def workspace_id_from_scope_header(header_value: str) -> str:
    """Parse ``ws:<id>``. A bare id is ignored so it cannot select a workspace."""
    raw = (header_value or "").strip()
    if raw.lower().startswith("ws:"):
        return raw[3:].strip()
    return ""


class BillingLockMiddleware(BaseHTTPMiddleware):
    """Refuse hosted writes when the base subscription is locked."""

    async def dispatch(self, request: Request, call_next) -> Response:
        if not settings.INTEGRAL_HOSTED:
            return await call_next(request)
        if not request_requires_subscription(request.method, request.url.path):
            return await call_next(request)
        workspace_id = workspace_id_from_scope_header(
            request.headers.get("x-integral-scope", "")
        )
        if not workspace_id:
            return _locked()
        from app.services.hosted_subscription import workspace_allows_hosted_writes

        allowed = await workspace_allows_hosted_writes(workspace_id)
        if not allowed:
            return _locked()
        return await call_next(request)


def _locked() -> JSONResponse:
    return JSONResponse(
        status_code=402,
        content={
            "error_code": "billing.subscription_required",
            "message": (
                "An active Integral Business subscription is required "
                "for this workspace."
            ),
            "details": {"access": "locked"},
        },
    )
