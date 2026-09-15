"""Account deletion lifecycle tests."""

from __future__ import annotations

from datetime import datetime

import pytest
from httpx import AsyncClient
from jvspatial.api.auth.models import User as AuthUser

from app.models.edges import IS_MEMBER_OF, OWNS
from app.models.nodes import App, User, Workspace
from app.services.app_graph import catalog_app, catalog_workspace
from app.services.personal_workspace import ensure_personal_workspace


@pytest.mark.asyncio
async def test_deletion_blocked_by_org_owned_app(
    authenticated_client: AsyncClient,
    test_user,
):
    """Deletion is blocked while the user owns an app in an org workspace."""
    user = test_user
    if user is None:
        pytest.skip("test_user unavailable")
    now = datetime.now().isoformat()
    org_ws = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="Blocker Org",
        name_fold="blocker org",
        description="",
        accent_color="",
        created_at=now,
        updated_at=now,
    )
    await user.connect(
        org_ws,
        edge=IS_MEMBER_OF,
        role="owner",
        joined_at=now,
        can_create_apps=True,
        can_create_tracks=True,
    )
    await catalog_workspace(org_ws)
    app = await App.create(
        name="Org App",
        name_fold="org app",
        workspace_id=org_ws.id,
        owner_user_id=user.id,
        created_at=now,
        updated_at=now,
    )
    await user.connect(app, edge=OWNS, role="owner", granted_at=now)
    await catalog_app(app)

    preview = await authenticated_client.get("/api/users/me/account-deletion-preview")
    assert preview.status_code == 200
    body = preview.json()
    assert body["can_delete"] is False
    assert any(b["code"] == "org_owned_resources" for b in body["blockers"])

    delete_resp = await authenticated_client.request(
        "DELETE",
        f"/api/users/{user.id}",
        json={"confirm_email": "test@example.com"},
    )
    assert delete_resp.status_code == 409


@pytest.mark.asyncio
async def test_deletion_blocked_sole_org_owner_with_members(
    authenticated_client: AsyncClient,
    test_user,
    test_user2,
):
    """Deletion is blocked when sole org owner with other members."""
    owner = test_user
    member = test_user2
    if owner is None or member is None:
        pytest.skip("test users unavailable")
    now = datetime.now().isoformat()
    org_ws = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="Shared Org",
        name_fold="shared org",
        description="",
        accent_color="",
        created_at=now,
        updated_at=now,
    )
    await owner.connect(
        org_ws,
        edge=IS_MEMBER_OF,
        role="owner",
        joined_at=now,
        can_create_apps=True,
        can_create_tracks=True,
    )
    await member.connect(
        org_ws,
        edge=IS_MEMBER_OF,
        role="member",
        joined_at=now,
        can_create_apps=False,
        can_create_tracks=False,
    )
    await catalog_workspace(org_ws)

    preview = await authenticated_client.get("/api/users/me/account-deletion-preview")
    assert preview.status_code == 200
    body = preview.json()
    assert body["can_delete"] is False
    assert any(b["code"] == "sole_org_owner_with_members" for b in body["blockers"])

    delete_resp = await authenticated_client.request(
        "DELETE",
        f"/api/users/{owner.id}",
        json={"confirm_email": "test@example.com"},
    )
    assert delete_resp.status_code == 409


@pytest.mark.asyncio
async def test_deletion_removes_auth_user(
    authenticated_client: AsyncClient,
    test_user,
):
    """Successful deletion removes AuthUser credentials."""
    user = test_user
    if user is None:
        pytest.skip("test_user unavailable")
    auth_id = user.user_id
    assert auth_id

    resp = await authenticated_client.request(
        "DELETE",
        f"/api/users/{user.id}",
        json={"confirm_email": "test@example.com"},
    )
    assert resp.status_code == 200

    auth_users = await AuthUser.find({"id": auth_id})
    assert not auth_users

    graph_users = await User.find({"context.user_id": auth_id})
    assert not graph_users


@pytest.mark.asyncio
async def test_deletion_cascades_personal_workspace(
    authenticated_client: AsyncClient,
    test_user,
):
    """Personal workspace is removed on account deletion."""
    user = test_user
    if user is None:
        pytest.skip("test_user unavailable")
    personal = await ensure_personal_workspace(user)
    personal_id = personal.id

    resp = await authenticated_client.request(
        "DELETE",
        f"/api/users/{user.id}",
        json={"confirm_email": "test@example.com"},
    )
    assert resp.status_code == 200

    ws = await Workspace.get(personal_id)
    assert ws is None
