"""Plan 07-02 — Coverage for POST /api/tracks/{track_id}/content-profile/revert-customizations.

Locks I-LIB-05: revert is reject-gated by
``compute_entry_impact_for_attached``. Track-side endpoint at
``backend/app/api/content_profiles.py:745``.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


def _track_manifest(name: str = "revert-track-pkg") -> dict:
    return {
        "content_profile_schema_version": 2,
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
    """Returns (track_id, library_id)."""
    ws = await authenticated_client.post(
        "/api/workspaces", json={"name": "Revert-track Ws"}
    )
    assert ws.status_code == 200, ws.text
    ws_id = ws.json()["workspace"]["id"]
    pub = await authenticated_client.post(
        "/api/content-profiles",
        json={
            "name": "Revert-track Pack",
            "workspace_id": ws_id,
            "manifest": _track_manifest(),
        },
    )
    assert pub.status_code == 200, pub.text
    lib_id = pub.json()["content_profile"]["id"]
    tr = await authenticated_client.post(
        "/api/tracks", json={"title": "Revert-track Track", "visibility": "private"}
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
class TestRevertTrackProfileCustomizations:
    """LIB-03 + ROADMAP AC#3: POST /api/tracks/{track_id}/content-profile/revert-customizations."""

    async def test_happy_path_revert_with_no_entries(
        self, authenticated_client: AsyncClient, test_user
    ):
        tid, _lib_id = await _seed_track_with_library(authenticated_client)

        resp = await authenticated_client.post(
            f"/api/tracks/{tid}/content-profile/revert-customizations",
            json={},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["track_id"] == tid
        # I-LIB-05: impacts list present (empty when there are no entries).
        assert "impacts" in body
        assert isinstance(body["impacts"], list)

    async def test_400_when_track_has_no_library_provenance(
        self, authenticated_client: AsyncClient, test_user
    ):
        tr = await authenticated_client.post(
            "/api/tracks",
            json={"title": "No-prov Revert Track", "visibility": "private"},
        )
        assert tr.status_code == 200
        tid = tr.json()["track"]["id"]
        resp = await authenticated_client.post(
            f"/api/tracks/{tid}/content-profile/revert-customizations", json={}
        )
        assert resp.status_code in (400, 404), resp.text

    async def test_403_when_caller_lacks_track_update(
        self,
        authenticated_client: AsyncClient,
        second_user_client: AsyncClient,
        test_user,
    ):
        tid, _lib_id = await _seed_track_with_library(authenticated_client)
        resp = await second_user_client.post(
            f"/api/tracks/{tid}/content-profile/revert-customizations",
            json={},
        )
        assert resp.status_code in (403, 404), resp.text

    async def test_force_true_accepted_when_no_blocking_impacts(
        self, authenticated_client: AsyncClient, test_user
    ):
        """I-LIB-05 force flag: passes through cleanly when impact set is
        empty (no entries to invalidate)."""
        tid, _lib_id = await _seed_track_with_library(authenticated_client)
        resp = await authenticated_client.post(
            f"/api/tracks/{tid}/content-profile/revert-customizations",
            json={"force": True},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["track_id"] == tid
        assert body.get("force") is True
        assert "impacts" in body

    async def test_revert_emits_change_event_with_revert_details(
        self, authenticated_client: AsyncClient, test_user
    ):
        """D-05 emission: the revert handler emits content_profile.update with
        details.revert=True. We verify via the audit-log endpoint."""
        tid, _lib_id = await _seed_track_with_library(authenticated_client)
        await authenticated_client.post(
            f"/api/tracks/{tid}/content-profile/revert-customizations",
            json={"force": False},
        )
        # Audit-log filter by scope; we expect at least one
        # content_profile.update event with details.revert.
        audit = await authenticated_client.get(
            f"/api/audit-log?scope=track:{tid}&limit=50"
        )
        assert audit.status_code == 200, audit.text
        events = (audit.json() or {}).get("events", [])
        revert_events = [
            e
            for e in events
            if e.get("action") == "content_profile.update"
            and (e.get("details") or {}).get("revert") is True
        ]
        assert (
            revert_events
        ), "no content_profile.update event with details.revert=True found"
