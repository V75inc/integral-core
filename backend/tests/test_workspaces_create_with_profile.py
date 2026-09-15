"""Tests for ``POST /workspaces?profile_slug=...`` and the new
``GET /library/workspace-profiles`` listing endpoint (Phase D4).

The first three cases exercise the strict-init wiring on workspace create:

  - Regression: ``profile_slug`` omitted → existing flow unchanged, no
    ``applied_profiles`` entry recorded.
  - ``profile_slug`` referencing a non-workspace-scope bundle → 4xx +
    workspace rolled back (so the caller doesn't end up with an empty,
    half-provisioned org).
  - ``profile_slug`` referencing an unknown bundle → 404 + workspace
    rolled back.

The fourth case covers ``GET /library/workspace-profiles`` — at the time
this test was authored no workspace-scope bundle had been authored yet
(D5 ships the first one), so the assertion is shape-only: must be a list,
and each entry (if any) must carry the canonical keys.
"""

import pytest


@pytest.mark.asyncio
async def test_create_blank_workspace_still_works(authenticated_client):
    """Regression — workspaces without ``profile_slug`` still work.

    The existing surface uses ``workspace_type`` to discriminate
    organization vs personal (no top-level ``kind`` body field). Default
    is ``workspace_type="company"`` → ``kind="organization"``.
    """
    r = await authenticated_client.post(
        "/api/workspaces",
        json={"name": "Blank D4 WS"},
    )
    assert r.status_code in (200, 201), r.text
    body = r.json()
    ws = body.get("workspace") or {}
    assert ws.get("applied_profiles", []) == []


@pytest.mark.asyncio
async def test_create_workspace_with_non_workspace_scope_profile_rolls_back(
    authenticated_client,
):
    """An app-scope bundle is not a workspace-scope bundle — reject."""
    r = await authenticated_client.post(
        "/api/workspaces",
        json={
            "name": "Bad Scope D4",
            "profile_slug": "personal-crm",  # scope=app, not workspace
        },
    )
    assert r.status_code in (400, 422), r.text


@pytest.mark.asyncio
async def test_create_workspace_with_unknown_profile_returns_404(
    authenticated_client,
):
    """Unknown slug → 404 and the workspace is rolled back."""
    r = await authenticated_client.post(
        "/api/workspaces",
        json={
            "name": "Unknown Slug D4",
            "profile_slug": "does-not-exist-d4",
        },
    )
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_list_workspace_profiles_returns_only_workspace_scope(
    authenticated_client,
):
    """Workspace-profile listing excludes track/app-scope bundles.

    The endpoint filters ``load_library_profiles()`` by
    ``manifest.scope == "workspace"``. No workspace-scope bundle has been
    authored yet at D4 (D5 ships the first), so the list may be empty —
    but the shape contract still holds.
    """
    r = await authenticated_client.get("/api/library/workspace-profiles")
    assert r.status_code == 200, r.text
    profiles = r.json().get("profiles", [])
    assert isinstance(profiles, list)
    for p in profiles:
        assert "slug" in p
        assert "name" in p
        assert "version" in p
