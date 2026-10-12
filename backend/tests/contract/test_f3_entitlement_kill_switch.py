"""F3 Phase One — commercial entitlement gate + revoke→pause contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest

from app.api.errors import InsufficientPermissionsError
from app.models.edges import CONTAINS
from app.models.nodes import App, Entry, OperationalModel
from app.services.app_lifecycle import install_app, resume_app
from app.services.entitlements import (
    grant_entitlement,
    revoke_entitlement,
)
from app.services.operational_model_loader import (
    load_library_operational_models_with_issues,
)
from app.services.package_paths import resolve_package_class
from tests.fixtures.library_models import publish_library_manifest, publish_library_spec
from tests.fixtures.workspaces import make_org_workspace, workspace_owner_id

REPO = Path(__file__).resolve().parents[3]
COMMERCIAL = REPO / "examples" / "reference-commercial-hello"
SLUG = "reference-commercial-hello"
ENT_KEY = "reference-commercial-hello"


def _community_sibling_manifest() -> Dict[str, Any]:
    """Minimal community App — proves sibling stays healthy without hello_board."""
    return {
        "operational_model_schema_version": 2,
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


async def _seed_commercial_library() -> OperationalModel:
    specs, _ = load_library_operational_models_with_issues(
        package_paths=[str(COMMERCIAL.parent)],
        core_only=False,
        verify_signatures=False,
    )
    spec = next(s for s in specs if s.slug == SLUG)
    return await publish_library_spec(spec)


async def _seed_community_library() -> OperationalModel:
    manifest = _community_sibling_manifest()
    return await publish_library_manifest(manifest=manifest, name="Sibling Community")


@pytest.mark.contract
@pytest.mark.asyncio
async def test_host_override_is_transient_and_restart_closes_access(monkeypatch):
    from unittest.mock import AsyncMock

    from app.api.errors import EntitlementRequiredError
    from app.services import host_hooks
    from app.services.app_lifecycle import pause_app
    from app.services.application_definitions import get_active_application_definition
    from app.services.entitlements import list_entitlements
    from app.services.hooks.install_hook import rehydrate_all_installed_bundles
    from app.services.resource_location import locate_resources

    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(COMMERCIAL.parent))
    monkeypatch.setenv("INTEGRAL_CORE_ONLY", "0")
    monkeypatch.setattr(
        host_hooks, "_entitlement_access_resolver", AsyncMock(return_value=True)
    )
    ws = await make_org_workspace("transient-host-access")
    actor = await workspace_owner_id(ws)
    library = await _seed_commercial_library()
    installed = await install_app(
        workspace_id=ws.id,
        library_cp_id=library.id,
        actor_id=actor,
        include_seed_data=False,
    )
    app_id = installed["app_id"]
    assert await list_entitlements(workspace_id=ws.id) == []
    # Most catalog packages use the installation slug as the default key;
    # compiled definitions do not retain that library identity.
    app_node = await App.get(app_id)
    definition = await get_active_application_definition(app_node)
    definition.canonical_manifest["package"].pop("slug", None)
    definition.canonical_manifest["package"].pop("entitlement_key", None)
    await definition.save()
    assert (await locate_resources("app", [app_id]))[app_id] is not None
    await pause_app(app_id=app_id, actor_id=actor)
    await resume_app(app_id=app_id, actor_id=actor)
    monkeypatch.setattr(host_hooks, "_entitlement_access_resolver", None)
    await rehydrate_all_installed_bundles()
    assert (await App.get(app_id)).lifecycle_state == "paused"
    with pytest.raises(EntitlementRequiredError):
        await resume_app(app_id=app_id, actor_id=actor)
    assert await list_entitlements(workspace_id=ws.id) == []


@pytest.mark.contract
def test_commercial_package_class_resolves():
    """Commercial fixture resolves to package.class commercial_app."""
    assert COMMERCIAL.is_dir()
    specs, _ = load_library_operational_models_with_issues(
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
    actor = await workspace_owner_id(ws)
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
