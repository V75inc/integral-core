"""Agentive error envelope handler.

Wraps RequestValidationError on /api/agentive/* paths into the canonical
{error_code, message, details, timestamp, path} 5-key shape (D-03 + D-04).

Starlette keys exception handlers by exception CLASS, so registering this
handler REPLACES any previously-registered ``RequestValidationError`` handler
rather than stacking in front of it. There is no fallthrough: re-raising here
does not reach the earlier handler, it escapes as an unhandled exception and
the server returns 500.

This module therefore takes the core handler as an explicit ``fallback`` and
calls it for non-agentive paths. Before that, every request-validation error on
every non-agentive endpoint returned 500 instead of 422 whenever the agentive
layer was enabled — which is always, since the layer is always-on.
"""

from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

#: Signature of the core validation handler this module defers to.
ValidationHandler = Callable[[Request, RequestValidationError], Awaitable[JSONResponse]]


def _envelope(
    error_code: str,
    message: str,
    details: object,
    request: Request,
    http_status: int,
) -> JSONResponse:
    """Build a canonical 5-key error envelope JSONResponse."""
    return JSONResponse(
        status_code=http_status,
        content={
            "error_code": error_code,
            "message": message,
            "details": details,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "path": str(request.url.path),
        },
    )


def install_agentive_error_handlers(
    app: FastAPI, fallback: Optional[ValidationHandler] = None
) -> None:
    """Install the agentive-scoped 422 handler.

    Args:
        app: the FastAPI application.
        fallback: handler to use for non-agentive paths. Registering this
            handler REPLACES the app's existing ``RequestValidationError``
            handler (Starlette keys handlers by exception class), so the core
            handler must be passed in explicitly — it can no longer be reached
            by re-raising. Omitting it degrades non-agentive validation errors
            to a plain 422 rather than the core envelope; it never 500s.
    """

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Non-agentive paths belong to the core handler. Delegate — do NOT
        # re-raise: nothing is left to catch it and the client gets a 500.
        if not request.url.path.startswith("/api/agentive/"):
            if fallback is not None:
                return await fallback(request, exc)
            return _envelope(
                error_code="VALIDATION_ERROR",
                message="Request validation failed",
                details=exc.errors(),
                request=request,
                http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        return _envelope(
            error_code="VALIDATION_ERROR",
            message="Request body failed validation",
            details=exc.errors(),
            request=request,
            http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
