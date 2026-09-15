"""Pure manifest patch DSL for the agent-authorable substrate (Pillar 3).

Agents emit a list of declarative ``operations`` against a candidate
manifest; this module applies them to produce a new manifest. The result
is then run through ``compile_canonical_manifest`` for full validation.

Operations supported (v1):

  - add_entry_type(spec)
  - modify_entry_type(key, patch)
  - remove_entry_type(key)
  - add_field(entry_type, spec)
  - remove_field(entry_type, field_key)
  - modify_field(entry_type, field_key, patch)
  - add_view(spec)
  - remove_view(key)
  - modify_view(key, patch)
  - add_tag(group_key, spec)
  - remove_tag(group_key, key)
  - add_relation(spec)
  - register_composite_field_type(spec)
  - register_composite_view_type(spec)

Track-scope manifests carry their EntryTypes / Views / Taxonomy at
``manifest.track``. App-scope manifests carry per-track tiers under
``manifest.app_node.tracks[i]``; an op targeting an entry type in an
app-scope manifest accepts an additional ``track`` arg to disambiguate.

The interpreter is pure and side-effect free — it does NOT touch the
database. Persistence happens via ``apply_to_draft`` which writes the
returned manifest onto the draft CP.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from app.exceptions import BadRequestError

# ---------------------------------------------------------------------------
# Tier resolution helpers (track scope vs app_node.tracks[])
# ---------------------------------------------------------------------------


def _scope_of(manifest: Dict[str, Any]) -> str:
    return str((manifest or {}).get("scope") or "track")


def _resolve_track_tier(
    manifest: Dict[str, Any], track_key: Optional[str]
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Return ``(tier_dict, container_list)`` for the targeted tier.

    ``container_list`` is the ``tracks[]`` list when scope is app (so
    the caller can mutate that list in place); empty for track scope.
    """
    scope = _scope_of(manifest)
    if scope == "track":
        manifest.setdefault("track", {})
        return manifest["track"], []
    app_node = manifest.setdefault("app", {})
    tracks = app_node.setdefault("tracks", [])
    if not tracks:
        raise BadRequestError(
            message="Cannot apply patch to app-scope manifest with no tracks"
        )
    if track_key:
        for t in tracks:
            if str(t.get("key") or "") == track_key:
                return t, tracks
        raise BadRequestError(
            message=f"track '{track_key}' not found in app_node.tracks"
        )
    if len(tracks) == 1:
        return tracks[0], tracks
    raise BadRequestError(
        message=(
            "App-scope manifest has multiple tracks — patch op requires "
            "a 'track' argument disambiguating which one to target"
        )
    )


def _ensure_taxonomy_group(tier: Dict[str, Any], group_key: str) -> Dict[str, Any]:
    taxonomy = tier.setdefault("taxonomy", {})
    groups = taxonomy.setdefault("tag_groups", [])
    for g in groups:
        if str(g.get("key") or "") == group_key:
            return g
    new_group = {"key": group_key, "name": group_key, "tags": []}
    groups.append(new_group)
    return new_group


# ---------------------------------------------------------------------------
# Op handlers
# ---------------------------------------------------------------------------


def _op_add_entry_type(manifest: Dict[str, Any], op: Dict[str, Any]) -> None:
    spec = op.get("spec") or {}
    if not isinstance(spec, dict) or not (spec.get("name") or spec.get("key")):
        raise BadRequestError(message="add_entry_type requires spec with name/key")
    tier, _ = _resolve_track_tier(manifest, op.get("track"))
    ets = tier.setdefault("entry_types", [])
    key = str(spec.get("key") or spec.get("name") or "").lower().replace(" ", "_")
    for et in ets:
        if str(et.get("key") or "") == key:
            raise BadRequestError(message=f"entry type with key '{key}' already exists")
    ets.append(dict(spec))


def _op_modify_entry_type(manifest: Dict[str, Any], op: Dict[str, Any]) -> None:
    key = str(op.get("key") or "").strip()
    patch = op.get("patch") or {}
    if not key or not isinstance(patch, dict):
        raise BadRequestError(message="modify_entry_type requires key + patch")
    tier, _ = _resolve_track_tier(manifest, op.get("track"))
    for et in tier.get("entry_types") or []:
        if str(et.get("key") or "") == key:
            et.update(patch)
            return
    raise BadRequestError(message=f"entry type '{key}' not found")


def _op_remove_entry_type(manifest: Dict[str, Any], op: Dict[str, Any]) -> None:
    key = str(op.get("key") or "").strip()
    if not key:
        raise BadRequestError(message="remove_entry_type requires key")
    tier, _ = _resolve_track_tier(manifest, op.get("track"))
    tier["entry_types"] = [
        et for et in (tier.get("entry_types") or []) if str(et.get("key") or "") != key
    ]


