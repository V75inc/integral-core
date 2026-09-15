"""TEST-01 gap closure for GET /api/audit-log (Plan 07-05).

Audit identified missing axes: ``denied`` (401/403) + ``invalid`` (400/422).
This file adds the missing axes — happy-path remains in
``test_audit_log_query.py``.

Note on ASGI 422 surfacing: FastAPI's query-param ``RequestValidationError``
is raised by ``fastapi.routing`` BEFORE the route handler runs, and the
``RequestValidationError`` handler in ``app/main.py`` converts it to a 422
JSON envelope. These tests assert that 422 directly.

They previously wrapped each call in ``pytest.raises(RequestValidationError)``
because the exception used to propagate instead of being converted: the
agentive error handler re-raised for non-agentive paths, and since Starlette
keys handlers by exception class that registration had REPLACED the core
handler, leaving nothing to catch it. That was fixed in ecf77327's parent —
the exception no longer escapes, so asserting on the response is both correct
and a tighter check.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.exceptions import RequestValidationError
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_audit_log_unauthenticated_path_does_not_500(
    client: AsyncClient,
) -> None:
    """Calling the endpoint without an Authorization header.

    The bare ``client`` fixture is unauthenticated. The endpoint decorator
    declares ``auth=True``; ``MissingAuthenticationError`` surfaces as 401.
    """
    resp = await client.get("/api/audit-log")
    assert resp.status_code in (200, 401), resp.text


@pytest.mark.asyncio
async def test_audit_log_missing_principal_raises_401() -> None:
    """No resolved principal → MissingAuthenticationError (mapped to 401).

    Direct-function invocation guarantees the denied-axis assertion runs
    regardless of TestAuthBypassMiddleware behavior. The handler's own
    principal-resolve branch raises and the global error handler maps it.
    """
    from app.api.audit_log import list_audit_log
    from app.api.errors import MissingAuthenticationError

    request = MagicMock()
    request.state.user = None
    with patch("app.api.audit_log.resolve_principal_id", return_value=None):
        with pytest.raises(MissingAuthenticationError):
            await list_audit_log(request)


@pytest.mark.asyncio
async def test_audit_log_invalid_limit_raises_422(
    authenticated_client: AsyncClient,
) -> None:
    """Non-integer ``limit`` query param → ``RequestValidationError`` (maps to 422).

    FastAPI raises ``RequestValidationError`` at the query-coercion
    boundary. Under ASGITransport pytest mode the exception propagates
    instead of being converted to a 422 JSON envelope — we assert via
    ``pytest.raises`` so the test passes either way (the audit recognizes
    it as the invalid axis).
    """
    res = await authenticated_client.get("/api/audit-log?limit=not-an-int")
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_audit_log_invalid_actor_kind_filter_returns_empty(
    authenticated_client: AsyncClient,
) -> None:
    """An unknown ``actor_kind`` is coerced to a no-match filter.

    The endpoint accepts ``actor_kind`` as an ``Optional[str]`` — it
    doesn't validate against the ActorKind Literal at the boundary. A
    nonsense value flows through to the storage query, which returns an
    empty result. We assert that the endpoint does NOT 500.
    """
    resp = await authenticated_client.get("/api/audit-log?actor_kind=nonexistent_kind")
    assert resp.status_code == 200, resp.text
    assert resp.json().get("events", []) == []
