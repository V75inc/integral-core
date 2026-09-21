"""Phase 1 (payroll-filings infra) — related_views[].position round-trip.

The one approved core touch: an optional ``position: 'primary' | 'related'``
on related_views entries, defaulting to ``'related'`` (today's behavior) so
every existing profile compiles identically. Covers both call sites that
parse related_views: ``_normalize_entry_type_spec`` (manifest ingestion) and
``normalize_entry_type_form_schema`` (EntryType.form_schema round-trip).
"""

import pytest

from app.exceptions import BadRequestError
from app.services.operational_model_compile import (
    _normalize_entry_type_spec,
    compile_canonical_manifest,
    normalize_entry_type_form_schema,
)
from app.services.operational_model_merge import _form_schema_from_entry_type_spec


def _rv(view="a/b", position=None):
    d = {"view": view, "bind": {}}
    if position is not None:
        d["position"] = position
    return d


def test_entry_type_spec_related_view_defaults_to_related():
    out = _normalize_entry_type_spec({"name": "Widget", "related_views": [_rv()]})
    assert out["related_views"][0]["position"] == "related"


def test_entry_type_spec_related_view_position_primary_preserved():
    out = _normalize_entry_type_spec(
        {"name": "Widget", "related_views": [_rv(position="primary")]}
    )
    assert out["related_views"][0]["position"] == "primary"


def test_entry_type_spec_related_view_invalid_position_falls_back():
    out = _normalize_entry_type_spec(
        {"name": "Widget", "related_views": [_rv(position="bogus")]}
    )
    assert out["related_views"][0]["position"] == "related"


def test_entry_type_spec_no_related_views_is_backward_compatible():
    out = _normalize_entry_type_spec({"name": "Widget"})
    assert out["related_views"] == []


def test_form_schema_related_view_defaults_to_related():
    out = normalize_entry_type_form_schema({"related_views": [_rv()]})
    assert out["related_views"][0]["position"] == "related"


def test_form_schema_related_view_position_primary_preserved():
    out = normalize_entry_type_form_schema({"related_views": [_rv(position="primary")]})
    assert out["related_views"][0]["position"] == "primary"


def test_form_schema_no_related_views_is_backward_compatible():
    out = normalize_entry_type_form_schema({})
    assert out["related_views"] == []


# Regression: found via live browser install of payroll_filings — the app's
# related_views compiled fine through _normalize_entry_type_spec (covered
# above), but _form_schema_from_entry_type_spec is the SEPARATE path actually
# used by merge_library_manifest_into_operational_model when a library app gets
# installed into a workspace. It reconstructed form_schema from `fields` /
# `base_fields` / `required_tag_groups` only, silently dropping
# `related_views` — so every installed app lost its inline anchored-track
# rendering (table + action bar) even though the operational-model.yaml was correct.
def test_merge_form_schema_related_view_defaults_to_related():
    out = _form_schema_from_entry_type_spec(
        {"name": "Widget", "related_views": [_rv()]}
    )
    assert out["related_views"][0]["position"] == "related"


def test_merge_form_schema_related_view_position_primary_preserved():
    out = _form_schema_from_entry_type_spec(
        {"name": "Widget", "related_views": [_rv(position="primary")]}
    )
    assert out["related_views"][0]["position"] == "primary"


def test_merge_form_schema_no_related_views_is_backward_compatible():
    out = _form_schema_from_entry_type_spec({"name": "Widget"})
    assert out["related_views"] == []


def test_merge_form_schema_related_views_preserved_when_nested_form_schema_present():
    # The other branch of _form_schema_from_entry_type_spec: an entry type
    # spec that already carries an explicit form_schema dict (e.g. re-merging
    # an already-compiled manifest) must also preserve related_views.
    out = _form_schema_from_entry_type_spec(
        {
            "name": "Widget",
            "form_schema": {"fields": [], "related_views": [_rv(position="primary")]},
        }
    )
    assert out["related_views"][0]["position"] == "primary"


# ``open_as_page`` — entry types opt into EntryPage.tsx's full-page chrome
# instead of the default Modal overlay. Same three call sites as
# related_views/position, same default-preserves-behavior contract.
def test_entry_type_spec_open_as_page_defaults_false():
    out = _normalize_entry_type_spec({"name": "Widget"})
    assert out["open_as_page"] is False


def test_entry_type_spec_open_as_page_true_preserved():
    out = _normalize_entry_type_spec({"name": "Widget", "open_as_page": True})
    assert out["open_as_page"] is True


