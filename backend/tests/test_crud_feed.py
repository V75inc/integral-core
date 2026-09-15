"""CRUD tests for Feed API (aggregated activity stream)."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
class TestFeedCRUD:
    """Test suite for Feed CRUD operations."""

    async def _create_track(self, client: AsyncClient, title: str = "Feed Test Track"):
        response = await client.post(
            "/api/tracks", json={"title": title, "visibility": "private"}
        )
        assert response.status_code == 200, f"Track creation failed: {response.text}"
        return response.json()["track"]["id"]

    async def _create_entry(self, client: AsyncClient, track_id: str, title: str):
        response = await client.post(
            "/api/entries",
            json={"track_id": track_id, "title": title, "body": "Body"},
        )
        assert response.status_code == 200, f"Entry creation failed: {response.text}"
        return response.json()["entry"]["id"]

    async def test_get_feed(self, authenticated_client: AsyncClient, test_user):
        """Test getting aggregated feed returns entries from accessible tracks."""
        track_id = await self._create_track(authenticated_client)
        await self._create_entry(authenticated_client, track_id, "Feed Entry 1")

        response = await authenticated_client.get("/api/feed")

        assert response.status_code == 200
        data = response.json()
        assert "entries" in data
        assert isinstance(data["entries"], list)
        assert len(data["entries"]) >= 1
        titles = [e["title"] for e in data["entries"]]
        assert "Feed Entry 1" in titles

    async def test_get_feed_with_track_filter(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test feed filtered by track_id."""
        track_id = await self._create_track(authenticated_client)
        await self._create_entry(authenticated_client, track_id, "Filtered Entry")

        response = await authenticated_client.get(
            "/api/feed", params={"track_id": track_id}
        )

        assert response.status_code == 200
        data = response.json()
        assert "entries" in data
        for e in data["entries"]:
            tid = e.get("track_id") or (e.get("track") or {}).get("id")
            assert tid == track_id, f"Entry has wrong track: {tid}"

    async def test_get_feed_cursor_pagination(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test feed with cursor-based pagination."""
        track_id = await self._create_track(authenticated_client)
        for i in range(5):
            await self._create_entry(authenticated_client, track_id, f"Entry {i}")

        response = await authenticated_client.get("/api/feed", params={"limit": 2})

        assert response.status_code == 200
        data = response.json()
        assert "entries" in data
        assert len(data["entries"]) <= 2
        assert "has_more" in data or "next_cursor" in data

    async def test_get_feed_limit_pagination(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test feed respects limit with cursor-style metadata."""
        track_id = await self._create_track(authenticated_client)
        for i in range(5):
            await self._create_entry(authenticated_client, track_id, f"Page Entry {i}")

        response = await authenticated_client.get("/api/feed", params={"limit": 2})

        assert response.status_code == 200
        data = response.json()
        assert "entries" in data
        assert len(data["entries"]) <= 2
        assert "has_more" in data

    async def test_get_feed_entries_alias(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test /feed_entries is alias for /feed."""
        track_id = await self._create_track(authenticated_client)
        await self._create_entry(authenticated_client, track_id, "Alias Entry")

        response = await authenticated_client.get("/api/feed_entries")

        assert response.status_code == 200
        data = response.json()
        assert "entries" in data

    async def test_create_feed_entry(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test creating entry via feed_entries endpoint."""
        track_id = await self._create_track(authenticated_client)

        response = await authenticated_client.post(
            "/api/feed_entries",
            json={"track_id": track_id, "title": "Feed Entry via API"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "entry" in data
        assert data["entry"]["title"] == "Feed Entry via API"

    async def test_update_feed_entry(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test updating entry via feed_entries endpoint."""
        track_id = await self._create_track(authenticated_client)
        entry_id = await self._create_entry(
            authenticated_client, track_id, "Original Title"
        )

        response = await authenticated_client.put(
            f"/api/feed_entries/{entry_id}",
            json={"body": "Updated body via feed"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "entry" in data
        assert data["entry"]["body"] == "Updated body via feed"

    async def test_delete_feed_entry(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test deleting entry via feed_entries endpoint."""
        track_id = await self._create_track(authenticated_client)
        entry_id = await self._create_entry(
            authenticated_client, track_id, "Entry to Delete"
        )

        response = await authenticated_client.delete(f"/api/feed_entries/{entry_id}")

        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "deleted_entry_id" in data

    async def test_get_feed_with_query(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Feed supports case-insensitive substring search via ``q``."""
        track_id = await self._create_track(authenticated_client)
        await self._create_entry(authenticated_client, track_id, "Alpha Widget")
        await self._create_entry(authenticated_client, track_id, "Beta Gadget")

        response = await authenticated_client.get(
            "/api/entries", params={"q": "widget"}
        )
        assert response.status_code == 200
        titles = [e["title"] for e in response.json()["entries"]]
        assert "Alpha Widget" in titles
        assert "Beta Gadget" not in titles

        # Body-level match + case-insensitivity
        response = await authenticated_client.post(
            "/api/entries",
            json={
                "track_id": track_id,
                "title": "Gamma",
                "body": "Contains SpecialMarker text",
            },
        )
        assert response.status_code == 200
        response = await authenticated_client.get(
            "/api/entries", params={"q": "specialmarker"}
        )
        assert response.status_code == 200
        titles = [e["title"] for e in response.json()["entries"]]
        assert "Gamma" in titles
        assert "Alpha Widget" not in titles

    async def test_feed_entries_include_comment_count(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Feed list includes ``comment_count`` per entry."""
        track_id = await self._create_track(authenticated_client)
        entry_id = await self._create_entry(
            authenticated_client, track_id, "Feed comment count"
        )
        await authenticated_client.post(
            f"/api/entries/{entry_id}/comments", json={"text": "c1"}
        )

        response = await authenticated_client.get("/api/feed_entries")
        assert response.status_code == 200
        entries = response.json()["entries"]
        row = next(e for e in entries if e["id"] == entry_id)
        assert row.get("comment_count") == 1