def _op_add_field(manifest: Dict[str, Any], op: Dict[str, Any]) -> None:
    et_key = str(op.get("entry_type") or "").strip()
    spec = op.get("spec") or {}
    if not et_key or not isinstance(spec, dict) or not spec.get("key"):
        raise BadRequestError(message="add_field requires entry_type + spec.key")
    tier, _ = _resolve_track_tier(manifest, op.get("track"))
    for et in tier.get("entry_types") or []:
        if str(et.get("key") or "") == et_key:
            fields = et.setdefault("fields", [])
            for f in fields:
                if str(f.get("key") or "") == spec.get("key"):
                    raise BadRequestError(
                        message=(
                            f"field '{spec.get('key')}' already exists on "
                            f"entry type '{et_key}'"
                        )
                    )
            fields.append(dict(spec))
            return
    raise BadRequestError(message=f"entry type '{et_key}' not found")


def _op_remove_field(manifest: Dict[str, Any], op: Dict[str, Any]) -> None:
    et_key = str(op.get("entry_type") or "").strip()
    field_key = str(op.get("field_key") or "").strip()
    if not (et_key and field_key):
        raise BadRequestError(message="remove_field requires entry_type + field_key")
    tier, _ = _resolve_track_tier(manifest, op.get("track"))
    for et in tier.get("entry_types") or []:
        if str(et.get("key") or "") == et_key:
            et["fields"] = [
                f
                for f in (et.get("fields") or [])
                if str(f.get("key") or "") != field_key
            ]
            return
    raise BadRequestError(message=f"entry type '{et_key}' not found")


def _op_modify_field(manifest: Dict[str, Any], op: Dict[str, Any]) -> None:
    et_key = str(op.get("entry_type") or "").strip()
    field_key = str(op.get("field_key") or "").strip()
    patch = op.get("patch") or {}
    if not (et_key and field_key) or not isinstance(patch, dict):
        raise BadRequestError(
            message="modify_field requires entry_type + field_key + patch"
        )
    tier, _ = _resolve_track_tier(manifest, op.get("track"))
    for et in tier.get("entry_types") or []:
        if str(et.get("key") or "") == et_key:
            for f in et.get("fields") or []:
                if str(f.get("key") or "") == field_key:
                    f.update(patch)
                    return
    raise BadRequestError(
        message=f"field '{field_key}' on entry type '{et_key}' not found"
    )


def _op_add_view(manifest: Dict[str, Any], op: Dict[str, Any]) -> None:
    spec = op.get("spec") or {}
    if not isinstance(spec, dict) or not (spec.get("name") or spec.get("key")):
        raise BadRequestError(message="add_view requires spec with name/key")
    tier, _ = _resolve_track_tier(manifest, op.get("track"))
    views = tier.setdefault("views", [])
    key = str(spec.get("key") or spec.get("name") or "").lower().replace(" ", "_")
    for v in views:
        if str(v.get("key") or "") == key:
            raise BadRequestError(message=f"view with key '{key}' already exists")
    views.append(dict(spec))


def _op_remove_view(manifest: Dict[str, Any], op: Dict[str, Any]) -> None:
    key = str(op.get("key") or "").strip()
    if not key:
        raise BadRequestError(message="remove_view requires key")
    tier, _ = _resolve_track_tier(manifest, op.get("track"))
    tier["views"] = [
        v for v in (tier.get("views") or []) if str(v.get("key") or "") != key
    ]


def _op_modify_view(manifest: Dict[str, Any], op: Dict[str, Any]) -> None:
    key = str(op.get("key") or "").strip()
    patch = op.get("patch") or {}
    if not key or not isinstance(patch, dict):
        raise BadRequestError(message="modify_view requires key + patch")
    tier, _ = _resolve_track_tier(manifest, op.get("track"))
    for v in tier.get("views") or []:
        if str(v.get("key") or "") == key:
            if "visible_fields" in patch:
                vf = patch["visible_fields"]
                if not isinstance(vf, list) or not all(isinstance(x, str) for x in vf):
                    raise BadRequestError(
                        message="modify_view patch.visible_fields must be a list of strings"
                    )
            if "default_sort" in patch:
                ds = patch["default_sort"]
                if not isinstance(ds, dict) or "field" not in ds:
                    raise BadRequestError(
                        message="modify_view patch.default_sort must be a dict with a 'field' key"
                    )
            config = v.setdefault("config", {})
            config.update(patch)
            return
    raise BadRequestError(message=f"view '{key}' not found")


