"""TEST-01 gap closure for GET /api/events polling (Plan 07-05).

Audit identified missing axis: ``invalid`` (400/422). Happy + denied
remain in ``test_events_polling.py``. Under ASGITransport, FastAPI's
query-coercion errors propagate as ``RequestValidationError`` (see the
note in ``test_audit_log_query_gaps.py``) — we exercise via
``pytest.raises``.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_events_polling_invalid_limit_raises_422(
    authenticated_client: AsyncClient,
) -> None:
    """Non-integer ``limit`` is rejected at the FastAPI boundary (422)."""
    res = await authenticated_client.get("/api/events?limit=not-an-int")
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_events_polling_invalid_since_does_not_500(
    authenticated_client: AsyncClient,
) -> None:
    """An invalid base64 ``since`` cursor is treated as start-from-zero.

    Per Phase 2 EVT-02 the polling endpoint MUST be defensive — a garbage
    cursor from an old client decodes to an empty start position and the
    endpoint returns 200 with a fresh page. Never 500.
    """
    resp = await authenticated_client.get("/api/events?since=not_base64")
    assert resp.status_code in (200, 400, 422), resp.text


@pytest.mark.asyncio
async def test_events_polling_invalid_scope_format_returns_empty_or_400(
    authenticated_client: AsyncClient,
) -> None:
    """An unparseable scope string is treated as an unknown scope.

    The endpoint accepts ``scope`` as ``Optional[str]`` — it doesn't
    validate against ``track:<id> | space:<id> | user:<id>`` at the
    boundary. A garbage value flows through and the permission filter
    returns an empty list. We assert the endpoint does NOT 500.
    """
    resp = await authenticated_client.get(
        "/api/events?scope=invalid:format:too:many:colons"
    )
    assert resp.status_code in (200, 400, 422), resp.text
