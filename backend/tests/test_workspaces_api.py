"""W3 — /api/workspaces canonical surface."""

from datetime import datetime

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_workspaces_includes_personal(
    authenticated_client: AsyncClient, test_user
):
    r = await authenticated_client.get("/api/workspaces")
    assert r.status_code == 200, r.text
    items = r.json()["workspaces"]
    assert any(w.get("kind") == "personal" for w in items), items


@pytest.mark.asyncio
async def test_create_workspace_and_owner_role(
    authenticated_client: AsyncClient, test_user
):
    create = await authenticated_client.post(
        "/api/workspaces", json={"name": "Workspace API Default"}
    )
    assert create.status_code == 200, create.text
    ws = create.json()["workspace"]
    assert ws["kind"] == "organization"
    assert ws["workspace_type"] == "collaborative"
    assert ws["your_role"] == "owner"

    listing = await authenticated_client.get("/api/workspaces")
    items = listing.json()["workspaces"]
    assert any(w["id"] == ws["id"] and w["kind"] == "organization" for w in items)


@pytest.mark.asyncio
async def test_create_workspace_with_company_type(
    authenticated_client: AsyncClient, test_user
):
    create = await authenticated_client.post(
        "/api/workspaces",
        json={"name": "Acme Co", "workspace_type": "Company"},
    )
    assert create.status_code == 200, create.text
    ws = create.json()["workspace"]
    assert ws["workspace_type"] == "company"


@pytest.mark.asyncio
async def test_create_collaborative_workspace_type(
    authenticated_client: AsyncClient, test_user
):
    """workspace_type='collaborative' (the new canonical term) provisions a
    multi-member workspace — stored kind stays 'organization'."""
    create = await authenticated_client.post(
        "/api/workspaces",
        json={"name": "Acme Collab", "workspace_type": "collaborative"},
    )
    assert create.status_code == 200, create.text
    ws = create.json()["workspace"]
    assert ws["workspace_type"] == "collaborative"
    assert ws["kind"] == "organization"


@pytest.mark.asyncio
async def test_create_personal_workspace_via_api(
    authenticated_client: AsyncClient, test_user
):
    """workspace_type='personal' provisions a kind='personal' workspace
    alongside the auto-provisioned one."""
    create = await authenticated_client.post(
        "/api/workspaces",
        json={"name": "Side Notes", "workspace_type": "personal"},
    )
    assert create.status_code == 200, create.text
    ws = create.json()["workspace"]
    assert ws["kind"] == "personal"
    assert ws["workspace_type"] == "personal"


@pytest.mark.asyncio
async def test_cannot_delete_last_personal_workspace(
    authenticated_client: AsyncClient, test_user
):
    listing = await authenticated_client.get("/api/workspaces")
    personals = [w for w in listing.json()["workspaces"] if w.get("kind") == "personal"]
    assert len(personals) >= 1
    delete = await authenticated_client.delete(f"/api/workspaces/{personals[0]['id']}")
    assert delete.status_code == 400, delete.text
    assert "at least one personal workspace" in delete.json().get("message", "").lower()


@pytest.mark.asyncio
async def test_can_delete_extra_personal_workspace(
    authenticated_client: AsyncClient, test_user
):
    create = await authenticated_client.post(
        "/api/workspaces",
        json={"name": "Disposable Personal", "workspace_type": "personal"},
    )
    assert create.status_code == 200, create.text
    extra_id = create.json()["workspace"]["id"]

    delete = await authenticated_client.delete(f"/api/workspaces/{extra_id}")
    assert delete.status_code == 200, delete.text

    listing = await authenticated_client.get("/api/workspaces")
    ids = [w["id"] for w in listing.json()["workspaces"]]
    assert extra_id not in ids
    assert any(w.get("kind") == "personal" for w in listing.json()["workspaces"])


@pytest.mark.asyncio
async def test_owner_can_delete_organization_workspace(
    authenticated_client: AsyncClient, test_user
):
    create = await authenticated_client.post(
        "/api/workspaces", json={"name": "Disposable Org"}
    )
    assert create.status_code == 200, create.text
    org_id = create.json()["workspace"]["id"]

    delete = await authenticated_client.delete(f"/api/workspaces/{org_id}")
    assert delete.status_code == 200, delete.text

    listing = await authenticated_client.get("/api/workspaces")
    ids = [w["id"] for w in listing.json()["workspaces"]]
    assert org_id not in ids


@pytest.mark.asyncio
async def test_personal_workspace_members_returns_owner_only(
    authenticated_client: AsyncClient, test_user
):
    listing = await authenticated_client.get("/api/workspaces")
    personal = next(
        w for w in listing.json()["workspaces"] if w.get("kind") == "personal"
    )
    members = await authenticated_client.get(
        f"/api/workspaces/{personal['id']}/members"
    )
    assert members.status_code == 200
    payload = members.json()
    assert payload["total"] == 1
    assert payload["members"][0]["role"] == "owner"


