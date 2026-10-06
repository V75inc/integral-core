"""Controlled context resolver for document template fields.

Only allowlisted registry keys are resolved. Max relation-walk depth: 2.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from app.services.documents.field_ref import normalize_field_ref, slug_ref_part
from app.services.documents.field_registry import (
    get_field_spec,
    get_workspace_context_index,
)
from app.services.documents.formatters import format_value

logger = logging.getLogger(__name__)

MAX_RELATION_DEPTH = 2

PLACEHOLDER_SAMPLES: Dict[str, str] = {
    "text": "Sample Text",
    "date": "2026-09-01",
    "datetime": "2026-09-01T09:00:00",
    "currency": "300000",
    "number": "42",
    "boolean": "true",
}


def _role_rank(role: Optional[str]) -> int:
    order = {
        "viewer": 1,
        "commenter": 2,
        "editor": 3,
        "owner": 4,
        "admin": 5,
    }
    return order.get(role or "", 0)


async def _load_entry(entry_id: str):
    from app.models.nodes import Entry

    if not entry_id:
        return None
    return await Entry.get(entry_id)


def _read_path(entry: Any, path: str) -> Any:
    """Read ``title`` / ``body`` / ``custom_fields.foo`` from an Entry."""
    if not entry or not path:
        return None
    parts = path.split(".")
    if parts[0] == "custom_fields":
        cf = getattr(entry, "custom_fields", None) or {}
        if not isinstance(cf, dict):
            return None
        cur: Any = cf
        for p in parts[1:]:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(p)
        return cur
    return getattr(entry, parts[0], None)


async def _walk_relation(
    entry: Any, relation_path: str, field: str, depth: int = 1
) -> Any:
    if depth > MAX_RELATION_DEPTH:
        return None
    related_id = _relation_entry_id(_read_path(entry, relation_path))
    if not related_id:
        return None
    related = await _load_entry(related_id)
    if not related:
        return None
    if field.startswith("custom_fields.") or field in ("title", "body"):
        return _read_path(related, field)
    # Nested walk: field may itself be another relation path encoded as
    # ``custom_fields.manager|title`` — keep simple for v1.
    return _read_path(related, field)


def _relation_entry_id(value: Any) -> str:
    """Normalize relation field values (id string, list, or hydrated {id} object)."""
    if value is None:
        return ""
    if isinstance(value, dict):
        return str(value.get("id") or value.get("entry_id") or "").strip()
    if isinstance(value, list):
        return _relation_entry_id(value[0]) if value else ""
    return str(value).strip()


def _as_single_id(value: Any) -> str:
    return _relation_entry_id(value)


def _declared_relation_fields(workspace_id: str, context_key: str) -> List[str]:
    """Custom-field keys the owning app declared for this document context."""
    spec = get_workspace_context_index(workspace_id).get(context_key) or {}
    raw = spec.get("relation_fields") or []
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


async def _entry_track_key(entry: Any) -> str:
    track_id = str(getattr(entry, "track_id", "") or "")
    if not track_id:
        return ""
    from app.models.nodes import Track

    track = await Track.get(track_id)
    if not track:
        return ""
    template_key = str(getattr(track, "template_id", "") or "").strip()
    if template_key:
        return slug_ref_part(template_key)
    return slug_ref_part(str(getattr(track, "title", "") or ""))


def _relation_id_for_context(
    anchor_entry: Any, context_key: str, workspace_id: str = ""
) -> str:
    cf = getattr(anchor_entry, "custom_fields", None) or {}
    if not isinstance(cf, dict):
        return ""
    keys_to_try = _declared_relation_fields(workspace_id, context_key)
    if context_key not in keys_to_try:
        keys_to_try.insert(0, context_key)
    for key in keys_to_try:
        rel_id = _as_single_id(cf.get(key))
        if rel_id:
            return rel_id
    return ""


async def _entry_matches_document_context(
    entry: Any,
    context_key: str,
    workspace_id: str,
) -> bool:
    ctx_spec = get_workspace_context_index(workspace_id).get(context_key)
    if not ctx_spec:
        return False
    allowed = [
        str(t).strip()
        for t in (ctx_spec.get("context_entry_types") or [])
        if str(t).strip()
    ]
    if not allowed:
        return False
    type_id = str(getattr(entry, "type_id", "") or "")
    if not type_id:
        return False
    from app.models.nodes import EntryType
    from app.services.documents.profile_compat import slug_manifest_key

    et = await EntryType.get(type_id)
    if not et:
        return False
    fs = getattr(et, "form_schema", None) or {}
    manifest_key = ""
    if isinstance(fs, dict):
        manifest_key = slug_manifest_key(str(fs.get("_manifest_entry_type_key") or ""))
    name_key = slug_manifest_key(str(getattr(et, "name", "") or ""))
    allowed_slugs = {slug_manifest_key(t) for t in allowed}
    return manifest_key in allowed_slugs or name_key in allowed_slugs


async def _entry_for_document_context(
    *,
    anchor_entry: Any,
    context_key: str,
    primary_entry: Any,
    workspace_id: str,
    fallback: Any,
    spec: Optional[Dict[str, Any]] = None,
) -> Any:
    """Resolve the entry that owns values for a template field's source context."""
    spec = spec or {}
    track_id = str(spec.get("track_id") or "").strip()
    track_key = str(spec.get("track_key") or "").strip()
    semantic_ctx = str(spec.get("context_key") or "").strip()

    primary_track_id = str(getattr(primary_entry, "track_id", "") or "")
    if track_id and track_id == primary_track_id:
        return primary_entry
    if context_key and context_key == primary_track_id:
        return primary_entry

    primary_track_key = await _entry_track_key(primary_entry)
    for tk in (track_key, context_key):
        if tk and slug_ref_part(tk) == primary_track_key:
            return primary_entry

    for ctx in (semantic_ctx, context_key, track_key):
        if ctx and await _entry_matches_document_context(
            primary_entry, ctx, workspace_id
        ):
            return primary_entry

    for rel_ctx in (track_key, semantic_ctx, context_key):
        if not rel_ctx:
            continue
        rel_id = _relation_id_for_context(anchor_entry, rel_ctx, workspace_id)
        if rel_id:
            related = await _load_entry(rel_id)
            if related:
                return related

    return fallback


