"""Authored language is preserved; access, schema and share controls still govern writes."""

import pytest

from app.services.app_graph import catalog_track, catalog_workspace

TEXTS = [
    "Fictional dummy record for smoke testing",
    "this is fucking terrible",
    "**shit**",
    "Test 3",
    "ordinary text",
]


@pytest.fixture
async def shared_track_setup(test_user):
    """Track + entry type + public-share-eligible workspace for public-path tests."""
    from app.models.edges import COLLABORATES_ON, IS_MEMBER_OF
    from app.models.nodes import EntryType, Track, Workspace
    from app.utils.time import utc_now_iso

    now = utc_now_iso()
    ws = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="Test Moderation WS",
        name_fold="test moderation ws",
        created_at=now,
        updated_at=now,
    )
    await catalog_workspace(ws)
    await test_user.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=now)

    track = await Track.create(
        title="Public Moderation Project",
        owner_id=test_user.id,
        workspace_id=ws.id,
        visibility="private",
    )
    await catalog_track(track)
    await test_user.connect(track, edge=COLLABORATES_ON, role="owner", added_at=now)

    et = await EntryType.create(
        name="Task Item",
        name_fold="task item",
        track_id=track.id,
        form_schema={"fields": []},
    )

    from app.services.app_graph import ensure_track_attached_operational_model

    await ensure_track_attached_operational_model(track)

    return track, et, ws


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


@pytest.mark.parametrize("text", TEXTS)
@pytest.mark.asyncio
async def test_authorized_entry_and_comment_language_is_preserved(
    authenticated_client, text
):
    track = (
        await authenticated_client.post(
            "/api/tracks", json={"title": "Language checks"}
        )
    ).json()["track"]["id"]
    created = await authenticated_client.post(
        "/api/entries", json={"track_id": track, "title": text, "body": text}
    )
    assert created.status_code == 200, created.text
    entry = created.json()["entry"]
    assert entry["title"] == entry["body"] == text
    changed = text + " updated"
    update = await authenticated_client.put(
        f"/api/entries/{entry['id']}", json={"title": changed, "body": changed}
    )
    assert update.status_code == 200, update.text
    assert update.json()["entry"]["title"] == update.json()["entry"]["body"] == changed
    comment = await authenticated_client.post(
        f"/api/entries/{entry['id']}/comments", json={"text": text}
    )
    assert comment.status_code == 200, comment.text
    row = comment.json()["comment"]
    assert row["text"] == text
    edit = await authenticated_client.put(
        f"/api/comments/{row['id']}", json={"text": changed}
    )
    assert edit.status_code == 200, edit.text
    assert edit.json()["comment"]["text"] == changed


@pytest.mark.parametrize("text", TEXTS)
@pytest.mark.asyncio
async def test_public_share_entry_and_comment_language_is_preserved(
    authenticated_client, shared_track_setup, text
):
    track, et, _ = shared_track_setup
    token = await _enable_public_share(authenticated_client, track.id)
    authorization = authenticated_client.headers.pop("Authorization", None)
    try:
        route = f"/api/public-share/track/{token}/entries"
        created = await authenticated_client.post(
            route, json={"title": text, "body": text, "type_id": et.id}
        )
        assert created.status_code == 200, created.text
        entry = created.json()["entry"]
        assert entry["title"] == entry["body"] == text
        changed = text + " updated"
        update = await authenticated_client.patch(
            f"{route}/{entry['id']}", json={"title": changed, "body": changed}
        )
        assert update.status_code == 200, update.text
        assert (
            update.json()["entry"]["title"] == update.json()["entry"]["body"] == changed
        )
        comment = await authenticated_client.post(
            f"{route}/{entry['id']}/comments", json={"text": text}
        )
        assert comment.status_code == 200, comment.text
        assert comment.json()["comment"]["text"] == text
    finally:
        if authorization:
            authenticated_client.headers["Authorization"] = authorization


@pytest.mark.asyncio
async def test_removing_language_filter_does_not_grant_public_write_permissions(
    authenticated_client, shared_track_setup
):
    track, et, _ = shared_track_setup
    res = await authenticated_client.post(
        f"/api/tracks/{track.id}/public-share",
        json={
            "enabled": True,
            "public_permissions": {"read_entries": True, "create_entries": False},
        },
    )
    assert res.status_code == 200, res.text
    token = res.json()["token"]
    authorization = authenticated_client.headers.pop("Authorization", None)
    try:
        rejected = await authenticated_client.post(
            f"/api/public-share/track/{token}/entries",
            json={"title": "dummy fucking record", "type_id": et.id},
        )
        assert rejected.status_code == 403, rejected.text
    finally:
        if authorization:
            authenticated_client.headers["Authorization"] = authorization
