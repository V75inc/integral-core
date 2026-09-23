"""Tests for the manifest patch DSL interpreter (Pillar 3)."""

import pytest

from app.exceptions import BadRequestError
from app.services.agent_profile_patches import (
    apply_operations,
    supported_ops,
)


def _empty_track_manifest():
    return {
        "scope": "track",
        "track": {
            "entry_types": [],
            "views": [],
            "taxonomy": {"tag_groups": []},
        },
    }


def test_supported_ops_lists_known_kinds():
    ops = set(supported_ops())
    assert {
        "add_entry_type",
        "modify_entry_type",
        "remove_entry_type",
        "add_field",
        "remove_field",
        "modify_field",
        "add_view",
        "remove_view",
        "add_tag",
        "remove_tag",
        "add_relation",
        "register_composite_field_type",
        "register_composite_view_type",
    }.issubset(ops)


def test_add_entry_type_inserts_new_type():
    out = apply_operations(
        _empty_track_manifest(),
        [
            {
                "op": "add_entry_type",
                "spec": {"key": "task", "name": "Task", "fields": []},
            }
        ],
    )
    assert out["track"]["entry_types"][0]["key"] == "task"


def test_add_entry_type_rejects_duplicate_key():
    with pytest.raises(BadRequestError):
        apply_operations(
            _empty_track_manifest(),
            [
                {"op": "add_entry_type", "spec": {"key": "task", "name": "Task"}},
                {"op": "add_entry_type", "spec": {"key": "task", "name": "Task 2"}},
            ],
        )


def test_remove_entry_type_drops_target():
    base = _empty_track_manifest()
    base["track"]["entry_types"].append({"key": "task", "name": "Task"})
    out = apply_operations(base, [{"op": "remove_entry_type", "key": "task"}])
    assert out["track"]["entry_types"] == []


def test_add_field_appends_to_entry_type():
    base = _empty_track_manifest()
    base["track"]["entry_types"].append({"key": "task", "name": "Task", "fields": []})
    out = apply_operations(
        base,
        [
            {
                "op": "add_field",
                "entry_type": "task",
                "spec": {"key": "priority", "type": "select", "enum": ["low"]},
            }
        ],
    )
    assert out["track"]["entry_types"][0]["fields"][0]["key"] == "priority"


def test_add_field_accepts_resident_field_aliases():
    """The live resident payload remains a safe alias for the canonical DSL."""
    base = _empty_track_manifest()
    base["track"]["entry_types"].append(
        {"key": "service_request", "name": "Service Request", "fields": []}
    )
    out = apply_operations(
        base,
        [
            {
                "op": "add_field",
                "entry_type_key": "service_request",
                "field": {
                    "key": "priority",
                    "name": "Priority",
                    "type": "select",
                    "enum": ["Low", "Normal", "High"],
                },
            }
        ],
    )
    assert out["track"]["entry_types"][0]["fields"] == [
        {
            "key": "priority",
            "name": "Priority",
            "type": "select",
            "enum": ["Low", "Normal", "High"],
        }
    ]


def test_remove_field_drops_target():
    base = _empty_track_manifest()
    base["track"]["entry_types"].append(
        {
            "key": "task",
            "name": "Task",
            "fields": [{"key": "f", "type": "text"}],
        }
    )
    out = apply_operations(
        base,
        [{"op": "remove_field", "entry_type": "task", "field_key": "f"}],
    )
    assert out["track"]["entry_types"][0]["fields"] == []


def test_modify_field_patches_existing():
    base = _empty_track_manifest()
    base["track"]["entry_types"].append(
        {
            "key": "task",
            "name": "Task",
            "fields": [{"key": "priority", "type": "select", "enum": ["low"]}],
        }
    )
    out = apply_operations(
        base,
        [
            {
                "op": "modify_field",
                "entry_type": "task",
                "field_key": "priority",
                "patch": {"enum": ["low", "high"]},
            }
        ],
    )
    assert out["track"]["entry_types"][0]["fields"][0]["enum"] == ["low", "high"]


def test_add_view_inserts_view():
    out = apply_operations(
        _empty_track_manifest(),
        [
            {
                "op": "add_view",
                "spec": {"key": "feed", "name": "Feed", "view_type": "feed"},
            }
        ],
    )
    assert out["track"]["views"][0]["key"] == "feed"


def test_add_tag_creates_group_if_missing():
    out = apply_operations(
        _empty_track_manifest(),
        [
            {
                "op": "add_tag",
                "group_key": "priority",
                "spec": {"key": "high", "name": "High"},
            }
        ],
    )
    groups = out["track"]["taxonomy"]["tag_groups"]
    assert len(groups) == 1
    assert groups[0]["key"] == "priority"
    assert groups[0]["tags"][0]["key"] == "high"


def test_register_composite_field_type_appends():
    out = apply_operations(
        _empty_track_manifest(),
        [
            {
                "op": "register_composite_field_type",
                "spec": {"key": "currency", "base": "number"},
            }
        ],
    )
    assert out["field_types"][0]["key"] == "currency"


def test_register_composite_view_type_appends():
    out = apply_operations(
        _empty_track_manifest(),
        [
            {
                "op": "register_composite_view_type",
                "spec": {"key": "roadmap", "base": "composable_board"},
            }
        ],
    )
    assert out["view_types"][0]["key"] == "roadmap"


def test_unknown_op_rejected():
    with pytest.raises(BadRequestError):
        apply_operations(
            _empty_track_manifest(),
            [{"op": "totally_unknown"}],
        )


def test_apply_does_not_mutate_input():
    manifest = _empty_track_manifest()
    snapshot = {"entry_types": list(manifest["track"]["entry_types"])}
    apply_operations(
        manifest,
        [{"op": "add_entry_type", "spec": {"key": "task", "name": "Task"}}],
    )
    assert manifest["track"]["entry_types"] == snapshot["entry_types"]


def test_space_scope_requires_track_when_multiple():
    base = {
        "scope": "app",
        "app": {
            "tracks": [
                {"key": "a", "name": "A", "entry_types": [], "views": []},
                {"key": "b", "name": "B", "entry_types": [], "views": []},
            ],
            "relations": [],
        },
    }
    with pytest.raises(BadRequestError):
        apply_operations(
            base,
            [
                {
                    "op": "add_entry_type",
                    "spec": {"key": "x", "name": "X"},
                }
            ],
        )


def test_space_scope_targets_named_track():
    base = {
        "scope": "app",
        "app": {
            "tracks": [
                {"key": "a", "name": "A", "entry_types": [], "views": []},
                {"key": "b", "name": "B", "entry_types": [], "views": []},
            ],
            "relations": [],
        },
    }
    out = apply_operations(
        base,
        [
            {
                "op": "add_entry_type",
                "track": "b",
                "spec": {"key": "x", "name": "X"},
            }
        ],
    )
    assert out["app"]["tracks"][1]["entry_types"][0]["key"] == "x"
    assert out["app"]["tracks"][0]["entry_types"] == []


def test_add_relation_only_in_space_scope():
    with pytest.raises(BadRequestError):
        apply_operations(
            _empty_track_manifest(),
            [
                {
                    "op": "add_relation",
                    "spec": {
                        "source_track_type": "a",
                        "target_track_type": "b",
                    },
                }
            ],
        )
