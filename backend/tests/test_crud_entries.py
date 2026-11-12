"""CRUD tests for Entries API."""

import pytest
from httpx import AsyncClient

from app.models.edges import TAGGED_WITH
from app.models.nodes import Entry, Tag, Track
from app.services.app_graph import get_track_attached_operational_model


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
        initial_revision = create_response.json()["entry"]["record_revision"]
        initial_schema_revision = create_response.json()["entry"]["schema_revision"]

        update_data = {
            "title": "Updated Title",
            "body": "Updated Body",
            "status": "completed",
            "custom_fields": {},
            "expected_record_revision": initial_revision,
            "expected_schema_revision": initial_schema_revision,
        }

        response = await authenticated_client.put(
            f"/api/entries/{entry_id}", json=update_data
        )

        assert response.status_code == 200
        data = response.json()
        assert "entry" in data
        assert data["entry"]["status"] == "completed"
        assert data["entry"]["title"] == "Updated Title"
        assert data["entry"]["record_revision"] == initial_revision + 1
        assert data["entry"]["schema_revision"] == initial_schema_revision

        stale_response = await authenticated_client.put(
            f"/api/entries/{entry_id}",
            json={
                "title": "Stale update",
                "expected_record_revision": initial_revision,
            },
        )
        assert stale_response.status_code == 409
        assert stale_response.json()["error_code"] == "conflict"
        assert (
            stale_response.json()["details"]["error_code"] == "record_revision_conflict"
        )

        track = await Track.get(track_id)
        assert track is not None
        operational_model = await get_track_attached_operational_model(track)
        assert operational_model is not None
        operational_model.version_number = initial_schema_revision + 1
        await operational_model.save()

        stale_schema_response = await authenticated_client.put(
            f"/api/entries/{entry_id}",
            json={
                "title": "Schema-stale update",
                "expected_record_revision": initial_revision + 1,
                "expected_schema_revision": initial_schema_revision,
            },
        )
        assert stale_schema_response.status_code == 409
        assert (
            stale_schema_response.json()["details"]["error_code"]
            == "schema_revision_conflict"
        )
        assert (
            stale_schema_response.json()["details"]["current_schema_revision"]
            == initial_schema_revision + 1
        )

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

    async def test_title_page_preserves_stored_and_edge_only_tags(
        self, authenticated_client: AsyncClient, test_user
    ):
        track_id = await self._create_track(authenticated_client)
        stored_id = await self._create_tag(authenticated_client, track_id, "stored")
        edge_id = await self._create_tag(authenticated_client, track_id, "edge-only")
        edge_tag = await Tag.get(edge_id)

        for title, tags in [
            ("A legacy entry", []),
            ("B explicitly tagged", [stored_id]),
            ("C untagged", []),
        ]:
            created = await authenticated_client.post(
                "/api/entries",
                json={"track_id": track_id, "title": title, "tags": tags},
            )
            assert created.status_code == 200, created.text
            if title != "C untagged":
                entry = await Entry.get(created.json()["entry"]["id"])
                await entry.connect(edge_tag, edge=TAGGED_WITH)

        view = await authenticated_client.post(
            f"/api/tracks/{track_id}/views",
            json={
                "name": "Title order",
                "type": "feed",
                "config": {"sort": [{"field": "title", "direction": "asc"}]},
            },
        )
        assert view.status_code in (200, 201), view.text
        data = view.json()
        path = f"/api/tracks/{track_id}/entries"
        params = {
            "view_id": data.get("view", data)["id"],
            "include_total": "false",
            "limit": 2,
        }
        response = await authenticated_client.get(path, params=params)
        assert response.status_code == 200, response.text
        first = response.json()
        assert first["has_more"] is True
        response = await authenticated_client.get(
            path, params={**params, "cursor": first["next_cursor"]}
        )
        assert response.status_code == 200, response.text
        last = response.json()
        assert last["has_more"] is False
        entries = first["entries"] + last["entries"]
        assert [entry["title"] for entry in entries] == [
            "A legacy entry",
            "B explicitly tagged",
            "C untagged",
        ]
        assert {
            entry["title"]: [tag["id"] for tag in entry["tags"]] for entry in entries
        } == {
            "A legacy entry": [edge_id],
            "B explicitly tagged": [stored_id],
            "C untagged": [],
        }

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

    async def test_typed_goal_cannot_be_completed_through_generic_entry_update(
        self, authenticated_client: AsyncClient, test_user
    ):
        from app.models.edges import CONTAINS
        from app.models.nodes import EntryType

        client = authenticated_client
        track_id = await self._create_track(client)
        track = await Track.get(track_id)
        profile = await get_track_attached_operational_model(track)
        goal_type = await EntryType.create(
            track_id=track_id,
            name="Reviewed outcome",
            form_schema={
                "fields": [
                    {
                        "id": "evidence",
                        "key": "evidence",
                        "type": "relation",
                        "relation": {"target": "entry"},
                    }
                ],
                "goal_semantics": {
                    "version": 1,
                    "outcome": {"source": "title"},
                    "criteria": [
                        {
                            "key": "approval",
                            "evidence_relation_field_id": "evidence",
                            "predicate": {
                                "kind": "equals",
                                "field_id": "approved",
                                "value": True,
                            },
                        }
                    ],
                },
            },
        )
        await profile.connect(goal_type, edge=CONTAINS)

        async def create(title, **fields):
            response = await client.post(
                "/api/entries", json={"track_id": track_id, "title": title, **fields}
            )
            assert response.status_code == 200, response.text
            return response.json()["entry"]

        plain = await create("Ordinary Entry")
        goal = await create("Typed goal", type_id=goal_type.id)
        assert goal["type_id"] == goal_type.id
        goal_url = f"/api/entries/{goal['id']}"
        for patch in (
            {"status": "completed"},
            {"status": "completed", "type_id": plain["type_id"]},
        ):
            response = await client.put(goal_url, json=patch)
            assert response.status_code == 409, response.text
            assert response.json()["details"]["error_code"] == "goal_closure_required"
        assert (await client.get(goal_url)).json()["entry"]["status"] == "active"

        plain_url = f"/api/entries/{plain['id']}"
        assert (
            await client.put(plain_url, json={"status": "completed"})
        ).status_code == 200
        retype = await client.put(plain_url, json={"type_id": goal_type.id})
        assert retype.status_code == 409, retype.text
        assert retype.json()["details"]["error_code"] == "goal_closure_required"
        assert (
            await client.put(goal_url, json={"title": "Updated goal"})
        ).status_code == 200
