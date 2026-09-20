"""Read-only scaffold checks shared by commit and continuation hints.

No domain identities live here. A valid build has shaped, visible tracks and
backward-only references; execution still checks policy for every operation.
"""

from typing import Any, Dict, List


def _view_binding_error(payload: Dict[str, Any]) -> str | None:
    """Return why a scaffold view would render as a generic surface.

    A saved view is not evidence of a usable app merely because its node
    exists.  The built-in widgets fall back to generic platform columns and
    lanes when their schema binding is omitted, which is useful for manual UI
    creation but is a failed result for an agent-authored operational app.
    """
    view_type = str(payload.get("view_type") or "feed")
    config = payload.get("config") or {}
    if not isinstance(config, dict):
        return "config must be an object"
    if view_type == "table":
        columns = config.get("columns")
        if not isinstance(columns, list) or not any(
            isinstance(column, dict)
            and str(column.get("field") or "").startswith("custom_fields.")
            for column in columns
        ):
            return "table views need config.columns using custom_fields.<field_key>"
    if view_type == "kanban":
        group_by = str(config.get("group_by") or "")
        if not group_by.startswith("custom_fields."):
            return "kanban views need group_by: custom_fields.<select_field>"
        if not config.get("kanban_columns"):
            return "kanban views need config.kanban_columns"
    if view_type == "calendar":
        mapping = config.get("calendar_mapping") or {}
        if not isinstance(mapping, dict) or not mapping.get("dateField"):
            return "calendar views need calendar_mapping.dateField"
    return None


def _field_specs(entry_types: Any) -> List[Dict[str, Any]]:
    """Return declared field specs from inline track entry types in order."""
    out: List[Dict[str, Any]] = []
    if not isinstance(entry_types, list):
        return out
    for entry_type in entry_types:
        if not isinstance(entry_type, dict):
            continue
        fields = entry_type.get("fields")
        if not isinstance(fields, list):
            continue
        for field in fields:
            if isinstance(field, dict) and str(field.get("key") or "").strip():
                out.append(field)
    return out


def _field_label(field: Dict[str, Any]) -> str:
    return str(field.get("name") or field.get("key") or "Field").strip()


def _write_normalized_config(op: Dict[str, Any], config: Dict[str, Any]) -> None:
    """Keep the executable payload and approval diff describing the same view."""
    payload = op.get("payload")
    if isinstance(payload, dict):
        payload["config"] = config
    diff_machine = op.get("diff_machine")
    if isinstance(diff_machine, dict):
        diff_machine["config"] = config


def materialize_scaffold_view_bindings(ops: List[Dict[str, Any]]) -> int:
    """Fill missing schema bindings for views in a greenfield batch.

    A scaffold creates tracks and views in the same uncommitted operation list,
    so the view stager cannot read a persisted Content Profile.  The declared
    inline entry-type fields are nevertheless authoritative.  This compiler
    pass uses them to make an incomplete *existing* view useful; it never
    invents a view, overwrites a valid schema-bound config, or guesses a field
    when the track did not declare one.

    Returns the number of view payloads repaired.  It is deliberately pure
    aside from mutating the batch operations supplied by the caller.
    """
    from app.agentive.staging_executors import _capture_batch_refs, _resolve_batch_refs

    refs: Dict[str, str] = {}
    track_fields: Dict[str, List[Dict[str, Any]]] = {}
    repaired = 0
    for index, op in enumerate(ops):
        kind = op.get("kind")
        payload = _resolve_batch_refs(op.get("payload") or {}, refs)
        if kind in {"create_track", "create_app_track"}:
            identity = f"step:{index}"
            track_fields[identity] = _field_specs(payload.get("entry_types"))
            _capture_batch_refs(
                refs,
                index,
                {
                    "track": {
                        "id": identity,
                        "title": payload.get("title") or payload.get("name"),
                    }
                },
            )
            continue
        if kind != "save_view":
            continue
        fields = track_fields.get(str(payload.get("track_id") or ""), [])
        if not fields or _view_binding_error(payload) is None:
            continue
        view_type = str(payload.get("view_type") or "feed")
        config = dict(payload.get("config") or {})
        if view_type == "table":
            field = fields[0]
            config["columns"] = [
                {"field": "title", "label": "Name"},
                {
                    "field": f"custom_fields.{field['key']}",
                    "label": _field_label(field),
                },
            ]
        elif view_type == "kanban":
            field = next(
                (item for item in fields if item.get("type") == "select"), None
            )
            if field is None:
                continue
            config["group_by"] = f"custom_fields.{field['key']}"
            values = field.get("enum") or field.get("options") or []
            if isinstance(values, list) and values:
                config["kanban_columns"] = [
                    {"key": str(value), "label": str(value).replace("_", " ").title()}
                    for value in values
                ]
            else:
                config["kanban_columns"] = [
                    {"key": "unassigned", "label": "Unassigned"}
                ]
        elif view_type == "calendar":
            field = next((item for item in fields if item.get("type") == "date"), None)
            if field is None:
                continue
            config["calendar_mapping"] = {"dateField": f"custom_fields.{field['key']}"}
        else:
            continue
        _write_normalized_config(op, config)
        repaired += 1
    return repaired


