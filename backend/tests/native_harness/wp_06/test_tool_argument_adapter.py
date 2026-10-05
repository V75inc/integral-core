"""Provider argument encoding normalization stays schema-driven."""

from __future__ import annotations

from app.agentive.harness.tool_argument_adapter import normalize_tool_arguments


def test_decodes_object_and_array_values_declared_by_the_tool_schema() -> None:
    """Decode provider strings at schema locations with structured types."""
    schema = {
        "type": "object",
        "properties": {
            "blueprint": {
                "type": "object",
                "properties": {"tracks": {"type": "array"}},
            },
            "labels": {"type": "array", "items": {"type": "string"}},
        },
    }

    result = normalize_tool_arguments(
        {
            "blueprint": '{"tracks":"[{\\"id\\":\\"tools\\"}]"}',
            "labels": '["Hand Tools","Power Tools"]',
        },
        schema,
    )

    assert result == {
        "blueprint": {"tracks": [{"id": "tools"}]},
        "labels": ["Hand Tools", "Power Tools"],
    }


def test_resolves_local_schema_references_before_normalizing() -> None:
    """Local schema references retain their structured value requirement."""
    schema = {
        "type": "object",
        "properties": {"blueprint": {"$ref": "#/$defs/Blueprint"}},
        "$defs": {"Blueprint": {"type": "object", "properties": {}}},
    }

    assert normalize_tool_arguments({"blueprint": '{"tracks": []}'}, schema) == {
        "blueprint": {"tracks": []}
    }


def test_preserves_values_when_the_schema_does_not_declare_structured_json() -> None:
    """Primitive fields are not reinterpreted as encoded structured values."""
    arguments = {"text": '{"looks":"like json"}', "count": "12"}
    schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}, "count": {"type": "integer"}},
    }

    assert normalize_tool_arguments(arguments, schema) == arguments


def test_leaves_malformed_or_wrong_shape_structured_values_for_authoritative_rejection() -> (
    None
):
    """Invalid JSON and mismatched shapes remain for broker rejection."""
    arguments = {"blueprint": '["not", "an", "object"]', "items": "not-json"}
    schema = {
        "type": "object",
        "properties": {
            "blueprint": {"type": "object"},
            "items": {"type": "array"},
        },
    }

    assert normalize_tool_arguments(arguments, schema) == arguments
