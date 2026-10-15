"""Bounded generic operation-output dependency inspection; no domain heuristics."""

from __future__ import annotations

import math
import re
from typing import Any

from app.services.event_snapshot_projection import KINDS

# Core stores identity/credential objects under o.* as well as graph n.*
# nodes. Neither namespace can silently become an empty dependency set.
# Activity snapshots retain their independent, reviewed inspection contract.
REFERENCE = re.compile(r"[no]\.([A-Za-z][A-Za-z0-9_]*)\.[A-Za-z0-9_-]+")

# A standard 100-entry ObjectRef collection includes scope and title fields.
# Its structure needs more room than an activity-feed snapshot; authority
# checks still have a small independent global cap, including scope references.
MAX_OUTPUT_VALUES = 10000
MAX_OUTPUT_REFERENCES = 128
MAX_MUTATION_REFERENCES = 100
MAX_OUTPUT_DEPTH = 20
MAX_OUTPUT_STRING = 65536
MAX_OUTPUT_TEXT = 1048576


RESOURCE_KINDS = frozenset(KINDS.values())


def _typed_reference(value: Any) -> tuple[str, str] | None:
    if not isinstance(value, dict):
        return None
    kind, resource_id = value.get("kind"), value.get("id")
    if (
        not isinstance(kind, str)
        or kind not in RESOURCE_KINDS
        or not isinstance(resource_id, str)
        or not resource_id
        or resource_id != resource_id.strip()
    ):
        return None
    matched = REFERENCE.fullmatch(resource_id)
    if matched is not None and KINDS.get(matched.group(1)) != kind:
        return None
    return kind, resource_id


def output_json_within_bounds(value: Any) -> bool:
    """Bound the complete envelope, including trusted non-resource metadata."""
    return _inspect_output(value, inspect_references=False) is not None


def output_references(value: Any) -> set[tuple[str, str]] | None:
    """Inspect canonical IDs and supported ObjectRef shapes within strict bounds.

    None means incomplete/invalid inspection, never an empty dependency set.
    Domain identifiers without resource claims are not graph references. Whole
    output admission must fail when any inspected resource cannot be admitted.
    """
    return _inspect_output(value, inspect_references=True)


def _inspect_output(
    value: Any, *, inspect_references: bool
) -> set[tuple[str, str]] | None:
    references: set[tuple[str, str]] = set()
    pending = [(value, 0)]
    visited = text_size = 0
    while pending:
        item, depth = pending.pop()
        visited += 1
        if visited > MAX_OUTPUT_VALUES or depth > MAX_OUTPUT_DEPTH:
            return None
        remaining = MAX_OUTPUT_VALUES - visited - len(pending)
        if isinstance(item, dict):
            if 2 * len(item) > remaining:
                return None
            if (
                inspect_references
                and isinstance(item.get("kind"), str)
                and item["kind"] in RESOURCE_KINDS
                and "id" in item
            ):
                reference = _typed_reference(item)
                if reference is None:
                    return None
                references.add(reference)
            for key, part in item.items():
                if inspect_references and key == "object_refs":
                    if (
                        not isinstance(part, list)
                        or len(part) > MAX_MUTATION_REFERENCES
                    ):
                        return None
                    for raw_reference in part:
                        reference = _typed_reference(raw_reference)
                        if reference is None:
                            return None
                        references.add(reference)
                pending.append((key, depth + 1))
                pending.append((part, depth + 1))
        elif isinstance(item, list):
            if len(item) > remaining:
                return None
            pending.extend((part, depth + 1) for part in item)
        elif isinstance(item, str):
            text_size += len(item)
            if len(item) > MAX_OUTPUT_STRING or text_size > MAX_OUTPUT_TEXT:
                return None
            for matched in REFERENCE.finditer(item) if inspect_references else ():
                kind = KINDS.get(matched.group(1))
                if kind is None:
                    return None
                references.add((kind, matched.group()))
                if len(references) > MAX_OUTPUT_REFERENCES:
                    return None
        elif isinstance(item, float):
            if not math.isfinite(item):
                return None
        elif item is not None and not isinstance(item, (bool, int)):
            return None
        if len(references) > MAX_OUTPUT_REFERENCES:
            return None
    return references
