"""Tests for GET /public-share/track/{token}/relation-options.

June 23 QA bug: "Required Content Piece Field Has No Available Options" — a
relation field on the public entry-create form that targets a SIBLING track
(e.g. Performance -> Content Pipeline) previously always resolved zero
candidates, because the public entries list only covers the shared track
itself. This endpoint resolves candidates from sibling tracks under the same
App, per the field's target_track_types/allow_cross_track operational-model
config.
"""

import pytest


@pytest.fixture
async def cross_track_relation_setup(test_user):
    """App with two sibling tracks; source track has a relation field
    targeting the other track's entry type."""
    from app.models.edges import CONTAINS, IS_MEMBER_OF, OWNS
    from app.models.nodes import App, EntryType, Track, Workspace
    from app.utils.time import utc_now_iso

    now = utc_now_iso()
    ws = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="Cross Track WS",
        name_fold="cross track ws",
        created_at=now,
        updated_at=now,
    )
    await test_user.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=now)

    app_node = await App.create(
        name="Content Factory Test",
        owner_id=test_user.id,
        workspace_id=ws.id,
    )
    await test_user.connect(app_node, edge=OWNS, added_at=now)

    target_track = await Track.create(
        title="Content Pipeline",
        template_id="content_pipeline",
        owner_id=test_user.id,
        workspace_id=ws.id,
        visibility="private",
    )
    await app_node.connect(target_track, edge=CONTAINS, added_at=now)
    # Mirror the real creation path: services/track_service.py wires an OWNS
    # edge from the creator to the track. Hand-built fixtures that set only the
    # scalar owner_id used to pass because org staff got an implicit admin role;
    # Wave 1 narrowed that to viewer, so the missing edge now surfaces as a 403.
    await test_user.connect(target_track, edge=OWNS, added_at=now)

    target_type = await EntryType.create(
        name="Content Piece",
        name_fold="content piece",
        track_id=target_track.id,
        form_schema={"fields": []},
    )

    source_track = await Track.create(
        title="Performance",
        template_id="performance",
        owner_id=test_user.id,
        workspace_id=ws.id,
        visibility="private",
    )
    await app_node.connect(source_track, edge=CONTAINS, added_at=now)
    await test_user.connect(source_track, edge=OWNS, added_at=now)

    source_type = await EntryType.create(
        name="Performance Record",
        name_fold="performance record",
        track_id=source_track.id,
        form_schema={
            "fields": [
                {
                    "key": "content_piece",
                    "type": "relation",
                    "required": True,
                    "relation": {
                        "target": "entry",
                        "many": False,
                        "target_entry_types": ["content_piece"],
                        "target_track_types": ["content_pipeline"],
                        "allow_cross_track": True,
                    },
                }
            ]
        },
    )

    return source_track, source_type, target_track, target_type, ws


async def _enable_public_share(authenticated_client, track_id):
    body = {
        "enabled": True,
        "public_permissions": {
            "read_entries": True,
            "create_entries": True,
            "update_entries": True,
            "read_comments": True,
            "create_comments": True,
        },
    }
    res = await authenticated_client.post(
        f"/api/tracks/{track_id}/public-share", json=body
    )
    assert res.status_code == 200, res.text
    return res.json()["token"]


@pytest.mark.asyncio
async def test_relation_options_resolves_sibling_track_candidates(
    authenticated_client, test_user, cross_track_relation_setup
):
    """A relation field targeting a sibling track returns that track's entries."""
    source_track, source_type, target_track, target_type, ws = (
        cross_track_relation_setup
    )

    # Seed two content_piece entries in the SIBLING track.
    content_entry_resp = await authenticated_client.post(
        "/api/entries",
        json={
            "track_id": target_track.id,
            "type_id": target_type.id,
            "title": "Founder POV - short video",
        },
    )
    assert content_entry_resp.status_code == 200, content_entry_resp.text
    content_entry_id = content_entry_resp.json()["entry"]["id"]

    # A sibling track only contributes relation candidates when it is itself
    # publicly shared — a share on one track must not expose a neighbour's
    # entries. Share both so this test exercises sibling resolution; the
    # negative case is pinned by the test below.
    token = await _enable_public_share(authenticated_client, source_track.id)
    await _enable_public_share(authenticated_client, target_track.id)

    auth_header = authenticated_client.headers.pop("Authorization", None)
    try:
        resp = await authenticated_client.get(
            f"/api/public-share/track/{token}/relation-options",
            params={"entry_type_id": source_type.id, "field_key": "content_piece"},
        )
        assert resp.status_code == 200, resp.text
        targets = resp.json()["targets"]
        assert any(t["id"] == content_entry_id for t in targets)
        match = next(t for t in targets if t["id"] == content_entry_id)
        assert match["title"] == "Founder POV - short video"
        assert match["track_title"] == "Content Pipeline"
    finally:
        if auth_header:
            authenticated_client.headers["Authorization"] = auth_header


