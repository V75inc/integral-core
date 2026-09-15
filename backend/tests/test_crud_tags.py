"""CRUD tests for Tags API (track-scoped)."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
class TestTagsCRUD:
    """Test suite for Tags CRUD operations."""

    async def _create_track(self, client: AsyncClient, title: str = "Tag Test Track"):
        response = await client.post(
            "/api/tracks", json={"title": title, "visibility": "private"}
        )
        assert response.status_code == 200, f"Track creation failed: {response.text}"
        return response.json()["track"]["id"]

    async def _create_space(self, client: AsyncClient, name: str = "Tag Test App"):
        response = await client.post(
            "/api/apps", json={"name": name, "description": ""}
        )
        assert response.status_code == 200, f"App creation failed: {response.text}"
        return response.json()["app"]["id"]

    async def _create_tag(
        self, client: AsyncClient, track_id: str, name: str, color: str = "#6B7280"
    ):
        tag_data = {"name": name, "color": color, "track_id": track_id}
        response = await client.post("/api/tags", json=tag_data)
        assert response.status_code == 200, f"Tag creation failed: {response.text}"
        return response.json()["tag"]

    async def test_create_tag(self, authenticated_client: AsyncClient, test_user):
        """Test creating a new track-scoped tag."""
        track_id = await self._create_track(authenticated_client)

        response = await authenticated_client.post(
            "/api/tags",
            json={"name": "important", "color": "#FF0000", "track_id": track_id},
        )

        assert response.status_code == 200
        data = response.json()
        assert "tag" in data
        assert data["tag"]["name"] == "important"
        assert data["tag"]["color"] == "#ff0000"
        assert data["tag"]["track_id"] == track_id
        assert "id" in data["tag"]

    async def test_create_tag_with_default_color(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test creating a tag with the default color."""
        track_id = await self._create_track(authenticated_client, "Default Color Track")

        response = await authenticated_client.post(
            "/api/tags",
            json={"name": "default-color-tag", "track_id": track_id},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["tag"]["color"] == "#6b7280"

    async def test_create_duplicate_tag_same_track(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test that creating duplicate tags in the same track fails."""
        track_id = await self._create_track(authenticated_client, "Dup Tag Track")

        tag_data = {"name": "duplicate-tag", "color": "#000000", "track_id": track_id}
        await authenticated_client.post("/api/tags", json=tag_data)
        response = await authenticated_client.post("/api/tags", json=tag_data)

        assert response.status_code == 409

    async def test_list_tags(self, authenticated_client: AsyncClient, test_user):
        """Test listing all tags (unfiltered)."""
        track_id = await self._create_track(authenticated_client, "List Tag Track")
        await self._create_tag(
            authenticated_client, track_id, "list-test-tag", "#00FF00"
        )

        response = await authenticated_client.get("/api/tags")

        assert response.status_code == 200
        data = response.json()
        assert "tags" in data
        assert "total" in data
        assert isinstance(data["tags"], list)
        assert data["total"] > 0

    async def test_list_tags_scoped_to_track(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test listing tags scoped to a specific track."""
        track_id = await self._create_track(authenticated_client, "Scoped Tag Track")
        await self._create_tag(authenticated_client, track_id, "scoped-tag", "#00FF00")

        response = await authenticated_client.get(f"/api/tags?track_id={track_id}")

        assert response.status_code == 200
        data = response.json()
        assert all(t["track_id"] == track_id for t in data["tags"])

    async def test_get_tag_by_id(self, authenticated_client: AsyncClient, test_user):
        """Test getting a specific tag by ID."""
        track_id = await self._create_track(authenticated_client, "Get Tag Track")
        tag = await self._create_tag(
            authenticated_client, track_id, "get-test-tag", "#0000FF"
        )
        tag_id = tag["id"]

        response = await authenticated_client.get(f"/api/tags/{tag_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["tag"]["id"] == tag_id
        assert data["tag"]["name"] == "get-test-tag"

    async def test_get_nonexistent_tag(self, authenticated_client: AsyncClient):
        """Test getting a tag that doesn't exist."""
        response = await authenticated_client.get("/api/tags/nonexistent-id")
        assert response.status_code == 404

    async def test_update_tag(self, authenticated_client: AsyncClient, test_user):
        """Test updating a tag."""
        track_id = await self._create_track(authenticated_client, "Update Tag Track")
        tag = await self._create_tag(
            authenticated_client, track_id, "update-test-tag", "#FF0000"
        )
        tag_id = tag["id"]

        response = await authenticated_client.put(
            f"/api/tags/{tag_id}",
            json={"name": "updated-tag-name", "color": "#00FF00"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["tag"]["name"] == "updated-tag-name"
        assert data["tag"]["color"] == "#00ff00"

    async def test_delete_tag(self, authenticated_client: AsyncClient, test_user):
        """Test deleting a tag."""
        track_id = await self._create_track(authenticated_client, "Delete Tag Track")
        tag = await self._create_tag(
            authenticated_client, track_id, "delete-test-tag", "#FF0000"
        )
        tag_id = tag["id"]

        response = await authenticated_client.delete(f"/api/tags/{tag_id}")

        assert response.status_code == 200
        data = response.json()
        assert "deleted_tag_id" in data

        get_response = await authenticated_client.get(f"/api/tags/{tag_id}")
        assert get_response.status_code == 404

    async def test_create_and_list_app_tags(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Tags can be scoped to an App (space-attached content profile)."""
        app_id = await self._create_space(authenticated_client, "App-scoped tags")
        r = await authenticated_client.post(
            "/api/tags",
            json={"name": "space-label", "app_id": app_id},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["tag"]["name"] == "space-label"
        assert body["tag"].get("app_id") == app_id

        listed = await authenticated_client.get(f"/api/tags?app_id={app_id}")
        assert listed.status_code == 200
        tags = listed.json()["tags"]
        assert any(t["name"] == "space-label" for t in tags)
