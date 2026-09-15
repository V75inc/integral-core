"""Duplicate-prevention regression tests for the validation framework."""

import pytest
from httpx import AsyncClient

# ── Workspaces ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_duplicate_organization_workspace_under_same_owner_rejected(
    authenticated_client: AsyncClient, test_user
):
    r1 = await authenticated_client.post("/api/workspaces", json={"name": "Dup Org A"})
    assert r1.status_code == 200, r1.text
    r2 = await authenticated_client.post("/api/workspaces", json={"name": "Dup Org A"})
    assert r2.status_code == 409, r2.text
    body = r2.json()
    assert body.get("error_code") == "resource.duplicate_workspace"


@pytest.mark.asyncio
async def test_workspace_name_collision_is_case_insensitive(
    authenticated_client: AsyncClient, test_user
):
    r1 = await authenticated_client.post("/api/workspaces", json={"name": "CaseWS"})
    assert r1.status_code == 200, r1.text
    r2 = await authenticated_client.post("/api/workspaces", json={"name": "caseWS"})
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_workspace_name_collision_strips_whitespace(
    authenticated_client: AsyncClient, test_user
):
    r1 = await authenticated_client.post(
        "/api/workspaces", json={"name": "  spaceyws  "}
    )
    assert r1.status_code == 200, r1.text
    r2 = await authenticated_client.post("/api/workspaces", json={"name": "spaceyws"})
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_workspace_empty_name_rejected(
    authenticated_client: AsyncClient, test_user
):
    r = await authenticated_client.post("/api/workspaces", json={"name": "   "})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_workspace_bad_accent_color_rejected(
    authenticated_client: AsyncClient, test_user
):
    r = await authenticated_client.post(
        "/api/workspaces", json={"name": "BadColorWS", "accent_color": "not-hex"}
    )
    assert r.status_code == 400


# ── Apps ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_duplicate_space_in_same_workspace_rejected(
    authenticated_client: AsyncClient, test_user
):
    r1 = await authenticated_client.post("/api/apps", json={"name": "Dup App"})
    assert r1.status_code == 200, r1.text
    r2 = await authenticated_client.post("/api/apps", json={"name": "Dup App"})
    assert r2.status_code == 409
    assert r2.json().get("error_code") == "resource.duplicate_app"


@pytest.mark.asyncio
async def test_space_case_only_collision(authenticated_client: AsyncClient, test_user):
    r1 = await authenticated_client.post("/api/apps", json={"name": "MixedCaseSpace"})
    assert r1.status_code == 200
    r2 = await authenticated_client.post("/api/apps", json={"name": "mixedcasespace"})
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_space_empty_name_rejected(authenticated_client: AsyncClient, test_user):
    r = await authenticated_client.post("/api/apps", json={"name": ""})
    assert r.status_code == 400


# ── Tracks ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_duplicate_track_in_same_workspace_rejected(
    authenticated_client: AsyncClient, test_user
):
    r1 = await authenticated_client.post(
        "/api/tracks", json={"title": "Dup Track Title"}
    )
    assert r1.status_code == 200, r1.text
    r2 = await authenticated_client.post(
        "/api/tracks", json={"title": "Dup Track Title"}
    )
    assert r2.status_code == 409
    assert r2.json().get("error_code") == "resource.duplicate_track"


@pytest.mark.asyncio
async def test_track_whitespace_only_collision(
    authenticated_client: AsyncClient, test_user
):
    r1 = await authenticated_client.post("/api/tracks", json={"title": "spacey-track"})
    assert r1.status_code == 200
    r2 = await authenticated_client.post(
        "/api/tracks", json={"title": "  spacey-track  "}
    )
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_track_empty_title_rejected(authenticated_client: AsyncClient, test_user):
    r = await authenticated_client.post("/api/tracks", json={"title": "   "})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_track_rename_to_existing_collides(
    authenticated_client: AsyncClient, test_user
):
    r1 = await authenticated_client.post(
        "/api/tracks", json={"title": "Original Track A"}
    )
    assert r1.status_code == 200
    r2 = await authenticated_client.post("/api/tracks", json={"title": "Other Track B"})
    assert r2.status_code == 200
    rename = await authenticated_client.put(
        f"/api/tracks/{r2.json()['track']['id']}",
        json={"title": "Original Track A"},
    )
    assert rename.status_code == 409


