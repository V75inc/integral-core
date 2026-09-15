"""F3 Phase One — commercial entitlement gate + revoke→pause contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest

from app.api.errors import InsufficientPermissionsError
from app.models.edges import CONTAINS
from app.models.nodes import App, ContentProfile, Entry
from app.services.app_lifecycle import install_app, resume_app
from app.services.content_profile_loader import load_library_profiles_with_issues
from app.services.entitlements import (
    grant_entitlement,
    revoke_entitlement,
)
from app.services.package_paths import resolve_package_class
from app.utils.time import utc_now_iso
from tests.fixtures.workspaces import make_org_workspace

REPO = Path(__file__).resolve().parents[3]
COMMERCIAL = REPO / "examples" / "reference-commercial-hello"
SLUG = "reference-commercial-hello"
ENT_KEY = "reference-commercial-hello"


def _community_sibling_manifest() -> Dict[str, Any]:
    """Minimal community App — proves sibling stays healthy without hello_board."""
    return {
        "content_profile_schema_version": 2,
        "scope": "app",
        "package": {
            "name": "sibling-community",
            "slug": "sibling-community",
            "class": "community_app",
            "version": "1.0.0",
            "description": "F3 sibling community App",
            "tags": ["test"],
        },
        "app": {
            "description": "Sibling for entitlement kill-switch contract",
            "tracks": [
                {
                    "key": "notes",
                    "name": "Notes",
                    "provision_on_create": True,
                    "entry_types": [
                        {
                            "key": "note",
                            "name": "Note",
                            "fields": [
                                {"key": "body", "name": "Body", "type": "markdown"},
                            ],
                        },
                    ],
                    "defaults": {"default_entry_type": "note"},
                    "views": [
                        {"key": "feed", "name": "Feed", "type": "feed"},
                    ],
                },
            ],
        },
    }


async def _seed_commercial_library() -> ContentProfile:
    specs, _ = load_library_profiles_with_issues(
        package_paths=[str(COMMERCIAL.parent)],
        core_only=False,
        verify_signatures=False,
    )
    spec = next(s for s in specs if s.slug == SLUG)
    now = utc_now_iso()
    return await ContentProfile.create(
        name=spec.name or SLUG,
        scope="app",
        manifest=spec.manifest,
        library_package=True,
        version=spec.version or "1.0.0",
        metadata={
            "slug": spec.slug,
            "bundle_fingerprint": getattr(spec, "bundle_fingerprint", "") or "test-fp",
            "package_class": spec.package_class,
            "bundle_dir": str(spec.bundle_dir) if spec.bundle_dir else str(COMMERCIAL),
        },
        created_at=now,
        updated_at=now,
    )


async def _seed_community_library() -> ContentProfile:
    manifest = _community_sibling_manifest()
    now = utc_now_iso()
    return await ContentProfile.create(
        name="Sibling Community",
        scope="app",
        manifest=manifest,
        library_package=True,
        version="1.0.0",
        metadata={
            "slug": "sibling-community",
            "bundle_fingerprint": "sibling-fp",
            "package_class": "community_app",
        },
        created_at=now,
        updated_at=now,
    )


@pytest.mark.contract
def test_commercial_package_class_resolves():
    """Commercial fixture resolves to package.class commercial_app."""
    assert COMMERCIAL.is_dir()
    specs, _ = load_library_profiles_with_issues(
        package_paths=[str(COMMERCIAL.parent)],
        core_only=False,
        verify_signatures=False,
    )
    spec = next(s for s in specs if s.slug == SLUG)
    assert spec.package_class == "commercial_app"
    assert (
        resolve_package_class(slug=SLUG, declared="commercial_app") == "commercial_app"
    )


@pytest.mark.contract
@pytest.mark.asyncio
async def test_entitlement_kill_switch_e2e(monkeypatch):
    """Deny install → grant → install → revoke pauses → data readable → sibling OK."""
    assert COMMERCIAL.is_dir()
    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(COMMERCIAL.parent))
    monkeypatch.setenv("INTEGRAL_CORE_ONLY", "0")

    ws = await make_org_workspace("ws-f3-entitlement")
    actor = "u_f3_ent"
    commercial_lib = await _seed_commercial_library()
    sibling_lib = await _seed_community_library()

    # Community sibling installs without entitlement.
    sibling = await install_app(
        workspace_id=ws.id,
        library_cp_id=sibling_lib.id,
        actor_id=actor,
        include_seed_data=False,
    )
    assert sibling["status"] == "active"
    sibling_app_id = sibling["app_id"]

    # Commercial install denied without entitlement.
    with pytest.raises(InsufficientPermissionsError) as denied:
        await install_app(
            workspace_id=ws.id,
            library_cp_id=commercial_lib.id,
            actor_id=actor,
            include_seed_data=False,
        )
    assert "entitlement" in str(denied.value).lower()

    # Grant → install succeeds.
    await grant_entitlement(
        workspace_id=ws.id,
        entitlement_key=ENT_KEY,
        package_slug=SLUG,
        actor_id=actor,
    )
    installed = await install_app(
        workspace_id=ws.id,
        library_cp_id=commercial_lib.id,
        actor_id=actor,
        include_seed_data=False,
    )
    assert installed["status"] == "active"
    app_id = installed["app_id"]
    app = await App.get(app_id)
    assert app is not None
    assert app.lifecycle_state == "active"
    assert app.installed_package_slug == SLUG

    # Create an entry under the commercial App track (data retained after revoke).
    tracks = await app.nodes(edge=[CONTAINS], node=["Track"])
    assert tracks
    track = tracks[0]
    entry = await Entry.create(
        title="Paid note",
        track_id=track.id,
        body="kept after revoke",
    )
    await track.connect(entry, edge=CONTAINS)
    entry_id = entry.id

    # Revoke → commercial App paused; sibling community App stays active.
    result = await revoke_entitlement(
        workspace_id=ws.id,
        entitlement_key=ENT_KEY,
        actor_id=actor,
    )
    assert result["status"] == "revoked"
    assert app_id in result["paused_app_ids"]
    assert result["data_access"] == "core_generic_read"

    paused = await App.get(app_id)
    assert paused is not None
    assert paused.lifecycle_state == "paused"

    sibling_app = await App.get(sibling_app_id)
    assert sibling_app is not None
    assert sibling_app.lifecycle_state == "active"

    # Core generic read of App data still works.
    still = await Entry.get(entry_id)
    assert still is not None
    assert still.title == "Paid note"

    # Core generic export remains available after entitlement revoke → pause.
    from app.services.app_export import export_app_bundle

    bundle = await export_app_bundle(app_id=app_id)
    assert bundle["policy"]["data_access"] == "core_generic_read"
    titles = {e.get("title") for e in bundle["entries"]}
    assert "Paid note" in titles

    # Resume blocked while entitlement revoked.
    with pytest.raises(InsufficientPermissionsError):
        await resume_app(app_id=app_id, actor_id=actor)

    # Re-grant → resume restores active.
    await grant_entitlement(
        workspace_id=ws.id,
        entitlement_key=ENT_KEY,
        package_slug=SLUG,
        actor_id=actor,
    )
    resumed = await resume_app(app_id=app_id, actor_id=actor)
    assert resumed["status"] == "active"
    final = await App.get(app_id)
    assert final is not None
    assert final.lifecycle_state == "active"