@pytest.mark.asyncio
async def test_workspace_spaces_listing(authenticated_client: AsyncClient, test_user):
    # Create a personal-scope App; should appear under the user's
    # personal workspace listing.
    sp = await authenticated_client.post(
        "/api/apps", json={"name": "Workspace Listing App"}
    )
    assert sp.status_code == 200
    sp_id = sp.json()["app"]["id"]

    listing = await authenticated_client.get("/api/workspaces")
    personal = next(
        w for w in listing.json()["workspaces"] if w.get("kind") == "personal"
    )
    spaces_resp = await authenticated_client.get(
        f"/api/workspaces/{personal['id']}/apps"
    )
    ids = [s["id"] for s in spaces_resp.json()["apps"]]
    assert sp_id in ids


@pytest.mark.asyncio
async def test_workspace_tracks_includes_uncataloged_app_contained_track(
    authenticated_client: AsyncClient, test_user
):
    """Workspace tracks listing must include app-contained tracks even when
    they are missing from the workspace Tracks branch registry.

    This mirrors historical anchor/entry-extension tracks created via the
    auto-provision path before branch cataloging was consistent.
    """
    from app.models.edges import CONTAINS
    from app.models.nodes import App, Track

    listing = await authenticated_client.get("/api/workspaces")
    personal = next(
        w for w in listing.json()["workspaces"] if w.get("kind") == "personal"
    )
    workspace_id = personal["id"]

    created_app = await authenticated_client.post(
        "/api/apps", json={"name": "Uncataloged Track App"}
    )
    assert created_app.status_code == 200, created_app.text
    app_id = created_app.json()["app"]["id"]
    app = await App.get(app_id)
    assert app is not None

    now = datetime.now().isoformat()
    shadow_track = await Track.create(
        title="Entry extension shadow",
        title_fold="entry extension shadow",
        owner_id=test_user.id,
        workspace_id=workspace_id,
        visibility="inherit",
        created_at=now,
        updated_at=now,
    )
    await app.connect(shadow_track, edge=CONTAINS, added_at=now)

    tracks_resp = await authenticated_client.get(
        f"/api/workspaces/{workspace_id}/tracks"
    )
    assert tracks_resp.status_code == 200, tracks_resp.text
    ids = [t["id"] for t in tracks_resp.json()["tracks"]]
    assert shadow_track.id in ids


@pytest.mark.asyncio
async def test_workspace_admin_can_patch_settings_and_manage_members(
    authenticated_client: AsyncClient,
    second_user_client: AsyncClient,
    second_user,
    test_user,
):
    """Workspace ``IS_MEMBER_OF`` admin may edit settings and member pool."""
    if not second_user or not getattr(second_user, "id", None):
        pytest.skip("second_user unavailable")

    create = await authenticated_client.post(
        "/api/workspaces",
        json={"name": "Admin Rights Org"},
    )
    assert create.status_code == 200, create.text
    ws_id = create.json()["workspace"]["id"]

    add_admin = await authenticated_client.post(
        f"/api/workspaces/{ws_id}/members",
        json={
            "member_user_id": second_user.id,
            "role": "admin",
            "can_create_apps": True,
            "can_create_tracks": False,
        },
    )
    assert add_admin.status_code == 200, add_admin.text

    patch_ws = await second_user_client.patch(
        f"/api/workspaces/{ws_id}",
        json={"name": "Admin Rights Org (renamed)"},
    )
    assert patch_ws.status_code == 200, patch_ws.text
    assert patch_ws.json()["workspace"]["name"] == "Admin Rights Org (renamed)"

    listing = await second_user_client.get("/api/workspaces")
    assert listing.status_code == 200, listing.text
    row = next(w for w in listing.json()["workspaces"] if w["id"] == ws_id)
    assert row["your_role"] == "admin"
    assert row.get("can_create_apps") is True
    assert row.get("can_create_tracks") is False


@pytest.mark.asyncio
async def test_workspace_member_cannot_manage_members(
    authenticated_client: AsyncClient,
    second_user_client: AsyncClient,
    second_user,
):
    if not second_user or not getattr(second_user, "id", None):
        pytest.skip("second_user unavailable")

    create = await authenticated_client.post(
        "/api/workspaces",
        json={"name": "Member Rights Org"},
    )
    assert create.status_code == 200, create.text
    ws_id = create.json()["workspace"]["id"]

    add_member = await authenticated_client.post(
        f"/api/workspaces/{ws_id}/members",
        json={"member_user_id": second_user.id, "role": "member"},
    )
    assert add_member.status_code == 200, add_member.text

    denied = await second_user_client.post(
        f"/api/workspaces/{ws_id}/members",
        json={"member_user_id": second_user.id, "role": "guest"},
    )
    assert denied.status_code == 403, denied.text
