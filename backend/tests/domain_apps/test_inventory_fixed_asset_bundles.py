"""Fixed Asset Management + Inventory Management bundle manifest tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest
import yaml

from app.exceptions import AppDependencyError
from app.models.edges import CONTAINS
from app.models.nodes import App, ContentProfile, Entry, Workspace
from app.services.app_lifecycle import finalize_install, install_app
from app.services.content_profile_compile import compile_canonical_manifest
from app.services.content_profile_loader import load_library_profiles_with_issues
from app.utils.time import utc_now_iso
from tests.fixtures.workspaces import make_org_workspace

_PROFILES = Path("app/profiles")
_BUNDLES = ("fixed-asset-management", "inventory-management")


async def _make_workspace(name: str = "WS Bundle Test") -> Workspace:
    return await make_org_workspace(name)


async def _load_library_cp(slug: str) -> ContentProfile:
    specs, _ = load_library_profiles_with_issues(profiles_root=_PROFILES)
    spec = next(s for s in specs if s.slug == slug)
    manifest: Dict[str, Any] = dict(spec.manifest)
    pkg = dict(manifest.get("package") or {})
    pkg["version"] = spec.version or pkg.get("version") or "1.0.0"
    manifest["package"] = pkg
    now = utc_now_iso()
    return await ContentProfile.create(
        name=manifest["package"]["name"],
        scope="app",
        manifest=manifest,
        library_package=True,
        version=pkg["version"],
        created_at=now,
        updated_at=now,
    )


async def _track_titles(app_node: App) -> set[str]:
    tracks = await app_node.nodes(edge=[CONTAINS], node=["Track"])
    return {getattr(t, "title", "") for t in tracks}


async def _install_to_active(
    ws: Workspace,
    slug: str,
    *,
    settings: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    lib = await _load_library_cp(slug)
    result = await install_app(workspace_id=ws.id, library_cp_id=lib.id, actor_id="u_1")
    if result["status"] == "awaiting_settings":
        merged = dict(settings or {})
        if slug == "inventory-management" and "valuation_method" not in merged:
            merged["valuation_method"] = "average_cost"
        result = await finalize_install(
            app_id=result["app_id"],
            install_token=result["install_token"],
            settings=merged,
            actor_id="u_1",
        )
    assert result["status"] == "active"
    return result


@pytest.fixture(scope="module")
def fam_compiled():
    specs, _ = load_library_profiles_with_issues(profiles_root=_PROFILES)
    spec = next(s for s in specs if s.slug == "fixed-asset-management")
    return compile_canonical_manifest(manifest=spec.manifest, scope_hint="app")


@pytest.fixture(scope="module")
def inv_compiled():
    specs, _ = load_library_profiles_with_issues(profiles_root=_PROFILES)
    spec = next(s for s in specs if s.slug == "inventory-management")
    return compile_canonical_manifest(manifest=spec.manifest, scope_hint="app")


@pytest.mark.parametrize("slug", _BUNDLES)
def test_bundle_loads_without_loader_issues(slug):
    specs, issues = load_library_profiles_with_issues(profiles_root=_PROFILES)
    assert not [i for i in issues if i.slug == slug]
    assert any(s.slug == slug for s in specs)


@pytest.mark.parametrize("slug", _BUNDLES)
def test_bundle_skill_md_declares_embedded_integral_extends(slug):
    for skill_dir in (_PROFILES / slug / "skills").iterdir():
        skill_md = skill_dir / "SKILL.md"
        assert skill_md.is_file(), f"missing {skill_md}"
        fm = yaml.safe_load(skill_md.read_text(encoding="utf-8").split("---", 2)[1])
        assert fm.get("extends") == "action:integral/embedded_integral_action"
        assert "EmbeddedIntegralAction" in (fm.get("requires-actions") or [])


def test_fixed_asset_management_three_tracks(fam_compiled):
    track_keys = {t["key"] for t in fam_compiled["app"]["tracks"]}
    assert track_keys == {"assets", "locations", "asset_events"}


def test_fixed_asset_management_five_skills(fam_compiled):
    skills = fam_compiled["app"]["skills"]
    keys = {s if isinstance(s, str) else s["key"] for s in skills}
    assert keys == {
        "register_asset",
        "assign_asset",
        "record_maintenance",
        "retire_asset",
        "asset_audit_report",
    }


def test_fixed_asset_no_finance_dependency(fam_compiled):
    assert fam_compiled["app"].get("requires_apps") in (None, [])


def test_inventory_management_five_tracks_and_po_template(inv_compiled):
    track_keys = {t["key"] for t in inv_compiled["app"]["tracks"]}
    assert track_keys == {
        "catalog",
        "locations",
        "stock",
        "movements",
        "purchase_orders",
    }
    templates = inv_compiled["app"].get("track_templates") or []
    assert any(t["key"] == "po-details" for t in templates)


def test_inventory_management_seven_skills(inv_compiled):
    skills = inv_compiled["app"]["skills"]
    keys = {s if isinstance(s, str) else s["key"] for s in skills}
    assert keys == {
        "receive_shipment",
        "record_sale",
        "issue_stock",
        "transfer_stock",
        "adjust_inventory",
        "create_purchase_order",
        "low_stock_report",
    }


def test_inventory_management_hard_finance_dep(inv_compiled):
    deps = inv_compiled["app"].get("requires_apps") or []
    finance = next(d for d in deps if d["key"] == "finance")
    assert finance.get("optional") is False


def test_inventory_item_supplier_cross_app(inv_compiled):
    catalog = next(t for t in inv_compiled["app"]["tracks"] if t["key"] == "catalog")
    item_et = next(et for et in catalog["entry_types"] if et["key"] == "item")
    supplier = next(f for f in item_et["fields"] if f["key"] == "default_supplier")
    assert supplier["relation"]["target_app"] == "finance"


@pytest.mark.asyncio
async def test_fixed_asset_installs_without_finance():
    ws = await _make_workspace("WS Fixed Assets")
    result = await _install_to_active(ws, "fixed-asset-management")

    app = await App.get(result["app_id"])
    titles = await _track_titles(app)
    assert {"Assets", "Locations", "Asset Events"}.issubset(titles)

    assets_track = next(
        t
        for t in await app.nodes(edge=[CONTAINS], node=["Track"])
        if t.title == "Assets"
    )
    entries = await Entry.find({"track_id": assets_track.id})
    assert len(entries) >= 4  # readme + 3 sample assets
    assert all(e.type_id for e in entries)


@pytest.mark.asyncio
async def test_inventory_install_blocked_without_finance():
    ws = await _make_workspace("WS Inventory Blocked")
    inv_lib = await _load_library_cp("inventory-management")
    with pytest.raises(AppDependencyError):
        await install_app(workspace_id=ws.id, library_cp_id=inv_lib.id, actor_id="u_1")


@pytest.mark.asyncio
async def test_inventory_installs_after_finance_present():
    ws = await _make_workspace("WS Inventory OK")
    finance_lib = await _load_library_cp("finance")

    fin = await install_app(
        workspace_id=ws.id, library_cp_id=finance_lib.id, actor_id="u_1"
    )
    assert fin["status"] == "active"

    inv = await _install_to_active(ws, "inventory-management")

    inv_app = await App.get(inv["app_id"])
    titles = await _track_titles(inv_app)
    assert {
        "Catalog",
        "Locations",
        "Stock",
        "Movements",
        "Purchase Orders",
    }.issubset(titles)


@pytest.mark.asyncio
async def test_both_bundles_install_in_same_workspace():
    ws = await _make_workspace("WS Both Bundles")
    finance_lib = await _load_library_cp("finance")

    fam = await _install_to_active(ws, "fixed-asset-management")
    fin = await install_app(
        workspace_id=ws.id, library_cp_id=finance_lib.id, actor_id="u_1"
    )
    inv = await _install_to_active(ws, "inventory-management")

    assert fin["status"] == "active"
    assert fam["app_id"] != inv["app_id"] != fin["app_id"]

    fam_app = await App.get(fam["app_id"])
    inv_app = await App.get(inv["app_id"])
    assert fam_app.lifecycle_state == inv_app.lifecycle_state == "active"
    assert fam_app.name == "fixed-asset-management"
    assert inv_app.name == "inventory-management"
