"""Projects bundle install-smoke and anchor provisioning tests."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

pytestmark = [pytest.mark.smoke, pytest.mark.library]


async def _personal_workspace_id(client) -> str:
    resp = await client.get("/api/workspaces")
    assert resp.status_code == 200, resp.text
    for ws in resp.json().get("workspaces") or []:
        if ws.get("kind") == "personal":
            return str(ws["id"])
    pytest.fail("no personal workspace found for test user")


def _scope_headers(client, workspace_id: str) -> Dict[str, str]:
    auth = client.headers.get("Authorization", "")
    return {
        "Authorization": auth,
        "X-Integral-Scope": f"ws:{workspace_id}",
    }


async def _projects_library_profile_id() -> str:
    from app.models.nodes import ContentProfile

    listed = await ContentProfile.find({"context.library_package": True})
    rows: List[Any] = (
        listed if isinstance(listed, list) else ([listed] if listed else [])
    )
    for cp in rows:
        meta = getattr(cp, "metadata", {}) or {}
        if str(meta.get("slug") or "") == "projects":
            return str(cp.id)
    pytest.fail("projects library ContentProfile not cataloged")


async def _install_projects_app(client, workspace_id: str) -> str:
    lib_id = await _projects_library_profile_id()
    headers = _scope_headers(client, workspace_id)
    resp = await client.post(
        f"/api/workspaces/{workspace_id}/apps/install",
        json={"library_content_profile_id": lib_id},
        headers=headers,
    )
    assert resp.status_code in (200, 201), resp.text
    payload = resp.json()
    if payload.get("status") == "awaiting_settings":
        finalize = await client.post(
            f"/api/apps/{payload['app_id']}/install/settings",
            json={
                "install_token": payload.get("install_token") or "",
                "settings": {},
            },
            headers=headers,
        )
        assert finalize.status_code in (200, 201), finalize.text
        payload = finalize.json()
    app = payload.get("app") or payload
    app_id = str(app.get("id") or payload.get("app_id") or "")
    assert app_id, payload
    return app_id


async def _projects_track_id(client, app_id: str, workspace_id: str) -> str:
    headers = _scope_headers(client, workspace_id)
    resp = await client.get(f"/api/apps/{app_id}/tracks", headers=headers)
    assert resp.status_code == 200, resp.text
    for track in resp.json().get("tracks") or []:
        if (track.get("title") or "").strip() in {
            "Customer Projects",
            "Projects",
        }:
            return str(track["id"])
    pytest.fail("Customer Projects track missing after install")


async def _project_entry_type_id(
    client, track_id: str, workspace_id: str
) -> Optional[str]:
    headers = _scope_headers(client, workspace_id)
    resp = await client.get(
        "/api/entry-types",
        headers=headers,
        params={"track_id": track_id},
    )
    if resp.status_code != 200:
        return None
    for et in resp.json().get("entry_types") or []:
        if (et.get("name") or "").strip().lower() == "project":
            return str(et.get("id") or "")
    return None


@pytest.mark.asyncio
async def test_projects_bundle_install_smoke(
    authenticated_client, library_catalog_seeded
):
    """Install Projects library bundle → track + manifest seeds exist."""
    client = authenticated_client
    ws_id = await _personal_workspace_id(client)
    app_id = await _install_projects_app(client, ws_id)
    track_id = await _projects_track_id(client, app_id, ws_id)

    headers = _scope_headers(client, ws_id)
    entries_resp = await client.get(
        f"/api/tracks/{track_id}/entries",
        headers=headers,
        params={"limit": 50},
    )
    assert entries_resp.status_code == 200, entries_resp.text
    titles = {
        (e.get("title") or "").strip() for e in entries_resp.json().get("entries") or []
    }
    assert "Onboarding: Contoso e-commerce" in titles
    assert "Northwind multi-region rollout" in titles
    assert len(titles) >= 3

    tracks_resp = await client.get(f"/api/apps/{app_id}/tracks", headers=headers)
    assert tracks_resp.status_code == 200, tracks_resp.text
    track_titles = {
        (t.get("title") or "").strip() for t in tracks_resp.json().get("tracks") or []
    }
    assert "Sprints" in track_titles


@pytest.mark.asyncio
async def test_projects_install_seeds_have_details_and_sprint_project(
    authenticated_client, library_catalog_seeded
):
    """Install seeds auto-provision details_track; Contoso sprint links project."""
    client = authenticated_client
    ws_id = await _personal_workspace_id(client)
    app_id = await _install_projects_app(client, ws_id)
    headers = _scope_headers(client, ws_id)

    tracks_resp = await client.get(f"/api/apps/{app_id}/tracks", headers=headers)
    assert tracks_resp.status_code == 200, tracks_resp.text
    tracks = tracks_resp.json().get("tracks") or []
    projects_tid = None
    sprints_tid = None
    for track in tracks:
        title = (track.get("title") or "").strip()
        if title in {"Customer Projects", "Projects"}:
            projects_tid = str(track["id"])
        if title == "Sprints":
            sprints_tid = str(track["id"])
    assert projects_tid and sprints_tid

    proj_resp = await client.get(
        f"/api/tracks/{projects_tid}/entries",
        headers=headers,
        params={"limit": 50},
    )
    assert proj_resp.status_code == 200, proj_resp.text
    contoso = None
    for entry in proj_resp.json().get("entries") or []:
        if (entry.get("title") or "").strip() == "Onboarding: Contoso e-commerce":
            contoso = entry
            break
    assert contoso, "Contoso project seed missing"
    contoso_id = str(contoso["id"])
    get_proj = await client.get(f"/api/entries/{contoso_id}", headers=headers)
    assert get_proj.status_code == 200, get_proj.text
    cf = (get_proj.json().get("entry") or {}).get("custom_fields") or {}
    assert cf.get("details_track"), "install seed Contoso should auto-provision details"

    sprint_resp = await client.get(
        f"/api/tracks/{sprints_tid}/entries",
        headers=headers,
        params={"limit": 50},
    )
    assert sprint_resp.status_code == 200, sprint_resp.text
    contoso_sprint = None
    for entry in sprint_resp.json().get("entries") or []:
        if "Contoso Sprint 1" in (entry.get("title") or ""):
            contoso_sprint = entry
            break
    assert contoso_sprint, "Contoso sprint seed missing"
    get_sprint = await client.get(
        f"/api/entries/{contoso_sprint['id']}", headers=headers
    )
    assert get_sprint.status_code == 200, get_sprint.text
    scf = (get_sprint.json().get("entry") or {}).get("custom_fields") or {}
    project_val = scf.get("project")
    if isinstance(project_val, list):
        assert contoso_id in project_val
    else:
        assert project_val == contoso_id


@pytest.mark.asyncio
async def test_projects_anchor_provision(authenticated_client, library_catalog_seeded):
    """Creating a project auto-provisions details; financials/contracts stay opt-in."""
    client = authenticated_client
    ws_id = await _personal_workspace_id(client)
    app_id = await _install_projects_app(client, ws_id)
    track_id = await _projects_track_id(client, app_id, ws_id)
    type_id = await _project_entry_type_id(client, track_id, ws_id)
    assert type_id

    headers = _scope_headers(client, ws_id)
    create_resp = await client.post(
        "/api/entries",
        headers=headers,
        json={
            "track_id": track_id,
            "title": "Smoke test project",
            "body": "Anchor provisioning check",
            "type_id": type_id,
            "custom_fields": {
                "status": "todo",
                "budget": 10000,
            },
        },
    )
    assert create_resp.status_code in (200, 201), create_resp.text
    entry = create_resp.json().get("entry") or {}
    entry_id = str(entry.get("id") or "")
    assert entry_id

    get_resp = await client.get(f"/api/entries/{entry_id}", headers=headers)
    assert get_resp.status_code == 200, get_resp.text
    cf = (get_resp.json().get("entry") or {}).get("custom_fields") or {}
    assert cf.get("details_track")
    assert not cf.get("financials_track")
    assert not cf.get("contracts_track")
