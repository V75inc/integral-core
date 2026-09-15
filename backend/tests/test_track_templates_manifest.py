"""space.track_templates[] manifest registry tests — Phase 3.1 Plan 03.1-02 Task 2 (ANC-04).

Covers:
  - ``space.track_templates[]`` survives compile_canonical_manifest round-trip.
  - Empty / absent defaults to [] (back-compat).
  - Unique-key enforcement.
  - Nested entry_types[] go through full _normalize_field_spec
    (so relation.target='track' / target_track_template / auto_provision
    keys are preserved end-to-end).
  - find_app_track_template_spec_by_key resolves by key.
  - Distinctness from space.tracks[] — track_templates are NEVER auto-
    provisioned on space create, only on entry create via Plan 03.1-02's
    materialize_anchor_track hook.

Mirrors the test pattern from ``tests/test_anchor_manifest.py``. No
``app.main`` import (deferred-items.md infrastructure failure).
"""

from __future__ import annotations

import pytest

from app.exceptions import BadRequestError
from app.services.content_profile_runtime import (
    compile_canonical_manifest,
    find_app_track_template_spec_by_key,
)


def _make_space_manifest_with_templates(templates):
    return {
        "content_profile_schema_version": 2,
        "scope": "app",
        "package": {"slug": "test", "name": "Test", "version": "1.0.0"},
        "app": {
            "tracks": [],
            "track_templates": templates,
            "relations": [],
            "defaults": {},
        },
    }


def test_track_templates_empty_compiles_to_empty_list():
    """Absent track_templates defaults to [] — full back-compat for existing space manifests."""
    manifest = {
        "content_profile_schema_version": 2,
        "scope": "app",
        "package": {"slug": "t", "name": "T", "version": "1.0.0"},
        "app": {
            "tracks": [],
            "relations": [],
            "defaults": {},
        },
    }
    compiled = compile_canonical_manifest(manifest=manifest, scope_hint="app")
    assert compiled["app"]["track_templates"] == []


def test_track_templates_round_trip_minimal():
    """Single template with name only compiles and key auto-slugs from name."""
    manifest = _make_space_manifest_with_templates([{"name": "Project Details"}])
    compiled = compile_canonical_manifest(manifest=manifest, scope_hint="app")
    templates = compiled["app"]["track_templates"]
    assert len(templates) == 1
    assert templates[0]["name"] == "Project Details"
    assert templates[0]["key"]  # auto-slugged, non-empty
    assert templates[0]["entry_types"] == []
    assert templates[0]["views"] == []


def test_track_templates_round_trip_full_with_entry_types_and_views():
    """Full template spec round-trips through compile_canonical_manifest."""
    manifest = _make_space_manifest_with_templates(
        [
            {
                "key": "project-details",
                "name": "Project Details",
                "description": "Detail track template for Project anchor.",
                "entry_types": [
                    {
                        "key": "milestone",
                        "name": "Milestone",
                        "fields": [
                            {"key": "due", "type": "date"},
                            {"key": "title", "type": "text"},
                        ],
                    }
                ],
                "views": [{"key": "default", "type": "feed", "name": "Feed"}],
                "taxonomy": {"tag_groups": []},
                "defaults": {},
            }
        ]
    )
    compiled = compile_canonical_manifest(manifest=manifest, scope_hint="app")
    tmpl = compiled["app"]["track_templates"][0]
    assert tmpl["key"] == "project-details"
    assert tmpl["name"] == "Project Details"
    assert tmpl["description"] == "Detail track template for Project anchor."
    assert len(tmpl["entry_types"]) == 1
    assert tmpl["entry_types"][0]["key"] == "milestone"
    assert {f["key"] for f in tmpl["entry_types"][0]["fields"]} == {"due", "title"}
    assert len(tmpl["views"]) == 1


def test_track_templates_name_required():
    """Each template entry requires name."""
    manifest = _make_space_manifest_with_templates([{"key": "no-name"}])
    with pytest.raises(BadRequestError) as ei:
        compile_canonical_manifest(manifest=manifest, scope_hint="app")
    assert "track_templates" in str(ei.value)
    assert "name" in str(ei.value)


