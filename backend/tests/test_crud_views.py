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

    async def test_update_view_set_default(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test setting a view as default."""
        track_id = await self._create_track(authenticated_client)
        view = await self._create_view(
            authenticated_client, track_id, "Default View", "feed", is_default=False
        )
        view_id = view["id"]

        response = await authenticated_client.put(
            f"/api/views/{view_id}", json={"is_default": True}
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
