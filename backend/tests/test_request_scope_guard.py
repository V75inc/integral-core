"""SM3 — an inaccessible ``X-Integral-Scope`` must fail, not fall back.

Before this fix, ``X-Integral-Scope: ws:<unknown-or-forbidden-id>`` silently
resolved to the caller's Personal Workspace and returned its contents with a
200. Not a cross-tenant leak (it is the caller's own data), but it contradicted
the documented contract — "backend validates caller has access to that
workspace and refuses cross-workspace reads" — and made a client pinned to a
stale workspace id look like it was working.

Contract now:
  * no header            → Personal Workspace (unchanged; deliberate)
  * ``ws:<accessible>``  → that workspace
  * ``ws:<unknown>``     → 403
  * ``ws:<other user's>``→ 403 (indistinguishable from unknown, by design)
  * ``<bare id>``        → 400 (missing the mandatory ``ws:`` prefix)
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_no_scope_header_falls_back_to_personal_workspace(
    authenticated_client: AsyncClient, test_user
):
    """Absent header stays a valid request resolving to Personal Workspace."""
    created = await authenticated_client.post(
        "/api/tracks", json={"title": "Guard Personal Track", "visibility": "private"}
    )
    assert created.status_code == 200, created.text

    listing = await authenticated_client.get("/api/tracks")
    assert listing.status_code == 200, listing.text
    titles = [t["title"] for t in listing.json()["tracks"]]
    assert "Guard Personal Track" in titles


@pytest.mark.asyncio
async def test_accessible_scope_header_is_honored(
    authenticated_client: AsyncClient, test_user
):
    """A workspace the caller belongs to still resolves normally."""
    ws = await authenticated_client.post(
        "/api/workspaces", json={"name": "Guard Accessible Org"}
    )
    assert ws.status_code == 200, ws.text
    workspace_id = ws.json()["workspace"]["id"]

    listing = await authenticated_client.get(
        "/api/tracks", headers={"X-Integral-Scope": f"ws:{workspace_id}"}
    )
    assert listing.status_code == 200, listing.text
    assert listing.json().get("scope_workspace_id") == workspace_id


@pytest.mark.asyncio
async def test_unknown_workspace_scope_is_rejected(
    authenticated_client: AsyncClient, test_user
):
    """A workspace id that does not exist must 403, not serve Personal."""
    listing = await authenticated_client.get(
        "/api/tracks", headers={"X-Integral-Scope": "ws:n.Workspace.deadbeef"}
    )
    assert listing.status_code == 403, listing.text
    assert "tracks" not in listing.json()


@pytest.mark.asyncio
async def test_other_users_workspace_scope_is_rejected(
    authenticated_client: AsyncClient,
    second_user_client: AsyncClient,
    test_user,
    second_user,
):
    """A real workspace the caller has no membership in must 403."""
    ws = await second_user_client.post(
        "/api/workspaces", json={"name": "Guard Foreign Org"}
    )
    assert ws.status_code == 200, ws.text
    foreign_id = ws.json()["workspace"]["id"]

    listing = await authenticated_client.get(
        "/api/tracks", headers={"X-Integral-Scope": f"ws:{foreign_id}"}
    )
    assert listing.status_code == 403, listing.text
    assert "tracks" not in listing.json()


@pytest.mark.asyncio
async def test_bare_workspace_id_without_ws_prefix_is_rejected(
    authenticated_client: AsyncClient, test_user
):
    """A header missing the ``ws:`` prefix parses to None — that must 400."""
    ws = await authenticated_client.post(
        "/api/workspaces", json={"name": "Guard Bare Id Org"}
    )
    assert ws.status_code == 200, ws.text
    workspace_id = ws.json()["workspace"]["id"]

    listing = await authenticated_client.get(
        "/api/tracks", headers={"X-Integral-Scope": workspace_id}
    )
    assert listing.status_code == 400, listing.text
    assert "tracks" not in listing.json()


@pytest.mark.asyncio
async def test_resolver_raises_for_inaccessible_scope(test_user):
    """Unit-level: the resolver itself raises rather than returning a fallback."""
    from jvspatial.api.exceptions import InsufficientPermissionsError

    from app.services.request_scope import resolve_workspace_id_from_request

    class _Req:
        headers = {"x-integral-scope": "ws:n.Workspace.does-not-exist"}
        state = None

    with pytest.raises(InsufficientPermissionsError):
        await resolve_workspace_id_from_request(_Req(), test_user.id)


@pytest.mark.asyncio
async def test_foreign_scope_rejects_create_effects(
    authenticated_client: AsyncClient,
    second_user_client: AsyncClient,
    test_user,
    second_user,
):
    """A create must not succeed in a fallback workspace after a bad header."""
    foreign = await second_user_client.post(
        "/api/workspaces", json={"name": "Create Scope Foreign Org"}
    )
    assert foreign.status_code == 200, foreign.text
    foreign_id = foreign.json()["workspace"]["id"]
    headers = {"X-Integral-Scope": f"ws:{foreign_id}"}

    for path, body in (
        ("/api/tracks", {"title": "Forbidden Scoped Track"}),
        ("/api/apps", {"name": "Forbidden Scoped App"}),
    ):
        response = await authenticated_client.post(path, headers=headers, json=body)
        assert response.status_code == 403, response.text

    tracks = await authenticated_client.get("/api/tracks")
    apps = await authenticated_client.get("/api/apps")
    assert "Forbidden Scoped Track" not in [
        track["title"] for track in tracks.json()["tracks"]
    ]
    assert "Forbidden Scoped App" not in [app["name"] for app in apps.json()["apps"]]


@pytest.mark.asyncio
async def test_create_header_selects_workspace_and_rejects_body_conflict(
    authenticated_client: AsyncClient, test_user
):
    """A valid create header binds the effect and cannot disagree with body."""
    first = await authenticated_client.post(
        "/api/workspaces", json={"name": "Create Scope First Org"}
    )
    second = await authenticated_client.post(
        "/api/workspaces", json={"name": "Create Scope Second Org"}
    )
    assert first.status_code == second.status_code == 200
    first_id = first.json()["workspace"]["id"]
    second_id = second.json()["workspace"]["id"]
    headers = {"X-Integral-Scope": f"ws:{first_id}"}

    track = await authenticated_client.post(
        "/api/tracks", headers=headers, json={"title": "Bound Track"}
    )
    assert track.status_code == 200, track.text
    assert track.json()["track"]["workspace_id"] == first_id

    app = await authenticated_client.post(
        "/api/apps", headers=headers, json={"name": "Bound App"}
    )
    assert app.status_code == 200, app.text
    assert app.json()["app"]["workspace_id"] == first_id

    for path, body in (
        ("/api/tracks", {"title": "Conflicted Track", "workspace_id": second_id}),
        ("/api/apps", {"name": "Conflicted App", "workspace_id": second_id}),
    ):
        response = await authenticated_client.post(path, headers=headers, json=body)
        assert response.status_code == 400, response.text

    other_app = await authenticated_client.post(
        "/api/apps", json={"name": "Other Workspace App", "workspace_id": second_id}
    )
    assert other_app.status_code == 200, other_app.text
    nested = await authenticated_client.post(
        "/api/tracks",
        headers=headers,
        json={"title": "Conflicted App Track", "app_id": other_app.json()["app"]["id"]},
    )
    assert nested.status_code == 400, nested.text


@pytest.mark.asyncio
async def test_parent_resource_creates_reject_foreign_or_mismatched_scope(
    authenticated_client: AsyncClient,
    second_user_client: AsyncClient,
    test_user,
    second_user,
):
    """Tag and view writes must agree with the authenticated header scope."""
    own = await authenticated_client.post(
        "/api/workspaces", json={"name": "Parent Scope Own Org"}
    )
    other = await authenticated_client.post(
        "/api/workspaces", json={"name": "Parent Scope Other Org"}
    )
    foreign = await second_user_client.post(
        "/api/workspaces", json={"name": "Parent Scope Foreign Org"}
    )
    assert own.status_code == other.status_code == foreign.status_code == 200
    own_id = own.json()["workspace"]["id"]
    other_id = other.json()["workspace"]["id"]
    foreign_id = foreign.json()["workspace"]["id"]
    track = await authenticated_client.post(
        "/api/tracks", json={"title": "Parent Scope Track", "workspace_id": own_id}
    )
    app = await authenticated_client.post(
        "/api/apps", json={"name": "Parent Scope App", "workspace_id": own_id}
    )
    assert track.status_code == app.status_code == 200
    track_id = track.json()["track"]["id"]
    app_id = app.json()["app"]["id"]

    cases = (
        ("/api/tags", {"name": "Guard Track Tag", "track_id": track_id}),
        ("/api/tags", {"name": "Guard App Tag", "app_id": app_id}),
        (f"/api/tracks/{track_id}/views", {"name": "Guard View"}),
    )
    for workspace_id, expected_status in ((foreign_id, 403), (other_id, 400)):
        headers = {"X-Integral-Scope": f"ws:{workspace_id}"}
        for path, body in cases:
            response = await authenticated_client.post(path, headers=headers, json=body)
            assert response.status_code == expected_status, response.text

    own_headers = {"X-Integral-Scope": f"ws:{own_id}"}
    tags = await authenticated_client.get(
        "/api/tags", headers=own_headers, params={"track_id": track_id}
    )
    views = await authenticated_client.get(
        f"/api/tracks/{track_id}/views", headers=own_headers
    )
    assert tags.status_code == views.status_code == 200
    assert "Guard Track Tag" not in [tag["name"] for tag in tags.json()["tags"]]
    assert "Guard View" not in [view["name"] for view in views.json()["views"]]


@pytest.mark.asyncio
async def test_entry_and_comment_effects_match_header_scope(
    authenticated_client: AsyncClient,
    second_user_client: AsyncClient,
    test_user,
    second_user,
):
    own = await authenticated_client.post(
        "/api/workspaces", json={"name": "Entry Scope Own Org"}
    )
    other = await authenticated_client.post(
        "/api/workspaces", json={"name": "Entry Scope Other Org"}
    )
    foreign = await second_user_client.post(
        "/api/workspaces", json={"name": "Entry Scope Foreign Org"}
    )
    assert own.status_code == other.status_code == foreign.status_code == 200
    own_id = own.json()["workspace"]["id"]
    track = await authenticated_client.post(
        "/api/tracks", json={"title": "Entry Scope Track", "workspace_id": own_id}
    )
    assert track.status_code == 200, track.text
    track_id = track.json()["track"]["id"]
    entry = await authenticated_client.post(
        "/api/entries",
        headers={"X-Integral-Scope": f"ws:{own_id}"},
        json={"track_id": track_id, "title": "Entry Scope Existing"},
    )
    assert entry.status_code == 200, entry.text
    entry_id = entry.json()["entry"]["id"]

    for workspace_id, expected_status in (
        (foreign.json()["workspace"]["id"], 403),
        (other.json()["workspace"]["id"], 400),
    ):
        headers = {"X-Integral-Scope": f"ws:{workspace_id}"}
        create_entry = await authenticated_client.post(
            "/api/entries",
            headers=headers,
            json={"track_id": track_id, "title": "Entry Scope Rejected"},
        )
        create_comment = await authenticated_client.post(
            f"/api/entries/{entry_id}/comments",
            headers=headers,
            json={"text": "Comment Scope Rejected"},
        )
        assert create_entry.status_code == expected_status, create_entry.text
        assert create_comment.status_code == expected_status, create_comment.text
