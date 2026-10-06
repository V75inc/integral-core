"""Workspace field registry compiled from bundle ``document_contexts``.

Allowlisted field keys only — no arbitrary path traversal from templates.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.services.documents.field_ref import (
    build_field_ref,
    format_field_ref_display,
    legacy_keys_for_spec,
    local_field_from_context_key,
    normalize_field_ref,
    parse_field_ref,
    resolve_context_track_key,
    slug_ref_part,
)

logger = logging.getLogger(__name__)

# In-memory per-workspace index rebuilt on app install / uninstall / rescan.
# Shape: {workspace_id: {field_key: field_spec, ...}}
_FIELD_INDEX: Dict[str, Dict[str, Dict[str, Any]]] = {}
# Context metadata: {workspace_id: {context_key: context_spec}}
_CONTEXT_INDEX: Dict[str, Dict[str, Dict[str, Any]]] = {}


def clear_workspace_field_index(workspace_id: str) -> None:
    _FIELD_INDEX.pop(workspace_id, None)
    _CONTEXT_INDEX.pop(workspace_id, None)


def get_workspace_field_index(workspace_id: str) -> Dict[str, Dict[str, Any]]:
    return dict(_FIELD_INDEX.get(workspace_id) or {})


def get_workspace_context_index(workspace_id: str) -> Dict[str, Dict[str, Any]]:
    return dict(_CONTEXT_INDEX.get(workspace_id) or {})


def _ingest_document_contexts(
    *,
    workspace_id: str,
    module: str,
    contexts: List[Dict[str, Any]],
) -> None:
    fields_bucket = _FIELD_INDEX.setdefault(workspace_id, {})
    contexts_bucket = _CONTEXT_INDEX.setdefault(workspace_id, {})
    for ctx in contexts or []:
        if not isinstance(ctx, dict):
            continue
        ctx_key = str(ctx.get("key") or "").strip()
        if not ctx_key:
            continue
        track_key = resolve_context_track_key(ctx)
        mod = module or str(ctx.get("module") or "")
        contexts_bucket[ctx_key] = {
            "key": ctx_key,
            "module": mod,
            "track_key": track_key,
            "label": str(ctx.get("label") or ctx_key),
            "context_entry_types": list(ctx.get("context_entry_types") or []),
            "relation_fields": [
                str(item).strip()
                for item in (ctx.get("relation_fields") or [])
                if str(item).strip()
            ],
            "resolver_tool": ctx.get("resolver_tool"),
        }
        for field in ctx.get("fields") or []:
            if not isinstance(field, dict):
                continue
            raw_field_key = str(field.get("key") or "").strip()
            if not raw_field_key:
                continue
            local_field = local_field_from_context_key(ctx_key, raw_field_key)
            field_ref = build_field_ref(
                module=mod,
                track_key=track_key,
                local_field=local_field,
            )
            spec = {
                "key": field_ref,
                "field_ref": field_ref,
                "placeholder": format_field_ref_display(field_ref),
                "label": str(field.get("label") or raw_field_key),
                "description": str(field.get("description") or ""),
                "category": str(field.get("category") or ""),
                "data_type": str(field.get("data_type") or "text"),
                "module": mod,
                "track_key": track_key,
                "local_field_key": local_field,
                "legacy_key": raw_field_key,
                "context_key": ctx_key,
                "source": dict(field.get("source") or {}),
                "requires_role": field.get("requires_role"),
                "active": bool(field.get("active", True)),
                "sample": field.get("sample"),
            }
            _register_field_spec(fields_bucket, spec)


async def _apps_in_workspace(workspace_id: str) -> List[Any]:
    """Load workspace apps — tolerate both flat and legacy ``context.*`` query keys."""
    from app.models.nodes import App

    ws = str(workspace_id or "").strip()
    if not ws:
        return []
    by_id: Dict[str, Any] = {}
    for query in (
        {"workspace_id": ws},
        {"context.workspace_id": ws},
    ):
        rows = await App.find(query) or []
        if not isinstance(rows, list):
            rows = [rows]
        for app in rows:
            aid = str(getattr(app, "id", "") or "")
            if aid and aid not in by_id:
                by_id[aid] = app
    return list(by_id.values())


async def rebuild_workspace_field_index(workspace_id: str) -> Dict[str, int]:
    """Rebuild the field index from all Apps installed in the workspace."""
    from app.services.documents.profile_compat import (
        compile_canonical_manifest,
        manifest_dict_for_app,
    )

    clear_workspace_field_index(workspace_id)
    apps = await _apps_in_workspace(workspace_id)
    modules = 0
    fields = 0
    for app in apps:
        manifest = await manifest_dict_for_app(app)
        if not manifest:
            continue
        try:
            canonical = compile_canonical_manifest(manifest=manifest)
        except Exception:
            logger.exception(
                "rebuild_workspace_field_index: compile failed app=%s", app.id
            )
            continue
        app_node = (canonical.get("app") or {}) if isinstance(canonical, dict) else {}
        contexts = list(app_node.get("document_contexts") or [])
        if not contexts:
            continue
        package = (
            (canonical.get("package") or {}) if isinstance(canonical, dict) else {}
        )
        module = str(package.get("slug") or getattr(app, "slug", "") or "")
        before = len(_FIELD_INDEX.get(workspace_id) or {})
        _ingest_document_contexts(
            workspace_id=workspace_id, module=module, contexts=contexts
        )
        after = len(_FIELD_INDEX.get(workspace_id) or {})
        modules += 1
        fields += max(0, after - before)
    from app.services.documents.system_fields import ingest_system_document_fields

    ingest_system_document_fields(workspace_id)
    return {"modules": modules, "fields": fields}


def list_document_contexts(
    workspace_id: str,
    *,
    module: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return registered document contexts for template create/filter UIs."""
    index = get_workspace_context_index(workspace_id)
    out: List[Dict[str, Any]] = []
    for spec in index.values():
        if module and str(spec.get("module") or "") != module:
            continue
        out.append(dict(spec))
    out.sort(
        key=lambda s: (s.get("module") or "", s.get("label") or s.get("key") or "")
    )
    return out


