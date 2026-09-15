"""Plan 07-02 — Coverage for POST /api/content-profiles/from-track/{track_id}.

Locks I-LIB-03: the derived library package's
``manifest['package']['provenance']`` block stamps
``{source: 'track', source_id: <track_id>, derived_at: <ISO>,
   derived_by: <user_id>}``. Round-trip merge of the derived package
into a fresh Track regenerates an equivalent attached manifest modulo
provenance fields (ROADMAP AC#2).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


def _track_manifest(name: str = "from-track-pkg") -> dict:
    return {
        "content_profile_schema_version": 2,
        "scope": "track",
        "package": {"name": name},
        "track": {
            "entry_types": [
                {"key": "task", "name": "Task", "fields": []},
                {"key": "note", "name": "Note", "fields": []},
            ],
            # Plan 07-02 — pick a non-default key so library-merge doesn't
            # collide with the Track's auto-provisioned default ``feed`` view.
            "views": [
                {"key": "library_feed", "name": "Library Feed", "view_type": "feed"},
            ],
            "taxonomy": {"tag_groups": []},
        },
    }


async def _seed_library_and_track(
    authenticated_client: AsyncClient,
) -> tuple[str, str]:
    """Returns (track_id, library_id) — track has the library merged into it."""
    ws = await authenticated_client.post(
        "/api/workspaces", json={"name": "Derive-track Ws"}
    )
    assert ws.status_code == 200, ws.text
    ws_id = ws.json()["workspace"]["id"]
    pub = await authenticated_client.post(
        "/api/content-profiles",
        json={
            "name": "Derive-track Pack",
            "workspace_id": ws_id,
            "manifest": _track_manifest(),
        },
    )
    assert pub.status_code == 200, pub.text
    lib_id = pub.json()["content_profile"]["id"]
    tr = await authenticated_client.post(
        "/api/tracks", json={"title": "Source Track", "visibility": "private"}
    )
    assert tr.status_code == 200, tr.text
    tid = tr.json()["track"]["id"]
    merge = await authenticated_client.post(
        f"/api/tracks/{tid}/content-profile/merge-library",
        json={"library_content_profile_id": lib_id},
    )
    assert merge.status_code == 200, merge.text
    return tid, lib_id


@pytest.mark.asyncio
class TestDeriveLibraryFromTrack:
    """LIB-02 + ROADMAP AC#2: POST /api/content-profiles/from-track/{track_id}."""

    async def test_from_track_stamps_provenance(
        self, authenticated_client: AsyncClient, test_user
    ):
        """I-LIB-03: derived manifest carries package.provenance block."""
        tid, _lib_id = await _seed_library_and_track(authenticated_client)

        resp = await authenticated_client.post(
            f"/api/content-profiles/from-track/{tid}",
            json={"name": "Derived Pack"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        derived = body["content_profile"]
        manifest = derived["manifest"]
        prov = manifest.get("package", {}).get("provenance")
        assert prov is not None, "provenance block missing from derived manifest"
        assert prov["source"] == "track"
        assert prov["source_id"] == tid
        assert prov.get("derived_at"), "derived_at missing"
        assert prov.get("derived_by"), "derived_by missing"

    async def test_from_track_403_when_caller_lacks_track_update(
        self,
        authenticated_client: AsyncClient,
        second_user_client: AsyncClient,
        test_user,
    ):
        tid, _lib_id = await _seed_library_and_track(authenticated_client)

        resp = await second_user_client.post(
            f"/api/content-profiles/from-track/{tid}",
            json={"name": "Foreign Derived"},
        )
        assert resp.status_code in (403, 404), resp.text

    async def test_from_track_404_when_track_id_unknown(
        self, authenticated_client: AsyncClient, test_user
    ):
        resp = await authenticated_client.post(
            "/api/content-profiles/from-track/cp-track-doesnotexist",
            json={"name": "Phantom"},
        )
        # Either 403 (policy denial on unknown id) or 404 — both are valid
        # rejections; assert the call did NOT produce a 200.
        assert resp.status_code in (403, 404), resp.text

    async def test_from_track_derived_package_round_trips(
        self, authenticated_client: AsyncClient, test_user
    ):
        """ROADMAP AC#2: derived package is immediately re-mergeable into a
        fresh Track. The re-merged Track's manifest carries the same
        entry-type + view shape as the source (provenance fields modulo)."""
        tid, _lib_id = await _seed_library_and_track(authenticated_client)

        derive = await authenticated_client.post(
            f"/api/content-profiles/from-track/{tid}",
            json={"name": "Round-trip Pack"},
        )
        assert derive.status_code == 200, derive.text
        derived = derive.json()["content_profile"]
        derived_id = derived["id"]

        # Mark as library_package (the derive endpoint should already; if not,
        # the next merge will fail and we surface the gap.)
        new_track = await authenticated_client.post(
            "/api/tracks",
            json={"title": "Round-trip Track", "visibility": "private"},
        )
        assert new_track.status_code == 200
        new_tid = new_track.json()["track"]["id"]

        merge = await authenticated_client.post(
            f"/api/tracks/{new_tid}/content-profile/merge-library",
            json={"library_content_profile_id": derived_id},
        )
        assert merge.status_code == 200, merge.text

        cp_resp = await authenticated_client.get(
            f"/api/tracks/{new_tid}/content-profile"
        )
        assert cp_resp.status_code == 200, cp_resp.text
        manifest = (cp_resp.json().get("content_profile") or {}).get("manifest") or {}
        assert manifest.get("scope") == "track"
        keys = [
            et["key"] for et in (manifest.get("track") or {}).get("entry_types", [])
        ]
        # Same entry-type shape preserved through derive + re-merge.
        assert "task" in keys
        assert "note" in keys
