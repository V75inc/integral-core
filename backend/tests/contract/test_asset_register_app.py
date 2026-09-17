"""Contract: independent Asset Register install (AC-02, WP-05)."""

from __future__ import annotations

import pytest

from app.models.nodes import App
from app.services.app_lifecycle import install_app
from app.services.app_operations.registry import list_registered_operations
from tests.contract.asset_register_helpers import ASSET_APP, seed_asset_register_library_cp
from tests.fixtures.workspaces import make_org_workspace


@pytest.mark.contract
@pytest.mark.asyncio
async def test_asset_register_installs_from_examples_only(monkeypatch):
    assert ASSET_APP.is_dir()
    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(ASSET_APP.parent))
    monkeypatch.setenv("INTEGRAL_CORE_ONLY", "0")

    ws = await make_org_workspace("ws-asset-install")
    lib = await seed_asset_register_library_cp()
    installed = await install_app(
        workspace_id=ws.id,
        library_cp_id=lib.id,
        actor_id="u_asset_install",
        include_seed_data=False,
    )
    app_id = installed["app_id"]
    app = await App.get(app_id)
    assert app is not None
    assert app.lifecycle_state == "active"
    assert app.installed_package_slug == "asset-register"

    ops = list_registered_operations(ws.id, app_id)
    assert "register_asset" in ops
    assert "check_out_asset" in ops
    assert "review_warranties" in ops
