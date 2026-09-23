"""Resolve manifest ``expose_metadata`` projections into derived entry fields.

Plan 03 — Phase 4.

When an entry type's manifest declares a ``file`` or ``files`` field
with ``config.expose_metadata``, the entry's read endpoint surfaces
the mapped values alongside its ``custom_fields`` so queries and the
UI can use attachment metadata as if it were native entry data.

Resolution is read-time, not write-time. That keeps the derived value
fresh after metadata reprocessing without an entry-side write. The
cost is one ``Attachment.get`` per referenced file field on read —
cheap, and we already touch the attachment graph for the standard
attachments list.

The resolved bag is returned as a separate dict so the caller can
either nest it under a top-level ``derived_fields`` key or merge it
into ``custom_fields``. The default behaviour we've chosen is the
former — keeps the boundary clear between writer-controlled state
(custom_fields) and projected state (derived_fields).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.models.nodes import Attachment, Entry, EntryType
from app.services.operational_model_runtime import (
    resolve_entry_type_spec,
    resolve_track_runtime_profile,
)

logger = logging.getLogger(__name__)


def _resolve_attribute_path(obj: Any, path: str) -> Any:
    """Walk a dotted path through a nested dict-of-dicts.

    Supports the manifest's two common shapes:

        - "common.page_count"            → obj["common"]["page_count"]
        - "type_specific.author"         → obj["type_specific"]["author"]
        - "type_specific.exif.Make"      → nested dict traversal
    """
    cursor = obj
    for segment in path.split("."):
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(segment)
        if cursor is None:
            return None
    return cursor


async def resolve_derived_fields_for_entry(
    entry: Entry,
    entry_type: Optional[EntryType] = None,
) -> Dict[str, Any]:
    """Resolve the entry's derived fields from its file-field bindings.

    Returns an empty dict when:
        - the entry type has no manifest-declared file fields, or
        - none of those fields opt-in to ``expose_metadata``, or
        - the referenced attachments are missing / unscanned / not
          yet through the metadata pipeline.
    """
    if entry_type is None:
        # Lookup is cheap and avoids stale references when the caller
        # didn't already have the EntryType in hand.
        if not entry.type_id:
            return {}
        entry_type = await EntryType.get(entry.type_id)
        if entry_type is None:
            return {}

    # The form_schema on EntryType is the post-merge runtime tier — no
    # need to re-resolve unless the manifest is mutating mid-request.
    try:
        spec = (
            entry_type.form_schema
            if isinstance(entry_type.form_schema, dict) and entry_type.form_schema
            else {}
        )
        fields = list(spec.get("fields") or [])
    except Exception:  # noqa: BLE001
        return {}

    # Build a flat list of (field_key, attachment_ids, expose_specs).
    plans: List[Dict[str, Any]] = []
    for f in fields:
        if not isinstance(f, dict):
            continue
        ftype = str(f.get("type") or "")
        if ftype not in ("file", "files"):
            continue
        cfg = f.get("config") or {}
        expose = cfg.get("expose_metadata") or []
        if not expose:
            continue
        field_key = str(f.get("key") or "")
        if not field_key:
            continue
        raw_value = (entry.custom_fields or {}).get(field_key)
        if raw_value in (None, "", []):
            continue
        ids: List[str]
        if ftype == "file":
            ids = [str(raw_value)] if isinstance(raw_value, str) else []
        else:
            ids = []
            if isinstance(raw_value, list):
                ids = [str(x) for x in raw_value]
        plans.append(
            {
                "field_key": field_key,
                "ftype": ftype,
                "attachment_ids": ids,
                "expose": expose,
            }
        )

    if not plans:
        return {}

    derived: Dict[str, Any] = {}
    for plan in plans:
        # ``file`` (singular): resolve once. ``files``: resolve per
        # attachment and present as a list so the consumer can index.
        is_many = plan["ftype"] == "files"
        per_attachment_payloads: List[Dict[str, Any]] = []
        for aid in plan["attachment_ids"]:
            try:
                att = await Attachment.get(aid)
            except Exception:  # noqa: BLE001
                att = None
            if att is None:
                continue
            metadata = att.metadata if isinstance(att.metadata, dict) else {}
            single: Dict[str, Any] = {}
            for mapping in plan["expose"]:
                src = str(mapping.get("from") or "").strip()
                alias = str(mapping.get("as") or src).strip()
                if not src:
                    continue
                value = _resolve_attribute_path(metadata, src)
                if value is None:
                    continue
                single[alias] = value
            if single:
                per_attachment_payloads.append(single)

        if not per_attachment_payloads:
            continue
        if is_many:
            # Aggregate: produce one entry per attachment, plus a flat
            # "first" copy for query convenience.
            derived[f"{plan['field_key']}__derived"] = per_attachment_payloads
            for k, v in per_attachment_payloads[0].items():
                derived.setdefault(k, v)
        else:
            for k, v in per_attachment_payloads[0].items():
                derived[k] = v

    return derived


async def resolve_derived_fields_for_entry_lookup(
    entry: Entry,
    *,
    entry_type: Optional[EntryType] = None,
    runtime_tier: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Variant that also resolves the runtime tier when needed.

    Most callers already have the EntryType (entry-read endpoints
    pre-fetch it). For the rare path where the runtime tier needs to
    be materialised here, this helper takes care of it so the call
    site stays a one-liner.
    """
    if entry_type is None and entry.type_id:
        entry_type = await EntryType.get(entry.type_id)
    if runtime_tier is None and entry.track_id:
        try:
            from app.models.nodes import Track

            track = await Track.get(entry.track_id)
            if track is not None:
                _, runtime_tier, _ = await resolve_track_runtime_profile(track)
        except Exception:  # noqa: BLE001
            runtime_tier = None
    if runtime_tier and entry_type is not None:
        # If we resolved a runtime tier, the form_schema we want is
        # the *tier-merged* one — not the raw EntryType.form_schema
        # which may lag behind library merges.
        try:
            spec = resolve_entry_type_spec(entry_type, runtime_tier)
            entry_type.form_schema = spec  # in-memory only; not persisted
        except Exception:  # noqa: BLE001
            pass
    return await resolve_derived_fields_for_entry(entry, entry_type)
