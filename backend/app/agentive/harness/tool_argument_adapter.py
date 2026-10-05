"""Normalize schema-declared structured values from model tool arguments.

Some providers emit an object or array as a JSON string even when their tool
call schema declares the field as structured JSON. This adapter repairs only
that representation mismatch. Integral's capability broker remains the
authority for validating the normalized value against the complete schema.
"""

from __future__ import annotations

import json
from typing import Any


def normalize_tool_arguments(
    arguments: dict[str, Any], schema: dict[str, Any]
) -> dict[str, Any]:
    """Decode JSON strings only at schema locations requiring objects/arrays."""
    normalized = _normalize_value(arguments, schema, schema)
    return normalized if isinstance(normalized, dict) else arguments


def _resolve_schema(
    schema: dict[str, Any], root_schema: dict[str, Any]
) -> dict[str, Any]:
    """Resolve a local JSON Schema reference without fetching external refs."""
    reference = schema.get("$ref")
    if not isinstance(reference, str) or not reference.startswith("#/"):
        return schema

    resolved: Any = root_schema
    for part in reference[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(resolved, dict) or part not in resolved:
            return schema
        resolved = resolved[part]
    if not isinstance(resolved, dict):
        return schema
    return {
        **resolved,
        **{key: value for key, value in schema.items() if key != "$ref"},
    }


def _normalize_value(
    value: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
) -> Any:
    schema = _resolve_schema(schema, root_schema)
    expected_type = schema.get("type")

    if isinstance(value, str) and expected_type in {"object", "array"}:
        # Some model/provider combinations JSON-encode a structured tool
        # argument more than once. Decode only while the schema requires a
        # container and the decoded value remains a string. The bound avoids
        # unbounded parsing while accommodating common double-encoding wobble.
        candidate = value
        for _ in range(3):
            try:
                decoded = json.loads(candidate)
            except (json.JSONDecodeError, TypeError):
                break
            if (expected_type == "object" and isinstance(decoded, dict)) or (
                expected_type == "array" and isinstance(decoded, list)
            ):
                value = decoded
                break
            if not isinstance(decoded, str):
                break
            candidate = decoded

    if isinstance(value, dict):
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return value
        return {
            key: (
                _normalize_value(item, properties[key], root_schema)
                if key in properties and isinstance(properties[key], dict)
                else item
            )
            for key, item in value.items()
        }

    if isinstance(value, list):
        items_schema = schema.get("items")
        if isinstance(items_schema, dict):
            return [_normalize_value(item, items_schema, root_schema) for item in value]
    return value
