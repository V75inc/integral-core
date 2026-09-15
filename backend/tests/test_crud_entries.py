"""CRUD tests for Entries API."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
class TestEntriesCRUD:
    """Test suite for Entries CRUD operations."""

    async def _create_track(self, client: AsyncClient):
        """Helper to create a track."""
        track_data = {"title": "Test Track", "visibility": "private"}
        response = await client.post("/api/tracks", json=track_data, timeout=5.0)
        assert (
            response.status_code == 200
        ), f"Track creation failed: {response.status_code} - {response.text}"
        data = response.json()
        assert "track" in data, f"Response missing 'track': {data}"
        return data["track"]["id"]

    async def _create_tag(
        self, client: AsyncClient, track_id: str, name: str = "test-tag"
    ):
        """Helper to create a track-scoped tag."""
        tag_data = {"name": name, "color": "#FF0000", "track_id": track_id}
        response = await client.post("/api/tags", json=tag_data)
        assert response.status_code == 200, f"Tag creation failed: {response.text}"
        return response.json()["tag"]["id"]

    async def test_create_entry(self, authenticated_client: AsyncClient, test_user):
        """Test creating a new entry with updated schema fields."""
        track_id = await self._create_track(authenticated_client)

        entry_data = {
            "track_id": track_id,
            "title": "My First Entry",
            "body": "This is a test entry",
            "custom_fields": {},
        }

        response = await authenticated_client.post("/api/entries", json=entry_data)

        assert response.status_code == 200
        data = response.json()
        assert "entry" in data
        assert data["entry"]["title"] == "My First Entry"
        assert "id" in data["entry"]

        return data["entry"]["id"]

    async def test_list_entries(self, authenticated_client: AsyncClient, test_user):
        """Test listing all accessible entries."""
        track_id = await self._create_track(authenticated_client)

        entry_data = {
            "track_id": track_id,
            "title": "Test entry",
            "body": "Test entry body",
        }
        await authenticated_client.post("/api/entries", json=entry_data)

        response = await authenticated_client.get("/api/entries")

        assert response.status_code == 200
        data = response.json()
        assert "entries" in data
        assert "total" in data
        assert isinstance(data["entries"], list)

    async def test_list_entries_cursor_pagination(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test cursor-based pagination for entries."""
        track_id = await self._create_track(authenticated_client)

        for i in range(5):
            await authenticated_client.post(
                "/api/entries", json={"track_id": track_id, "title": f"Entry {i}"}
            )

        response = await authenticated_client.get("/api/entries?limit=2")

        assert response.status_code == 200
        data = response.json()
        assert "entries" in data
        assert len(data["entries"]) <= 2
        assert "has_more" in data
        assert "next_cursor" in data

    async def test_list_entries_with_track_filter(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test listing entries filtered by track."""
        track_id = await self._create_track(authenticated_client)

        for i in range(3):
            await authenticated_client.post(
                "/api/entries",
                json={"track_id": track_id, "title": f"Entry {i}"},
            )

        response = await authenticated_client.get(f"/api/entries?track_id={track_id}")

        assert response.status_code == 200
        data = response.json()
        assert "entries" in data
        assert len(data["entries"]) >= 3

    async def test_list_entries_with_status_filter(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test listing entries filtered by status."""
        track_id = await self._create_track(authenticated_client)
        await authenticated_client.post(
            "/api/entries", json={"track_id": track_id, "title": "Active entry"}
        )

        response = await authenticated_client.get("/api/entries?status=active")

        assert response.status_code == 200
        data = response.json()
        assert "entries" in data

    async def test_list_entries_with_pagination(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test listing entries with cursor pagination."""
        track_id = await self._create_track(authenticated_client)

        for i in range(5):
            await authenticated_client.post(
                "/api/entries", json={"track_id": track_id, "title": f"Entry {i}"}
            )

        response = await authenticated_client.get("/api/entries?limit=2")

        assert response.status_code == 200
        data = response.json()
        assert "entries" in data
        assert "has_more" in data
        assert len(data["entries"]) <= 2

    async def test_get_entry_by_id(self, authenticated_client: AsyncClient, test_user):
        """Test getting a specific entry by ID."""
        track_id = await self._create_track(authenticated_client)

        create_response = await authenticated_client.post(
            "/api/entries",
            json={"track_id": track_id, "title": "Get Entry Test", "body": "body"},
        )
        entry_id = create_response.json()["entry"]["id"]

        response = await authenticated_client.get(f"/api/entries/{entry_id}")

        assert response.status_code == 200
        data = response.json()
        assert "entry" in data
        assert data["entry"]["id"] == entry_id
        assert data["entry"]["title"] == "Get Entry Test"

    async def test_get_nonexistent_entry(self, authenticated_client: AsyncClient):
        """Test getting an entry that doesn't exist."""
        response = await authenticated_client.get("/api/entries/nonexistent-id")
        assert response.status_code == 404

    async def test_update_entry(self, authenticated_client: AsyncClient, test_user):
        """Test updating an entry with new schema fields."""
        track_id = await self._create_track(authenticated_client)

        create_response = await authenticated_client.post(
            "/api/entries",
            json={
                "track_id": track_id,
                "title": "Original Title",
                "body": "Original Body",
            },
        )
        entry_id = create_response.json()["entry"]["id"]

        update_data = {
            "title": "Updated Title",
            "body": "Updated Body",
            "status": "completed",
            "custom_fields": {},
        }

        response = await authenticated_client.put(
            f"/api/entries/{entry_id}", json=update_data
        )

        assert response.status_code == 200
        data = response.json()
        assert "entry" in data
        assert data["entry"]["status"] == "completed"
        assert data["entry"]["title"] == "Updated Title"

    async def test_update_entry_clears_link_preview_with_null(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Explicit ``_link_preview: null`` must remove merged stored preview (omit key alone does not)."""
        track_id = await self._create_track(authenticated_client)

        create_response = await authenticated_client.post(
            "/api/entries",
            json={
                "track_id": track_id,
                "title": "Link post",
                "body": "https://example.com/page",
                "custom_fields": {
                    "_link_preview": {
                        "url": "https://example.com/page",
                        "title": "Example",
                    }
                },
            },
        )
        assert create_response.status_code == 200
        entry_id = create_response.json()["entry"]["id"]
        assert (
            create_response.json()["entry"]["custom_fields"]["_link_preview"]["title"]
            == "Example"
        )

        clear_response = await authenticated_client.put(
            f"/api/entries/{entry_id}",
            json={"custom_fields": {"_link_preview": None}},
        )
        assert clear_response.status_code == 200
        cf = clear_response.json()["entry"].get("custom_fields") or {}
        assert "_link_preview" not in cf

        get_response = await authenticated_client.get(f"/api/entries/{entry_id}")
        assert get_response.status_code == 200
        cf2 = get_response.json()["entry"].get("custom_fields") or {}
        assert "_link_preview" not in cf2

    async def test_delete_entry(self, authenticated_client: AsyncClient, test_user):
        """Test deleting an entry."""
        track_id = await self._create_track(authenticated_client)

        create_response = await authenticated_client.post(
            "/api/entries", json={"track_id": track_id, "title": "Entry to Delete"}
        )
        entry_id = create_response.json()["entry"]["id"]

        response = await authenticated_client.delete(f"/api/entries/{entry_id}")

        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "deleted_entry_id" in data

        get_response = await authenticated_client.get(f"/api/entries/{entry_id}")
        assert get_response.status_code == 404

    async def test_create_entry_with_tags(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test creating an entry with track-scoped tags."""
        track_id = await self._create_track(authenticated_client)
        tag_id = await self._create_tag(authenticated_client, track_id, "important")

        entry_data = {
            "track_id": track_id,
            "title": "Tagged Entry",
            "body": "Tagged Entry body",
            "tags": [tag_id],
        }

        response = await authenticated_client.post("/api/entries", json=entry_data)

        assert response.status_code == 200
        data = response.json()
        assert "entry" in data
        tags_out = data["entry"]["tags"]
        tag_refs = [
            t if isinstance(t, str) else (t.get("id") if isinstance(t, dict) else None)
            for t in tags_out
        ]
        assert tag_id in tag_refs

    async def test_add_remove_tag_on_entry(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test adding and removing a tag on an entry."""
        track_id = await self._create_track(authenticated_client)

        entry_resp = await authenticated_client.post(
            "/api/entries", json={"track_id": track_id, "title": "Entry for Tagging"}
        )
        entry_id = entry_resp.json()["entry"]["id"]
        tag_id = await self._create_tag(authenticated_client, track_id, "urgent-tag")

        add_resp = await authenticated_client.post(
            f"/api/entries/{entry_id}/tags", json={"tag_id": tag_id}
        )
        assert add_resp.status_code == 200

        del_resp = await authenticated_client.delete(
            f"/api/entries/{entry_id}/tags/{tag_id}"
        )
        assert del_resp.status_code == 200

    async def test_add_reaction(self, authenticated_client: AsyncClient, test_user):
        """Test adding a reaction to an entry."""
        track_id = await self._create_track(authenticated_client)
        entry_resp = await authenticated_client.post(
            "/api/entries", json={"track_id": track_id, "title": "Entry for reactions"}
        )
        entry_id = entry_resp.json()["entry"]["id"]

        response = await authenticated_client.post(
            f"/api/entries/{entry_id}/reactions",
            json={"emoji": "👍"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "reactions" in data
        assert "👍" in data["reactions"]
        assert len(data["reactions"]["👍"]) >= 1

    async def test_remove_reaction(self, authenticated_client: AsyncClient, test_user):
        """Test removing a reaction from an entry."""
        track_id = await self._create_track(authenticated_client)
        entry_resp = await authenticated_client.post(
            "/api/entries",
            json={"track_id": track_id, "title": "Entry for remove reaction"},
        )
        entry_id = entry_resp.json()["entry"]["id"]

        await authenticated_client.post(
            f"/api/entries/{entry_id}/reactions",
            json={"emoji": "❤️"},
        )

        response = await authenticated_client.delete(
            f"/api/entries/{entry_id}/reactions/❤️"
        )

        assert response.status_code == 200
        data = response.json()
        assert "reactions" in data

    async def test_toggle_reaction(self, authenticated_client: AsyncClient, test_user):
        """Test toggling a reaction (add then remove by posting again)."""
        track_id = await self._create_track(authenticated_client)
        entry_resp = await authenticated_client.post(
            "/api/entries", json={"track_id": track_id, "title": "Entry for toggle"}
        )
        entry_id = entry_resp.json()["entry"]["id"]

        add_resp = await authenticated_client.post(
            f"/api/entries/{entry_id}/reactions",
            json={"emoji": "🔥"},
        )
        assert add_resp.status_code == 200
        assert "🔥" in add_resp.json()["reactions"]

        toggle_resp = await authenticated_client.post(
            f"/api/entries/{entry_id}/reactions",
            json={"emoji": "🔥"},
        )
        assert toggle_resp.status_code == 200
        reactions = toggle_resp.json()["reactions"]
        assert "🔥" not in reactions or len(reactions.get("🔥", [])) == 0

    async def test_reaction_nonexistent_entry(self, authenticated_client: AsyncClient):
        """Test adding reaction to non-existent entry returns 404 or 403."""
        response = await authenticated_client.post(
            "/api/entries/nonexistent-id/reactions",
            json={"emoji": "👍"},
        )
        assert response.status_code in (403, 404)

    async def test_comment_count_on_list_get_and_create(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Entry payloads include ``comment_count``; list/get match comment API."""
        track_id = await self._create_track(authenticated_client)
        entry_resp = await authenticated_client.post(
            "/api/entries",
            json={"track_id": track_id, "title": "Commented entry"},
        )
        assert entry_resp.status_code == 200
        entry_id = entry_resp.json()["entry"]["id"]
        assert entry_resp.json()["entry"].get("comment_count") == 0

        await authenticated_client.post(
            f"/api/entries/{entry_id}/comments", json={"text": "first"}
        )
        c2 = await authenticated_client.post(
            f"/api/entries/{entry_id}/comments", json={"text": "second"}
        )
        assert c2.status_code == 200
        parent_id = c2.json()["comment"]["id"]
        await authenticated_client.post(
            f"/api/entries/{entry_id}/comments",
            json={"text": "reply", "parent_id": parent_id},
        )

        list_resp = await authenticated_client.get("/api/entries")
        assert list_resp.status_code == 200
        mine = next(e for e in list_resp.json()["entries"] if e["id"] == entry_id)
        assert mine.get("comment_count") == 3

        get_resp = await authenticated_client.get(f"/api/entries/{entry_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["entry"].get("comment_count") == 3

        upd = await authenticated_client.put(
            f"/api/entries/{entry_id}", json={"title": "Commented entry (edited)"}
        )
        assert upd.status_code == 200
        assert upd.json()["entry"].get("comment_count") == 3
