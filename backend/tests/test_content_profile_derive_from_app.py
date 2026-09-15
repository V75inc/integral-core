"""Plan 07-02 — Coverage for POST /api/content-profiles/from-space/{app_id}.

Locks I-LIB-03: derived library package's
``manifest['package']['provenance']`` stamps
``{source: 'space', source_id: <app_id>, derived_at: <ISO>,
   derived_by: <user_id>}``.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


def _space_manifest() -> dict:
    return {
        "content_profile_schema_version": 2,
        "scope": "app",
        "package": {"name": "from-space-libpkg"},
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


async def _seed_space_with_library(authenticated_client: AsyncClient) -> str:
    ws = await authenticated_client.post(
        "/api/workspaces", json={"name": "Derive-space Ws"}
    )
    assert ws.status_code == 200, ws.text
    ws_id = ws.json()["workspace"]["id"]
    pub = await authenticated_client.post(
        "/api/content-profiles",
        json={
            "name": "Derive-space Pack",
            "workspace_id": ws_id,
            "manifest": _space_manifest(),
        },
    )
    assert pub.status_code == 200, pub.text
    lib_id = pub.json()["content_profile"]["id"]
    sp = await authenticated_client.post(
        "/api/apps",
        json={"name": "Source App", "library_content_profile_id": lib_id},
    )
    assert sp.status_code == 200, sp.text
    app_id = sp.json()["app"]["id"]
    merge = await authenticated_client.post(
        f"/api/apps/{app_id}/content-profile/merge-library",
        json={"library_content_profile_id": lib_id},
    )
    assert merge.status_code in (200, 400), merge.text
    return app_id


@pytest.mark.asyncio
class TestDeriveLibraryFromSpace:
    """LIB-02 + ROADMAP AC#2: POST /api/content-profiles/from-space/{app_id}."""

    async def test_from_space_stamps_provenance(
        self, authenticated_client: AsyncClient, test_user
    ):
        """I-LIB-03: derived manifest carries package.provenance block."""
        app_id = await _seed_space_with_library(authenticated_client)

        resp = await authenticated_client.post(
            f"/api/content-profiles/from-app/{app_id}",
            json={"name": "Derived App Pack"},
        )
        assert resp.status_code == 200, resp.text
        derived = resp.json()["content_profile"]
        manifest = derived["manifest"]
        prov = manifest.get("package", {}).get("provenance")
        assert prov is not None, "provenance block missing from derived manifest"
        assert prov["source"] == "app"
        assert prov["source_id"] == app_id
        assert prov.get("derived_at"), "derived_at missing"
        assert prov.get("derived_by"), "derived_by missing"

    async def test_from_space_403_when_caller_lacks_space_update(
        self,
        authenticated_client: AsyncClient,
        second_user_client: AsyncClient,
        test_user,
    ):
        app_id = await _seed_space_with_library(authenticated_client)
        resp = await second_user_client.post(
            f"/api/content-profiles/from-app/{app_id}",
            json={"name": "Foreign App Derived"},
        )
        assert resp.status_code in (403, 404), resp.text

    async def test_from_space_404_when_space_id_unknown(
        self, authenticated_client: AsyncClient, test_user
    ):
        resp = await authenticated_client.post(
            "/api/content-profiles/from-app/cp-space-doesnotexist",
            json={"name": "Phantom App"},
        )
        assert resp.status_code in (403, 404), resp.text