def _resolution_context_key(field_key: str, spec: Dict[str, Any]) -> str:
    track_id = str(spec.get("track_id") or "").strip()
    if track_id:
        return track_id
    track_key = str(spec.get("track_key") or "").strip()
    if track_key:
        return track_key
    ctx_key = str(spec.get("context_key") or "").strip()
    if ctx_key:
        return ctx_key
    normalized = normalize_field_ref(field_key)
    if "." in normalized:
        return normalized.split(".", 1)[0]
    return ""


async def _coalesce_entry_field_value(
    *,
    field_key: str,
    path: str,
    target_entry: Any,
    anchor_entry: Optional[Any],
) -> Any:
    """Read ``path`` on the target entry, then on the anchor when it differs."""
    del field_key
    val = _read_path(target_entry, path)
    if val not in (None, ""):
        return val
    if anchor_entry is not None and anchor_entry is not target_entry:
        return _read_path(anchor_entry, path)
    return None


async def resolve_field_value(
    *,
    workspace_id: str,
    field_key: str,
    context_entry: Any,
    caller_role: Optional[str] = None,
    input_values: Optional[Dict[str, Any]] = None,
    anchor_entry: Optional[Any] = None,
) -> Tuple[Any, Optional[str]]:
    """Return (raw_value, warning). warning set when omitted/blocked."""
    spec = get_field_spec(workspace_id, field_key) or {}
    has_spec = bool(spec.get("key") or spec.get("field_ref"))
    if not has_spec:
        if field_key in ("title", "body"):
            return _read_path(context_entry, field_key), None
        cf = getattr(context_entry, "custom_fields", None) or {}
        if isinstance(cf, dict) and field_key in cf:
            return cf.get(field_key), None
        return None, f"unknown_field:{field_key}"
    if not spec.get("active", True):
        return None, f"inactive_field:{field_key}"

    required = spec.get("requires_role")
    if required and _role_rank(caller_role) < _role_rank(str(required)):
        return None, f"insufficient_role:{field_key}"

    source = spec.get("source") or {}
    kind = str(source.get("kind") or "entry_field")
    anchor = anchor_entry if anchor_entry is not None else context_entry
    ctx_key = _resolution_context_key(field_key, spec)
    target_entry = context_entry
    if ctx_key:
        target_entry = await _entry_for_document_context(
            anchor_entry=anchor,
            context_key=ctx_key,
            primary_entry=context_entry,
            workspace_id=workspace_id,
            fallback=context_entry,
            spec=spec,
        )

    if kind == "document_input":
        key = str(source.get("input_key") or field_key.split(".")[-1])
        return (input_values or {}).get(key), None

    if kind == "static":
        return source.get("value"), None

    if kind == "generation_time":
        from app.services.documents.system_fields import resolve_generation_time

        return resolve_generation_time(source), None

    if kind == "entry_field":
        path = str(source.get("path") or "")
        val = await _coalesce_entry_field_value(
            field_key=field_key,
            path=path,
            target_entry=target_entry,
            anchor_entry=anchor,
        )
        if val in (None, "") and input_values:
            local = str(spec.get("local_field_key") or field_key.split(".")[-1])
            if local == "work_email" or field_key.endswith("work_email"):
                injected = input_values.get("work_email")
                if injected not in (None, ""):
                    return injected, None
        return val, None

    if kind == "relation_walk":
        path = str(source.get("path") or "")
        field = str(source.get("field") or "title")
        val = await _walk_relation(target_entry, path, field, depth=1)
        return val, None

    if kind == "computed":
        # Computed fields require a bundle resolver_tool — not executed here.
        return None, f"computed_unresolved:{field_key}"

    return None, f"unsupported_source:{kind}"


