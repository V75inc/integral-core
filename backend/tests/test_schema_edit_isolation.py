"""I-SCHEMA-EDIT-ISOLATION-01:
Field edits on a track-attached ContentProfile MUST NOT propagate to
(a) the library package the attachment was derived from, or
(b) any sibling track that derived from the same library package.

The graph topology already guarantees this — ``merge-library`` materializes
a fresh attached ContentProfile node with its own ``CONTAINS → EntryType``
subgraph, while the library ContentProfile remains untouched as a separate
node under the ``ContentProfiles`` registry. This test locks that invariant
against accidental regression (e.g., a future "edit the library in place"
shortcut).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


def _track_manifest() -> dict:
    return {
        "content_profile_schema_version": 2,
        "scope": "track",
        "package": {"name": "schema-edit-isolation-pkg"},
        "track": {
            "entry_types": [
                {
                    "key": "item",
                    "name": "Item",
                    "icon": "document",
                    "fields": [
                        {"key": "priority", "name": "priority", "type": "text"},
                        {"key": "owner", "name": "owner", "type": "text"},
                    ],
                }
            ],
            "views": [
                {
                    "key": "library_feed",
                    "name": "Library Feed",
                    "view_type": "feed",
                }
            ],
            "taxonomy": {"tag_groups": []},
        },
    }


@pytest.mark.asyncio
class TestSchemaEditIsolation:
    async def _publish_library_profile(
        self, client: AsyncClient, workspace_id: str
    ) -> str:
        """Publish a minimal track-scope library package with one entry type."""
        resp = await client.post(
            "/api/content-profiles",
            json={
                "name": "Iso-Test Pack",
                "workspace_id": workspace_id,
                "manifest": _track_manifest(),
            },
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["content_profile"]["id"]

    async def _create_track(self, client: AsyncClient, title: str) -> str:
        r = await client.post(
            "/api/tracks", json={"title": title, "visibility": "private"}
        )
        assert r.status_code == 200, r.text
        return r.json()["track"]["id"]

    async def _merge_library_into_track(
        self, client: AsyncClient, track_id: str, library_cp_id: str
    ) -> None:
        r = await client.post(
            f"/api/tracks/{track_id}/content-profile/merge-library",
            json={"library_content_profile_id": library_cp_id},
        )
        assert r.status_code == 200, r.text

    async def _list_entry_types(self, client: AsyncClient, track_id: str) -> list[dict]:
        r = await client.get("/api/entry-types", params={"track_id": track_id})
        assert r.status_code == 200, r.text
        return r.json().get("entry_types", [])

    @staticmethod
    def _field_keys(entry_type: dict) -> list[str]:
        return sorted(
            f["key"] for f in (entry_type.get("form_schema") or {}).get("fields", [])
        )

    @staticmethod
    def _library_field_keys(library_cp: dict) -> list[str]:
        manifest = library_cp["manifest"]
        return sorted(f["key"] for f in manifest["track"]["entry_types"][0]["fields"])

    async def test_field_edit_does_not_mutate_library_or_siblings(
        self, authenticated_client: AsyncClient, test_user
    ):
        # 1. Create a fresh workspace (mirrors pattern from sibling tests).
        ws = await authenticated_client.post(
            "/api/workspaces", json={"name": "Iso-Test Ws"}
        )
        assert ws.status_code == 200, ws.text
        workspace_id = ws.json()["workspace"]["id"]

        # 2. Publish a library track-scope ContentProfile.
        library_cp_id = await self._publish_library_profile(
            authenticated_client, workspace_id
        )

        # 3. Create two tracks; merge the library into each.
        track_a = await self._create_track(authenticated_client, "Track A")
        track_b = await self._create_track(authenticated_client, "Track B")
        await self._merge_library_into_track(
            authenticated_client, track_a, library_cp_id
        )
        await self._merge_library_into_track(
            authenticated_client, track_b, library_cp_id
        )

        # 4. Snapshot library manifest BEFORE edit.
        before = await authenticated_client.get(
            f"/api/content-profiles/{library_cp_id}"
        )
        assert before.status_code == 200, before.text
        lib_before_keys = self._library_field_keys(before.json()["content_profile"])

        # 5. Snapshot track B's entry-type field keys BEFORE edit on track A.
        b_ets_before = await self._list_entry_types(authenticated_client, track_b)
        assert b_ets_before, "track B should have an entry type from the merge"
        b_before_keys = self._field_keys(b_ets_before[0])

        # 6. Mutate track A's attached entry type — add a field.
        a_ets = await self._list_entry_types(authenticated_client, track_a)
        assert a_ets, "track A should have an entry type from the merge"
        a_et_id = a_ets[0]["id"]
        a_fields = list((a_ets[0].get("form_schema") or {}).get("fields", []))
        a_fields.append(
            {"key": "custom_a_only", "name": "custom_a_only", "type": "text"}
        )
        upd = await authenticated_client.put(
            f"/api/entry-types/{a_et_id}",
            json={"form_schema": {"fields": a_fields}},
        )
        assert upd.status_code == 200, upd.text

        # 7. Assert library manifest unchanged.
        after = await authenticated_client.get(f"/api/content-profiles/{library_cp_id}")
        assert after.status_code == 200, after.text
        lib_after_keys = self._library_field_keys(after.json()["content_profile"])
        assert (
            lib_after_keys == lib_before_keys
        ), f"Library manifest mutated: {lib_before_keys} -> {lib_after_keys}"

        # 8. Assert track B unchanged.
        b_ets_after = await self._list_entry_types(authenticated_client, track_b)
        b_after_keys = self._field_keys(b_ets_after[0])
        assert (
            b_after_keys == b_before_keys
        ), f"Sibling track B mutated: {b_before_keys} -> {b_after_keys}"

        # 9. Sanity — track A actually got the new field.
        a_ets_after = await self._list_entry_types(authenticated_client, track_a)
        a_after_keys = self._field_keys(a_ets_after[0])
        assert (
            "custom_a_only" in a_after_keys
        ), f"track A did not receive the new field: {a_after_keys}"
