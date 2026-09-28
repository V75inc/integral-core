"""Resolve a custom-field reference to the EntryType key.

A reference matches the stored key, or one exact label. Anything else is
refused. Two labels that point at different keys are refused too.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping


def catalog_from_entry_types(entry_types: Iterable[Any]) -> List[Dict[str, str]]:
    """Flatten ``form_schema.fields`` into ``{key, label}`` rows."""
    rows: List[Dict[str, str]] = []
    for entry_type in entry_types:
        schema = getattr(entry_type, "form_schema", None)
        if not isinstance(schema, dict):
            continue
        fields = schema.get("fields") or []
        if not isinstance(fields, list):
            continue
        for field in fields:
            if not isinstance(field, dict) or not field.get("key"):
                continue
            label = str(field.get("label") or field.get("name") or "").strip()
            rows.append({"key": str(field["key"]), "label": label})
    return rows


def resolve_field_reference(
    reference: str, catalog: Iterable[Mapping[str, str]]
) -> str:
    """Return the canonical key for ``reference``."""
    raw = str(reference or "").strip()
    if raw.startswith("custom_fields."):
        raw = raw[len("custom_fields.") :]
    if not raw:
        raise ValueError("field reference is empty")
    if raw.startswith("_"):
        return raw
    fields = [row for row in catalog if row.get("key")]
    exact = [row for row in fields if row["key"] == raw]
    if len(exact) == 1:
        return exact[0]["key"]
    folded = [row for row in fields if row["key"].casefold() == raw.casefold()]
    folded_keys = {row["key"] for row in folded}
    if len(folded_keys) == 1:
        return folded_keys.pop()
    labeled = [
        row
        for row in fields
        if row.get("label") and str(row["label"]).casefold() == raw.casefold()
    ]
    label_keys = {row["key"] for row in labeled}
    if len(label_keys) == 1:
        return label_keys.pop()
    if len(label_keys) > 1:
        raise ValueError(f"field {reference!r} matches more than one field")
    raise ValueError(f"field {reference!r} is not on this entry type")


def resolve_field_map(
    fields: Mapping[str, Any], catalog: Iterable[Mapping[str, str]]
) -> Dict[str, Any]:
    """Rewrite a write payload onto canonical keys."""
    resolved: Dict[str, Any] = {}
    unknown: List[str] = []
    for key, value in fields.items():
        try:
            canonical = resolve_field_reference(str(key), catalog)
        except ValueError:
            unknown.append(str(key))
            continue
        if canonical in resolved and resolved[canonical] != value:
            raise ValueError(f"field {canonical!r} was given more than once")
        resolved[canonical] = value
    if unknown:
        raise ValueError("fields not on this entry type: " + ", ".join(unknown))
    return resolved


def resolve_custom_field_path(path: str, catalog: Iterable[Mapping[str, str]]) -> str:
    """Rewrite ``custom_fields.Value`` to ``custom_fields.value``."""
    if not str(path).startswith("custom_fields."):
        return path
    key = resolve_field_reference(path, catalog)
    return f"custom_fields.{key}"
