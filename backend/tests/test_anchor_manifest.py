"""ANCHORS manifest validator + relation_refs routing tests — Phase 3.1 Plan 03.1-01 Task 2 (ANC-02).

Covers:
  - _normalize_field_spec extension (relation.target branch)
  - relation_refs builder carries new keys
  - _validate_relation_values branches on target
  - target='track' rejects cross-workspace target Track ids (ANC-08 defense-in-depth)

Note on error class: the canonical Integral runtime uses ``BadRequestError`` from
``app.exceptions`` for all client-side validation failures (no
``ResourceNotFoundError`` is defined in this codebase). The target='track'
validator therefore raises ``BadRequestError`` for unknown track ids (mirroring
the existing target='entry' branch).
"""

from __future__ import annotations

import pytest

from app.exceptions import BadRequestError
from app.models.nodes import Entry, Track
from app.services.operational_model_compile import (
    _normalize_field_spec,
    compile_canonical_manifest,
)
from app.services.operational_model_entry_fields import _validate_relation_values


def test_relation_target_defaults_to_entry():
    """CONTEXT ANC-02: target default is 'entry' (back-compat)."""
    out = _normalize_field_spec(
        {
            "key": "related",
            "type": "relation",
            "relation": {"target_entry_types": ["Note"]},
        }
    )
    assert out["relation"]["target"] == "entry"


def test_relation_target_entry_explicit():
    """Explicit target='entry' round-trips."""
    out = _normalize_field_spec(
        {
            "key": "related",
            "type": "relation",
            "relation": {"target": "entry", "target_entry_types": ["Note"]},
        }
    )
    assert out["relation"]["target"] == "entry"


def test_relation_target_track_round_trip():
    """target='track' + target_track_template + auto_provision round-trip."""
    out = _normalize_field_spec(
        {
            "key": "detail_track",
            "type": "relation",
            "relation": {
                "target": "track",
                "target_track_template": "project-details",
                "auto_provision": True,
            },
        }
    )
    assert out["relation"]["target"] == "track"
    assert out["relation"]["target_track_template"] == "project-details"
    assert out["relation"]["auto_provision"] is True


def test_relation_target_unknown_raises():
    """Unknown target value raises BadRequestError with deterministic message."""
    with pytest.raises(BadRequestError) as ei:
        _normalize_field_spec(
            {
                "key": "weird",
                "type": "relation",
                "relation": {"target": "workspace"},
            }
        )
    msg = str(ei.value)
    assert "Unsupported relation.target 'workspace'" in msg
    assert "weird" in msg


def test_relation_target_track_compiles_via_canonical_manifest():
    """Full manifest with target='track' survives compile_canonical_manifest."""
    manifest = {
        "operational_model_schema_version": 2,
        "scope": "track",
        "package": {"slug": "test", "name": "Test", "version": "1.0.0"},
        "track": {
            "entry_types": [
                {
                    "key": "project",
                    "name": "Project",
                    "fields": [
                        {
                            "key": "details",
                            "type": "relation",
                            "relation": {
                                "target": "track",
                                "target_track_template": "details",
                                "auto_provision": True,
                            },
                        }
                    ],
                }
            ],
            "taxonomy": {"tag_groups": []},
            "views": [],
        },
    }
    compiled = compile_canonical_manifest(manifest=manifest, scope_hint="track")
    rel_spec = compiled["track"]["entry_types"][0]["fields"][0]["relation"]
    assert rel_spec["target"] == "track"
    assert rel_spec["target_track_template"] == "details"
    assert rel_spec["auto_provision"] is True


def test_relation_target_entry_defaults_when_absent_on_compile():
    """When manifest omits target, compiled spec defaults to target='entry' (back-compat)."""
    manifest = {
        "operational_model_schema_version": 2,
        "scope": "track",
        "package": {"slug": "test", "name": "Test", "version": "1.0.0"},
        "track": {
            "entry_types": [
                {
                    "key": "note",
                    "name": "Note",
                    "fields": [
                        {
                            "key": "related",
                            "type": "relation",
                            "relation": {"target_entry_types": ["Note"]},
                        }
                    ],
                }
            ],
            "taxonomy": {"tag_groups": []},
            "views": [],
        },
    }
    compiled = compile_canonical_manifest(manifest=manifest, scope_hint="track")
    rel = compiled["track"]["entry_types"][0]["fields"][0]["relation"]
    assert rel["target"] == "entry"