def test_form_schema_open_as_page_defaults_false():
    out = normalize_entry_type_form_schema({})
    assert out["open_as_page"] is False


def test_form_schema_open_as_page_true_preserved():
    out = normalize_entry_type_form_schema({"open_as_page": True})
    assert out["open_as_page"] is True


def test_merge_form_schema_open_as_page_true_preserved():
    out = _form_schema_from_entry_type_spec({"name": "Widget", "open_as_page": True})
    assert out["open_as_page"] is True


def test_merge_form_schema_open_as_page_defaults_false():
    out = _form_schema_from_entry_type_spec({"name": "Widget"})
    assert out["open_as_page"] is False


# ---------------------------------------------------------------------------
# UI Packs Standard — related_views[] placement `scope` enforcement.
#
# ``related_views[].view`` is a reference to a SavedView declared in the
# SAME track's ``views[]`` by key (or a resolver-prefixed ``:token/key``
# reference into another track's CP — not resolvable at this compile, and
# not this check's concern). The check therefore runs at full manifest
# compile (``compile_canonical_manifest``), once both ``entry_types`` and
# ``views`` are known — NOT inside ``_normalize_entry_type_spec`` /
# ``normalize_entry_type_form_schema`` in isolation, which only ever see
# one entry type at a time and have no visibility into the track's own
# view registry to resolve a bare key against.
# ---------------------------------------------------------------------------


def _track_manifest_with_related_view(view_type: str, related_view_ref: str):
    return {
        "operational_model_schema_version": 2,
        "scope": "track",
        "track": {
            "views": [
                {"key": "the_view", "name": "The View", "view_type": view_type},
            ],
            "entry_types": [
                {
                    "name": "Widget",
                    "fields": [],
                    "related_views": [_rv(view=related_view_ref)],
                }
            ],
        },
    }


def test_track_scoped_type_in_related_views_raises():
    """example-desk/desk-board is scope='track' — placing it in
    related_views[] (by key reference) is rejected at compile time."""
    manifest = _track_manifest_with_related_view("example-desk/desk-board", "the_view")
    with pytest.raises(BadRequestError, match="related_views"):
        compile_canonical_manifest(manifest=manifest)


def test_entry_scoped_type_in_related_views_compiles_cleanly():
    """example-desk/desk-summary is scope='entry' — the intended placement,
    must compile without error."""
    manifest = _track_manifest_with_related_view(
        "example-desk/desk-summary", "the_view"
    )
    compiled = compile_canonical_manifest(manifest=manifest)
    rv = compiled["track"]["entry_types"][0]["related_views"]
    assert rv[0]["view"] == "the_view"


def test_resolver_prefixed_related_view_skips_scope_check():
    """A cross-track resolver-prefixed reference can't be resolved at this
    compile (the target track's CP isn't available) — fail-soft, not a
    compile error, regardless of the (unresolvable) view type."""
    manifest = _track_manifest_with_related_view(
        "example-desk/desk-board", ":anchored_track/the_view"
    )
    compile_canonical_manifest(manifest=manifest)  # must not raise


def test_dangling_related_view_key_skips_scope_check():
    """A bare key with no matching ``views[]`` entry is a different failure
    mode (dangling reference) — not this check's concern."""
    manifest = _track_manifest_with_related_view(
        "example-desk/desk-board", "does_not_exist"
    )
    compile_canonical_manifest(manifest=manifest)  # must not raise


def test_both_scoped_type_in_related_views_compiles_cleanly():
    """A scope='both' type (e.g. chart_region) is valid in related_views[]
    too — only strictly track-scoped types are rejected."""
    manifest = _track_manifest_with_related_view("chart_region", "the_view")
    compile_canonical_manifest(manifest=manifest)  # must not raise


def test_entry_scoped_type_declared_in_views_is_not_rejected():
    """The inverse direction is deliberately NOT enforced: views[] is the
    tier's full view registry (both tab candidates and related_views
    lookup targets), not a track-tabs-only list — every existing
    entry-scoped widget (action_bar, summary_tiles, ...) is declared there
    today specifically so related_views[] can reference it by key. See
    UI_PACKS.md's placement section for the real-profile evidence."""
    manifest = {
        "operational_model_schema_version": 2,
        "scope": "track",
        "track": {
            "views": [
                {
                    "key": "an_action_bar",
                    "name": "Actions",
                    "view_type": "action_bar",
                },
            ],
            "entry_types": [],
        },
    }
    compile_canonical_manifest(manifest=manifest)  # must not raise
