"""CRUD tests for Entry Types API (track-scoped)."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
class TestEntryTypesCRUD:
    """Test suite for Entry Types CRUD operations."""

    async def _create_track(self, client: AsyncClient, title: str = "ET Track"):
        response = await client.post(
            "/api/tracks", json={"title": title, "visibility": "private"}
        )
        assert response.status_code == 200, f"Track creation failed: {response.text}"
        return response.json()["track"]["id"]

    async def _create_entry_type(self, client: AsyncClient, track_id: str, name: str):
        response = await client.post(
            "/api/entry-types",
            json={"name": name, "icon": "document", "track_id": track_id},
        )
        assert (
            response.status_code == 200
        ), f"Entry type creation failed: {response.text}"
        return response.json()["entry_type"]

    async def test_create_entry_type(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test creating a new track-scoped entry type."""
        track_id = await self._create_track(authenticated_client)

        response = await authenticated_client.post(
            "/api/entry-types",
            json={
                "name": "custom-note",
                "icon": "document",
                "track_id": track_id,
                "form_schema": {
                    "fields": [{"key": "priority", "name": "priority", "type": "text"}]
                },
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert "entry_type" in data
        assert data["entry_type"]["name"] == "custom-note"
        assert data["entry_type"]["track_id"] == track_id
        assert "id" in data["entry_type"]

    async def test_create_duplicate_entry_type_same_track(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test that creating duplicate entry types in the same track fails."""
        track_id = await self._create_track(authenticated_client, "Dup ET Track")
        payload = {"name": "dup-type", "icon": "document", "track_id": track_id}

        await authenticated_client.post("/api/entry-types", json=payload)
        response = await authenticated_client.post("/api/entry-types", json=payload)

        assert response.status_code == 409

    async def test_list_entry_types(self, authenticated_client: AsyncClient, test_user):
        """Test listing all entry types."""
        track_id = await self._create_track(authenticated_client, "List ET Track")
        await self._create_entry_type(authenticated_client, track_id, "list-test-type")

        response = await authenticated_client.get("/api/entry-types")

        assert response.status_code == 200
        data = response.json()
        assert "entry_types" in data
        assert data["total"] > 0

    async def test_list_entry_types_scoped_to_track(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test listing entry types filtered by track."""
        track_id = await self._create_track(authenticated_client, "Scoped ET Track")
        await self._create_entry_type(authenticated_client, track_id, "scoped-type")

        response = await authenticated_client.get(
            f"/api/entry-types?track_id={track_id}"
        )

        assert response.status_code == 200
        data = response.json()
        assert all(et["track_id"] == track_id for et in data["entry_types"])

    async def test_list_entry_types_exposes_manifest_key(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Each entry type carries a `key` an agent can pass as type_hint.

        EntryType nodes have no top-level ``key`` attribute — regression for
        a live bug where the resident asked the user to disambiguate an
        entry type because the schema response never surfaced one. A type
        created via this plain CRUD path (no manifest key embedded) falls
        back to a slug of its display name.
        """
        track_id = await self._create_track(authenticated_client, "Key ET Track")
        await self._create_entry_type(authenticated_client, track_id, "Pay Run")

        response = await authenticated_client.get(
            f"/api/entry-types?track_id={track_id}"
        )
        assert response.status_code == 200
        data = response.json()
        et = next(e for e in data["entry_types"] if e["name"] == "Pay Run")
        assert et["key"] == "pay_run", et

    async def test_get_entry_type_by_id(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test getting a specific entry type by ID."""
        track_id = await self._create_track(authenticated_client, "Get ET Track")
        et = await self._create_entry_type(
            authenticated_client, track_id, "get-test-type"
        )
        et_id = et["id"]

        response = await authenticated_client.get(f"/api/entry-types/{et_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["entry_type"]["id"] == et_id
        assert data["entry_type"]["name"] == "get-test-type"

    async def test_get_nonexistent_entry_type(self, authenticated_client: AsyncClient):
        """Test getting an entry type that doesn't exist."""
        response = await authenticated_client.get("/api/entry-types/nonexistent-id")
        assert response.status_code == 404

    async def test_update_entry_type(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test updating an entry type."""
        track_id = await self._create_track(authenticated_client, "Update ET Track")
        et = await self._create_entry_type(
            authenticated_client, track_id, "update-test-type"
        )
        et_id = et["id"]

        response = await authenticated_client.put(
            f"/api/entry-types/{et_id}",
            json={"name": "updated-type", "icon": "star"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["entry_type"]["name"] == "updated-type"
        assert data["entry_type"]["icon"] == "star"

    async def test_delete_entry_type(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test deleting an entry type."""
        track_id = await self._create_track(authenticated_client, "Delete ET Track")
        et = await self._create_entry_type(
            authenticated_client, track_id, "delete-test-type"
        )
        et_id = et["id"]

        response = await authenticated_client.delete(f"/api/entry-types/{et_id}")

        assert response.status_code == 200
        data = response.json()
        assert "deleted_entry_type_id" in data

        get_response = await authenticated_client.get(f"/api/entry-types/{et_id}")
        assert get_response.status_code == 404

    async def test_create_entry_type_with_complex_schema(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test creating an entry type with a complex form schema."""
        track_id = await self._create_track(authenticated_client, "Complex ET Track")
        complex_schema = {
            "fields": [
                {
                    "key": "priority",
                    "name": "Priority",
                    "type": "select",
                    "required": True,
                    "enum": ["low", "high"],
                }
            ]
        }

        response = await authenticated_client.post(
            "/api/entry-types",
            json={
                "name": "complex-type",
                "icon": "task",
                "track_id": track_id,
                "form_schema": complex_schema,
            },
        )

        assert response.status_code == 200
        data = response.json()
        fs = data["entry_type"]["form_schema"]
        assert fs["fields"][0]["key"] == "priority"
        assert fs["fields"][0]["type"] == "select"
        assert fs["fields"][0]["enum"] == ["low", "high"]

    async def test_entry_type_base_fields_defaults_and_overrides(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Entry type form schema should normalize base fields."""
        track_id = await self._create_track(
            authenticated_client, "Base Fields ET Track"
        )
        response = await authenticated_client.post(
            "/api/entry-types",
            json={
                "name": "interaction-note",
                "icon": "document",
                "track_id": track_id,
                "form_schema": {
                    "fields": [],
                    "base_fields": {
                        "title": {
                            "label": "Subject line",
                            "placeholder": "What this note is about",
                        },
                        "body": {
                            "label": "Outcome",
                            "placeholder": "Capture the interaction outcome",
                        },
                        "attachments": {
                            "label": "References",
                            "allow_url_reference": True,
                            "allow_file_upload": False,
                        },
                    },
                },
            },
        )
        assert response.status_code == 200
        fs = response.json()["entry_type"]["form_schema"]
        assert fs["base_fields"]["title"]["enabled"] is True
        assert fs["base_fields"]["title"]["label"] == "Subject line"
        assert fs["base_fields"]["title"]["placeholder"] == "What this note is about"
        assert fs["base_fields"]["body"]["enabled"] is True
        assert fs["base_fields"]["body"]["label"] == "Outcome"
        assert fs["base_fields"]["attachments"]["label"] == "References"
        assert fs["base_fields"]["attachments"]["allow_file_upload"] is False
        assert fs["base_fields"]["attachments"]["allow_url_reference"] is True

    async def test_entry_type_base_fields_order(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Optional base_fields body/attachments order is normalized."""
        track_id = await self._create_track(authenticated_client, "Base Order ET Track")
        response = await authenticated_client.post(
            "/api/entry-types",
            json={
                "name": "ordered-base",
                "icon": "document",
                "track_id": track_id,
                "form_schema": {
                    "fields": [],
                    "base_fields": {
                        "body": {"order": 750},
                        "attachments": {"order": 800},
                    },
                },
            },
        )
        assert response.status_code == 200
        fs = response.json()["entry_type"]["form_schema"]
        assert fs["base_fields"]["title"]["enabled"] is True
        assert fs["base_fields"]["title"]["label"] == "Title"
        assert fs["base_fields"]["body"]["order"] == 750
        assert fs["base_fields"]["attachments"]["order"] == 800

    async def test_remove_field_preserves_entry_custom_field_data(
        self, authenticated_client: AsyncClient, test_user
    ):
        """I-SCHEMA-SOFT-DELETE: removing a field from form_schema.fields
        does NOT touch Entry.custom_fields data on existing entries."""
        track_id = await self._create_track(
            authenticated_client, title="Soft Delete Track"
        )

        # 1. Create entry type with a `priority` field
        et_resp = await authenticated_client.post(
            "/api/entry-types",
            json={
                "name": "note",
                "icon": "document",
                "track_id": track_id,
                "form_schema": {
                    "fields": [
                        {"key": "priority", "name": "priority", "type": "text"},
                        {"key": "owner", "name": "owner", "type": "text"},
                    ]
                },
            },
        )
        assert et_resp.status_code == 200
        entry_type = et_resp.json()["entry_type"]

        # 2. Create an entry with values in both fields
        entry_resp = await authenticated_client.post(
            "/api/entries",
            json={
                "title": "Triage",
                "track_id": track_id,
                "type_id": entry_type["id"],
                "custom_fields": {"priority": "high", "owner": "ada"},
            },
        )
        assert entry_resp.status_code == 200
        entry_id = entry_resp.json()["entry"]["id"]

        # 3. Remove `priority` from the entry type's form_schema
        upd_resp = await authenticated_client.put(
            f"/api/entry-types/{entry_type['id']}",
            json={
                "form_schema": {
                    "fields": [
                        {"key": "owner", "name": "owner", "type": "text"},
                    ]
                }
            },
        )
        assert upd_resp.status_code == 200
        updated = upd_resp.json()["entry_type"]
        assert [f["key"] for f in updated["form_schema"]["fields"]] == ["owner"]

        # 4. Reload the entry — priority data MUST still be present
        get_entry = await authenticated_client.get(f"/api/entries/{entry_id}")
        assert get_entry.status_code == 200
        entry = get_entry.json()["entry"]
        assert (
            entry["custom_fields"].get("priority") == "high"
        ), "Soft-delete invariant violated: priority data was removed from entry"
        assert entry["custom_fields"].get("owner") == "ada"

    async def test_reorder_fields_persists(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Reordering rewrites `order` integers and survives a reload."""
        track_id = await self._create_track(authenticated_client, title="Reorder Track")
        et_resp = await authenticated_client.post(
            "/api/entry-types",
            json={
                "name": "task",
                "icon": "check",
                "track_id": track_id,
                "form_schema": {
                    "fields": [
                        {"key": "a", "name": "A", "type": "text", "order": 0},
                        {"key": "b", "name": "B", "type": "text", "order": 1},
                        {"key": "c", "name": "C", "type": "text", "order": 2},
                    ]
                },
            },
        )
        entry_type = et_resp.json()["entry_type"]

        upd = await authenticated_client.put(
            f"/api/entry-types/{entry_type['id']}",
            json={
                "form_schema": {
                    "fields": [
                        {"key": "c", "name": "C", "type": "text", "order": 0},
                        {"key": "a", "name": "A", "type": "text", "order": 1},
                        {"key": "b", "name": "B", "type": "text", "order": 2},
                    ]
                }
            },
        )
        assert upd.status_code == 200

        reloaded = await authenticated_client.get(
            f"/api/entry-types/{entry_type['id']}"
        )
        keys_in_order = [
            f["key"]
            for f in sorted(
                reloaded.json()["entry_type"]["form_schema"]["fields"],
                key=lambda f: f.get("order", 0),
            )
        ]
        assert keys_in_order == ["c", "a", "b"]


def test_form_schema_from_entry_type_spec_merges_top_level_base_fields():
    """Library manifests use top-level ``fields`` + ``base_fields`` (not only ``form_schema``)."""
    from app.services.content_profile_merge import _form_schema_from_entry_type_spec

    spec = {
        "key": "contact",
        "name": "Contact",
        "fields": [{"key": "email", "name": "Email", "type": "text"}],
        "base_fields": {
            "title": {"label": "Contact name", "placeholder": "Full name"},
            "body": {"label": "Notes"},
        },
    }
    fs = _form_schema_from_entry_type_spec(spec)
    assert len(fs["fields"]) == 1
    assert fs["fields"][0]["key"] == "email"
    assert fs["base_fields"]["title"]["label"] == "Contact name"
    assert fs["base_fields"]["body"]["label"] == "Notes"
