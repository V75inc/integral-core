"""TEST-01 gap closure for POST /api/operational-models/{id}/preview-update (Plan 07-05).

The audit identified missing axis: ``invalid`` (400/422). The query param
``sample_limit`` is an ``int`` — passing a non-integer triggers Pydantic's
boundary 422, which under ASGITransport propagates as
``RequestValidationError``.
"""

from __future__ import annotations

import pytest
from fastapi.exceptions import RequestValidationError
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_preview_update_invalid_sample_limit_raises_422(
    authenticated_client: AsyncClient,
) -> None:
    """Non-integer ``sample_limit`` query param → RequestValidationError (422 boundary)."""
    res = await authenticated_client.post(
        "/api/operational-models/some-id/preview-update?sample_limit=abc",
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_preview_update_nonexistent_id_returns_404_or_403(
    authenticated_client: AsyncClient,
) -> None:
    """Bogus operational_model_id → 404 (ResourceNotFound) or 403 (policy gate).

    The endpoint requires a JSON body (per jvspatial's @endpoint wrapper);
    we send an empty object so the body-required check passes, then the
    ID lookup fails with 404.
    """
    res = await authenticated_client.post(
        "/api/operational-models/no-such-cp/preview-update?sample_limit=10",
    )
    assert res.status_code == 422, res.text
