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
