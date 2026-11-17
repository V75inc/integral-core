"""CRUD tests for Views API (saved track view configurations)."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
class TestViewsCRUD:
    """Test suite for Views CRUD operations."""

    async def _create_track(self, client: AsyncClient, title: str = "View Test Track"):
        response = await client.post(
            "/api/tracks", json={"title": title, "visibility": "private"}
        )
        assert response.status_code == 200, f"Track creation failed: {response.text}"
        return response.json()["track"]["id"]

    async def _create_view(
        self,
        client: AsyncClient,
        track_id: str,
        name: str = "Dev Board",
        type: str = "kanban",
        config: dict = None,
        is_default: bool = False,
    ):
        payload = {"name": name, "type": type, "is_default": is_default}
        if config is not None:
            payload["config"] = config
        response = await client.post(
            f"/api/tracks/{track_id}/views",
            json=payload,
        )
        assert response.status_code == 200, f"View creation failed: {response.text}"
        return response.json()["view"]

    async def test_create_view(self, authenticated_client: AsyncClient, test_user):
        """Test creating a new saved view for a track."""
        track_id = await self._create_track(authenticated_client)

        response = await authenticated_client.post(
            f"/api/tracks/{track_id}/views",
            json={"name": "Dev Board", "type": "kanban", "config": {"columns": []}},
        )

        assert response.status_code == 200
        data = response.json()
        assert "view" in data
        assert data["view"]["name"] == "Dev Board"
        assert data["view"]["type"] == "kanban"
        assert data["view"]["track_id"] == track_id
        assert "id" in data["view"]

    async def test_create_view_invalid_type(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test creating a view with invalid type returns 400."""
        track_id = await self._create_track(authenticated_client)

        response = await authenticated_client.post(
            f"/api/tracks/{track_id}/views",
            json={"name": "Bad View", "type": "invalid_type"},
        )

        assert response.status_code == 400

    async def test_list_track_views(self, authenticated_client: AsyncClient, test_user):
        """Test listing all views for a track."""
        track_id = await self._create_track(authenticated_client)
        await self._create_view(authenticated_client, track_id, "View 1", "feed")
        await self._create_view(authenticated_client, track_id, "View 2", "table")

        response = await authenticated_client.get(f"/api/tracks/{track_id}/views")

        assert response.status_code == 200
        data = response.json()
        assert "views" in data
        assert "total" in data
        assert data["total"] >= 2
        names = [v["name"] for v in data["views"]]
        assert "View 1" in names
        assert "View 2" in names

    async def test_get_view_by_id(self, authenticated_client: AsyncClient, test_user):
        """Test getting a specific view by ID."""
        track_id = await self._create_track(authenticated_client)
        view = await self._create_view(
            authenticated_client, track_id, "Get View Test", "calendar"
        )
        view_id = view["id"]

        response = await authenticated_client.get(f"/api/views/{view_id}")

        assert response.status_code == 200
        data = response.json()
        assert "view" in data
        assert data["view"]["id"] == view_id
        assert data["view"]["name"] == "Get View Test"
        assert data["view"]["type"] == "calendar"

    async def test_get_nonexistent_view(self, authenticated_client: AsyncClient):
        """Test getting a view that doesn't exist."""
        response = await authenticated_client.get("/api/views/nonexistent-id")
        assert response.status_code == 404

    async def test_update_view(self, authenticated_client: AsyncClient, test_user):
        """Test updating a view."""
        track_id = await self._create_track(authenticated_client)
        view = await self._create_view(
            authenticated_client, track_id, "Original Name", "feed"
        )
        view_id = view["id"]

        response = await authenticated_client.put(
            f"/api/views/{view_id}",
            json={
                "name": "Updated Name",
                "type": "table",
                "config": {"columns": [{"field": "title", "label": "Title"}]},
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert "view" in data
        assert data["view"]["name"] == "Updated Name"
        assert data["view"]["type"] == "table"
        assert "columns" in data["view"].get("config", {})

    @pytest.mark.parametrize("include_layout", [False, True])
    async def test_update_view_set_default(
        self, authenticated_client: AsyncClient, test_user, include_layout
    ):
        """Test setting a view as default."""
        track_id = await self._create_track(authenticated_client)
        view = await self._create_view(
            authenticated_client, track_id, "Default View", "feed", is_default=False
        )
        view_id = view["id"]

        response = await authenticated_client.put(
            f"/api/views/{view_id}",
            json={
                "is_default": True,
                **(
                    {"name": "Default View", "view_type": "feed", "config": {}}
                    if include_layout
                    else {}
                ),
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["view"]["is_default"] is True

    async def test_delete_view(self, authenticated_client: AsyncClient, test_user):
        """Test deleting a view."""
        track_id = await self._create_track(authenticated_client)
        view = await self._create_view(
            authenticated_client, track_id, "View to Delete", "gallery"
        )
        view_id = view["id"]

        response = await authenticated_client.delete(f"/api/views/{view_id}")

        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "deleted_view_id" in data

        get_response = await authenticated_client.get(f"/api/views/{view_id}")
        assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_saved_gallery_default_stages_with_real_view_node_and_preserves_layouts(
    authenticated_client, test_user
):
    """Exercise real API-created View shape, not a permissive attribute mock."""
    from app.agentive.tooling.bindings import _stage_save_view

    client = authenticated_client
    setup = TestViewsCRUD()
    track_id = await setup._create_track(client, "Default selection regression")
    await setup._create_view(
        client, track_id, "Records", "table", {"columns": ["title"]}, True
    )
    await setup._create_view(
        client, track_id, "Member profiles", "gallery", {"entry_type_keys": []}
    )
    before = (await client.get(f"/api/tracks/{track_id}/views")).json()["views"]
    target = next(v for v in before if v["name"] == "Member profiles")
    staged = await _stage_save_view(
        dict(
            track_id=track_id,
            view_id=target["id"],
            name=target["name"],
            view_type=target["type"],
            config=target["config"],
            is_default=True,
        )
    )
    assert staged["payload"]["view_id"] == target["id"]
    assert staged["payload"]["view_type"] == "gallery"
    assert staged["payload"]["is_default"] is True
    after = (await client.get(f"/api/tracks/{track_id}/views")).json()["views"]
    assert after == before  # A proposal does not execute or change either view.


@pytest.mark.asyncio
@pytest.mark.parametrize("include_layout", [False, True])
async def test_default_change_updates_canonical_track_navigation(
    authenticated_client, test_user, include_layout
):
    """Node flags and track metadata must identify the same default after reload."""
    client = authenticated_client
    setup = TestViewsCRUD()
    track_id = await setup._create_track(client, "Default navigation regression")
    await setup._create_view(client, track_id, "Records", "table", {}, True)
    gallery = await setup._create_view(
        client, track_id, "Member profiles", "gallery", {}
    )
    before = (await client.get(f"/api/tracks/{track_id}/views")).json()["views"]
    response = await client.put(
        f"/api/views/{gallery['id']}",
        json={
            "is_default": True,
            **(
                {
                    "name": gallery["name"],
                    "view_type": gallery["type"],
                    "config": gallery["config"],
                }
                if include_layout
                else {}
            ),
        },
    )
    assert response.status_code == 200, response.text
    views = (await client.get(f"/api/tracks/{track_id}/views")).json()["views"]
    winner = next(v for v in views if v["is_default"])
    assert winner["id"] == gallery["id"]
    track = (await client.get(f"/api/tracks/{track_id}")).json()["track"]
    assert (
        track["operational_model_defaults"]["default_view"]
        == winner["config"]["_manifest_view_key"]
    )
    for view in views:
        old = next(v for v in before if v["id"] == view["id"])
        assert view["name"] == old["name"]
        assert view["type"] == old["type"]
        assert view["config"] == old["config"]


@pytest.mark.asyncio
async def test_hiding_default_keeps_manifest_visibility_and_promoted_default(
    authenticated_client, test_user
):
    client = authenticated_client
    setup = TestViewsCRUD()
    track_id = await setup._create_track(client, "Hidden default regression")
    gallery = await setup._create_view(client, track_id, "Cards", "gallery", {}, True)
    response = await client.put(f"/api/views/{gallery['id']}", json={"hidden": True})
    assert response.status_code == 200, response.text
    views = (await client.get(f"/api/tracks/{track_id}/views")).json()["views"]
    assert next(v for v in views if v["id"] == gallery["id"])["hidden"] is True
    winner = next(v for v in views if v["is_default"])
    assert winner["id"] != gallery["id"]
    assert winner["hidden"] is False
    track = (await client.get(f"/api/tracks/{track_id}")).json()["track"]
    assert (
        track["operational_model_defaults"]["default_view"]
        == winner["config"]["_manifest_view_key"]
    )

    # Reopening a retired tab must preserve the promoted default and survive
    # materialization on the following view-list request.
    response = await client.put(f"/api/views/{gallery['id']}", json={"hidden": False})
    assert response.status_code == 200, response.text
    reopened = (await client.get(f"/api/tracks/{track_id}/views")).json()["views"]
    assert next(v for v in reopened if v["id"] == gallery["id"])["hidden"] is False
    assert next(v for v in reopened if v["is_default"])["id"] == winner["id"]
