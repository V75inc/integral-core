"""Phase 20 — Discovery / Rubric / Proposal manifest tests.

Pricing computation tests live in ``test_crm_bundle_tool_pricing.py``;
the legacy ``app.services.pricing`` module was deleted in Phase 30
(DR-30-01) once the logic moved into the bundle's ``tools[]`` block.

Phase 31 (DR-31-01) — the four tracks (Discovery Sessions / Scoping
Documents / Pricing Rubrics / Project Proposals) plus the two pre-sales
agent skills (scope_from_transcript + proposal_from_scope) all live in
the **sales** bundle post-decomposition.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.content_profile_compile import compile_canonical_manifest
from app.services.content_profile_loader import load_library_profiles_with_issues


@pytest.fixture(scope="module")
def sales_compiled():
    specs, _ = load_library_profiles_with_issues(profiles_root=Path("app/profiles"))
    sales = next(s for s in specs if s.slug == "sales")
    return compile_canonical_manifest(manifest=sales.manifest, scope_hint="app")


# ---- manifest shape ---------------------------------------------------------


def test_phase20_four_tracks_declared(sales_compiled):
    track_keys = {t["key"] for t in sales_compiled["app"]["tracks"]}
    expected = {
        "discovery_sessions",
        "scoping_documents",
        "pricing_rubrics",
        "project_proposals",
    }
    missing = expected - track_keys
    assert not missing, f"Phase 20 missing tracks: {missing}; have {track_keys}"


def test_discovery_transcript_has_required_fields(sales_compiled):
    track = next(
        t for t in sales_compiled["app"]["tracks"] if t["key"] == "discovery_sessions"
    )
    et = next(et for et in track["entry_types"] if et["key"] == "discovery_transcript")
    keys = {f["key"] for f in et["fields"]}
    assert {
        "account",
        "attendees",
        "call_date",
        "agent_summary",
        "transcript_source",
    }.issubset(keys)


def test_pricing_rubric_has_target_margin_and_rubric_lines(sales_compiled):
    track = next(
        t for t in sales_compiled["app"]["tracks"] if t["key"] == "pricing_rubrics"
    )
    et = next(et for et in track["entry_types"] if et["key"] == "pricing_rubric")
    keys = {f["key"] for f in et["fields"]}
    assert {
        "target_margin_pct",
        "overhead_factor",
        "rubric_lines",
        "is_active",
    }.issubset(keys)


def test_project_proposal_has_margin_and_under_margin_fields(sales_compiled):
    track = next(
        t for t in sales_compiled["app"]["tracks"] if t["key"] == "project_proposals"
    )
    et = next(et for et in track["entry_types"] if et["key"] == "project_proposal")
    keys = {f["key"] for f in et["fields"]}
    expected = {
        "source_scoping_document",
        "pricing_rubric",
        "line_items",
        "total_price",
        "total_cost",
        "projected_margin_pct",
        "target_margin_pct",
        "under_margin",
    }
    missing = expected - keys
    assert not missing, f"project_proposal missing fields: {missing}"


def test_phase20_two_skills_declared(sales_compiled):
    skills = sales_compiled["app"].get("skills") or []
    keys = {s["key"] for s in skills}
    expected = {"scope_from_transcript", "proposal_from_scope"}
    assert expected.issubset(keys)
    for s in skills:
        if s["key"] in expected:
            assert s["kind"] == "declarative"
            assert s.get("private") is True
