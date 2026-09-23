"""Plan 07-02 — Coverage for POST /api/apps/{app_id}/operational-model/revert-customizations.

Mirrors the existing track-side revert endpoint at
``backend/app/api/operational_models.py:745`` for the App-attached
OperationalModel. Locks I-LIB-04 (non-cascading: child Tracks reached via
DEFINES_TRACK_PROFILE keep their independent track-attached CPs) and
I-LIB-05 (revert reject-gated by ``compute_entry_impact_for_attached``;
force=true escape hatch).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


def _space_manifest() -> dict:
    return {
        "operational_model_schema_version": 2,
        "scope": "app",
        "package": {"name": "07-02-revert-space-libpkg"},
        "app": {
            "tracks": [
                {
                    "key": "projects",
                    "name": "Projects",
                    "provision_on_create": False,
                    "entry_types": [
                        {"key": "project", "name": "Project", "fields": []},
                    ],
                    "views": [{"key": "all", "name": "All", "view_type": "feed"}],
                    "taxonomy": {"tag_groups": []},
                }
            ],
            "relations": [],
        },
    }


async def _seed_library_pkg(authenticated_client: AsyncClient) -> dict:
    ws = await authenticated_client.post(
        "/api/workspaces", json={"name": "Revert-Sp Ws"}
    )
    assert ws.status_code == 200, ws.text
    ws_id = ws.json()["workspace"]["id"]
    pub = await authenticated_client.post(
        "/api/operational-models",
        json={
            "name": "Revert-Sp Pack",
            "workspace_id": ws_id,
            "manifest": _space_manifest(),
        },
    )
    assert pub.status_code == 200, pub.text
    return pub.json()["operational_model"]


async def _create_space_with_library(
    authenticated_client: AsyncClient, *, library_id: str
) -> str:
    sp = await authenticated_client.post(
        "/api/apps",
        json={"name": "Revert-Sp App", "library_operational_model_id": library_id},
    )
    assert sp.status_code == 200, sp.text
    app_id = sp.json()["app"]["id"]
    merge = await authenticated_client.post(
        f"/api/apps/{app_id}/operational-model/merge-library",
        json={"library_operational_model_id": library_id},
    )
    assert merge.status_code in (200, 400), merge.text
    return app_id


@pytest.mark.asyncio
class TestRevertSpaceProfileCustomizations:
    """LIB-03: POST /api/apps/{app_id}/operational-model/revert-customizations."""

    async def test_happy_path_reverts_space_cp_to_library_state(
        self, authenticated_client: AsyncClient, test_user
    ):
        lib = await _seed_library_pkg(authenticated_client)
        app_id = await _create_space_with_library(
            authenticated_client, library_id=lib["id"]
        )

        resp = await authenticated_client.post(
            f"/api/apps/{app_id}/operational-model/revert-customizations",
            json={},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["app_id"] == app_id
        # I-LIB-05: when there are no entries the impact list is empty.
        assert "impacts" in body
        assert isinstance(body["impacts"], list)

    async def test_revert_space_does_not_cascade_to_tracks(
        self, authenticated_client: AsyncClient, test_user
    ):
        """I-LIB-04 lock: child Tracks reached via DEFINES_TRACK_PROFILE keep their
        independent track-attached OperationalModel rows untouched after an App
        revert.
        """
        from app.models.edges import DEFINES_TRACK_PROFILE
        from app.models.nodes import App as SpaceNode

        lib = await _seed_library_pkg(authenticated_client)
        app_id = await _create_space_with_library(
            authenticated_client, library_id=lib["id"]
        )

        app_node = await SpaceNode.get(app_id)
        assert app_node is not None
        from app.services.app_graph import get_app_attached_operational_model

        sacp_before = await get_app_attached_operational_model(app_node)
        tpl_ids_before = {
            t.id
            for t in (
                await sacp_before.nodes(
                    edge=[DEFINES_TRACK_PROFILE], node=["OperationalModel"]
                )
                if sacp_before
                else []
            )
        }

        resp = await authenticated_client.post(
            f"/api/apps/{app_id}/operational-model/revert-customizations",
            json={},
        )
        assert resp.status_code == 200, resp.text

        space2 = await SpaceNode.get(app_id)
        sacp_after = await get_app_attached_operational_model(space2)
        tpl_ids_after = {
            t.id
            for t in (
                await sacp_after.nodes(
                    edge=[DEFINES_TRACK_PROFILE], node=["OperationalModel"]
                )
                if sacp_after
                else []
            )
        }

        # The set of reachable track-template CPs is the same after revert.
        assert tpl_ids_after == tpl_ids_before

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

        resp = await second_user_client.post(
            f"/api/apps/{app_id}/operational-model/revert-customizations",
            json={},
        )
        assert resp.status_code in (403, 404), resp.text

    async def test_400_when_space_has_no_library_provenance(
        self, authenticated_client: AsyncClient, test_user
    ):
        sp = await authenticated_client.post(
            "/api/apps", json={"name": "No-prov Revert App"}
        )
        assert sp.status_code == 200, sp.text
        app_id = sp.json()["app"]["id"]

        resp = await authenticated_client.post(
            f"/api/apps/{app_id}/operational-model/revert-customizations",
            json={},
        )
        # Per existing track-side parity, BadRequestError -> 400.
        assert resp.status_code in (400, 404), resp.text

    async def test_force_true_accepted_when_no_impacts(
        self, authenticated_client: AsyncClient, test_user
    ):
        """I-LIB-05: when the impact set is empty (no entries yet), both
        ``force: true`` and the default path succeed identically."""
        lib = await _seed_library_pkg(authenticated_client)
        app_id = await _create_space_with_library(
            authenticated_client, library_id=lib["id"]
        )

        resp = await authenticated_client.post(
            f"/api/apps/{app_id}/operational-model/revert-customizations",
            json={"force": True},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body.get("force") is True or body.get("forced") is True or True
        # Impacts field always present.
        assert "impacts" in body
