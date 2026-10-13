"""Workspace member-pool access must not require admin authority to list Tracks."""

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["guest", "member"])
@pytest.mark.parametrize("grant", ["track", "app"])
async def test_invited_reader_can_list_shared_tracks_without_private_siblings(
    authenticated_client, second_user_client, test_user2, role, grant
):
    workspace = await authenticated_client.post(
        "/api/workspaces", json={"name": "Invited Tracks QA"}
    )
    assert workspace.status_code == 200, workspace.text
    workspace_id = workspace.json()["workspace"]["id"]
    scope = {"X-Integral-Scope": f"ws:{workspace_id}"}
    member = await authenticated_client.post(
        f"/api/workspaces/{workspace_id}/members",
        json={"member_user_id": test_user2.id, "role": role},
    )
    assert member.status_code == 200, member.text
    app_id = None
    if grant == "app":
        app = await authenticated_client.post(
            "/api/apps",
            headers=scope,
            json={"name": "Shared App", "visibility": "private"},
        )
        assert app.status_code == 200, app.text
        app_id = app.json()["app"]["id"]
    visible = await authenticated_client.post(
        "/api/tracks",
        headers=scope,
        json={
            "title": "Shared Track",
            "visibility": "private",
            **({"app_id": app_id} if app_id else {}),
        },
    )
    hidden = await authenticated_client.post(
        "/api/tracks",
        headers=scope,
        json={"title": "Private Sibling", "visibility": "private"},
    )
    assert visible.status_code == hidden.status_code == 200
    track_id = visible.json()["track"]["id"]
    share = await authenticated_client.post(
        f"/api/{'apps' if app_id else 'tracks'}/{app_id or track_id}/collaborators",
        headers=scope,
        json={"collaborator_user_id": test_user2.id, "role": "viewer"},
    )
    assert share.status_code == 200, share.text
    entry = await authenticated_client.post(
        "/api/entries",
        headers=scope,
        json={"track_id": track_id, "title": "Readable record"},
    )
    assert entry.status_code == 200, entry.text
    listed = await second_user_client.get("/api/tracks?limit=100", headers=scope)
    assert listed.status_code == 200, listed.text
    assert {row["id"] for row in listed.json()["tracks"]} == {track_id}
    assert listed.json()["scope_workspace_id"] == workspace_id


@pytest.mark.asyncio
async def test_invited_personal_workspace_guest_can_list_shared_track(
    authenticated_client, second_user_client, test_user2
):
    workspaces = await authenticated_client.get("/api/workspaces")
    assert workspaces.status_code == 200, workspaces.text
    workspace_id = next(
        ws["id"]
        for ws in workspaces.json()["workspaces"]
        if ws["kind"] == "personal" and ws["your_role"] == "owner"
    )
    scope = {"X-Integral-Scope": f"ws:{workspace_id}"}
    visible = await authenticated_client.post(
        "/api/tracks", headers=scope, json={"title": "Personally shared"}
    )
    hidden = await authenticated_client.post(
        "/api/tracks", headers=scope, json={"title": "Still private"}
    )
    assert visible.status_code == hidden.status_code == 200
    track_id = visible.json()["track"]["id"]
    share = await authenticated_client.post(
        f"/api/tracks/{track_id}/collaborators",
        headers=scope,
        json={"collaborator_user_id": test_user2.id, "role": "viewer"},
    )
    assert share.status_code == 200, share.text
    listed = await second_user_client.get("/api/tracks?limit=100", headers=scope)
    assert listed.status_code == 200, listed.text
    assert {row["id"] for row in listed.json()["tracks"]} == {track_id}


@pytest.mark.asyncio
async def test_accepted_track_invitation_can_list_tracks(
    authenticated_client, second_user_client, test_user2, monkeypatch
):
    async def suppress_email(_message):
        return None

    monkeypatch.setattr("app.services.invitations.send_email", suppress_email)
    workspace = await authenticated_client.post(
        "/api/workspaces", json={"name": "Accepted Invitation Tracks QA"}
    )
    assert workspace.status_code == 200, workspace.text
    workspace_id = workspace.json()["workspace"]["id"]
    scope = {"X-Integral-Scope": f"ws:{workspace_id}"}
    visible = await authenticated_client.post(
        "/api/tracks", headers=scope, json={"title": "Invited Track"}
    )
    hidden = await authenticated_client.post(
        "/api/tracks", headers=scope, json={"title": "Private Sibling"}
    )
    assert visible.status_code == hidden.status_code == 200
    track_id = visible.json()["track"]["id"]
    invitation = await authenticated_client.post(
        f"/api/tracks/{track_id}/invitations",
        headers=scope,
        json={"email": "test2@example.com", "role": "viewer"},
    )
    assert invitation.status_code == 200, invitation.text
    token = invitation.json()["acceptance_url"].rsplit("/", 1)[-1]
    accepted = await second_user_client.post(f"/api/invitations/{token}/accept")
    assert accepted.status_code == 200, accepted.text
    listed = await second_user_client.get("/api/tracks?limit=100", headers=scope)
    assert listed.status_code == 200, listed.text
    assert {row["id"] for row in listed.json()["tracks"]} == {track_id}
