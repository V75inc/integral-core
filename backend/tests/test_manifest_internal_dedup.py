"""Manifest-internal key duplicate checks inside compile_canonical_manifest."""

import pytest

from app.exceptions import BadRequestError
from app.services.content_profile_runtime import compile_canonical_manifest


def _scope_track(entry_types=None, views=None, tag_groups=None):
    return {
        "content_profile_schema_version": 2,
        "scope": "track",
        "track": {
            "entry_types": entry_types or [],
            "views": views or [],
            "taxonomy": {"tag_groups": tag_groups or []},
        },
    }


def test_track_entry_types_duplicate_key_rejected():
    manifest = _scope_track(
        entry_types=[
            {"key": "task", "name": "Task"},
            {"key": "task", "name": "Task 2"},
        ]
    )
    with pytest.raises(BadRequestError, match="track.entry_types"):
        compile_canonical_manifest(manifest=manifest)


def test_track_views_duplicate_key_rejected():
    manifest = _scope_track(
        entry_types=[{"key": "task", "name": "Task"}],
        views=[
            {"key": "board", "type": "kanban"},
            {"key": "board", "type": "table"},
        ],
    )
    with pytest.raises(BadRequestError, match="track.views"):
        compile_canonical_manifest(manifest=manifest)


def test_tag_groups_duplicate_key_rejected():
    manifest = _scope_track(
        tag_groups=[
            {"key": "status", "name": "Status", "tags": [{"name": "open"}]},
            {"key": "status", "name": "Status 2", "tags": [{"name": "done"}]},
        ]
    )
    with pytest.raises(BadRequestError, match="taxonomy.tag_groups"):
        compile_canonical_manifest(manifest=manifest)


def test_space_tracks_duplicate_key_rejected():
    manifest = {
        "content_profile_schema_version": 2,
        "scope": "app",
        "app": {
            "tracks": [
                {"key": "contacts", "name": "Contacts"},
                {"key": "contacts", "name": "Contacts 2"},
            ]
        },
    }
    with pytest.raises(BadRequestError, match="app.tracks"):
        compile_canonical_manifest(manifest=manifest)


def test_space_track_inner_entry_types_duplicate_key_rejected():
    manifest = {
        "content_profile_schema_version": 2,
        "scope": "app",
        "app": {
            "tracks": [
                {
                    "key": "contacts",
                    "name": "Contacts",
                    "entry_types": [
                        {"key": "person", "name": "Person"},
                        {"key": "person", "name": "Person 2"},
                    ],
                }
            ]
        },
    }
    with pytest.raises(BadRequestError, match="entry_types"):
        compile_canonical_manifest(manifest=manifest)


def test_valid_manifest_compiles_cleanly():
    manifest = _scope_track(
        entry_types=[
            {"key": "task", "name": "Task"},
            {"key": "note", "name": "Note"},
        ],
        views=[{"key": "feed", "type": "feed"}],
        tag_groups=[
            {"key": "priority", "name": "Priority", "tags": [{"name": "high"}]}
        ],
    )
    out = compile_canonical_manifest(manifest=manifest)
    assert out["scope"] == "track"
    assert {e["key"] for e in out["track"]["entry_types"]} == {"task", "note"}
