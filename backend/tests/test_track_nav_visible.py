"""Track.nav_visible — OM compile, provision reconcile, list filters."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.exceptions import BadRequestError
from app.models.nodes import Track
from app.services.operational_model_compile import compile_canonical_manifest


def _minimal_app_manifest(*, nav_visible=None):
    track = {
        "key": "invoice_lines",
        "name": "Invoice lines",
        "provision_on_create": True,
        "entry_types": [{"name": "Line", "fields": []}],
        "views": [],
    }
    if nav_visible is not None:
        track["nav_visible"] = nav_visible
    return {
        "operational_model_schema_version": 2,
        "scope": "app",
        "package": {"name": "NavVis", "slug": "nav-vis", "version": "0.0.1"},
        "app": {
            "tracks": [track],
            "defaults": {"provision_prescribed_tracks": True},
        },
    }


def test_compile_nav_visible_defaults_true():
    """Omitted nav_visible compiles to True on app.tracks[]."""
    compiled = compile_canonical_manifest(manifest=_minimal_app_manifest())
    tracks = (compiled.get("app") or {}).get("tracks") or []
    assert len(tracks) == 1
    assert tracks[0]["nav_visible"] is True


def test_compile_nav_visible_false_round_trips():
    """Explicit nav_visible: false is preserved through compile."""
    compiled = compile_canonical_manifest(
        manifest=_minimal_app_manifest(nav_visible=False)
    )
    tracks = (compiled.get("app") or {}).get("tracks") or []
    assert tracks[0]["nav_visible"] is False


def test_compile_nav_visible_must_be_boolean():
    """Non-boolean nav_visible is a hard compile error."""
    with pytest.raises(BadRequestError):
        compile_canonical_manifest(
            manifest=_minimal_app_manifest(nav_visible="nope")  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_list_app_tracks_filters_nav_hidden(
    authenticated_client: AsyncClient, test_user
):
    """GET /apps/{id}/tracks omits nav_hidden unless include_nav_hidden."""
    sp = await authenticated_client.post("/api/apps", json={"name": "Nav Filter App"})
    assert sp.status_code == 200
    app_id = sp.json()["app"]["id"]

    visible = await authenticated_client.post(
        "/api/tracks",
        json={"title": "Visible Track", "app_id": app_id},
    )
    assert visible.status_code == 200
    visible_id = visible.json()["track"]["id"]

    hidden = await authenticated_client.post(
        "/api/tracks",
        json={"title": "Hidden Line Track", "app_id": app_id},
    )
    assert hidden.status_code == 200
    hidden_id = hidden.json()["track"]["id"]

    node = await Track.get(hidden_id)
    assert node is not None
    node.nav_visible = False
    await node.save()

    listed = await authenticated_client.get(f"/api/apps/{app_id}/tracks")
    assert listed.status_code == 200
    ids = {t["id"] for t in listed.json().get("tracks", [])}
    assert visible_id in ids
    assert hidden_id not in ids

    full = await authenticated_client.get(
        f"/api/apps/{app_id}/tracks",
        params={"include_nav_hidden": "true"},
    )
    assert full.status_code == 200
    full_ids = {t["id"] for t in full.json().get("tracks", [])}
    assert visible_id in full_ids
    assert hidden_id in full_ids
    hidden_row = next(t for t in full.json()["tracks"] if t["id"] == hidden_id)
    assert hidden_row.get("nav_visible") is False


@pytest.mark.asyncio
async def test_list_tracks_filters_nav_hidden(
    authenticated_client: AsyncClient, test_user
):
    """GET /tracks?app_id=… omits nav_hidden unless include_nav_hidden."""
    sp = await authenticated_client.post(
        "/api/apps", json={"name": "Nav Filter Workspace Tracks"}
    )
    assert sp.status_code == 200
    app_id = sp.json()["app"]["id"]

    hidden = await authenticated_client.post(
        "/api/tracks",
        json={"title": "Hidden Workspace Line", "app_id": app_id},
    )
    assert hidden.status_code == 200
    hidden_id = hidden.json()["track"]["id"]

    node = await Track.get(hidden_id)
    assert node is not None
    node.nav_visible = False
    await node.save()

    listed = await authenticated_client.get(
        "/api/tracks", params={"app_id": app_id, "limit": 100}
    )
    assert listed.status_code == 200
    ids = {t["id"] for t in listed.json().get("tracks", [])}
    assert hidden_id not in ids

    full = await authenticated_client.get(
        "/api/tracks",
        params={
            "app_id": app_id,
            "limit": 100,
            "include_nav_hidden": "true",
        },
    )
    assert full.status_code == 200
    full_ids = {t["id"] for t in full.json().get("tracks", [])}
    assert hidden_id in full_ids