@pytest.mark.asyncio
async def test_validate_relation_values_target_track_accepts_existing_track():
    """target='track' validator returns id when Track exists in same workspace."""
    track = await Track.create(
        title="Detail Same",
        owner_id="anc-author-1",
        workspace_id="ws-anc-a",
    )
    source_track = await Track.create(
        title="Source",
        owner_id="anc-author-1",
        workspace_id="ws-anc-a",
    )
    out = await _validate_relation_values(
        value=track.id,
        relation={"target": "track"},
        source_track=source_track,
        field_key="detail_track",
    )
    assert out == [track.id]


@pytest.mark.asyncio
async def test_validate_relation_values_target_track_rejects_unknown_id():
    """target='track' validator raises BadRequestError for unknown Track id."""
    source_track = await Track.create(
        title="Source Unknown",
        owner_id="anc-author-2",
        workspace_id="ws-anc-b",
    )
    with pytest.raises(BadRequestError) as ei:
        await _validate_relation_values(
            value="nonexistent-track-id",
            relation={"target": "track"},
            source_track=source_track,
            field_key="detail_track",
        )
    assert "unknown Track" in str(ei.value) or "nonexistent-track-id" in str(ei.value)


@pytest.mark.asyncio
async def test_validate_relation_values_target_track_rejects_cross_workspace():
    """ANC-08 defense-in-depth: cross-workspace target Track raises BadRequestError."""
    source_track = await Track.create(
        title="Source CW",
        owner_id="anc-author-cw",
        workspace_id="ws-anc-a",
    )
    other_ws_track = await Track.create(
        title="Other",
        owner_id="anc-author-cw",
        workspace_id="ws-anc-b",
    )
    with pytest.raises(BadRequestError) as ei:
        await _validate_relation_values(
            value=other_ws_track.id,
            relation={"target": "track"},
            source_track=source_track,
            field_key="detail_track",
        )
    assert "Cross-workspace anchoring" in str(ei.value)


@pytest.mark.asyncio
async def test_validate_relation_values_target_track_rejects_entry_id():
    """target='track' validator with an Entry id (not a Track id) raises BadRequestError."""
    source_track = await Track.create(
        title="Source Ent",
        owner_id="anc-author-3",
        workspace_id="ws-anc-a",
    )
    entry = await Entry.create(
        track_id="parent-anc-3",
        title="Note",
        author_id="anc-author-3",
    )
    with pytest.raises(BadRequestError):
        await _validate_relation_values(
            value=entry.id,
            relation={"target": "track"},
            source_track=source_track,
            field_key="detail_track",
        )


@pytest.mark.asyncio
async def test_validate_relation_values_target_entry_unchanged():
    """Regression: target='entry' (default) validates against Entry.get unchanged.

    Same-track constraint is preserved (Phase 0 invariant): the sibling Entry MUST live
    on the source Track for the default ``allow_cross_track=False`` path.
    """
    source_track = await Track.create(
        title="Source Reg",
        owner_id="anc-author-reg",
        workspace_id="ws-anc-a",
    )
    sibling = await Entry.create(
        track_id=source_track.id,
        title="Sibling",
        author_id="anc-author-reg",
    )
    out = await _validate_relation_values(
        value=sibling.id,
        relation={"target": "entry"},
        source_track=source_track,
        field_key="related",
    )
    assert sibling.id in out


@pytest.mark.asyncio
async def test_validate_relation_values_target_entry_default_when_absent():
    """Regression: when target is absent on the relation dict, validator behaves as target='entry'."""
    source_track = await Track.create(
        title="Source Abs",
        owner_id="anc-author-abs",
        workspace_id="ws-anc-a",
    )
    sibling = await Entry.create(
        track_id=source_track.id,
        title="Sibling",
        author_id="anc-author-abs",
    )
    out = await _validate_relation_values(
        value=sibling.id,
        relation={},  # no target key at all — must default to entry
        source_track=source_track,
        field_key="related",
    )
    assert sibling.id in out