def _entry_type_fields(et: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Accept both manifest ``fields[]`` and materialized ``form_schema.fields``."""
    if isinstance(et.get("fields"), list):
        return [f for f in et["fields"] if isinstance(f, dict)]
    schema = et.get("form_schema") if isinstance(et.get("form_schema"), dict) else {}
    raw = schema.get("fields") if isinstance(schema, dict) else []
    if not isinstance(raw, list):
        return []
    return [f for f in raw if isinstance(f, dict)]


def _entry_type_base_fields(et: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(et.get("base_fields"), dict):
        return et["base_fields"]
    schema = et.get("form_schema") if isinstance(et.get("form_schema"), dict) else {}
    raw = schema.get("base_fields") if isinstance(schema, dict) else {}
    return raw if isinstance(raw, dict) else {}


def _register_field_spec(
    bucket: Dict[str, Dict[str, Any]], spec: Dict[str, Any]
) -> None:
    key = str(spec.get("key") or "").strip()
    if not key:
        return
    bucket[key] = dict(spec)
    for legacy in legacy_keys_for_spec(spec):
        if legacy and legacy != key and legacy not in bucket:
            bucket[legacy] = dict(spec)


def fields_from_entry_types(
    entry_types: List[Dict[str, Any]],
    *,
    module: str = "",
    track_id: str = "",
    track_key: str = "",
) -> List[Dict[str, Any]]:
    """Build document field specs from a track's entry-type form schemas."""
    title_label = "Title"
    body_label = "Body"
    for et in entry_types or []:
        if not isinstance(et, dict):
            continue
        base = _entry_type_base_fields(et)
        title_spec = base.get("title") if isinstance(base.get("title"), dict) else {}
        body_spec = base.get("body") if isinstance(base.get("body"), dict) else {}
        if title_spec.get("label"):
            title_label = str(title_spec["label"])
        if body_spec.get("label"):
            body_label = str(body_spec["label"])
        break
    tk = slug_ref_part(track_key or track_id or "track")
    mod = module or "app"

    def _track_spec(local_field: str, **extra: Any) -> Dict[str, Any]:
        field_ref = build_field_ref(module=mod, track_key=tk, local_field=local_field)
        return {
            "key": field_ref,
            "field_ref": field_ref,
            "placeholder": format_field_ref_display(field_ref),
            "label": extra.get("label") or local_field,
            "description": extra.get("description") or "",
            "category": extra.get("category") or "Entry",
            "data_type": extra.get("data_type") or "text",
            "module": mod,
            "track_key": tk,
            "track_id": track_id,
            "local_field_key": local_field,
            "legacy_key": local_field,
            "context_key": track_id or tk,
            "source": extra.get("source") or {},
            "active": True,
            "sample": extra.get("sample") or "",
        }

    out: List[Dict[str, Any]] = [
        _track_spec(
            "title",
            label=title_label,
            description="Entry title",
            data_type="text",
            source={"kind": "entry_field", "path": "title"},
            sample="Sample title",
        ),
        _track_spec(
            "body",
            label=body_label,
            description="Entry body",
            data_type="markdown",
            source={"kind": "entry_field", "path": "body"},
            sample="Sample body",
        ),
    ]
    seen = {"title", "body"}
    for et in entry_types or []:
        if not isinstance(et, dict):
            continue
        et_name = str(et.get("name") or et.get("key") or "Entry").strip() or "Entry"
        raw_fields = _entry_type_fields(et)
        for field in raw_fields:
            if not isinstance(field, dict):
                continue
            fkey = str(field.get("key") or "").strip()
            if not fkey or fkey in seen:
                continue
            seen.add(fkey)
            ftype = str(field.get("type") or "text")
            if ftype == "relation":
                source = {
                    "kind": "relation_walk",
                    "path": f"custom_fields.{fkey}",
                    "field": "title",
                }
            else:
                source = {"kind": "entry_field", "path": f"custom_fields.{fkey}"}
            out.append(
                _track_spec(
                    fkey,
                    label=str(field.get("name") or field.get("label") or fkey),
                    description=str(field.get("help") or ""),
                    category=et_name,
                    data_type=ftype,
                    source=source,
                    sample=field.get("default") or field.get("placeholder") or "",
                )
            )
    return out


def ingest_field_specs(workspace_id: str, specs: List[Dict[str, Any]]) -> None:
    bucket = _FIELD_INDEX.setdefault(workspace_id, {})
    for spec in specs:
        _register_field_spec(bucket, spec)


async def list_fields_for_track(
    workspace_id: str,
    track_id: str,
    *,
    module: str = "",
    q: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fields come from the track's compiled content-profile schema.

    Materialized ``EntryType`` nodes are only a fallback. Anchored / template
    tracks often have no per-track EntryType rows — their ``fields[]`` live on
    the attached (or app) content profile, same source the track UI uses.
    """
    from app.models.nodes import Track
    from app.services.documents.profile_compat import resolve_track_runtime_profile
    from app.services.entry_type_resolver import entry_types_for_track

    entry_types: List[Dict[str, Any]] = []
    track = await Track.get(track_id)
    if track:
        try:
            _cp, tier, _key = await resolve_track_runtime_profile(track)
        except Exception:
            logger.exception(
                "list_fields_for_track: profile compile failed track=%s", track_id
            )
            tier = {}
        for spec in (tier or {}).get("entry_types") or []:
            if isinstance(spec, dict):
                entry_types.append(spec)
    if not entry_types:
        for et in await entry_types_for_track(track_id):
            schema = getattr(et, "form_schema", None) or {}
            entry_types.append(
                {
                    "name": getattr(et, "name", "") or "",
                    "form_schema": schema if isinstance(schema, dict) else {},
                }
            )
    track_key = ""
    if track:
        track_key = str(getattr(track, "template_id", "") or "").strip()
        if not track_key:
            track_key = slug_ref_part(getattr(track, "title", "") or "track")
        resolved_module = await _module_for_track(track)
        if resolved_module:
            module = resolved_module
        elif not module:
            module = "app"
    specs = fields_from_entry_types(
        entry_types,
        module=module,
        track_id=track_id,
        track_key=track_key,
    )
    ingest_field_specs(workspace_id, specs)
    needle = (q or "").strip().lower()
    if not needle:
        return specs
    return [
        s
        for s in specs
        if needle
        in " ".join(
            [
                str(s.get("key") or ""),
                str(s.get("label") or ""),
                str(s.get("category") or ""),
                str(s.get("description") or ""),
            ]
        ).lower()
    ]


def list_document_fields(
    workspace_id: str,
    *,
    module: Optional[str] = None,
    context: Optional[str] = None,
    q: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Search/filter registered fields for the editor field browser."""
    from app.services.documents.system_fields import ingest_system_document_fields

    ingest_system_document_fields(workspace_id)
    index = get_workspace_field_index(workspace_id)
    needle = (q or "").strip().lower()
    out: List[Dict[str, Any]] = []
    seen_refs: set[str] = set()
    for spec in index.values():
        ref = str(spec.get("field_ref") or spec.get("key") or "").strip()
        if ref and ref in seen_refs:
            continue
        if ref:
            seen_refs.add(ref)
        if not spec.get("active", True):
            continue
        spec_module = str(spec.get("module") or "")
        if module and spec_module not in (module, "system"):
            continue
        if context and str(spec.get("context_key") or "") != context:
            continue
        if needle:
            hay = " ".join(
                [
                    str(spec.get("key") or ""),
                    str(spec.get("label") or ""),
                    str(spec.get("category") or ""),
                    str(spec.get("description") or ""),
                ]
            ).lower()
            if needle not in hay:
                continue
        out.append(dict(spec))
    out.sort(
        key=lambda s: (
            0 if str(s.get("module") or "") == "system" else 1,
            s.get("module") or "",
            s.get("category") or "",
            s.get("label") or "",
        )
    )
    return out


async def _module_for_track(track: Any) -> str:
    from app.models.edges import CONTAINS
    from app.models.nodes import App

    # Persisted entity is ``WorkspaceApp`` — same as tracks_public_share.
    apps = await track.nodes(edge=[CONTAINS], direction="in", node=["WorkspaceApp"])
    if not apps:
        return ""
    app = apps[0] if isinstance(apps, list) else apps
    if not isinstance(app, App):
        return ""
    return str(
        getattr(app, "source_profile_slug", None) or getattr(app, "slug", None) or ""
    ).strip()


def get_field_spec(workspace_id: str, field_key: str) -> Optional[Dict[str, Any]]:
    from app.services.documents.system_fields import (
        get_system_field_spec,
        ingest_system_document_fields,
    )

    key = normalize_field_ref(field_key)
    system_spec = get_system_field_spec(key)
    if system_spec:
        ingest_system_document_fields(workspace_id)
        return system_spec

    index = get_workspace_field_index(workspace_id)
    spec = index.get(key)
    if spec:
        return spec
    parsed = parse_field_ref(key)
    if parsed:
        needle = build_field_ref(
            module=parsed["module"],
            track_key=parsed["track_key"],
            local_field=parsed["local_field"],
        )
        spec = index.get(needle)
        if spec:
            return spec
    for candidate in index.values():
        if key in legacy_keys_for_spec(candidate):
            return candidate
        if key == str(candidate.get("key") or ""):
            return candidate
    return None
