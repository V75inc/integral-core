"""ACC-06 — Contacts / Opportunities taxonomy split assertions.

Confirms the **crm** library manifest declares the new EntryTypes
alongside the legacy ones (so existing data remains valid while new
entries can pick the more specific type) and the slice views project
each EntryType via the first-class ``entry_type_keys`` substrate
primitive — never a `filters` workaround per the CLAUDE.md modeling
tenet.

Phase 31 (DR-31-01 §3): Contacts + Opportunities tracks moved from
`crm-plus-pm-suite` to the standalone **crm** bundle.

The re-typing pass for legacy entries is exercised by
``backend/scripts/retype_taxonomy.py`` against a live DB; this test
focuses on manifest-shape regressions so a future edit cannot silently
drop the new-EntryType declarations.
"""

from __future__ import annotations

import pytest

from app.services.content_profile_compile import compile_canonical_manifest
from app.services.content_profile_loader import load_library_profiles_with_issues

CONTACTS_NEW_TYPES = {"account", "lead", "contact_person", "partner"}
OPPS_NEW_TYPES = {"bid", "prospect"}


@pytest.fixture(scope="module")
def crm_compiled():
    specs, _ = load_library_profiles_with_issues()
    spec = next((s for s in specs if s.slug == "crm"), None)
    assert spec is not None
    return compile_canonical_manifest(manifest=spec.manifest, scope_hint="app")


def test_contacts_declares_acc_06_entry_types(crm_compiled):
    contacts = next(t for t in crm_compiled["app"]["tracks"] if t["key"] == "contacts")
    et_keys = {et["key"] for et in contacts["entry_types"]}
    missing = CONTACTS_NEW_TYPES - et_keys
    assert not missing, f"Contacts missing EntryTypes: {missing}; have {et_keys}"
    # Legacy `contact` + `interaction` remain so existing entries stay valid
    # until the re-type pass migrates them.
    assert "contact" in et_keys
    assert "interaction" in et_keys


def test_opportunities_declares_acc_06_entry_types(crm_compiled):
    opps = next(t for t in crm_compiled["app"]["tracks"] if t["key"] == "opportunities")
    et_keys = {et["key"] for et in opps["entry_types"]}
    missing = OPPS_NEW_TYPES - et_keys
    assert not missing, f"Opportunities missing EntryTypes: {missing}; have {et_keys}"
    assert "opportunity" in et_keys


def test_contacts_slice_views_project_via_entry_type_keys(crm_compiled):
    """Each slice view uses entry_type_keys — never a filters workaround."""
    contacts = next(t for t in crm_compiled["app"]["tracks"] if t["key"] == "contacts")
    views_by_key = {v["key"]: v for v in contacts["views"]}
    expected_slice_views = {
        "accounts_table": "account",
        "leads_table": "lead",
        "contact_people_table": "contact_person",
        "partners_table": "partner",
    }
    for view_key, et_key in expected_slice_views.items():
        v = views_by_key.get(view_key)
        assert v is not None, f"Contacts missing slice view: {view_key}"
        assert v.get("entry_type_keys") == [et_key], (
            f"Slice view {view_key} should project entry_type_keys=[{et_key!r}], "
            f"got {v.get('entry_type_keys')}"
        )


def test_opportunities_slice_views_project_via_entry_type_keys(crm_compiled):
    opps = next(t for t in crm_compiled["app"]["tracks"] if t["key"] == "opportunities")
    views_by_key = {v["key"]: v for v in opps["views"]}
    expected = {
        "bids_kanban": "bid",
        "prospects_kanban": "prospect",
    }
    for view_key, et_key in expected.items():
        v = views_by_key.get(view_key)
        assert v is not None, f"Opportunities missing slice view: {view_key}"
        assert v.get("entry_type_keys") == [et_key], (
            f"Slice view {view_key} should project entry_type_keys=[{et_key!r}], "
            f"got {v.get('entry_type_keys')}"
        )


def test_retype_classifier_routes_stage_to_bid_or_prospect():
    """ACC-06 — opportunity classifier maps stage to bid vs prospect."""
    from scripts.retype_taxonomy import _classify_opportunity

    class _Fake:
        def __init__(self, custom_fields):
            self.custom_fields = custom_fields

    assert _classify_opportunity(_Fake({"stage": "negotiation"})) == "bid"
    assert _classify_opportunity(_Fake({"stage": "won"})) == "bid"
    assert _classify_opportunity(_Fake({"stage": "lost"})) == "bid"
    assert _classify_opportunity(_Fake({"stage": "lead"})) == "prospect"
    assert _classify_opportunity(_Fake({"stage": "prospect"})) == "prospect"
    assert _classify_opportunity(_Fake({"stage": "qualified"})) == "prospect"
    assert _classify_opportunity(_Fake({"stage": ""})) == "prospect"
    assert _classify_opportunity(_Fake({})) == "prospect"


def test_retype_classifier_routes_contacts_by_heuristic():
    """ACC-06 — contact classifier uses deterministic title/stage heuristics."""
    from scripts.retype_taxonomy import _classify_contact

    class _Fake:
        def __init__(self, title, custom_fields):
            self.title = title
            self.custom_fields = custom_fields

    assert _classify_contact(_Fake("Sarah at Acme — Lead", {})) == "lead"
    assert _classify_contact(_Fake("Jane Doe", {"stage": "lead"})) == "lead"
    assert _classify_contact(_Fake("Contoso Inc", {})) == "account"
    assert _classify_contact(_Fake("Acme Holdings", {})) == "account"
    assert _classify_contact(_Fake("Partner: Aurora Resellers", {})) == "partner"
    assert _classify_contact(_Fake("Jane Doe", {})) == "contact_person"
