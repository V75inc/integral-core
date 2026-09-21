"""CRUD tests for Tracks API."""

import uuid

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
class TestTracksCRUD:
    """Test suite for Tracks CRUD operations."""

    async def test_create_track(self, authenticated_client: AsyncClient, test_user):
        """Test creating a new track."""
        track_data = {
            "title": "My Test Track",
            "purpose": "Testing the track creation flow",
            "visibility": "private",
        }

        response = await authenticated_client.post("/api/tracks", json=track_data)

        assert response.status_code == 200
        data = response.json()
        assert "track" in data
        assert data["track"]["title"] == "My Test Track"
        assert data["track"]["visibility"] == "private"
        assert "id" in data["track"]

        return data["track"]["id"]

    async def test_list_tracks(self, authenticated_client: AsyncClient, test_user):
        """Test listing all accessible tracks."""
        # Create a track first
        track_data = {"title": "Test Track", "visibility": "private"}
        await authenticated_client.post("/api/tracks", json=track_data)

        response = await authenticated_client.get("/api/tracks")

        assert response.status_code == 200
        data = response.json()
        assert "tracks" in data
        assert "total" in data
        assert isinstance(data["tracks"], list)
        assert len(data["tracks"]) > 0

    async def test_list_tracks_with_pagination(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test listing tracks with pagination."""
        # Create multiple tracks
        for i in range(5):
            track_data = {"title": f"Track {i}", "visibility": "private"}
            await authenticated_client.post("/api/tracks", json=track_data)

        response = await authenticated_client.get("/api/tracks?limit=2")

        assert response.status_code == 200
        data = response.json()
        assert "tracks" in data
        assert "has_more" in data
        assert len(data["tracks"]) <= 2

    async def test_get_track_by_id(self, authenticated_client: AsyncClient, test_user):
        """Test getting a specific track by ID."""
        # Create a track
        track_data = {"title": "Get Track Test", "visibility": "private"}
        create_response = await authenticated_client.post(
            "/api/tracks", json=track_data
        )
        track_id = create_response.json()["track"]["id"]

        response = await authenticated_client.get(f"/api/tracks/{track_id}")

        assert response.status_code == 200
        data = response.json()
        assert "track" in data
        assert data["track"]["id"] == track_id
        assert data["track"]["title"] == "Get Track Test"

    async def test_get_nonexistent_track(self, authenticated_client: AsyncClient):
        """Test getting a track that doesn't exist."""
        response = await authenticated_client.get("/api/tracks/nonexistent-id")

        assert response.status_code == 404

    async def test_update_track(self, authenticated_client: AsyncClient, test_user):
        """Test updating a track."""
        # Create a track
        track_data = {"title": "Original Title", "visibility": "private"}
        create_response = await authenticated_client.post(
            "/api/tracks", json=track_data
        )
        track_id = create_response.json()["track"]["id"]

        # Update the track
        update_data = {
            "title": "Updated Title",
            "visibility": "public",
        }

        response = await authenticated_client.put(
            f"/api/tracks/{track_id}", json=update_data
        )

        assert response.status_code == 200
        data = response.json()
        assert "track" in data
        assert data["track"]["title"] == "Updated Title"
        assert data["track"]["visibility"] == "public"

    async def test_create_track_with_accent_color(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Custom hex accent is stored on create."""
        response = await authenticated_client.post(
            "/api/tracks",
            json={
                "title": "Accent Track",
                "visibility": "private",
                "accent_color": "#aBc",
            },
        )
        assert response.status_code == 200
        assert response.json()["track"]["accent_color"] == "#aabbcc"

    async def test_update_track_accent_clear(
        self, authenticated_client: AsyncClient, test_user
    ):
        """PUT with empty accent_color clears customization."""
        create_response = await authenticated_client.post(
            "/api/tracks",
            json={
                "title": "Colored",
                "visibility": "private",
                "accent_color": "#ff0000",
            },
        )
        track_id = create_response.json()["track"]["id"]
        assert create_response.json()["track"]["accent_color"] == "#ff0000"

        clear_response = await authenticated_client.put(
            f"/api/tracks/{track_id}",
            json={"accent_color": ""},
        )
        assert clear_response.status_code == 200
        assert clear_response.json()["track"]["accent_color"] == ""

    async def test_create_track_invalid_accent_rejected(
        self, authenticated_client: AsyncClient, test_user
    ):
        response = await authenticated_client.post(
            "/api/tracks",
            json={
                "title": "Bad color",
                "visibility": "private",
                "accent_color": "red",
            },
        )
        assert response.status_code == 400

    async def test_delete_track(self, authenticated_client: AsyncClient, test_user):
        """Test deleting a track."""
        # Create a track
        track_data = {"title": "Track to Delete", "visibility": "private"}
        create_response = await authenticated_client.post(
            "/api/tracks", json=track_data
        )
        track_id = create_response.json()["track"]["id"]

        # Delete the track
        response = await authenticated_client.delete(f"/api/tracks/{track_id}")

        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "deleted_track_id" in data

        # Verify track is deleted
        get_response = await authenticated_client.get(f"/api/tracks/{track_id}")
        assert get_response.status_code == 404

    async def test_get_track_entries(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test getting entries in a track."""
        # Create a track
        track_data = {"title": "Track with Entries", "visibility": "private"}
        create_response = await authenticated_client.post(
            "/api/tracks", json=track_data
        )
        track_id = create_response.json()["track"]["id"]

        # Get entries
        response = await authenticated_client.get(f"/api/tracks/{track_id}/entries")

        assert response.status_code == 200
        data = response.json()
        assert "entries" in data
        assert "total" in data
        assert isinstance(data["entries"], list)

    async def test_get_track_entries_with_query(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Track entries support case-insensitive substring search via ``q``."""
        create_response = await authenticated_client.post(
            "/api/tracks",
            json={"title": "Track for Query", "visibility": "private"},
        )
        track_id = create_response.json()["track"]["id"]

        for title, body in [
            ("Apple Pie", "sweet dessert"),
            ("Banana Bread", "Has WALNUTS inside"),
            ("Cherry Tart", "tangy filling"),
        ]:
            r = await authenticated_client.post(
                "/api/entries",
                json={"track_id": track_id, "title": title, "body": body},
            )
            assert r.status_code == 200, r.text

        # Title match (case-insensitive)
        response = await authenticated_client.get(
            f"/api/tracks/{track_id}/entries", params={"q": "BANANA"}
        )
        assert response.status_code == 200
        titles = [e["title"] for e in response.json()["entries"]]
        assert titles == ["Banana Bread"]

        # Body match (case-insensitive)
        response = await authenticated_client.get(
            f"/api/tracks/{track_id}/entries", params={"q": "walnuts"}
        )
        assert response.status_code == 200
        titles = [e["title"] for e in response.json()["entries"]]
        assert titles == ["Banana Bread"]

        # No match → empty
        response = await authenticated_client.get(
            f"/api/tracks/{track_id}/entries", params={"q": "xyzzy"}
        )
        assert response.status_code == 200
        assert response.json()["entries"] == []

    async def test_add_collaborator(
        self, authenticated_client: AsyncClient, test_user, test_user2
    ):
        """Test adding a collaborator to a track."""
        # Create a track
        track_data = {"title": "Collaborative Track", "visibility": "private"}
        create_response = await authenticated_client.post(
            "/api/tracks", json=track_data
        )
        track_id = create_response.json()["track"]["id"]

        # Add collaborator
        collaborator_data = {
            "collaborator_user_id": test_user2.id,
            "role": "editor",
        }

        response = await authenticated_client.post(
            f"/api/tracks/{track_id}/collaborators", json=collaborator_data
        )

        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert data["collaborator_user_id"] == test_user2.id

    async def test_space_cascade_grants_track_access(
        self,
        authenticated_client: AsyncClient,
        test_user,
        test_user2,
        second_user_client,
    ):
        """App collaborators inherit access to all tracks in the app_node."""
        # Owner creates space and a track inside it. Track visibility is
        # currently inert for cascade; inheritance is controlled by parent
        # grants and per-user exclusions.
        app_name = f"Cascade App {uuid.uuid4().hex[:8]}"
        sp_resp = await authenticated_client.post(
            "/api/apps", json={"name": app_name, "visibility": "private"}
        )
        assert sp_resp.status_code == 200, sp_resp.text
        app_id = sp_resp.json()["app"]["id"]

        track_title = f"Cascade Track {uuid.uuid4().hex[:8]}"
        tr_resp = await authenticated_client.post(
            "/api/tracks",
            json={
                "title": track_title,
                "app_id": app_id,
            },
        )
        assert tr_resp.status_code == 200, tr_resp.text
        track_id = tr_resp.json()["track"]["id"]

        # second_user has no access yet.
        deny = await second_user_client.get(f"/api/tracks/{track_id}")
        assert deny.status_code in (403, 404), deny.text

        # Add second_user as a SPACE collaborator — not a track collaborator.
        add = await authenticated_client.post(
            f"/api/apps/{app_id}/collaborators",
            json={"collaborator_user_id": test_user2.id, "role": "viewer"},
        )
        assert add.status_code == 200, add.text

        # second_user should now see the track via cascade.
        ok = await second_user_client.get(f"/api/tracks/{track_id}")
        assert ok.status_code == 200, ok.text

    async def test_exclusion_blocks_inherited_track_access(
        self,
        authenticated_client: AsyncClient,
        test_user,
        test_user2,
        second_user_client,
    ):
        """EXCLUDED_FROM edge denies inherited access; direct access still wins."""
        app_name = f"Excl App {uuid.uuid4().hex[:8]}"
        sp_resp = await authenticated_client.post(
            "/api/apps", json={"name": app_name, "visibility": "private"}
        )
        assert sp_resp.status_code == 200, sp_resp.text
        app_id = sp_resp.json()["app"]["id"]
        # Default visibility ("inherit") so the App cascade reaches the
        # track; the test then asserts EXCLUDED_FROM blocks that cascade.
        tr_resp = await authenticated_client.post(
            "/api/tracks",
            json={
                "title": f"Excl Track {uuid.uuid4().hex[:8]}",
                "app_id": app_id,
            },
        )
        assert tr_resp.status_code == 200, tr_resp.text
        track_id = tr_resp.json()["track"]["id"]
        add = await authenticated_client.post(
            f"/api/apps/{app_id}/collaborators",
            json={"collaborator_user_id": test_user2.id, "role": "viewer"},
        )
        assert add.status_code == 200, add.text

        # Confirm inherited access is in place.
        ok = await second_user_client.get(f"/api/tracks/{track_id}")
        assert ok.status_code == 200, ok.text

        # Exclude second_user from this specific track.
        excl = await authenticated_client.post(
            f"/api/tracks/{track_id}/exclusions",
            json={"user_id_to_exclude": test_user2.id, "reason": "test"},
        )
        assert excl.status_code == 200, excl.text

        # Access is now blocked despite App cascade.
        denied = await second_user_client.get(f"/api/tracks/{track_id}")
        assert denied.status_code in (403, 404), denied.text

        # Restore: exclusion removed → access returns.
        restored = await authenticated_client.delete(
            f"/api/tracks/{track_id}/exclusions/{test_user2.id}"
        )
        assert restored.status_code == 200, restored.text
        ok2 = await second_user_client.get(f"/api/tracks/{track_id}")
        assert ok2.status_code == 200, ok2.text

    async def test_exclusion_does_not_block_direct_collaborator(
        self,
        authenticated_client: AsyncClient,
        test_user,
        test_user2,
        second_user_client,
    ):
        """Direct COLLABORATES_ON beats any EXCLUDED_FROM edge."""
        tr_resp = await authenticated_client.post(
            "/api/tracks", json={"title": "Direct Beats", "visibility": "private"}
        )
        track_id = tr_resp.json()["track"]["id"]
        await authenticated_client.post(
            f"/api/tracks/{track_id}/collaborators",
            json={"collaborator_user_id": test_user2.id, "role": "editor"},
        )

        # Even if an exclusion edge somehow exists, direct collab wins.
        await authenticated_client.post(
            f"/api/tracks/{track_id}/exclusions",
            json={"user_id_to_exclude": test_user2.id},
        )
        ok = await second_user_client.get(f"/api/tracks/{track_id}")
        assert ok.status_code == 200, ok.text

    async def test_list_collaborators_includes_inherited_and_excluded(
        self,
        authenticated_client: AsyncClient,
        test_user,
        test_user2,
    ):
        """list_collaborators surfaces space-inherited rows with source + excluded flag."""
        sp_resp = await authenticated_client.post(
            "/api/apps", json={"name": "Listing App", "visibility": "private"}
        )
        app_id = sp_resp.json()["app"]["id"]
        tr_resp = await authenticated_client.post(
            "/api/tracks",
            json={
                "title": "Listing Track",
                "visibility": "private",
                "app_id": app_id,
            },
        )
        track_id = tr_resp.json()["track"]["id"]
        await authenticated_client.post(
            f"/api/apps/{app_id}/collaborators",
            json={"collaborator_user_id": test_user2.id, "role": "viewer"},
        )

        r = await authenticated_client.get(f"/api/tracks/{track_id}/collaborators")
        assert r.status_code == 200, r.text
        body = r.json()
        rows_by_id = {row["id"]: row for row in body["collaborators"]}
        assert test_user2.id in rows_by_id
        inherited = rows_by_id[test_user2.id]
        assert inherited["source"] == "app"
        assert inherited["source_app_id"] == app_id
        assert inherited["excluded"] is False
        assert inherited["effective_access"] is True
        assert body["effective_total"] >= 2  # owner + inherited

        # Exclude and re-list.
        await authenticated_client.post(
            f"/api/tracks/{track_id}/exclusions",
            json={"user_id_to_exclude": test_user2.id},
        )
        r2 = await authenticated_client.get(f"/api/tracks/{track_id}/collaborators")
        body2 = r2.json()
        rows2 = {row["id"]: row for row in body2["collaborators"]}
        assert rows2[test_user2.id]["excluded"] is True
        assert rows2[test_user2.id]["effective_access"] is False

    async def test_remove_collaborator(
        self, authenticated_client: AsyncClient, test_user, test_user2
    ):
        """Test removing a collaborator from a track."""
        # Create a track
        track_data = {"title": "Track for Removal", "visibility": "private"}
        create_response = await authenticated_client.post(
            "/api/tracks", json=track_data
        )
        track_id = create_response.json()["track"]["id"]

        # Add collaborator first
        collaborator_data = {
            "collaborator_user_id": test_user2.id,
            "role": "editor",
        }
        await authenticated_client.post(
            f"/api/tracks/{track_id}/collaborators", json=collaborator_data
        )

        # Remove collaborator
        response = await authenticated_client.delete(
            f"/api/tracks/{track_id}/collaborators/{test_user2.id}"
        )

        assert response.status_code == 200
        data = response.json()
        assert "message" in data

    async def test_create_track_with_template(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test creating a track from a template."""
        track_data = {
            "title": "Templated Track",
            "visibility": "private",
            "template_id": "template-123",
        }

        response = await authenticated_client.post("/api/tracks", json=track_data)

        # Should succeed even if template doesn't exist
        assert response.status_code in [200, 404]
        if response.status_code == 200:
            data = response.json()
            assert "track" in data

    async def test_track_library_merge_updates_attached_manifest(
        self, authenticated_client: AsyncClient, test_user
    ):
        packs = await authenticated_client.get("/api/operational-models")
        assert packs.status_code == 200
        profiles = packs.json().get("operational_models") or []
        if not profiles:
            pytest.skip("No seeded library packages found")
        lib = profiles[0]
        create_resp = await authenticated_client.post(
            "/api/tracks",
            json={"title": "Manifest Merge Track", "visibility": "private"},
        )
        assert create_resp.status_code == 200
        tid = create_resp.json()["track"]["id"]
        merge_resp = await authenticated_client.post(
            f"/api/tracks/{tid}/operational-model/merge-library",
            json={"library_operational_model_id": lib["id"]},
        )
        assert merge_resp.status_code == 200, merge_resp.text
        cp_resp = await authenticated_client.get(f"/api/tracks/{tid}/operational-model")
        assert cp_resp.status_code == 200, cp_resp.text
        cp = cp_resp.json().get("operational_model") or {}
        manifest = cp.get("manifest") or {}
        assert manifest.get("scope") == "track"
        track_tier = manifest.get("track") or {}
        assert isinstance(track_tier.get("entry_types"), list)

    async def test_validate_operational_model_manifest_endpoint(
        self, authenticated_client: AsyncClient, test_user
    ):
        good_manifest = {
            "operational_model_schema_version": 2,
            "scope": "track",
            "package": {
                "name": "qa-pack",
                "capabilities": ["table"],
                "dependencies": [{"id": "dep-a", "version": "^1.0.0"}],
            },
            "track": {
                "entry_types": [{"key": "item", "name": "Item", "fields": []}],
                "views": [{"key": "all", "name": "All", "view_type": "table"}],
                "taxonomy": {"tag_groups": []},
            },
        }
        ok = await authenticated_client.post(
            "/api/operational-models/validate", json={"manifest": good_manifest}
        )
        assert ok.status_code == 200
        assert ok.json().get("valid") is True
        bad = await authenticated_client.post(
            "/api/operational-models/validate",
            json={
                "manifest": {
                    "operational_model_schema_version": 2,
                    "scope": "track",
                    "package": {"capabilities": ["unknown-widget"]},
                    "track": {
                        "entry_types": [],
                        "views": [],
                        "taxonomy": {"tag_groups": []},
                    },
                }
            },
        )
        assert bad.status_code == 200
        assert bad.json().get("valid") is False

    async def test_publish_update_delete_org_operational_model_package(
        self, authenticated_client: AsyncClient, test_user
    ):
        org_resp = await authenticated_client.post(
            "/api/workspaces", json={"name": "Profiles Org"}
        )
        assert org_resp.status_code == 200
        org_id = org_resp.json()["workspace"]["id"]
        manifest = {
            "operational_model_schema_version": 2,
            "scope": "track",
            "package": {"name": "org-private"},
            "track": {
                "entry_types": [{"key": "record", "name": "Record", "fields": []}],
                "views": [{"key": "table", "name": "Table", "view_type": "table"}],
                "taxonomy": {"tag_groups": []},
            },
        }
        pub = await authenticated_client.post(
            "/api/operational-models",
            json={
                "name": "Org Private Pack",
                "workspace_id": org_id,
                "manifest": manifest,
            },
        )
        assert pub.status_code == 200, pub.text
        cp_id = pub.json()["operational_model"]["id"]
        listed = await authenticated_client.get(
            f"/api/workspaces/{org_id}/operational-models"
        )
        assert listed.status_code == 200
        ids = [p["id"] for p in listed.json().get("operational_models", [])]
        assert cp_id in ids
        upd = await authenticated_client.put(
            f"/api/operational-models/{cp_id}",
            json={"description": "Updated"},
        )
        assert upd.status_code == 200
        assert upd.json()["operational_model"]["description"] == "Updated"
        delete = await authenticated_client.delete(f"/api/operational-models/{cp_id}")
        assert delete.status_code == 200

    async def test_track_entries_include_comment_count(
        self, authenticated_client: AsyncClient, test_user
    ):
        """``GET /tracks/{id}/entries`` includes ``comment_count`` per entry."""
        create = await authenticated_client.post(
            "/api/tracks",
            json={"title": "Entries comment count", "visibility": "private"},
        )
        assert create.status_code == 200
        track_id = create.json()["track"]["id"]
        entry_resp = await authenticated_client.post(
            "/api/entries",
            json={"track_id": track_id, "title": "Commented post"},
        )
        assert entry_resp.status_code == 200
        entry_id = entry_resp.json()["entry"]["id"]
        await authenticated_client.post(
            f"/api/entries/{entry_id}/comments", json={"text": "one"}
        )

        list_resp = await authenticated_client.get(f"/api/tracks/{track_id}/entries")
        assert list_resp.status_code == 200
        entries = list_resp.json()["entries"]
        row = next(e for e in entries if e["id"] == entry_id)
        assert row.get("comment_count") == 1