@pytest.mark.asyncio
async def test_track_self_rename_no_op_allowed(
    authenticated_client: AsyncClient, test_user
):
    r1 = await authenticated_client.post(
        "/api/tracks", json={"title": "Self Rename Track"}
    )
    assert r1.status_code == 200
    t_id = r1.json()["track"]["id"]
    same = await authenticated_client.put(
        f"/api/tracks/{t_id}", json={"title": "Self Rename Track"}
    )
    assert same.status_code == 200


# ── Views ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_duplicate_view_in_same_track_rejected(
    authenticated_client: AsyncClient, test_user
):
    tr = await authenticated_client.post("/api/tracks", json={"title": "Views Track"})
    assert tr.status_code == 200
    t_id = tr.json()["track"]["id"]
    v1 = await authenticated_client.post(
        f"/api/tracks/{t_id}/views",
        json={"name": "My Board", "type": "kanban"},
    )
    assert v1.status_code == 200, v1.text
    v2 = await authenticated_client.post(
        f"/api/tracks/{t_id}/views",
        json={"name": "MY BOARD", "type": "kanban"},
    )
    assert v2.status_code == 409
    assert v2.json().get("error_code") == "resource.duplicate_view"


# ── EntryTypes ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_entry_type_rename_to_existing_collides(
    authenticated_client: AsyncClient, test_user
):
    tr = await authenticated_client.post(
        "/api/tracks", json={"title": "ET Rename Track"}
    )
    assert tr.status_code == 200
    t_id = tr.json()["track"]["id"]
    a = await authenticated_client.post(
        "/api/entry-types", json={"name": "Note", "track_id": t_id}
    )
    assert a.status_code == 200
    b = await authenticated_client.post(
        "/api/entry-types", json={"name": "Task", "track_id": t_id}
    )
    assert b.status_code == 200
    rename = await authenticated_client.put(
        f"/api/entry-types/{b.json()['entry_type']['id']}",
        json={"name": "note"},  # case-only diff to "Note"
    )
    assert rename.status_code == 409


# ── Tags ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tag_rename_to_existing_collides(
    authenticated_client: AsyncClient, test_user
):
    tr = await authenticated_client.post(
        "/api/tracks", json={"title": "Tag Rename Track"}
    )
    assert tr.status_code == 200
    t_id = tr.json()["track"]["id"]
    a = await authenticated_client.post(
        "/api/tags", json={"name": "important", "track_id": t_id}
    )
    assert a.status_code == 200
    b = await authenticated_client.post(
        "/api/tags", json={"name": "urgent", "track_id": t_id}
    )
    assert b.status_code == 200
    rename = await authenticated_client.put(
        f"/api/tags/{b.json()['tag']['id']}",
        json={"name": "IMPORTANT"},
    )
    assert rename.status_code == 409


@pytest.mark.asyncio
async def test_tag_bad_hex_color_rejected(authenticated_client: AsyncClient, test_user):
    tr = await authenticated_client.post("/api/tracks", json={"title": "Hex Tag Track"})
    assert tr.status_code == 200
    t_id = tr.json()["track"]["id"]
    bad = await authenticated_client.post(
        "/api/tags",
        json={"name": "bad-color-tag", "track_id": t_id, "color": "not-hex"},
    )
    assert bad.status_code == 400


# ── Invitations ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_duplicate_pending_invitation_rejected(
    authenticated_client: AsyncClient, test_user
):
    ws = await authenticated_client.post(
        "/api/workspaces", json={"name": "Inv Dupe Org"}
    )
    assert ws.status_code == 200
    ws_id = ws.json()["workspace"]["id"]
    a = await authenticated_client.post(
        f"/api/workspaces/{ws_id}/invitations",
        json={"email": "newperson@example.com", "role": "member"},
    )
    assert a.status_code == 200, a.text
    b = await authenticated_client.post(
        f"/api/workspaces/{ws_id}/invitations",
        json={"email": "newperson@example.com", "role": "member"},
    )
    assert b.status_code == 409
    assert b.json().get("error_code") == "resource.duplicate_invitation"


@pytest.mark.asyncio
async def test_invitation_bad_email_rejected(
    authenticated_client: AsyncClient, test_user
):
    ws = await authenticated_client.post(
        "/api/workspaces", json={"name": "Inv Bad Email Org"}
    )
    assert ws.status_code == 200
    ws_id = ws.json()["workspace"]["id"]
    bad = await authenticated_client.post(
        f"/api/workspaces/{ws_id}/invitations",
        json={"email": "not-an-email", "role": "member"},
    )
    assert bad.status_code == 400
