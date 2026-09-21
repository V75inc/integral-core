"""Tests for the field-type and view-type registries (Pillar 1)."""

import pytest

from app.exceptions import BadRequestError
from app.services import operational_model_field_types as field_types
from app.services.operational_model_runtime import compile_canonical_manifest
from app.views import operational_model_view_types as view_types

# ---------------------------------------------------------------------------
# Built-in registration sanity
# ---------------------------------------------------------------------------


def test_builtin_field_types_registered():
    keys = set(field_types.allowed_keys())
    expected = {
        "text",
        "number",
        "boolean",
        "date",
        "datetime",
        "markdown",
        "json",
        "select",
        "multi_select",
        "relation",
        "computed",
        "file",
        "files",
    }
    assert expected.issubset(keys)


def test_computed_field_requires_a_runtime_evaluator():
    manifest = {
        "operational_model_schema_version": 2,
        "scope": "track",
        "track": {
            "entry_types": [
                {
                    "key": "invoice",
                    "name": "Invoice",
                    "fields": [{"key": "total", "type": "computed"}],
                }
            ],
            "views": [],
            "taxonomy": {"tag_groups": []},
            "defaults": {},
        },
    }

    with pytest.raises(BadRequestError, match="not supported by the current runtime"):
        compile_canonical_manifest(manifest=manifest)


def test_builtin_view_types_registered():
    keys = set(view_types.allowed_keys())
    expected = {
        "feed",
        "kanban",
        "table",
        "calendar",
        "gallery",
        "composable_list",
        "composable_grid",
        "composable_board",
        "composable_timeline",
    }
    assert expected.issubset(keys)


def test_field_type_get_returns_spec():
    spec = field_types.get("number")
    assert spec is not None
    assert spec.type == "number"
    assert spec.source == "builtin"
    assert spec.base is None


def test_view_type_get_returns_spec():
    spec = view_types.get("composable_board")
    assert spec is not None
    assert spec.type == "composable_board"
    assert spec.config_schema  # non-empty config schema for meta-widget


def test_feed_view_contract_defaults_are_platform_neutral():
    spec = view_types.get("feed")
    assert spec is not None
    assert "web" in spec.supported_platforms
    assert "mobile" in spec.supported_platforms
    assert spec.default_always_on is True
    assert spec.hot_loadable is False
    assert spec.palette_group == "core"
    assert spec.scope == "track"


def test_view_type_spec_scope_defaults_to_track():
    """UI Packs Standard — ``ViewTypeSpec.scope`` defaults to 'track' so
    every existing manifest/spec that doesn't set it behaves unchanged."""
    assert view_types.ViewTypeSpec(type="anything").scope == "track"


def test_track_manifest_injects_feed_default_when_missing():
    manifest = {
        "operational_model_schema_version": 2,
        "scope": "track",
        "track": {
            "entry_types": [],
            "views": [{"key": "board", "name": "Board", "view_type": "kanban"}],
            "taxonomy": {"tag_groups": []},
            "defaults": {},
        },
    }
    compiled = compile_canonical_manifest(manifest=manifest)
    views = compiled["track"]["views"]
    assert any(v.get("view_type") == "feed" for v in views)
    assert (compiled["track"].get("defaults") or {}).get("default_view") == "feed"


def test_track_manifest_keeps_explicit_non_feed_default():
    manifest = {
        "operational_model_schema_version": 2,
        "scope": "track",
        "track": {
            "entry_types": [],
            "views": [{"key": "table", "name": "Table", "view_type": "table"}],
            "taxonomy": {"tag_groups": []},
            "defaults": {"default_view": "table"},
        },
    }
    compiled = compile_canonical_manifest(manifest=manifest)
    views = compiled["track"]["views"]
    assert any(v.get("view_type") == "feed" for v in views)
    assert (compiled["track"].get("defaults") or {}).get("default_view") == "table"


# ---------------------------------------------------------------------------
# Plugin-style registration
# ---------------------------------------------------------------------------


def test_register_field_type_increments_version():
    spec = field_types.FieldTypeSpec(
        type="test_only_field",
        label="Test Only",
        description="Registered by a unit test only.",
    )
    before = field_types.registry_version()
    field_types.register_field_type(spec)
    try:
        assert field_types.registry_version() > before
        assert field_types.is_known("test_only_field")
    finally:
        # Manual cleanup since the registry has no public unregister.
        field_types._REGISTRY.pop("test_only_field", None)


