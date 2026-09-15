"""Manifest ↔ TOOL_BINDINGS ↔ catalogue reconciliation (Phase A2)."""

from __future__ import annotations

from app.agentive.tooling.bindings import TOOL_BINDINGS
from app.agentive.tooling.catalogue import _is_dispatchable, build_tool_catalogue
from app.agentive.tooling.dispatch import _BATCH_CONTROL_TOOLS
from app.agentive.tooling.manifest import (
    _STAGING_EXEMPT_PROPOSE_TOOLS,
    load_manifest,
)


def _catalogue_before_a1_baseline() -> int:
    """Pre-A1 existing+dispatchable count was 45; after relabeling +14 reads."""
    return 59


def test_manifest_existing_tools_have_dispatchable_bindings():
    registry = load_manifest()
    deferred_existing = _STAGING_EXEMPT_PROPOSE_TOOLS & {
        "integral_workspace_setup",
        "integral_onboard_user",
    }
    missing = []
    for name, spec in registry.items():
        if spec.status != "existing":
            continue
        if name in deferred_existing:
            continue
        binding = TOOL_BINDINGS.get(name)
        if not _is_dispatchable(name, binding):
            missing.append(name)
    assert not missing, f"existing tools without dispatchable binding: {missing}"


def test_tool_bindings_keys_exist_in_manifest():
    registry = load_manifest()
    manifest_names = set(registry.keys())
    orphans = sorted(set(TOOL_BINDINGS.keys()) - manifest_names)
    assert not orphans, f"TOOL_BINDINGS keys missing from manifest: {orphans}"


def test_catalogue_matches_manifest_existing_dispatchable():
    registry = load_manifest()
    catalogue_names = {t["name"] for t in build_tool_catalogue()}
    expected = {
        name
        for name, spec in registry.items()
        if spec.status == "existing" and _is_dispatchable(name, TOOL_BINDINGS.get(name))
    }
    assert catalogue_names == expected


def test_gap_tools_not_in_catalogue():
    registry = load_manifest()
    catalogue_names = {t["name"] for t in build_tool_catalogue()}
    gaps_advertised = [name for name, spec in registry.items() if spec.status == "gap"]
    leaked = [n for n in gaps_advertised if n in catalogue_names]
    assert not leaked, f"gap tools incorrectly advertised: {leaked}"


def test_staging_exempt_and_batch_tools_documented():
    """Exempt propose tools and batch controls are known reconciliation sets."""
    assert "integral_set_focus" in _STAGING_EXEMPT_PROPOSE_TOOLS
    assert _BATCH_CONTROL_TOOLS <= set(TOOL_BINDINGS.keys())


def test_catalogue_count_after_stale_gap_relabel():
    catalogue = build_tool_catalogue()
    assert len(catalogue) >= _catalogue_before_a1_baseline()
