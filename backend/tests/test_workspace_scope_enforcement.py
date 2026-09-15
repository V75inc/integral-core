"""W5 — server enforces workspace scope on list endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_tracks_defaults_to_personal_workspace(
    authenticated_client: AsyncClient, test_user
):
    """No X-Integral-Scope → server defaults to Personal Workspace (fail-closed)."""
    # Create one personal track and one org-scoped track.
    org_r = await authenticated_client.post(
        "/api/workspaces", json={"name": "Scope Enforce Org"}
    )
    assert org_r.status_code == 200, org_r.text
    org_id = org_r.json()["workspace"]["id"]
    personal_r = await authenticated_client.post(
        "/api/tracks", json={"title": "Personal Enforce", "visibility": "private"}
    )
    org_track_r = await authenticated_client.post(
        "/api/tracks",
        json={"title": "Org Enforce", "workspace_id": org_id},
    )
    assert personal_r.status_code == 200
    assert org_track_r.status_code == 200

    # No header → Personal only.
    listing = await authenticated_client.get("/api/tracks")
    assert listing.status_code == 200
    titles = [t["title"] for t in listing.json()["tracks"]]
    assert "Personal Enforce" in titles
    assert "Org Enforce" not in titles


@pytest.mark.asyncio
async def test_list_tracks_filters_to_workspace_header(
    authenticated_client: AsyncClient, test_user
):
    """X-Integral-Scope: ws:<id> filters to that workspace only."""
    # Create a fresh org-workspace via /workspaces.
    ws_r = await authenticated_client.post(
        "/api/workspaces", json={"name": "WS Header Test"}
    )
    assert ws_r.status_code == 200, ws_r.text
    workspace_id = ws_r.json()["workspace"]["id"]

    # Personal + org track.
    await authenticated_client.post(
        "/api/tracks", json={"title": "Header Personal Track"}
    )
    # Create org-track via legacy org endpoint pointing at the paired org.
    # The Workspace.legacy_org_id back-pointer connects them.
    # Easier: just call /api/workspaces/{id}/tracks listing after creating
    # an org via /api/workspaces + ensure the paired workspace shows.
    legacy_org = await authenticated_client.post(
        "/api/workspaces", json={"name": "Header Org"}
    )
    assert legacy_org.status_code == 200, legacy_org.text
    org_id = legacy_org.json()["workspace"]["id"]
    await authenticated_client.post(
        "/api/tracks",
        json={"title": "Header Org Track", "workspace_id": org_id},
    )

    # Filter to the freshly-created Workspace (which has no tracks).
    listing = await authenticated_client.get(
        "/api/tracks",
        headers={"X-Integral-Scope": f"ws:{workspace_id}"},
    )
    titles = [t["title"] for t in listing.json()["tracks"]]
    assert "Header Personal Track" not in titles
    assert "Header Org Track" not in titles
    assert listing.json().get("scope_workspace_id") == workspace_id


async def _create_track_in_ws(client: AsyncClient, title: str, workspace_id=None):
    body = {"title": title, "visibility": "private"}
    if workspace_id:
        body["workspace_id"] = workspace_id
    r = await client.post("/api/tracks", json=body)
    assert r.status_code == 200, r.text
    return r.json()["track"]["id"]


async def _create_entry_in_track(client: AsyncClient, track_id: str, title: str):
    r = await client.post(
        "/api/entries",
        json={"track_id": track_id, "title": title, "body": "x"},
    )
    assert r.status_code == 200, r.text
    return r.json()["entry"]["id"]


@pytest.mark.asyncio
async def test_feed_total_is_scoped_to_active_workspace(
    authenticated_client: AsyncClient, test_user
):
    """`/feed` total + entries respect X-Integral-Scope (modality preservation)."""
    org_r = await authenticated_client.post(
        "/api/workspaces", json={"name": "Feed Scope Org"}
    )
    assert org_r.status_code == 200, org_r.text
    org_id = org_r.json()["workspace"]["id"]

    personal_track = await _create_track_in_ws(authenticated_client, "Feed P Track")
    org_track = await _create_track_in_ws(
        authenticated_client, "Feed O Track", workspace_id=org_id
    )
    await _create_entry_in_track(authenticated_client, personal_track, "P Entry")
    await _create_entry_in_track(authenticated_client, org_track, "O Entry")

    # Org scope returns only the org entry.
    r_org = await authenticated_client.get(
        "/api/feed", headers={"X-Integral-Scope": f"ws:{org_id}"}
    )
    assert r_org.status_code == 200, r_org.text
    titles_org = [e["title"] for e in r_org.json()["entries"]]
    assert "O Entry" in titles_org
    assert "P Entry" not in titles_org
    assert r_org.json()["total"] == len(titles_org)

    # Personal scope returns only the personal entry (no header → Personal).
    r_personal = await authenticated_client.get("/api/feed")
    assert r_personal.status_code == 200
    titles_personal = [e["title"] for e in r_personal.json()["entries"]]
    assert "P Entry" in titles_personal
    assert "O Entry" not in titles_personal


@pytest.mark.asyncio
async def test_feed_entries_alias_respects_scope(
    authenticated_client: AsyncClient, test_user
):
    """`/feed_entries` delegates to `/feed` so it must also be scoped."""
    org_r = await authenticated_client.post(
        "/api/workspaces", json={"name": "FeedEntries Scope Org"}
    )
    assert org_r.status_code == 200, org_r.text
    org_id = org_r.json()["workspace"]["id"]
    org_track = await _create_track_in_ws(
        authenticated_client, "FeedAlias O Track", workspace_id=org_id
    )
    await _create_entry_in_track(authenticated_client, org_track, "Alias O Entry")

    r = await authenticated_client.get(
        "/api/feed_entries", headers={"X-Integral-Scope": f"ws:{org_id}"}
    )
    assert r.status_code == 200
    titles = [e["title"] for e in r.json()["entries"]]
    assert titles == ["Alias O Entry"]


@pytest.mark.asyncio
async def test_entries_list_total_is_scoped_to_active_workspace(
    authenticated_client: AsyncClient, test_user
):
    """`/entries` listing total + rows respect X-Integral-Scope."""
    org_r = await authenticated_client.post(
        "/api/workspaces", json={"name": "Entries Scope Org"}
    )
    assert org_r.status_code == 200, org_r.text
    org_id = org_r.json()["workspace"]["id"]

    personal_track = await _create_track_in_ws(authenticated_client, "Entries P Track")
    org_track = await _create_track_in_ws(
        authenticated_client, "Entries O Track", workspace_id=org_id
    )
    await _create_entry_in_track(authenticated_client, personal_track, "EP Entry")
    await _create_entry_in_track(authenticated_client, org_track, "EO Entry")

    r_org = await authenticated_client.get(
        "/api/entries", headers={"X-Integral-Scope": f"ws:{org_id}"}
    )
    assert r_org.status_code == 200, r_org.text
    titles_org = [e["title"] for e in r_org.json()["entries"]]
    assert "EO Entry" in titles_org
    assert "EP Entry" not in titles_org

    r_personal = await authenticated_client.get("/api/entries")
    assert r_personal.status_code == 200
    titles_personal = [e["title"] for e in r_personal.json()["entries"]]
    assert "EP Entry" in titles_personal
    assert "EO Entry" not in titles_personal
