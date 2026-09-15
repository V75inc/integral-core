"""Plan 07-02 — Coverage for POST /api/apps/{app_id}/content-profile/detach-library.

Mirrors the existing track-side detach endpoint at
``backend/app/api/content_profiles.py:665`` for the App-attached
ContentProfile, locking I-LIB-04 (non-cascading: DEFINES_TRACK_PROFILE
edges to track-template ContentProfiles are preserved).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


def _track_manifest() -> dict:
    return {
        "content_profile_schema_version": 2,
        "scope": "track",
        "package": {"name": "07-02-detach-space-pkg"},
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


def _space_manifest() -> dict:
    return {
        "content_profile_schema_version": 2,
        "scope": "app",
        "package": {"name": "07-02-detach-space-libpkg"},
        "app": {
            "tracks": [
                {
                    "key": "projects",
                    "name": "Projects",
                    "provision_on_create": False,
                    "entry_types": [
                        {"key": "project", "name": "Project", "fields": []}
                    ],
                    "views": [{"key": "all", "name": "All", "view_type": "feed"}],
                    "taxonomy": {"tag_groups": []},
                }
            ],
            "relations": [],
        },
    }


async def _seed_library_pkg(
    authenticated_client: AsyncClient, *, scope: str = "app"
) -> dict:
    ws = await authenticated_client.post(
        "/api/workspaces", json={"name": "Detach-Sp Ws"}
    )
    assert ws.status_code == 200, ws.text
    ws_id = ws.json()["workspace"]["id"]
    manifest = _space_manifest() if scope == "app" else _track_manifest()
    pub = await authenticated_client.post(
        "/api/content-profiles",
        json={"name": "Detach-Sp Pack", "workspace_id": ws_id, "manifest": manifest},
    )
    assert pub.status_code == 200, pub.text
    return pub.json()["content_profile"]


async def _create_space_with_library(
    authenticated_client: AsyncClient, *, library_id: str
) -> str:
    sp = await authenticated_client.post(
        "/api/apps",
        json={"name": "Detach-Sp App", "library_content_profile_id": library_id},
    )
    assert sp.status_code == 200, sp.text
    app_id = sp.json()["app"]["id"]
    # If the create-space wiring did not auto-merge, merge explicitly.
    merge = await authenticated_client.post(
        f"/api/apps/{app_id}/content-profile/merge-library",
        json={"library_content_profile_id": library_id},
    )
    # Allow 200 (merged) or already-merged paths.
    assert merge.status_code in (200, 400), merge.text
    return app_id


@pytest.mark.asyncio
class TestDetachLibraryFromApp:
    """LIB-03: POST /api/apps/{app_id}/content-profile/detach-library."""

    async def test_happy_path_detaches_library_provenance(
        self, authenticated_client: AsyncClient, test_user
    ):
        lib = await _seed_library_pkg(authenticated_client)
        app_id = await _create_space_with_library(
            authenticated_client, library_id=lib["id"]
        )

        # Sanity: pre-detach the App carries the library provenance.
        get_sp = await authenticated_client.get(f"/api/apps/{app_id}")
        assert get_sp.status_code == 200
        assert (get_sp.json()["app"].get("library_merge_source_id") or "") == lib["id"]

        resp = await authenticated_client.post(
            f"/api/apps/{app_id}/content-profile/detach-library", json={}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["app_id"] == app_id
        assert "detached" in body["message"].lower()

        # Post-detach: library_merge_source_id cleared.
        get_sp2 = await authenticated_client.get(f"/api/apps/{app_id}")
        assert get_sp2.status_code == 200
        assert not (get_sp2.json()["app"].get("library_merge_source_id") or "")

    async def test_idempotent_when_no_library_provenance(
        self, authenticated_client: AsyncClient, test_user
    ):
        # Create a plain space (no library merge).
        sp = await authenticated_client.post("/api/apps", json={"name": "No-prov App"})
        assert sp.status_code == 200, sp.text
        app_id = sp.json()["app"]["id"]

        resp = await authenticated_client.post(
            f"/api/apps/{app_id}/content-profile/detach-library", json={}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # I-LIB-03 idempotency: no provenance to detach == 200 + clear message.
        assert "no library provenance" in body["message"].lower()
        assert body["app_id"] == app_id

    async def test_403_when_caller_lacks_space_update(
        self,
        authenticated_client: AsyncClient,
        second_user_client: AsyncClient,
        test_user,
    ):
        lib = await _seed_library_pkg(authenticated_client)
        app_id = await _create_space_with_library(
            authenticated_client, library_id=lib["id"]
        )

        # second_user has no role on this app_node.
        resp = await second_user_client.post(
            f"/api/apps/{app_id}/content-profile/detach-library", json={}
        )
        assert resp.status_code in (403, 404), resp.text

    async def test_404_when_space_id_unknown(
        self, authenticated_client: AsyncClient, test_user
    ):
        resp = await authenticated_client.post(
            "/api/apps/cp-doesnotexist/content-profile/detach-library", json={}
        )
        assert resp.status_code in (403, 404), resp.text

    async def test_defines_track_profile_edges_preserved_after_detach(
        self, authenticated_client: AsyncClient, test_user
    ):
        """I-LIB-04: detach does NOT cascade to track-template CPs reached via
        DEFINES_TRACK_PROFILE. Materialized track-template ContentProfile rows
        remain reachable from the (now-detached) space-attached CP."""
        from app.models.edges import DEFINES_TRACK_PROFILE
        from app.models.nodes import App as SpaceNode

        lib = await _seed_library_pkg(authenticated_client)
        app_id = await _create_space_with_library(
            authenticated_client, library_id=lib["id"]
        )

        # Walk substrate before detach: how many template CPs does the App CP own.
        app_node = await SpaceNode.get(app_id)
        assert app_node is not None
        from app.services.app_graph import get_app_attached_content_profile

        sacp_before = await get_app_attached_content_profile(app_node)
        templates_before = (
            await sacp_before.nodes(
                edge=[DEFINES_TRACK_PROFILE], node=["ContentProfile"]
            )
            if sacp_before
            else []
        )

        # Detach.
        resp = await authenticated_client.post(
            f"/api/apps/{app_id}/content-profile/detach-library", json={}
        )
        assert resp.status_code == 200, resp.text

        # Walk substrate after detach.
        space2 = await SpaceNode.get(app_id)
        sacp_after = await get_app_attached_content_profile(space2)
        templates_after = (
            await sacp_after.nodes(
                edge=[DEFINES_TRACK_PROFILE], node=["ContentProfile"]
            )
            if sacp_after
            else []
        )

        # I-LIB-04: same set of template CPs is reachable (count + ids).
        assert len(templates_after) == len(templates_before)
        ids_before = {t.id for t in templates_before}
        ids_after = {t.id for t in templates_after}
        assert ids_after == ids_before
