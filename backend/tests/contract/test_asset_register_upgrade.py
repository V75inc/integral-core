"""Contract: library upgrade preserves tenant customizations (AC-07, WP-04)."""

from __future__ import annotations

import copy

import pytest

from app.models.nodes import App, ApplicationDefinition, ContentProfile
from app.services.app_lifecycle import install_app, update_app_from_library
from app.services.content_profile_runtime import compile_canonical_manifest
from app.utils.time import utc_now_iso
from tests.contract.asset_register_helpers import (
    ASSET_APP,
    seed_asset_register_library_cp,
)
from tests.fixtures.workspaces import make_org_workspace


@pytest.mark.contract
@pytest.mark.asyncio
async def test_upgrade_preserves_app_settings_and_bumps_version(monkeypatch):
    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(ASSET_APP.parent))
    monkeypatch.setenv("INTEGRAL_CORE_ONLY", "0")

    ws = await make_org_workspace("ws-asset-upgrade")
    actor_id = "u_asset_upgrade"
    lib_v1 = await seed_asset_register_library_cp(version="1.0.0")

    installed = await install_app(
        workspace_id=ws.id,
        library_cp_id=lib_v1.id,
        actor_id=actor_id,
        include_seed_data=False,
    )
    app_id = installed["app_id"]
    app = await App.get(app_id)
    assert app is not None
    custom_marker = "tenant-custom-settings"
    app.settings = {**(app.settings or {}), "tenant_marker": custom_marker}
    app.updated_at = utc_now_iso()
    await app.save()

    lib_v2_manifest = copy.deepcopy(lib_v1.manifest or {})
    lib_v2_manifest.setdefault("package", {})
    lib_v2_manifest["package"]["version"] = "1.1.0"
    lib_v2_manifest.setdefault("app", {})
    lib_v2_manifest["app"]["description"] = (
        str(lib_v2_manifest["app"].get("description") or "") + " (v1.1)"
    )
    lib_v1.manifest = lib_v2_manifest
    lib_v1.version = "1.1.0"
    lib_v1.updated_at = utc_now_iso()
    await lib_v1.save()

    upgraded = await update_app_from_library(app_id=app_id, actor_id=actor_id)
    assert upgraded["version_after"] == "1.1.0"

    app_after = await App.get(app_id)
    assert app_after is not None
    assert (app_after.settings or {}).get("tenant_marker") == custom_marker
    assert app_after.version == "1.1.0"
    assert app_after.active_definition_revision == 2
    active = await ApplicationDefinition.get(app_after.active_definition_id)
    assert active is not None
    assert active.status == "active"
    attached_profile = await ContentProfile.get(app_after.attached_content_profile_id)
    assert attached_profile is not None
    assert active.canonical_manifest == compile_canonical_manifest(
        manifest=attached_profile.manifest or {}
    )
    prior = await ApplicationDefinition.find({"app_id": app_id, "revision": 1})
    assert prior and prior[0].status == "superseded"
