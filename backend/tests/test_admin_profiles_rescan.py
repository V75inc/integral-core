"""Phase C1 — admin profile hot-load + introspection endpoint tests.

Spec refs: §4.6, §9.3, §8.4 I-HOTLOAD-01.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_rescan_unauthenticated_is_rejected(client):
    """No JWT -> 401/403 (handled by jvspatial auth / TestAuthBypass)."""
    r = await client.post("/api/admin/profiles/rescan")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_rescan_non_admin_is_rejected(client, test_user):
    """Authenticated but non-admin caller -> 403.

    Note: jvspatial promotes the FIRST registered user to ``admin`` role
    automatically (``user_count == 0`` branch). The default
    ``authenticated_client`` fixture trips that path, so we mint a JWT
    directly with ``roles=["user"]`` to exercise the non-admin gate.
    """
    import jwt as pyjwt
    from httpx import ASGITransport, AsyncClient

    from app.config import settings
    from tests.conftest import TEST_BASE_URL, USE_LIVE_SERVER, get_app

    user_id = getattr(test_user, "user_id", None) or getattr(test_user, "id", "")
    if not user_id:
        pytest.skip("no test_user available (live-server mode)")

    payload = {
        "user_id": user_id,
        "email": getattr(test_user, "email", "") or "test@example.com",
        "name": getattr(test_user, "display_name", "") or "Test User",
        "roles": ["user"],  # explicitly non-admin
        "permissions": [],
    }
    token = pyjwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

    if USE_LIVE_SERVER:
        non_admin = AsyncClient(
            base_url=TEST_BASE_URL,
            timeout=10.0,
            headers={"Authorization": f"Bearer {token}"},
        )
    else:
        non_admin = AsyncClient(
            transport=ASGITransport(app=get_app()),
            base_url="http://test",
            timeout=10.0,
            headers={"Authorization": f"Bearer {token}"},
        )
    try:
        r = await non_admin.post("/api/admin/profiles/rescan")
        assert r.status_code == 403, r.text
    finally:
        await non_admin.aclose()


@pytest.mark.asyncio
async def test_rescan_returns_diff_after_bundle_drop(
    authenticated_admin_client, tmp_path, monkeypatch
):
    """Admin caller: empty scan -> drop bundle -> next scan reports added.

    Monkeypatches the loader's module-level ``_PROFILES_ROOT`` to a tmp
    path so the test owns the scanned tree exclusively.
    """
    # Reset the rescan endpoint's in-memory index between tests so the
    # tmp_path's "empty" state actually shows up as empty.
    import app.api.admin_profiles as admin_profiles_mod

    admin_profiles_mod._LAST_INDEX.clear()

    monkeypatch.setattr("app.services.content_profile_loader._PROFILES_ROOT", tmp_path)

    # initial scan: empty (no bundles in tmp_path)
    r1 = await authenticated_admin_client.post("/api/admin/profiles/rescan")
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    assert body1["added"] == []
    assert body1["updated"] == []
    assert body1["removed"] == []

    # drop a bundle
    b = tmp_path / "new-bundle"
    b.mkdir()
    (b / "profile.yaml").write_text(
        "integral_profile_version: 3\n"
        "scope: track\n"
        "package:\n"
        "  slug: new-bundle\n"
        "  name: New Bundle\n"
        "  version: 1.0.0\n"
        "  description: test bundle\n"
        "track:\n"
        "  entry_types: []\n"
    )

    r2 = await authenticated_admin_client.post("/api/admin/profiles/rescan")
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert "new-bundle" in body2["added"]
    assert body2["updated"] == []
    assert body2["removed"] == []
    assert "issues" in body2
    assert "reconciled_removed" in body2


@pytest.mark.asyncio
async def test_list_loaded_profiles_includes_issues_and_rescan_deactivates_removed_bundle(
    authenticated_admin_client, tmp_path, monkeypatch
):
    import app.api.admin_profiles as admin_profiles_mod
    from app.models.nodes import ContentProfile

    admin_profiles_mod._LAST_INDEX.clear()
    monkeypatch.setattr("app.services.content_profile_loader._PROFILES_ROOT", tmp_path)

    bad = tmp_path / "bad-bundle"
    bad.mkdir()
    (bad / "profile.yaml").write_text(
        "integral_profile_version: 3\n"
        "scope: track\n"
        "package:\n"
        "  slug: wrong-slug\n"
        "  name: Bad Bundle\n"
        "track:\n"
        "  entry_types: []\n"
    )
    bundle = tmp_path / "remove-me"
    bundle.mkdir()
    (bundle / "profile.yaml").write_text(
        "integral_profile_version: 3\n"
        "scope: track\n"
        "package:\n"
        "  slug: remove-me\n"
        "  name: Remove Me\n"
        "  version: 1.0.0\n"
        "track:\n"
        "  entry_types: []\n"
    )

    listed = await authenticated_admin_client.get("/api/admin/profiles")
    assert listed.status_code == 200, listed.text
    payload = listed.json()
    assert any(i.get("code") == "slug_mismatch" for i in payload.get("issues", []))

    seeded = await authenticated_admin_client.post("/api/admin/profiles/rescan")
    assert seeded.status_code == 200, seeded.text
    assert "remove-me" in seeded.json().get("added", [])

    (bundle / "profile.yaml").unlink()
    removed = await authenticated_admin_client.post("/api/admin/profiles/rescan")
    assert removed.status_code == 200, removed.text
    body = removed.json()
    assert "remove-me" in body.get("removed", [])
    assert body.get("reconciled_removed")

    rows = await ContentProfile.find({"context.metadata.slug": "remove-me"})
    if rows is None:
        found = []
    elif isinstance(rows, list):
        found = rows
    else:
        found = [rows]
    assert found
    assert found[0].library_package is False