async def batch_resolve(
    *,
    workspace_id: str,
    field_keys: List[str],
    context_entry_id: Optional[str] = None,
    context_entry: Optional[Any] = None,
    anchor_entry: Optional[Any] = None,
    anchor_entry_id: Optional[str] = None,
    caller_role: Optional[str] = None,
    input_values: Optional[Dict[str, Any]] = None,
    mode: str = "live",
    token_meta: Optional[Dict[str, Dict[str, Any]]] = None,
    missing_policy: str = "blank",
) -> Dict[str, Any]:
    """Resolve many field keys. Returns values, formatted, warnings."""
    token_meta = token_meta or {}
    entry = context_entry
    if entry is None and mode == "live" and context_entry_id:
        entry = await _load_entry(context_entry_id)
    anchor = anchor_entry
    if anchor is None and anchor_entry_id:
        anchor = await _load_entry(anchor_entry_id)
    if anchor is None:
        anchor = entry

    values: Dict[str, Any] = {}
    formatted: Dict[str, str] = {}
    warnings: List[str] = []

    for key in field_keys:
        meta = token_meta.get(key) or {}
        fmt = meta.get("format")
        fallback = meta.get("fallback")
        policy = meta.get("missing_policy") or missing_policy
        spec = get_field_spec(workspace_id, key) or {}
        data_type = str(spec.get("data_type") or "text")

        if mode == "placeholder":
            sample = spec.get("sample")
            if sample is None:
                sample = PLACEHOLDER_SAMPLES.get(data_type, spec.get("label") or key)
            values[key] = sample
            formatted[key] = format_value(sample, data_type=data_type, fmt=fmt)
            continue

        raw, warn = await resolve_field_value(
            workspace_id=workspace_id,
            field_key=key,
            context_entry=entry,
            caller_role=caller_role,
            input_values=input_values,
            anchor_entry=anchor,
        )
        if warn:
            warnings.append(warn)
        if raw is None or raw == "":
            if policy == "fallback" and fallback is not None:
                raw = fallback
            elif policy == "block":
                warnings.append(f"block:{key}")
            elif policy == "warn":
                warnings.append(f"missing:{key}")
                raw = fallback if fallback is not None else ""
            else:
                raw = ""
        values[key] = raw
        formatted[key] = format_value(raw, data_type=data_type, fmt=fmt)

    return {
        "values": values,
        "formatted": formatted,
        "warnings": warnings,
        "mode": mode,
    }


def collect_field_keys_from_document(
    editor_document: Dict[str, Any],
    token_metadata: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Walk ProseMirror JSON and collect fieldToken keys."""
    keys: List[str] = []
    seen = set()

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") in (
            "fieldToken",
            "signaturePlaceholder",
            "repeatSection",
            "conditionalSection",
        ):
            attrs = node.get("attrs") or {}
            fk = str(attrs.get("fieldKey") or attrs.get("field_key") or "").strip()
            if fk and fk not in seen:
                seen.add(fk)
                keys.append(fk)
        for child in node.get("content") or []:
            walk(child)

    walk(editor_document or {})
    # Also include keys declared only in token_metadata
    for tok in (token_metadata or {}).get("tokens") or []:
        if isinstance(tok, dict):
            fk = str(tok.get("field_key") or tok.get("fieldKey") or "").strip()
            if fk and fk not in seen:
                seen.add(fk)
                keys.append(fk)
    return keys


def token_meta_by_key(
    token_metadata: Optional[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for tok in (token_metadata or {}).get("tokens") or []:
        if not isinstance(tok, dict):
            continue
        fk = str(tok.get("field_key") or tok.get("fieldKey") or "").strip()
        if fk:
            out[fk] = tok
    return out


def token_meta_from_document(
    editor_document: Dict[str, Any],
    token_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Prefer live field-token attrs (format / fallback) over saved metadata."""
    out = token_meta_by_key(token_metadata)

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") == "fieldToken":
            attrs = node.get("attrs") or {}
            fk = str(attrs.get("fieldKey") or attrs.get("field_key") or "").strip()
            if fk:
                prev = dict(out.get(fk) or {})
                if attrs.get("format") is not None:
                    prev["format"] = attrs.get("format")
                if attrs.get("fallback") is not None:
                    prev["fallback"] = attrs.get("fallback")
                policy = attrs.get("missingPolicy") or attrs.get("missing_policy")
                if policy is not None:
                    prev["missing_policy"] = policy
                out[fk] = prev
        for child in node.get("content") or []:
            walk(child)

    walk(editor_document or {})
    return out
