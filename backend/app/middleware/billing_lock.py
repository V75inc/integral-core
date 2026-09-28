"""Hosted billing lock. Reads stay open. The browser is not the gate.

Inactive unless ``INTEGRAL_SUBSCRIPTION_REQUIRED`` is set.

Product model (hosted Business): free Apps install and run without a paid
base plan. Commercial Apps are gated by entitlements at install/resume.
This middleware stays registered for back-compat but does not refuse writes —
the paywall is per App, not a workspace-wide lock.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import settings

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Legacy allowlist kept for tests and any future re-enable of the write lock.
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
    """True when a path would have been base-plan gated (tests / docs).

    The live middleware no longer refuses these writes; commercial Apps are
    enforced via entitlements instead.
    """
    verb = (method or "").upper()
    if verb not in _WRITE_METHODS:
        return False
    clean = (path or "").split("?", 1)[0]
    if len(clean) > 1:
        clean = clean.rstrip("/")
    for prefix in _ALLOW_PREFIXES:
        if clean == prefix or clean.startswith(prefix + "/"):
            return False
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
    """No-op write lock. Commercial Apps use entitlement checks instead."""

    async def dispatch(self, request: Request, call_next) -> Response:
        # Per-App paywall: do not 402 workspace writes for a missing base plan.
        _ = settings.INTEGRAL_SUBSCRIPTION_REQUIRED
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