def scaffold_missing(
    ops: List[Dict[str, Any]], *, allow_empty: bool = False
) -> List[str]:
    """Return actionable omissions per track, not misleading global counts."""
    from app.agentive.staging_executors import _capture_batch_refs, _resolve_batch_refs

    if not any(op.get("kind") == "create_app" for op in ops):
        return []
    refs: Dict[str, str] = {}
    tracks: Dict[str, Dict[str, Any]] = {}
    apps: Dict[str, str] = {}
    for idx, op in enumerate(ops):
        kind = op.get("kind")
        payload = _resolve_batch_refs(op.get("payload") or {}, refs)
        identity = f"step:{idx}"
        name = payload.get("title") or payload.get("name") or identity
        if kind == "create_app":
            apps[identity] = name
            _capture_batch_refs(refs, idx, {"app": {"id": identity, "name": name}})
        elif kind in {"create_track", "create_app_track"}:
            tracks[identity] = {
                "name": name,
                "app_id": payload.get("app_id"),
                "shaped": bool(payload.get("entry_types")),
                "view": False,
                "view_error": None,
                "seed": False,
            }
            _capture_batch_refs(refs, idx, {"track": {"id": identity, "title": name}})
        elif payload.get("track_id") in tracks:
            target = tracks[payload["track_id"]]
            if kind == "apply_profile_to_track":
                target["shaped"] = True
            elif kind == "save_view":
                binding_error = _view_binding_error(payload)
                if binding_error is None:
                    target["view"] = True
                else:
                    target["view_error"] = binding_error
            elif kind == "create_entry":
                target["seed"] = True
    missing = []
    for app_id, name in apps.items():
        if not any(t["app_id"] == app_id for t in tracks.values()):
            missing.append(f"integral_create_app_track for app {name!r}")
    for track in tracks.values():
        name = track["name"]
        if not track["app_id"]:
            missing.append(f"Attach track {name!r} to its app with app_id")
        if not track["shaped"]:
            missing.append(
                f"integral_apply_profile_to_track for {name!r} (or inline entry_types)"
            )
        if not track["view"]:
            detail = track.get("view_error")
            if detail:
                missing.append(f"Configure a schema-bound view for {name!r}: {detail}")
            else:
                missing.append(f"integral_save_view for {name!r}")
        if not allow_empty and not track["seed"]:
            missing.append(
                f"integral_create_entry demo for {name!r}; allow_empty only if requested"
            )
    return missing


def validate_batch_references(ops: List[Dict[str, Any]]) -> None:
    """Reject dangling/forward or ambiguous named references before any writes."""
    from app.agentive.staging import StagingError
    from app.agentive.staging_executors import (
        _BATCH_REF_RE,
        _capture_batch_refs,
        _resolve_batch_refs,
    )

    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for child in value.values():
                yield from strings(child)
        elif isinstance(value, list):
            for child in value:
                yield from strings(child)

    creators = {
        "create_app": "app",
        "create_track": "track",
        "create_app_track": "track",
        "create_entry": "entry",
        "create_tag": "tag",
        "save_view": "view",
        "create_comment": "comment",
    }
    refs: Dict[str, str] = {}
    names = set()
    for idx, op in enumerate(ops):
        payload = op.get("payload") or {}
        resolved = _resolve_batch_refs(payload, refs)
        unresolved = sorted(
            {m.group(0) for s in strings(resolved) for m in _BATCH_REF_RE.finditer(s)}
        )
        if unresolved:
            raise StagingError(
                "invalid_batch_reference",
                f"Step {idx + 1} has unresolved references: {', '.join(unresolved)}. "
                "Create the target earlier in this batch and use its exact name. "
                "Cancel and rebuild the unapproved batch to correct existing operations.",
            )
        entity = creators.get(op.get("kind"))
        if entity:
            name = payload.get("title") or payload.get("name")
            if (
                name
                and (entity, name) in names
                and any(
                    f"{entity}.id:{name}" in s or f"{entity}_id:{name}" in s
                    for later in ops
                    for s in strings(later.get("payload") or {})
                )
            ):
                raise StagingError(
                    "invalid_batch_reference",
                    f"Duplicate {entity} name {name!r} makes named references ambiguous.",
                )
            names.add((entity, name))
            _capture_batch_refs(
                refs, idx, {entity: {"id": f"step:{idx}", "name": name}}
            )
