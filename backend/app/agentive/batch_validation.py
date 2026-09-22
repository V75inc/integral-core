"""Read-only scaffold checks shared by commit and continuation hints.

No domain identities live here. A valid build has shaped, visible tracks and
backward-only references; execution still checks policy for every operation.
"""

from datetime import date, datetime, timezone
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
        columns = config.get("kanban_columns")
        if not isinstance(columns, list) or not columns:
            return "kanban views need config.kanban_columns"
        if not all(
            isinstance(column, dict) and str(column.get("key") or "").strip()
            for column in columns
        ):
            return "kanban config.kanban_columns entries must be objects with keys"
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


def _example_field_values(fields: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return safe, visible starter values for writable scalar fields.

    A generated record is part of the scaffold's proof, not merely a count
    placeholder.  Populate values that make its declared table columns and
    date-bound calendar useful immediately.  Relations and attachment fields
    require real target records or uploads, while computed fields are read
    only, so this compiler deliberately leaves those to an authored workflow.
    """
    today = date.today().isoformat()
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    values: Dict[str, Any] = {}
    for field in fields:
        key = str(field.get("key") or "").strip()
        field_type = str(field.get("type") or "text")
        if not key or field_type in {"relation", "file", "files", "computed"}:
            continue
        if field_type in {"text", "markdown"}:
            values[key] = f"Example {_field_label(field)}"
        elif field_type == "number":
            values[key] = 1
        elif field_type == "boolean":
            values[key] = True
        elif field_type == "date":
            values[key] = today
        elif field_type == "datetime":
            values[key] = now
        elif field_type == "json":
            values[key] = {"example": True}
        elif field_type in {"select", "multi_select"}:
            options = field.get("enum") or field.get("options") or []
            if isinstance(options, list) and options:
                values[key] = (
                    [options[0]] if field_type == "multi_select" else options[0]
                )
    return values


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
    so the view stager cannot read a persisted Operational Model.  The declared
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
            config["columns"] = [
                {"field": "title", "label": "Name"},
                *[
                    {
                        "field": f"custom_fields.{field['key']}",
                        "label": _field_label(field),
                    }
                    for field in fields
                ],
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


def materialize_scaffold_defaults(ops: List[Dict[str, Any]]) -> int:
    """Append the minimum useful surface omitted from an app scaffold.

    A greenfield build has already declared its track schema before it reaches
    this compiler.  At that point an operational baseline is deterministic:
    every track needs a schema-bound table, date-bearing tracks need a calendar,
    and the new app needs one example record.  Completing that baseline here
    makes an interrupted tool sequence recoverable without fabricating a
    second, model-authored design.  This function never changes an existing
    view or record; it only adds omissions for tracks created in this batch.
    """
    from app.agentive.staging_executors import _capture_batch_refs, _resolve_batch_refs

    refs: Dict[str, str] = {}
    tracks: Dict[str, Dict[str, Any]] = {}
    additions: List[Dict[str, Any]] = []
    for index, op in enumerate(ops):
        kind = op.get("kind")
        payload = _resolve_batch_refs(op.get("payload") or {}, refs)
        if kind in {"create_track", "create_app_track"}:
            identity = f"step:{index}"
            title = str(payload.get("title") or payload.get("name") or "Track")
            entry_types = payload.get("entry_types")
            first_entry_type = (
                entry_types[0]
                if isinstance(entry_types, list)
                and entry_types
                and isinstance(entry_types[0], dict)
                else {}
            )
            tracks[identity] = {
                "title": title,
                "fields": _field_specs(payload.get("entry_types")),
                "entry_type": str(
                    first_entry_type.get("name") or first_entry_type.get("key") or ""
                ),
                "views": set(),
                "has_seed": False,
                "seed_ops": [],
            }
            _capture_batch_refs(
                refs,
                index,
                {"track": {"id": identity, "title": title}},
            )
            continue
        track = tracks.get(str(payload.get("track_id") or ""))
        if track is None:
            continue
        if kind == "save_view":
            track["views"].add(str(payload.get("view_type") or "feed"))
        elif kind == "create_entry":
            track["has_seed"] = True
            track["seed_ops"].append(op)

    for track in tracks.values():
        # A track without inline fields remains a model repair task: there is
        # no honest schema from which to derive an operational view.
        fields = track["fields"]
        if not fields:
            continue
        title = track["title"]
        track_ref = f"{{{{track.id:{title}}}}}"
        example_fields = _example_field_values(fields)
        example_entry_type = track.get("entry_type")
        # A model-provided demo often has only a title.  It satisfies the
        # record-count check but proves nothing in a table or calendar, so
        # enrich that otherwise blank seed with the same safe scalar values as
        # an omitted seed.  Explicit authored fields always win.
        for seed_op in track["seed_ops"]:
            seed_payload = seed_op.get("payload")
            if not isinstance(seed_payload, dict) or seed_payload.get("fields"):
                continue
            if example_entry_type and not seed_payload.get("entry_type"):
                seed_payload["entry_type"] = example_entry_type
            if example_fields:
                seed_payload["fields"] = example_fields
            seed_diff = seed_op.get("diff_machine")
            if isinstance(seed_diff, dict):
                if example_entry_type and not seed_diff.get("entry_type"):
                    seed_diff["entry_type"] = example_entry_type
                if example_fields and not seed_diff.get("fields"):
                    seed_diff["fields"] = example_fields
        if "table" not in track["views"]:
            additions.append(
                {
                    "kind": "save_view",
                    "summary": f"Add All {title} table",
                    "diff_human": "Generated schema-bound table for the scaffold.",
                    "diff_machine": {
                        "op": "save_view",
                        "track_id": track_ref,
                        "name": f"All {title}",
                        "view_type": "table",
                        "config": {"columns": [{"field": "title", "label": "Name"}]},
                    },
                    "payload": {
                        "track_id": track_ref,
                        "name": f"All {title}",
                        "view_type": "table",
                        "config": {"columns": [{"field": "title", "label": "Name"}]},
                    },
                }
            )
        date_field = next(
            (field for field in fields if field.get("type") == "date"), None
        )
        if date_field is not None and "calendar" not in track["views"]:
            additions.append(
                {
                    "kind": "save_view",
                    "summary": f"Add {title} calendar",
                    "diff_human": "Generated date-bound calendar for the scaffold.",
                    "diff_machine": {
                        "op": "save_view",
                        "track_id": track_ref,
                        "name": f"{title} Calendar",
                        "view_type": "calendar",
                        "config": {
                            "calendar_mapping": {
                                "dateField": f"custom_fields.{date_field['key']}"
                            }
                        },
                    },
                    "payload": {
                        "track_id": track_ref,
                        "name": f"{title} Calendar",
                        "view_type": "calendar",
                        "config": {
                            "calendar_mapping": {
                                "dateField": f"custom_fields.{date_field['key']}"
                            }
                        },
                    },
                }
            )
        if not track["has_seed"]:
            additions.append(
                {
                    "kind": "create_entry",
                    "summary": f"Add example {title} record",
                    "diff_human": "Generated example record for the scaffold.",
                    "diff_machine": {
                        "op": "create_entry",
                        "track_id": track_ref,
                        "title": f"Example {title}",
                        **(
                            {"entry_type": example_entry_type}
                            if example_entry_type
                            else {}
                        ),
                        "fields": example_fields,
                    },
                    "payload": {
                        "track_id": track_ref,
                        "title": f"Example {title}",
                        **(
                            {"entry_type": example_entry_type}
                            if example_entry_type
                            else {}
                        ),
                        "fields": example_fields,
                    },
                }
            )
    ops.extend(additions)
    return len(additions)


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
                f"integral_apply_model_to_track for {name!r} (or inline entry_types)"
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
