import pytest


@pytest.fixture
async def shared_track_setup(test_user):
    from app.models.edges import COLLABORATES_ON, IS_MEMBER_OF
    from app.models.nodes import EntryType, Track, Workspace
    from app.utils.time import utc_now_iso

    now = utc_now_iso()
    ws = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="Test Share WS",
        name_fold="test share ws",
        created_at=now,
        updated_at=now,
    )
    await test_user.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=now)

    track = await Track.create(
        title="Public Project",
        owner_id=test_user.id,
        workspace_id=ws.id,
        visibility="private",
    )
    await test_user.connect(track, edge=COLLABORATES_ON, role="owner", added_at=now)

    et = await EntryType.create(
        name="Task Item",
        name_fold="task item",
        track_id=track.id,
        form_schema={
            "fields": [
                {
                    "key": "priority",
                    "type": "select",
                    "enum": ["high", "medium", "low"],
                    "required": True,
                },
                {"key": "notes", "type": "text", "required": False},
            ]
        },
    )

    from app.services.app_graph import ensure_track_attached_content_profile

    await ensure_track_attached_content_profile(track)

    return track, et, ws


@pytest.mark.asyncio
async def test_get_and_post_public_share_settings(
    authenticated_client, test_user, shared_track_setup
):
    track, et, ws = shared_track_setup

    # Initially disabled
    res = await authenticated_client.get(f"/api/tracks/{track.id}/public-share")
    assert res.status_code == 200
    assert res.json()["enabled"] is False

    # Enable public sharing
    body = {
        "enabled": True,
        "public_permissions": {
            "read_entries": True,
            "create_entries": True,
            "update_entries": False,
            "read_comments": False,
            "create_comments": False,
        },
    }
    res = await authenticated_client.post(
        f"/api/tracks/{track.id}/public-share", json=body
    )
    assert res.status_code == 200
    data = res.json()
    assert data["enabled"] is True
    assert data["token"] is not None
    token = data["token"]

    # Verify settings persisted. The plaintext token is show-once at mint —
    # it is stored only as a hash, so reads never re-disclose it. `enabled`
    # is what tells the caller a live link exists.
    res = await authenticated_client.get(f"/api/tracks/{track.id}/public-share")
    assert res.status_code == 200
    assert res.json()["enabled"] is True
    assert res.json()["token"] is None

    # Check unauthenticated GET public track details
    # Temporarily remove authorization header
    auth_header = authenticated_client.headers.pop("Authorization", None)
    try:
        res = await authenticated_client.get(f"/api/public-share/track/{token}")
        assert res.status_code == 200
        pub_data = res.json()
        assert pub_data["track"]["title"] == "Public Project"
        assert pub_data["public_permissions"]["create_entries"] is True
        assert pub_data["public_permissions"]["update_entries"] is False
    finally:
        if auth_header:
            authenticated_client.headers["Authorization"] = auth_header

    # Disable public sharing
    body = {"enabled": False}
    res = await authenticated_client.post(
        f"/api/tracks/{track.id}/public-share", json=body
    )
    assert res.status_code == 200
    assert res.json()["enabled"] is False

    # Fetching details with old token should now 404
    auth_header = authenticated_client.headers.pop("Authorization", None)
    try:
        res = await authenticated_client.get(f"/api/public-share/track/{token}")
        assert res.status_code == 404
    finally:
        if auth_header:
            authenticated_client.headers["Authorization"] = auth_header


@pytest.mark.asyncio
async def test_public_entries_and_comments_permissions(
    authenticated_client, test_user, shared_track_setup
):
    track, et, ws = shared_track_setup

    # Enable sharing with read_entries=True, create_entries=True, others False
    body = {
        "enabled": True,
        "public_permissions": {
            "read_entries": True,
            "create_entries": True,
            "update_entries": False,
            "read_comments": False,
            "create_comments": False,
        },
    }
    res = await authenticated_client.post(
        f"/api/tracks/{track.id}/public-share", json=body
    )
    token = res.json()["token"]

    # 1. Create entry publicly
    entry_body = {
        "title": "Public Task 1",
        "type_id": et.id,
        "body": "This was added by the public",
        "custom_fields": {"priority": "high", "notes": "some notes"},
    }
    auth_header = authenticated_client.headers.pop("Authorization", None)
    try:
        res = await authenticated_client.post(
            f"/api/public-share/track/{token}/entries", json=entry_body
        )
        assert res.status_code == 200, res.text
        entry_id = res.json()["entry"]["id"]
        assert res.json()["entry"]["author_id"] == "public"

        # 2. Try to update entry (should fail because update_entries is False)
        update_body = {"title": "Malicious edit"}
        res = await authenticated_client.patch(
            f"/api/public-share/track/{token}/entries/{entry_id}", json=update_body
        )
        assert res.status_code == 403

        # 3. Try to add comment (should fail because create_comments is False)
        comment_body = {"text": "My public opinion"}
        res = await authenticated_client.post(
            f"/api/public-share/track/{token}/entries/{entry_id}/comments",
            json=comment_body,
        )
        assert res.status_code == 403
    finally:
        if auth_header:
            authenticated_client.headers["Authorization"] = auth_header

    # 4. Enable updates and comments
    body["public_permissions"] = {
        "read_entries": True,
        "create_entries": True,
        "update_entries": True,
        "read_comments": True,
        "create_comments": True,
    }
    res = await authenticated_client.post(
        f"/api/tracks/{track.id}/public-share", json=body
    )

    auth_header = authenticated_client.headers.pop("Authorization", None)
    try:
        # 5. Successfully update entry
        res = await authenticated_client.patch(
            f"/api/public-share/track/{token}/entries/{entry_id}",
            json={
                "title": "Updated Public Task",
                "expected_record_revision": 1,
                "expected_schema_revision": 1,
            },
        )
        assert res.status_code == 200
        assert res.json()["entry"]["title"] == "Updated Public Task"
        assert res.json()["entry"]["record_revision"] == 2

        res = await authenticated_client.patch(
            f"/api/public-share/track/{token}/entries/{entry_id}",
            json={"title": "Stale public update", "expected_record_revision": 1},
        )
        assert res.status_code == 409
        assert res.json()["details"]["error_code"] == "record_revision_conflict"

        # 6. Successfully post a comment
        res = await authenticated_client.post(
            f"/api/public-share/track/{token}/entries/{entry_id}/comments",
            json={"text": "My public opinion"},
        )
        assert res.status_code == 200
        assert res.json()["comment"]["author_id"] == "public"
        assert res.json()["comment"]["text"] == "My public opinion"

        # 7. Read comments
        res = await authenticated_client.get(
            f"/api/public-share/track/{token}/entries/{entry_id}/comments"
        )
        assert res.status_code == 200
        assert res.json()["total"] == 1
        assert res.json()["comments"][0]["text"] == "My public opinion"
    finally:
        if auth_header:
            authenticated_client.headers["Authorization"] = auth_header
