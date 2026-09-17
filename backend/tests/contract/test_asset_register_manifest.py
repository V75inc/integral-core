"""Contract: Asset Register manifest compiles (WP-05)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.content_profile_loader import load_library_profiles_with_issues
from app.services.content_profile_runtime import compile_canonical_manifest

REPO = Path(__file__).resolve().parents[3]
ASSET_APP = REPO / "examples" / "asset-register"

EXPECTED_TRACKS = {
    "assets",
    "locations",
    "custodians",
    "custody",
    "service_history",
}
EXPECTED_OPERATIONS = {
    "list_available_assets",
    "register_asset",
    "check_out_asset",
    "check_in_asset",
    "record_service",
    "review_warranties",
}
EXPECTED_SKILLS = {
    "register_asset",
    "find_available_asset",
    "prepare_asset_checkout",
    "review_warranties",
}


@pytest.fixture
def asset_register_root(monkeypatch):
    assert ASSET_APP.is_dir(), f"missing asset register package at {ASSET_APP}"
    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(ASSET_APP.parent))
    monkeypatch.setenv("INTEGRAL_CORE_ONLY", "0")
    from app.services.content_profile_library_sync import (
        reset_library_profiles_cache_for_testing,
    )

    reset_library_profiles_cache_for_testing()
    yield ASSET_APP
    reset_library_profiles_cache_for_testing()


@pytest.mark.contract
def test_asset_register_loads_from_external_path(asset_register_root):
    specs, issues = load_library_profiles_with_issues(
        package_paths=[asset_register_root.parent],
        core_only=False,
        verify_signatures=False,
    )
    assert not issues, issues
    by_slug = {s.slug: s for s in specs}
    assert "asset-register" in by_slug
    spec = by_slug["asset-register"]
    assert spec.manifest["package"]["trust_tier"] == "trusted"
    assert spec.manifest["scope"] == "app"


@pytest.mark.contract
def test_asset_register_manifest_compiles(asset_register_root):
    specs, _ = load_library_profiles_with_issues(
        package_paths=[str(asset_register_root.parent)],
        core_only=False,
        verify_signatures=False,
    )
    spec = next(s for s in specs if s.slug == "asset-register")
    canonical = compile_canonical_manifest(manifest=spec.manifest)

    app = canonical["app"]
    track_keys = {t["key"] for t in app["tracks"]}
    assert EXPECTED_TRACKS <= track_keys

    op_keys = {o["key"] for o in app["operations"]}
    assert EXPECTED_OPERATIONS == op_keys

    tool_keys = {t["key"] for t in app["tools"]}
    assert EXPECTED_OPERATIONS <= tool_keys

    skill_keys = {s["key"] for s in app["skills"]}
    assert EXPECTED_SKILLS == skill_keys

    for op in app["operations"]:
        assert op.get("tool"), f"operation {op['key']} missing tool binding"
        assert op["tool"] in tool_keys

    agents = app.get("agents") or []
    assert agents, "expected at least one agent for schedule declaration"
    schedules = agents[0].get("default_schedules") or []
    assert schedules, "expected warranty default_schedules on agent"
    assert schedules[0]["skill"] == "review_warranties"
    assert schedules[0]["cron"]

    relations = app.get("relations") or []
    assert relations, "expected manifest relations for asset graph"