@pytest.mark.asyncio
async def test_relation_options_excludes_unshared_sibling_track(
    authenticated_client, test_user, cross_track_relation_setup
):
    """A sibling track with no public share of its own contributes no candidates.

    Publicly sharing one track must not turn every sibling track in the app
    into an anonymously-readable entry list via the relation-options surface.
    """
    source_track, source_type, target_track, target_type, ws = (
        cross_track_relation_setup
    )

    content_entry_resp = await authenticated_client.post(
        "/api/entries",
        json={
            "track_id": target_track.id,
            "type_id": target_type.id,
            "title": "Unshared sibling entry",
        },
    )
    assert content_entry_resp.status_code == 200, content_entry_resp.text
    content_entry_id = content_entry_resp.json()["entry"]["id"]

    # Only the source track is shared — the sibling is not.
    token = await _enable_public_share(authenticated_client, source_track.id)

    auth_header = authenticated_client.headers.pop("Authorization", None)
    try:
        resp = await authenticated_client.get(
            f"/api/public-share/track/{token}/relation-options",
            params={"entry_type_id": source_type.id, "field_key": "content_piece"},
        )
        assert resp.status_code == 200, resp.text
        targets = resp.json()["targets"]
        assert not any(
            t["id"] == content_entry_id for t in targets
        ), "entry from an unshared sibling track leaked to an anonymous caller"
    finally:
        if auth_header:
            authenticated_client.headers["Authorization"] = auth_header


@pytest.mark.asyncio
async def test_relation_options_excludes_wrong_entry_type(
    authenticated_client, test_user, cross_track_relation_setup
):
    """Entries in the target track that don't match target_entry_types are excluded."""
    source_track, source_type, target_track, target_type, ws = (
        cross_track_relation_setup
    )

    from app.models.nodes import EntryType

    other_type = await EntryType.create(
        name="Other Type",
        name_fold="other type",
        track_id=target_track.id,
        form_schema={"fields": []},
    )
    other_entry_resp = await authenticated_client.post(
        "/api/entries",
        json={
            "track_id": target_track.id,
            "type_id": other_type.id,
            "title": "Should not appear",
        },
    )
    assert other_entry_resp.status_code == 200, other_entry_resp.text
    other_entry_id = other_entry_resp.json()["entry"]["id"]

    token = await _enable_public_share(authenticated_client, source_track.id)

    auth_header = authenticated_client.headers.pop("Authorization", None)
    try:
        resp = await authenticated_client.get(
            f"/api/public-share/track/{token}/relation-options",
            params={"entry_type_id": source_type.id, "field_key": "content_piece"},
        )
        assert resp.status_code == 200, resp.text
        ids = {t["id"] for t in resp.json()["targets"]}
        assert other_entry_id not in ids
    finally:
        if auth_header:
            authenticated_client.headers["Authorization"] = auth_header


@pytest.mark.asyncio
async def test_relation_options_unknown_field_404s(
    authenticated_client, test_user, cross_track_relation_setup
):
    """A field_key that isn't a relation field on the entry type 404s cleanly."""
    source_track, source_type, target_track, target_type, ws = (
        cross_track_relation_setup
    )
    token = await _enable_public_share(authenticated_client, source_track.id)

    auth_header = authenticated_client.headers.pop("Authorization", None)
    try:
        resp = await authenticated_client.get(
            f"/api/public-share/track/{token}/relation-options",
            params={"entry_type_id": source_type.id, "field_key": "nonexistent"},
        )
        assert resp.status_code == 404
    finally:
        if auth_header:
            authenticated_client.headers["Authorization"] = auth_header
