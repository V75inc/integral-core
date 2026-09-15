"""Unit tests for content profile merge helpers (library manifests)."""

import pytest

from app.services.content_profile_merge import (
    _merge_package_meta,
    merge_entry_type_schema_from_spec,
    merge_library_manifest_into_content_profile,
)


def test_merge_package_meta_merges_top_level_keys():
    target = {"name": "a", "version": "1"}
    incoming = {"version": "2", "description": "d"}
    merged = _merge_package_meta(target, incoming)
    assert merged["name"] == "a"
    assert merged["version"] == "2"
    assert merged["description"] == "d"


def test_merge_package_meta_dedupes_dependencies_by_id():
    target = {"dependencies": [{"id": "pkg-a", "version": "1.0.0"}]}
    incoming = {
        "dependencies": [
            {"id": "pkg-a", "version": "2.0.0"},
            {"id": "pkg-b", "version": "1.0.0"},
        ]
    }
    merged = _merge_package_meta(target, incoming)
    deps = merged["dependencies"]
    assert len(deps) == 2
    by_id = {d["id"]: d for d in deps}
    assert by_id["pkg-a"]["version"] == "2.0.0"
    assert by_id["pkg-b"]["version"] == "1.0.0"


def test_merge_package_meta_dedupes_capabilities_by_type():
    target = {"capabilities": [{"type": "x", "config": {"a": 1}}]}
    incoming = {"capabilities": [{"type": "x", "config": {"b": 2}}, "y"]}
    merged = _merge_package_meta(target, incoming)
    caps = merged["capabilities"]
    by_type = {c["type"]: c for c in caps}
    assert by_type["x"]["config"] == {"b": 2}
    assert by_type["y"] == {"type": "y", "config": {}}


# ---------------------------------------------------------------------------
# Phase 3.1 ANC-04 regression: space-level library merge MUST propagate
# ``space.track_templates[]`` to the attached space CP so the anchor
# auto-provision hook (``materialize_anchor_track``) can resolve the
# template key. Previously the merge dropped ``track_templates``, which
# made every project entry creation 400 with
# "template '<key>' not found in space.track_templates[]".
# ---------------------------------------------------------------------------


class _FakeLibraryCP:
    """Minimal stand-in for ContentProfile that the merge treats as a library."""

    def __init__(self, manifest, cp_id):
        self.manifest = manifest
        self.id = cp_id
        self.updated_at = ""
        self.library_package = True

    async def save(self):  # pragma: no cover — never called on library
        return None


class _FakeTargetCP:
    def __init__(self, manifest, cp_id):
        self.manifest = manifest
        self.id = cp_id
        self.updated_at = ""
        self.library_package = False

    async def save(self):
        return None


@pytest.mark.asyncio
async def test_space_merge_propagates_track_templates():
    lib_manifest = {
        "content_profile_schema_version": 2,
        "scope": "app",
        "package": {"slug": "t", "name": "T", "version": "1.0.0"},
        "app": {
            "tracks": [
                {
                    "key": "projects",
                    "name": "Projects",
                    "entry_types": [{"key": "project", "name": "Project"}],
                }
            ],
            "track_templates": [
                {
                    "key": "project-details",
                    "name": "Project Details",
                    "entry_types": [{"key": "task", "name": "Task"}],
                }
            ],
            "relations": [],
        },
        "migrations": [],
    }
    target_manifest = {
        "content_profile_schema_version": 2,
        "scope": "app",
        "app": {
            "tracks": [],
            "track_templates": [],
            "relations": [],
            "defaults": {},
        },
    }
    lib = _FakeLibraryCP(lib_manifest, "n.ContentProfile.lib1")
    target = _FakeTargetCP(target_manifest, "n.ContentProfile.target1")

    await merge_library_manifest_into_content_profile(
        lib, target, track=None, for_space=True, _skip_dependencies=True
    )

    space = target.manifest["app"]
    assert "track_templates" in space, "track_templates dropped during merge"
    keys = [t["key"] for t in space["track_templates"]]
    assert "project-details" in keys


@pytest.mark.asyncio
async def test_space_merge_track_templates_dedupes_by_key():
    """Re-applying a library doesn't double-list the same template key."""
    lib_manifest = {
        "content_profile_schema_version": 2,
        "scope": "app",
        "package": {"slug": "t", "name": "T", "version": "1.0.0"},
        "app": {
            "tracks": [],
            "track_templates": [
                {"key": "project-details", "name": "Project Details (v2)"}
            ],
            "relations": [],
        },
        "migrations": [],
    }
    target_manifest = {
        "content_profile_schema_version": 2,
        "scope": "app",
        "app": {
            "tracks": [],
            "track_templates": [
                {"key": "project-details", "name": "Project Details (v1)"}
            ],
            "relations": [],
            "defaults": {},
        },
    }
    lib = _FakeLibraryCP(lib_manifest, "n.ContentProfile.lib2")
    target = _FakeTargetCP(target_manifest, "n.ContentProfile.target2")

    await merge_library_manifest_into_content_profile(
        lib, target, track=None, for_space=True, _skip_dependencies=True
    )

    templates = target.manifest["app"]["track_templates"]
    keys = [t["key"] for t in templates]
    assert keys.count("project-details") == 1
    # Library wins (last-write-wins by key, mirroring tracks merge semantics).
    assert templates[0]["name"] == "Project Details (v2)"


def test_merge_entry_type_schema_adds_missing_custom_fields():
    cur = {
        "fields": [{"key": "status", "type": "select", "enum": ["todo", "done"]}],
        "_manifest_entry_type_key": "task",
    }
    desired = {
        "fields": [
            {"key": "status", "type": "select", "enum": ["todo", "done"]},
            {"key": "bucket", "type": "select", "enum": ["backlog", "done"]},
        ],
        "base_fields": {"title": {"label": "Task", "order": 0}},
    }
    merged, changed = merge_entry_type_schema_from_spec(cur, desired, spec_key="task")
    assert changed is True
    keys = [f["key"] for f in merged["fields"]]
    assert "bucket" in keys
    assert "status" in keys


def test_merge_entry_type_schema_idempotent_when_up_to_date():
    fields = [
        {"key": "bucket", "type": "select", "enum": ["backlog"]},
        {"key": "status", "type": "select", "enum": ["todo"]},
    ]
    schema = {"fields": fields, "_manifest_entry_type_key": "task"}
    _, changed = merge_entry_type_schema_from_spec(
        dict(schema),
        {"fields": list(fields)},
        spec_key="task",
    )
    assert changed is False
