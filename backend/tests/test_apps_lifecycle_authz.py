"""App settings / lifecycle endpoints are gated like the rest of the App
surface (``app.read`` for reads, ``app.update`` for mutations).

``GET/PATCH /apps/{id}/settings``, ``POST /apps/{id}/update-from-library``,
``/pause`` and ``/resume`` previously ran for any authenticated user.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


async def _create_app(client: AsyncClient) -> str:
    r = await client.post("/api/apps", json={"name": "Lifecycle Authz App"})
    assert r.status_code in (200, 201), r.text
    return r.json()["app"]["id"]


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_unrelated_user_is_denied_on_settings_and_lifecycle(
    authenticated_client: AsyncClient, second_user_client: AsyncClient
):
    """Settings + lifecycle endpoints return 403 for a stranger."""
    app_id = await _create_app(authenticated_client)

    r = await second_user_client.get(f"/api/apps/{app_id}/settings")
    assert r.status_code == 403, r.text
    r = await second_user_client.patch(
        f"/api/apps/{app_id}/settings", json={"settings": {"x": 1}}
    )
    assert r.status_code == 403, r.text
    for action in ("update-from-library", "pause", "resume"):
        r = await second_user_client.post(f"/api/apps/{app_id}/{action}", json={})
        assert r.status_code == 403, (action, r.text)


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_owner_can_read_settings(authenticated_client: AsyncClient):
    """The App owner still reads settings."""
    app_id = await _create_app(authenticated_client)
    r = await authenticated_client.get(f"/api/apps/{app_id}/settings")
    assert r.status_code == 200, r.text
    assert r.json()["app_id"] == app_id
