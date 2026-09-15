"""ACC-02 — Anchored privileged tracks manifest assertions.

Confirms the **projects** content-profile library package declares the two
privileged track templates (`project-financials`, `contracts-legal`) and that
the `Project` EntryType declares the two anchor relation fields
(`financials_track`, `contracts_track`) targeting them.

Phase 31 (DR-31-01 §3): the Projects track + its anchored privileged tracks
moved from `crm-plus-pm-suite` to the standalone **projects** bundle.

The actual `ANCHORS` edge materialization and graph reachability is covered by
`backend/tests/test_anchor_integration.py` and the runtime sweep in
`backend/tests/test_graph_contiguousness.py`. This test is a manifest-shape
regression so a future edit cannot silently drop the privileged-track
declarations the consulting dogfood milestone depends on.
"""

from __future__ import annotations

import pytest

from app.services.content_profile_compile import compile_canonical_manifest
from app.services.content_profile_loader import load_library_profiles_with_issues

PRIVILEGED_TEMPLATE_KEYS = {"project-financials", "contracts-legal"}
PRIVILEGED_ANCHOR_FIELD_KEYS = {"financials_track", "contracts_track"}


@pytest.fixture(scope="module")
def projects_compiled():
    specs, _ = load_library_profiles_with_issues()
    spec = next((s for s in specs if s.slug == "projects"), None)
    assert spec is not None, "projects profile missing from library"
    return compile_canonical_manifest(manifest=spec.manifest, scope_hint="app")


def test_projects_declares_privileged_track_templates(projects_compiled):
    """ACC-02 — project-financials and contracts-legal are declared."""
    tt = projects_compiled.get("app", {}).get("track_templates", [])
    declared = {t.get("key") for t in tt}
    missing = PRIVILEGED_TEMPLATE_KEYS - declared
    assert not missing, (
        f"Projects manifest missing privileged track_templates: {missing}; "
        f"declared keys: {sorted(declared)}"
    )


def test_project_entry_type_declares_privileged_anchor_fields(projects_compiled):
    """ACC-02 — Project EntryType declares financials_track + contracts_track anchors."""
    projects_track = next(
        (t for t in projects_compiled["app"]["tracks"] if t["key"] == "projects"),
        None,
    )
    assert projects_track is not None, "projects track missing from Projects manifest"
    project_et = next(
        (et for et in projects_track["entry_types"] if et["key"] == "project"),
        None,
    )
    assert project_et is not None, "project EntryType missing"
    anchor_fields = {
        f["key"]
        for f in project_et["fields"]
        if f.get("type") == "relation"
        and (f.get("relation") or {}).get("target") == "track"
    }
    missing = PRIVILEGED_ANCHOR_FIELD_KEYS - anchor_fields
    assert not missing, (
        f"Project EntryType missing privileged anchor fields: {missing}; "
        f"declared anchor fields: {sorted(anchor_fields)}"
    )


def test_privileged_anchor_fields_point_at_privileged_templates(projects_compiled):
    """ACC-02 — anchor relations point at the right privileged templates."""
    projects_track = next(
        t for t in projects_compiled["app"]["tracks"] if t["key"] == "projects"
    )
    project_et = next(
        et for et in projects_track["entry_types"] if et["key"] == "project"
    )
    relations_by_key = {
        f["key"]: f["relation"]
        for f in project_et["fields"]
        if f.get("type") == "relation"
        and (f.get("relation") or {}).get("target") == "track"
    }
    assert (
        relations_by_key["financials_track"]["target_track_template"]
        == "project-financials"
    )
    assert (
        relations_by_key["contracts_track"]["target_track_template"]
        == "contracts-legal"
    )
    # Both privileged tracks are opt-in (CREATE_ANCHOR_SENTINEL); Details alone
    # auto-provisions. ACC-02 still requires the template keys above.
    assert relations_by_key["financials_track"].get("auto_provision") is False
    assert relations_by_key["contracts_track"].get("auto_provision") is False


def test_privileged_templates_declare_canonical_entry_types(projects_compiled):
    """ACC-02 — privileged templates carry the documented EntryTypes + fields."""
    tt_by_key = {t["key"]: t for t in projects_compiled["app"]["track_templates"]}

    financials = tt_by_key["project-financials"]
    fin_et_keys = {et["key"] for et in financials["entry_types"]}
    assert "project_financials" in fin_et_keys
    pf_et = next(
        et for et in financials["entry_types"] if et["key"] == "project_financials"
    )
    pf_field_keys = {f["key"] for f in pf_et["fields"]}
    for required in ("cost", "margin", "billing_schedule", "budget_vs_actual"):
        assert (
            required in pf_field_keys
        ), f"project_financials missing required field '{required}'"

    contracts = tt_by_key["contracts-legal"]
    cd_et_keys = {et["key"] for et in contracts["entry_types"]}
    assert "contract_document" in cd_et_keys
    cd_et = next(
        et for et in contracts["entry_types"] if et["key"] == "contract_document"
    )
    cd_field_keys = {f["key"] for f in cd_et["fields"]}
    for required in ("document_type", "counterparty", "effective_date"):
        assert (
            required in cd_field_keys
        ), f"contract_document missing required field '{required}'"
