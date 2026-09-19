"""Contract: independent Asset Register install (AC-02, WP-05)."""

from __future__ import annotations

import importlib
import sys

import pytest

from app.models.edges import CONTAINS, IS_MEMBER_OF
from app.models.nodes import App
from app.services.app_lifecycle import install_app
from app.services.app_operations.context import OperationContext
from app.services.app_operations.registry import list_registered_operations
from tests.contract.asset_register_helpers import (
    ASSET_APP,
    seed_asset_register_library_cp,
)
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


@pytest.mark.contract
@pytest.mark.asyncio
async def test_operation_context_creates_a_typed_entry_in_its_installed_app(
    monkeypatch,
):
    """The reference App's creation facade is present on the real runtime.

    This is deliberately not a fake ``OperationContext``: the Asset Register
    uses ``create_entry`` while checking out an asset, so its advertised SDK
    method must resolve its manifest type and take the ordinary create path.
    """
    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(ASSET_APP.parent))
    monkeypatch.setenv("INTEGRAL_CORE_ONLY", "0")

    ws = await make_org_workspace("ws-asset-operation-create")
    owners = await ws.nodes(edge=[IS_MEMBER_OF], direction="in", node=["User"])
    assert len(owners) == 1
    owner = owners[0]
    lib = await seed_asset_register_library_cp()
    installed = await install_app(
        workspace_id=ws.id,
        library_cp_id=lib.id,
        actor_id=owner.id,
        include_seed_data=False,
    )
    app = await App.get(installed["app_id"])
    assert app is not None
    tracks = await app.nodes(edge=[CONTAINS], node=["Track"])
    assets = next(track for track in tracks if track.title == "Assets")
    ctx = OperationContext(
        user_id=owner.id,
        workspace_id=ws.id,
        scope=f"operation:{app.id}:register_asset",
        app_id=app.id,
        operation_key="register_asset",
    )

    created = await ctx.create_entry(
        track_id=assets.id,
        entry_type_key="asset",
        title="Contract Laptop",
        custom_fields={
            "asset_tag": "CONTRACT-001",
            "category": "it_equipment",
            "lifecycle_state": "available",
        },
    )

    assert created is not None
    assert created.track_id == assets.id
    assert created.custom_fields["asset_tag"] == "CONTRACT-001"
    assert created.type_id

    custodians = next(track for track in tracks if track.title == "Custodians")
    custodian = await ctx.create_entry(
        track_id=custodians.id,
        entry_type_key="custodian",
        title="Contract Custodian",
        custom_fields={"contact_email": "custodian@example.test"},
    )
    assert custodian is not None

    bundle_root = str(ASSET_APP)
    sdk_root = str(ASSET_APP.parents[1] / "sdk" / "python")
    for module_name in list(sys.modules):
        if module_name == "tools" or module_name.startswith("tools."):
            del sys.modules[module_name]
    for package_root in (sdk_root, bundle_root):
        if package_root not in sys.path:
            sys.path.insert(0, package_root)
    custody = importlib.import_module("tools.custody")

    ctx.operation_key = "check_out_asset"
    checked_out = await custody.check_out_asset(
        {"asset_id": created.id, "custodian_id": custodian.id}, ctx
    )
    assert checked_out["ok"] is True
    assert checked_out["custody_id"]
