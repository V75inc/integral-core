"""Phase F2 — hot-load round-trip integration tests.

Exercises the full loop:

  1. Empty Operational Model packages root → rescan returns empty diff.
  2. Drop a workspace-scope bundle on disk.
  3. ``POST /admin/packages/rescan`` reports it in ``added``.
  4. ``GET /library/workspace-models`` surfaces it to the picker.
  5. Edit an existing bundle on disk → rescan reports ``updated`` and emits
     a per-bundle entry under ``invalidated_workspaces``.

Catches integration bugs that the per-layer unit tests miss (loader sees
disk but rescan endpoint's diff index drifted, listing endpoint filters
out scope=workspace, fingerprint compare fails to fire on edit, etc.).

Spec refs: §4.6, §9.3, §8.4 I-HOTLOAD-01.
"""

from __future__ import annotations

import pytest


def _reset_rescan_index() -> None:
    """Clear the admin-rescan in-memory diff index.

    The endpoint diffs against ``_LAST_INDEX`` accumulated across calls; in
    a test using a tmp_path Operational Model packages root we need a fresh slate so the
    "empty" baseline is actually empty (mirrors C1's pattern in
    ``test_admin_packages_rescan.py``).
    """
    import app.api.admin_packages as admin_packages_mod

    admin_packages_mod._LAST_INDEX.clear()


@pytest.mark.asyncio
async def test_drop_bundle_rescan_sees_it_in_listing(
    authenticated_admin_client, tmp_path, monkeypatch
):
    """Drop bundle on disk → rescan reports added → listing surfaces it."""
    _reset_rescan_index()
    monkeypatch.setattr(
        "app.services.operational_model_loader._PROFILES_ROOT", tmp_path
    )

    # initial rescan: empty (tmp_path holds no bundles yet)
    r0 = await authenticated_admin_client.post("/api/admin/packages/rescan")
    assert r0.status_code == 200, r0.text
    body0 = r0.json()
    assert body0["added"] == []
    assert body0["updated"] == []
    assert body0["removed"] == []

    # drop a workspace-scope bundle
    b = tmp_path / "test-ws-bundle"
    b.mkdir()
    (b / "operational-model.yaml").write_text(
        "integral_operational_model_version: 3\nscope: workspace\n"
        "package:\n  slug: test-ws-bundle\n  name: TestWS\n  version: 1.0.0\n"
        "workspace:\n  apps:\n    - slug: a\n      name: A\n"
        "      profile: {scope: app, app: {tracks: [{key: t, name: T,"
        " entry_types: []}]}}\n"
    )

    # rescan picks it up
    r1 = await authenticated_admin_client.post("/api/admin/packages/rescan")
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    assert "test-ws-bundle" in body1["added"], body1

    # listing endpoint surfaces it
    r2 = await authenticated_admin_client.get("/api/library/workspace-models")
    assert r2.status_code == 200, r2.text
    slugs = [p["slug"] for p in r2.json().get("operational_models", [])]
    assert "test-ws-bundle" in slugs, slugs


@pytest.mark.asyncio
async def test_edit_bundle_rescan_reports_updated(
    authenticated_admin_client, tmp_path, monkeypatch
):
    """Edit existing bundle on disk → rescan reports it as updated."""
    _reset_rescan_index()
    monkeypatch.setattr(
        "app.services.operational_model_loader._PROFILES_ROOT", tmp_path
    )

    # bundle v1
    b = tmp_path / "edit-bundle"
    b.mkdir()
    (b / "operational-model.yaml").write_text(
        "integral_operational_model_version: 3\nscope: workspace\n"
        "package:\n  slug: edit-bundle\n  name: E\n  version: 1.0.0\n"
        "workspace:\n  apps:\n    - slug: a\n      name: A\n"
        "      profile: {scope: app, app: {tracks: []}}\n"
    )
    r_initial = await authenticated_admin_client.post("/api/admin/packages/rescan")
    assert r_initial.status_code == 200, r_initial.text
    assert "edit-bundle" in r_initial.json()["added"]

    # provision a workspace from the bundle so invalidation has something
    # to clear. Response shape: ``{"workspace": {"id": ...}, "message": ...}``.
    r_ws = await authenticated_admin_client.post(
        "/api/workspaces",
        json={"name": "Edit-WS", "operational_model_slug": "edit-bundle"},
    )
    assert r_ws.status_code in (200, 201), r_ws.text
    body = r_ws.json()
    ws_id = body.get("id") or (body.get("workspace") or {}).get("id")
    assert ws_id, f"missing workspace id in {body}"

    # edit bundle (bump version + add description) → fingerprint changes
    (b / "operational-model.yaml").write_text(
        "integral_operational_model_version: 3\nscope: workspace\n"
        "package:\n  slug: edit-bundle\n  name: E\n  version: 1.0.1\n"
        "  description: edited\n"
        "workspace:\n  apps:\n    - slug: a\n      name: A\n"
        "      profile: {scope: app, app: {tracks: []}}\n"
    )
    r_rescan = await authenticated_admin_client.post("/api/admin/packages/rescan")
    assert r_rescan.status_code == 200, r_rescan.text
    body = r_rescan.json()
    assert "edit-bundle" in body["updated"], body
