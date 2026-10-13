"""Admin inventory metadata follows canonical graph registries and identity."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_admin_inventory_counts_registry_children_and_resolves_auth_owner(
    authenticated_admin_client: AsyncClient,
):
    """Admin metadata matches resources cataloged beneath workspace branches."""
    client = authenticated_admin_client
    created = await client.post("/api/apps", json={"name": "Inventory proof"})
    assert created.status_code == 200, created.text
    app = created.json()["app"]
    scope = {"X-Integral-Scope": f"ws:{app['workspace_id']}"}
    created_track = await client.post(
        "/api/tracks",
        headers=scope,
        json={"title": "Inventory track", "app_id": app["id"]},
    )
    assert created_track.status_code == 200, created_track.text
    overview = await client.get(f"/api/admin/workspaces/{app['workspace_id']}")
    assert overview.status_code == 200, overview.text
    workspace = overview.json()["workspace"]
    assert workspace["app_count"] == 1
    assert workspace["track_count"] == 1
    apps = await client.get("/api/admin/apps")
    assert apps.status_code == 200, apps.text
    item = next(row for row in apps.json()["items"] if row["id"] == app["id"])
    assert item["owner"] is not None
    assert item["owner"]["display_name"] == "Platform Admin QA"