def _op_add_tag(manifest: Dict[str, Any], op: Dict[str, Any]) -> None:
    group_key = str(op.get("group_key") or "default").strip()
    spec = op.get("spec") or {}
    if not isinstance(spec, dict) or not (spec.get("name") or spec.get("key")):
        raise BadRequestError(message="add_tag requires spec with name/key")
    tier, _ = _resolve_track_tier(manifest, op.get("track"))
    group = _ensure_taxonomy_group(tier, group_key)
    tags = group.setdefault("tags", [])
    new_key = str(spec.get("key") or spec.get("name") or "").lower().replace(" ", "_")
    for t in tags:
        if str(t.get("key") or "") == new_key:
            raise BadRequestError(
                message=f"tag '{new_key}' already exists in group '{group_key}'"
            )
    tags.append(dict(spec))


def _op_remove_tag(manifest: Dict[str, Any], op: Dict[str, Any]) -> None:
    group_key = str(op.get("group_key") or "default").strip()
    key = str(op.get("key") or "").strip()
    if not key:
        raise BadRequestError(message="remove_tag requires key")
    tier, _ = _resolve_track_tier(manifest, op.get("track"))
    for g in (tier.get("taxonomy") or {}).get("tag_groups") or []:
        if str(g.get("key") or "") == group_key:
            g["tags"] = [
                t for t in (g.get("tags") or []) if str(t.get("key") or "") != key
            ]
            return


def _op_add_relation(manifest: Dict[str, Any], op: Dict[str, Any]) -> None:
    spec = op.get("spec") or {}
    if not isinstance(spec, dict) or not (
        spec.get("source_track_type") and spec.get("target_track_type")
    ):
        raise BadRequestError(
            message=(
                "add_relation requires spec with source_track_type + target_track_type"
            )
        )
    if _scope_of(manifest) != "app":
        raise BadRequestError(
            message="add_relation only supported in app-scope manifests"
        )
    app_node = manifest.setdefault("app", {})
    relations = app_node.setdefault("relations", [])
    relations.append(dict(spec))


def _op_register_composite_field_type(
    manifest: Dict[str, Any], op: Dict[str, Any]
) -> None:
    spec = op.get("spec") or {}
    if not isinstance(spec, dict) or not (spec.get("key") and spec.get("base")):
        raise BadRequestError(
            message="register_composite_field_type requires spec.key + spec.base"
        )
    field_types = manifest.setdefault("field_types", [])
    for ft in field_types:
        if str(ft.get("key") or "") == spec.get("key"):
            raise BadRequestError(
                message=f"composite field type '{spec.get('key')}' already declared"
            )
    field_types.append(dict(spec))


def _op_register_composite_view_type(
    manifest: Dict[str, Any], op: Dict[str, Any]
) -> None:
    spec = op.get("spec") or {}
    if not isinstance(spec, dict) or not (spec.get("key") and spec.get("base")):
        raise BadRequestError(
            message="register_composite_view_type requires spec.key + spec.base"
        )
    view_types = manifest.setdefault("view_types", [])
    for vt in view_types:
        if str(vt.get("key") or "") == spec.get("key"):
            raise BadRequestError(
                message=f"composite view type '{spec.get('key')}' already declared"
            )
    view_types.append(dict(spec))


_OP_HANDLERS: Dict[str, Any] = {
    "add_entry_type": _op_add_entry_type,
    "modify_entry_type": _op_modify_entry_type,
    "remove_entry_type": _op_remove_entry_type,
    "add_field": _op_add_field,
    "remove_field": _op_remove_field,
    "modify_field": _op_modify_field,
    "add_view": _op_add_view,
    "remove_view": _op_remove_view,
    "modify_view": _op_modify_view,
    "add_tag": _op_add_tag,
    "remove_tag": _op_remove_tag,
    "add_relation": _op_add_relation,
    "register_composite_field_type": _op_register_composite_field_type,
    "register_composite_view_type": _op_register_composite_view_type,
}


def supported_ops() -> List[str]:
    """Return every op kind the interpreter knows how to apply."""
    return sorted(_OP_HANDLERS.keys())


def apply_operations(
    manifest: Dict[str, Any],
    operations: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Apply ``operations`` to a deep copy of ``manifest`` and return the result.

    Raises ``BadRequestError`` on malformed ops or missing targets. Caller
    is expected to feed the returned manifest through
    ``compile_canonical_manifest`` for end-to-end validation before
    persisting.
    """
    if not isinstance(manifest, dict):
        raise BadRequestError(message="manifest must be a dict")
    if not isinstance(operations, list):
        raise BadRequestError(message="operations must be a list")
    out = copy.deepcopy(manifest)
    for idx, raw in enumerate(operations):
        if not isinstance(raw, dict):
            raise BadRequestError(message=f"operations[{idx}] must be an object")
        kind = str(raw.get("op") or "").strip()
        handler = _OP_HANDLERS.get(kind)
        if handler is None:
            raise BadRequestError(
                message=(
                    f"operations[{idx}] unknown op '{kind}'. Supported: "
                    f"{', '.join(supported_ops())}"
                )
            )
        handler(out, raw)
    return out
