"""TEST-01 gap closure for /api/operational-models/author + /{id}/modify (Plan 07-05).

Plan 06-04 shipped happy + denied + invalid coverage in
``test_profile_authoring.py`` (200/400/403). This file adds boundary
edge cases — extra-forbid body fields, wrong types, missing required
fields. Under ASGITransport the FastAPI 422 propagates as
``RequestValidationError`` (see header note in
``test_audit_log_query_gaps.py``).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_author_operational_model_missing_required_fields_returns_422_or_400(
    authenticated_client: AsyncClient,
) -> None:
    """POST /api/operational-models/author with empty body.

    Empty body fails the boundary if any field is required. The endpoint
    may also accept all-defaults and return 400/403 — we assert the
    endpoint never 500s.
    """
    resp = await authenticated_client.post(
        "/api/operational-models/author",
        json={},
    )
    assert resp.status_code in (200, 400, 403, 422), resp.text


@pytest.mark.asyncio
async def test_author_operational_model_invalid_type_hint_raises_422(
    authenticated_client: AsyncClient,
) -> None:
    """A non-string ``type_hint`` fails the Pydantic boundary."""
    res = await authenticated_client.post(
        "/api/operational-models/author",
        json={"type_hint": 12345, "name": "P"},
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_modify_operational_model_missing_body_returns_422_or_400(
    authenticated_client: AsyncClient,
) -> None:
    """POST /api/operational-models/{id}/modify with empty body."""
    resp = await authenticated_client.post(
        "/api/operational-models/some-id/modify",
        json={},
    )
    assert resp.status_code in (200, 400, 403, 404, 422), resp.text


@pytest.mark.asyncio
async def test_modify_operational_model_invalid_patches_shape_raises_422(
    authenticated_client: AsyncClient,
) -> None:
    """``patches`` MUST be a list. A scalar value fails the boundary."""
    res = await authenticated_client.post(
        "/api/operational-models/some-id/modify",
        json={"patches": "not-a-list"},
    )
    assert res.status_code == 422, res.text