def test_register_field_type_rejects_duplicate():
    with pytest.raises(ValueError):
        field_types.register_field_type(field_types.get("number"))


def test_register_view_type_rejects_duplicate():
    with pytest.raises(ValueError):
        view_types.register_view_type(view_types.get("table"))


# ---------------------------------------------------------------------------
# Composite resolution via compile_canonical_manifest
# ---------------------------------------------------------------------------


def test_manifest_composite_field_type_resolves():
    manifest = {
        "operational_model_schema_version": 2,
        "scope": "track",
        "field_types": [
            {
                "key": "currency",
                "base": "number",
                "config": {"currency": "USD", "min": 0},
            }
        ],
        "track": {
            "entry_types": [
                {
                    "name": "Invoice",
                    "fields": [{"key": "amount", "type": "currency"}],
                }
            ],
            "views": [{"name": "Feed", "view_type": "feed"}],
            "taxonomy": {},
        },
    }
    compiled = compile_canonical_manifest(manifest=manifest)
    field = compiled["track"]["entry_types"][0]["fields"][0]
    assert field["type"] == "currency"
    assert field["composite"]["base"] == "number"
    assert field["composite"]["config"] == {"currency": "USD", "min": 0}


def test_manifest_composite_view_type_resolves():
    manifest = {
        "operational_model_schema_version": 2,
        "scope": "track",
        "view_types": [
            {
                "key": "roadmap",
                "base": "composable_board",
                "config": {"group_by": "stage"},
            }
        ],
        "track": {
            "entry_types": [],
            "views": [{"name": "Roadmap", "view_type": "roadmap"}],
            "taxonomy": {},
        },
    }
    compiled = compile_canonical_manifest(manifest=manifest)
    view = compiled["track"]["views"][0]
    assert view["view_type"] == "roadmap"
    assert view["composite"]["base"] == "composable_board"
    assert view["composite"]["config"] == {"group_by": "stage"}


def test_manifest_unknown_composite_base_rejected():
    manifest = {
        "operational_model_schema_version": 2,
        "scope": "track",
        "field_types": [{"key": "x", "base": "totally_unknown"}],
        "track": {"entry_types": [], "views": [], "taxonomy": {}},
    }
    with pytest.raises(BadRequestError):
        compile_canonical_manifest(manifest=manifest)


def test_manifest_duplicate_composite_key_rejected():
    manifest = {
        "operational_model_schema_version": 2,
        "scope": "track",
        "field_types": [
            {"key": "currency", "base": "number"},
            {"key": "currency", "base": "text"},
        ],
        "track": {"entry_types": [], "views": [], "taxonomy": {}},
    }
    with pytest.raises(BadRequestError):
        compile_canonical_manifest(manifest=manifest)


def test_manifest_unknown_field_type_rejected():
    manifest = {
        "operational_model_schema_version": 2,
        "scope": "track",
        "track": {
            "entry_types": [
                {
                    "name": "X",
                    "fields": [{"key": "f", "type": "totally_unknown"}],
                }
            ],
            "views": [],
            "taxonomy": {},
        },
    }
    with pytest.raises(BadRequestError):
        compile_canonical_manifest(manifest=manifest)


def test_manifest_unknown_view_type_rejected():
    manifest = {
        "operational_model_schema_version": 2,
        "scope": "track",
        "track": {
            "entry_types": [],
            "views": [{"name": "X", "view_type": "totally_unknown"}],
            "taxonomy": {},
        },
    }
    with pytest.raises(BadRequestError):
        compile_canonical_manifest(manifest=manifest)


def test_v1_manifest_without_composites_compiles():
    """Backwards-compat: existing v1 manifests with no field_types / view_types
    block continue to compile to byte-identical output (no new keys leak in)."""
    manifest = {
        "operational_model_schema_version": 2,
        "scope": "track",
        "track": {
            "entry_types": [
                {
                    "name": "Task",
                    "fields": [{"key": "title", "type": "text"}],
                }
            ],
            "views": [{"name": "Feed", "view_type": "feed"}],
            "taxonomy": {"tag_groups": []},
        },
    }
    compiled = compile_canonical_manifest(manifest=manifest)
    assert "field_types" not in compiled
    assert "view_types" not in compiled
    assert "plugins" not in compiled
    field = compiled["track"]["entry_types"][0]["fields"][0]
    assert "composite" not in field
