"""Regression: sync_attached_manifest must not leave dangling default_entry_type."""

import pytest
from httpx import AsyncClient

from app.services.app_graph import get_track_attached_operational_model
from app.services.operational_model_compile import compile_canonical_manifest
from app.services.operational_model_graph import sync_attached_manifest


@pytest.mark.asyncio
async def test_sync_reconciles_dangling_default_entry_type(
    authenticated_client: AsyncClient,
):
    """When CP has defaults but no CONTAINS EntryTypes, sync clears invalid defaults."""
    tr = await authenticated_client.post(
        "/api/tracks",
        json={"title": "Sync Defaults Bug Track", "visibility": "private"},
    )
    assert tr.status_code == 200
    track_id = tr.json()["track"]["id"]

    from app.models.nodes import Track

    track = await Track.get(track_id)
    cp = await get_track_attached_operational_model(track)
    assert cp is not None

    cp.manifest = {
        "operational_model_schema_version": 2,
        "scope": "track",
        "track": {
            "entry_types": [
                {
                    "key": "content_piece",
                    "name": "Content Piece",
                    "icon": "document",
                    "fields": [],
                }
            ],
            "views": [],
            "taxonomy": {"tag_groups": []},
            "defaults": {
                "default_entry_type": "content_piece",
                "default_view": "feed",
            },
        },
        "package": {},
        "migrations": [],
    }
    await cp.save()

    for et in await cp.nodes(edge=["CONTAINS"], node=["EntryType"]):
        await et.delete()

    await sync_attached_manifest(cp)
    cp = await get_track_attached_operational_model(track)

    et_list = (cp.manifest or {}).get("track", {}).get("entry_types", [])
    defaults = (cp.manifest or {}).get("track", {}).get("defaults", {})
    assert et_list == []
    assert defaults.get("default_entry_type") is None

    compile_canonical_manifest(manifest=cp.manifest or {})

    view_resp = await authenticated_client.post(
        f"/api/tracks/{track_id}/operational-model/views",
        json={"name": "New Board", "view_type": "kanban"},
    )
    assert view_resp.status_code == 200, view_resp.text
