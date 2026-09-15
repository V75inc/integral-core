"""Phase D5: end-to-end test for the ``crm-pm-workspace`` sample bundle.

This is the first ``scope: workspace`` bundle and exercises BOTH sub-manifest
resolution modes:

  - ``crm`` declares a ``profile_ref`` resolved via
    ``load_library_profiles()`` (library-sibling reference). Post Phase 31
    (DR-31-01), the resolved library bundle is the **crm** bundle (slug:
    ``crm``) — the original ``crm-plus-pm-suite`` was decomposed into
    four standalone bundles.
  - ``projects`` is resolved via the library ``projects`` profile (display
    name ``Projects``) — the former inline ``project-ops`` sub-manifest
    was retired when the suite decomposed into standalone bundles.

The test verifies the FIRST level of provisioning: after applying the
bundle to a fresh workspace, ``GET /workspaces/{id}/apps`` returns the
two expected Apps with the names declared in the manifest. Deep substrate
materialization (entry types, views, plugins) is exercised by the broader
content-profile test suite and the merge-library tests; D5's contract is
"the two Apps exist and ``applied_profiles`` records the bundle."

NOTE on response shapes (validated against actual endpoint behavior):
  - ``POST /workspaces`` returns ``{"workspace": {...}, "message": "..."}``
    rather than the bare workspace, so we read the id from
    ``body["workspace"]["id"]``. Status is 200/201 per existing tests.
  - ``GET /workspaces/{id}/apps`` returns ``{"apps": [...], "total": ...,
    "workspace_id": ...}`` with each app exported via ``export_node`` —
    the ``name`` field carries the App's display name.
"""

import pytest


@pytest.mark.asyncio
async def test_apply_crm_pm_workspace_creates_two_apps(authenticated_client):
    r = await authenticated_client.post(
        "/api/workspaces",
        json={"name": "Acme D5", "profile_slug": "crm-pm-workspace"},
    )
    assert r.status_code in (200, 201), r.text
    body = r.json()
    ws = body.get("workspace") or {}
    ws_id = ws.get("id")
    assert ws_id, f"workspace id missing in response: {body}"

    r2 = await authenticated_client.get(
        f"/api/workspaces/{ws_id}/apps",
        headers={"X-Integral-Scope": ws_id},
    )
    assert r2.status_code == 200, r2.text
    apps = r2.json().get("apps") or []
    names = sorted(a["name"] for a in apps)
    assert names == [
        "CRM",
        "Projects",
    ], f"expected ['CRM', 'Projects'], got {names}"


@pytest.mark.asyncio
async def test_crm_pm_workspace_apps_have_tracks(authenticated_client):
    """G1: after applying the workspace bundle, each App must have >=1 Track.

    Before G1, sub-manifests were only stashed on ``App.metadata.pending_submanifest``
    and never materialized — the workspace came up with two empty Apps. This
    test guards against that regression by asserting the Projects App
    (library profile ``projects``) carries at least one Track after apply.
    """
    r = await authenticated_client.post(
        "/api/workspaces",
        json={"name": "AcmeG1", "profile_slug": "crm-pm-workspace"},
    )
    assert r.status_code in (200, 201), r.text
    body = r.json()
    ws = body.get("workspace") or {}
    ws_id = ws.get("id")
    assert ws_id, f"workspace id missing in response: {body}"

    r2 = await authenticated_client.get(
        f"/api/workspaces/{ws_id}/apps",
        headers={"X-Integral-Scope": ws_id},
    )
    assert r2.status_code == 200, r2.text
    apps = r2.json().get("apps") or []
    assert len(apps) == 2, f"expected 2 apps, got {apps}"

    projects_app = next((a for a in apps if a["name"] == "Projects"), None)
    assert projects_app is not None, f"Projects App missing in {apps}"

    r3 = await authenticated_client.get(
        f"/api/apps/{projects_app['id']}/tracks",
        headers={"X-Integral-Scope": ws_id},
    )
    assert r3.status_code == 200, r3.text
    tracks = r3.json().get("tracks") or []
    assert len(tracks) >= 1, f"expected >=1 track under Projects App, got {tracks}"