def test_track_templates_unique_keys_enforced():
    """Duplicate template keys raise BadRequestError."""
    manifest = _make_space_manifest_with_templates(
        [
            {"key": "dup", "name": "First"},
            {"key": "dup", "name": "Second"},
        ]
    )
    with pytest.raises(BadRequestError):
        compile_canonical_manifest(manifest=manifest, scope_hint="app")


def test_track_templates_carries_relation_target_track_keys():
    """Nested entry_type relation fields with target='track' / target_track_template
    survive compile_canonical_manifest end-to-end.

    Critical: Plan 03.1-02's auto-provision hook reads these keys off the
    runtime tier of the source entry's track. They must NOT be stripped by
    the template-level normalizer.
    """
    manifest = _make_space_manifest_with_templates(
        [
            {
                "key": "project",
                "name": "Project",
                "entry_types": [
                    {
                        "key": "card",
                        "name": "Card",
                        "fields": [
                            {
                                "key": "details",
                                "type": "relation",
                                "relation": {
                                    "target": "track",
                                    "target_track_template": "project-details",
                                    "auto_provision": True,
                                },
                            }
                        ],
                    }
                ],
            }
        ]
    )
    compiled = compile_canonical_manifest(manifest=manifest, scope_hint="app")
    rel = compiled["app"]["track_templates"][0]["entry_types"][0]["fields"][0][
        "relation"
    ]
    assert rel["target"] == "track"
    assert rel["target_track_template"] == "project-details"
    assert rel["auto_provision"] is True


def test_find_app_track_template_spec_by_key_resolves():
    """Lookup helper resolves a known template by key."""
    manifest = _make_space_manifest_with_templates(
        [
            {"key": "alpha", "name": "Alpha"},
            {"key": "beta", "name": "Beta"},
        ]
    )
    compiled = compile_canonical_manifest(manifest=manifest, scope_hint="app")
    spec = find_app_track_template_spec_by_key(compiled, "beta")
    assert spec is not None
    assert spec["name"] == "Beta"


def test_find_app_track_template_spec_by_key_unknown_returns_none():
    """Lookup helper returns None for unknown keys."""
    manifest = _make_space_manifest_with_templates([{"key": "alpha", "name": "Alpha"}])
    compiled = compile_canonical_manifest(manifest=manifest, scope_hint="app")
    assert find_app_track_template_spec_by_key(compiled, "nope") is None


def test_find_app_track_template_spec_by_key_wrong_scope_returns_none():
    """Lookup helper returns None when called on a track-scope manifest (defensive)."""
    track_manifest = {
        "content_profile_schema_version": 2,
        "scope": "track",
        "package": {"slug": "t", "name": "T", "version": "1.0.0"},
        "track": {
            "entry_types": [],
            "taxonomy": {"tag_groups": []},
            "views": [],
        },
    }
    compiled = compile_canonical_manifest(manifest=track_manifest, scope_hint="track")
    assert find_app_track_template_spec_by_key(compiled, "any") is None


def test_track_templates_distinct_from_space_tracks():
    """track_templates and tracks coexist without conflict.

    space.tracks[]          → prescribed-tracks list (auto-provisioned on space create).
    space.track_templates[] → registry of named templates (lazy provision on
                              entry create via materialize_anchor_track).

    Same key may legally appear in both lists since they are separate registries.
    """
    manifest = {
        "content_profile_schema_version": 2,
        "scope": "app",
        "package": {"slug": "t", "name": "T", "version": "1.0.0"},
        "app": {
            "tracks": [{"key": "projects", "name": "Projects"}],
            "track_templates": [{"key": "project-details", "name": "Project Details"}],
            "relations": [],
            "defaults": {"provision_prescribed_tracks": True},
        },
    }
    compiled = compile_canonical_manifest(manifest=manifest, scope_hint="app")
    assert len(compiled["app"]["tracks"]) == 1
    assert len(compiled["app"]["track_templates"]) == 1
    assert compiled["app"]["tracks"][0]["key"] == "projects"
    assert compiled["app"]["track_templates"][0]["key"] == "project-details"
