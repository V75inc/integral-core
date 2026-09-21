"""Plan 07-02 — Coverage for POST /api/tracks/{track_id}/operational-model/detach-library.

Locks the existing track-side detach endpoint at
``backend/app/api/operational_models.py:665``. Plan 07-02 does NOT change
behavior on this handler — these tests close the zero-coverage gap.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


def _track_manifest(name: str = "detach-track-pkg") -> dict:
    return {
        "operational_model_schema_version": 2,
        "scope": "track",
        "package": {"name": name},
        "track": {
            "entry_types": [
                {"key": "task", "name": "Task", "fields": []},
            ],
            "views": [
                {"key": "feed", "name": "Feed", "view_type": "feed"},
            ],
            "taxonomy": {"tag_groups": []},
        },
    }


async def _seed_track_with_library(
    authenticated_client: AsyncClient,
) -> tuple[str, str]:
    """Returns (track_id, library_id) — the Track has had the library merged in."""
    ws = await authenticated_client.post(
        "/api/workspaces", json={"name": "Detach-track Ws"}
    )
    assert ws.status_code == 200, ws.text
    ws_id = ws.json()["workspace"]["id"]
    pub = await authenticated_client.post(
        "/api/operational-models",
        json={
            "name": "Detach-track Pack",
            "workspace_id": ws_id,
            "manifest": _track_manifest(),
        },
    )
    assert pub.status_code == 200, pub.text
    lib_id = pub.json()["operational_model"]["id"]
    tr = await authenticated_client.post(
        "/api/tracks", json={"title": "Detach Track", "visibility": "private"}
    )
    assert tr.status_code == 200, tr.text
    tid = tr.json()["track"]["id"]
    merge = await authenticated_client.post(
        f"/api/tracks/{tid}/operational-model/merge-library",
        json={"library_operational_model_id": lib_id},
    )
    assert merge.status_code == 200, merge.text
    return tid, lib_id


@pytest.mark.asyncio
class TestDetachLibraryFromTrack:
    """LIB-03: POST /api/tracks/{track_id}/operational-model/detach-library."""

    async def test_happy_path_drops_library_merge_source_id(
        self, authenticated_client: AsyncClient, test_user
    ):
        tid, lib_id = await _seed_track_with_library(authenticated_client)
        # Pre-detach
        get_tr = await authenticated_client.get(f"/api/tracks/{tid}")
        assert get_tr.status_code == 200
        assert (get_tr.json()["track"].get("library_merge_source_id") or "") == lib_id

        resp = await authenticated_client.post(
            f"/api/tracks/{tid}/operational-model/detach-library", json={}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["track_id"] == tid
        assert "detached" in body["message"].lower()

        get_tr2 = await authenticated_client.get(f"/api/tracks/{tid}")
        assert not (get_tr2.json()["track"].get("library_merge_source_id") or "")

    async def test_idempotent_when_no_library_provenance(
        self, authenticated_client: AsyncClient, test_user
    ):
        tr = await authenticated_client.post(
            "/api/tracks", json={"title": "No-prov Detach", "visibility": "private"}
        )
        assert tr.status_code == 200
        tid = tr.json()["track"]["id"]

        resp = await authenticated_client.post(
            f"/api/tracks/{tid}/operational-model/detach-library", json={}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "no library provenance" in body["message"].lower()
        assert body["track_id"] == tid

    async def test_403_when_caller_lacks_track_update(
        self,
        authenticated_client: AsyncClient,
        second_user_client: AsyncClient,
        test_user,
    ):
        tid, _lib_id = await _seed_track_with_library(authenticated_client)
        resp = await second_user_client.post(
            f"/api/tracks/{tid}/operational-model/detach-library", json={}
        )
        assert resp.status_code in (403, 404), resp.text

    async def test_detach_then_remerge_does_not_duplicate_entry_types(
        self, authenticated_client: AsyncClient, test_user
    ):
        """07-RESEARCH Pitfall 5: detach drops provenance only; re-merging the
        same library package MUST be idempotent at the entry-type tier."""
        tid, lib_id = await _seed_track_with_library(authenticated_client)

        # Capture the current entry-type key set.
        cp1 = await authenticated_client.get(f"/api/tracks/{tid}/operational-model")
        assert cp1.status_code == 200
        keys_before = sorted(
            et["key"]
            for et in (
                (cp1.json().get("operational_model") or {})
                .get("manifest", {})
                .get("track", {})
                .get("entry_types", [])
            )
        )

        # Detach.
        detach = await authenticated_client.post(
            f"/api/tracks/{tid}/operational-model/detach-library", json={}
        )
        assert detach.status_code == 200, detach.text

        # Re-merge the same library.
        remerge = await authenticated_client.post(
            f"/api/tracks/{tid}/operational-model/merge-library",
            json={"library_operational_model_id": lib_id},
        )
        assert remerge.status_code == 200, remerge.text

        # Entry-type key set should be the same — no duplicates.
        cp2 = await authenticated_client.get(f"/api/tracks/{tid}/operational-model")
        assert cp2.status_code == 200
        ets_after = (
            (cp2.json().get("operational_model") or {})
            .get("manifest", {})
            .get("track", {})
            .get("entry_types", [])
        )
        keys_after = sorted(et["key"] for et in ets_after)
        assert keys_after == keys_before
        # No duplicate keys:
        assert len(keys_after) == len(set(keys_after))
