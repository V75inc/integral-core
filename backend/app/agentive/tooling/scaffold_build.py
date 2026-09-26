"""One bounded, approved scaffold plan through the normal staged-write seam."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import Any, Dict, Optional

from app.agentive.staging import (
    cancel_batch,
    is_batch_open,
    open_batch,
    peek_open_batch,
)
from app.agentive.tooling.dispatch import ToolResult
from app.services.chat_threads import (
    design_chat_affirmed_for_build,
    get_thread_by_session,
    record_design_partial_build,
)
from app.services.relative_date_filters import normalize_relative_date

_PLAN_TOOLS = frozenset(
    {
        "integral_create_app",
        "integral_create_app_track",
        "integral_save_view",
        "integral_create_entry",
        "integral_create_dashboard",
        "integral_author_skill",
        "integral_schedule_task",
    }
)
_MAX_OPERATIONS = 64
_MAX_RAW_OPERATIONS = 128
_FIELDS_ASSERTION = re.compile(r"^(.+?) track with fields:\s*(.+)$", re.IGNORECASE)
_TRACK_HEADING = re.compile(r"^#{3,4}\s*\d+\.\s*(.+?)(?:\s+Track)?\s*$", re.IGNORECASE)
_NAMED_TRACK_HEADING = re.compile(r"^#{2,4}\s*(.+?)\s+Track\s*$", re.IGNORECASE)
_NEW_TRACK_LABEL = re.compile(r"^\*\*New Track:\*\*\s*(.+?)\s*$", re.IGNORECASE)
_DESIGN_FIELD = re.compile(r"^\s*-\s+(.+?)\s*\([^)]*\)\s*$")
_OPERATOR_ALIASES = {
    "=": "eq",
    "==": "eq",
    "!=": "neq",
    ">": "gt",
    "<": "lt",
    ">=": "gte",
    "<=": "lte",
}


def _canonical_operator(value: Any) -> str:
    raw = str(value or "eq").strip()
    return _OPERATOR_ALIASES.get(raw, raw)


def _expanded_filter(
    item: Dict[str, Any], *, operator_key: str
) -> list[Dict[str, Any]]:
    """Compile negative membership to supported conjunctive comparisons."""
    op = _canonical_operator(
        item.get(operator_key) or item.get("op") or item.get("operator")
    )
    base = {key: value for key, value in item.items() if key not in {"op", "operator"}}
    if op in {"not_in", "nin", "$nin"}:
        values = item.get("value")
        if not isinstance(values, list) or not values:
            raise ValueError("not_in filter needs a non-empty list of values")
        return [{**base, operator_key: "neq", "value": value} for value in values]
    return [{**base, operator_key: op}]


def _coalesce_plan_operations(raw: list[Any]) -> list[Any]:
    """Remove repeated view writes and compatible staged seed refinements."""
    if len(raw) > _MAX_RAW_OPERATIONS:
        raise ValueError(f"Plan exceeds {_MAX_RAW_OPERATIONS} submitted operations.")
    result: list[Any] = []
    fingerprints: set[str] = set()
    seed_positions: Dict[tuple[str, str], int] = {}
    for item in raw:
        if not isinstance(item, dict):
            result.append(item)
            continue
        fingerprint = json.dumps(item, sort_keys=True, default=str)
        if fingerprint in fingerprints:
            continue
        fingerprints.add(fingerprint)
        args = item.get("args")
        if item.get("tool") == "integral_create_entry" and isinstance(args, dict):
            identity = (
                str(args.get("track_id") or "").casefold(),
                str(args.get("title") or "").casefold(),
            )
            prior_index = seed_positions.get(identity)
            if prior_index is not None:
                prior = result[prior_index]
                prior_args = prior.get("args") or {}
                old_fields = prior_args.get("fields") or {}
                new_fields = args.get("fields") or {}
                if not isinstance(old_fields, dict) or not isinstance(new_fields, dict):
                    raise ValueError(f"Conflicting seed for {identity[1]!r}.")
                if any(
                    key in new_fields and new_fields[key] != value
                    for key, value in old_fields.items()
                ):
                    raise ValueError(f"Conflicting seed fields for {identity[1]!r}.")
                if {
                    key: value
                    for key, value in prior_args.items()
                    if key not in {"fields", "text"}
                } != {
                    key: value
                    for key, value in args.items()
                    if key not in {"fields", "text"}
                }:
                    raise ValueError(f"Conflicting seed shape for {identity[1]!r}.")
                result[prior_index] = None
                merged = {**old_fields, **new_fields}
                item = {**item, "args": {**args, "fields": merged}}
            seed_positions[identity] = len(result)
        result.append(item)
    return [item for item in result if item is not None]


def _approved_field_requirements(marker: Dict[str, Any]) -> Dict[str, set[str]]:
    """Read concrete field promises from the persisted design assertions."""
    required: Dict[str, set[str]] = {}
    for assertion in marker.get("acceptance_assertions") or []:
        match = _FIELDS_ASSERTION.match(str(assertion).strip())
        if not match:
            continue
        required[match.group(1).strip().casefold()] = {
            re.sub(r"\s*\([^)]*\)", "", label).strip().casefold()
            for label in match.group(2).split(",")
            if label.strip()
        }
    proposal_fields: Dict[str, set[str]] = {}
    track_name: str | None = None
    in_fields = False
    for line in str(marker.get("proposal") or "").splitlines():
        heading = (
            _TRACK_HEADING.match(line.strip())
            or _NAMED_TRACK_HEADING.match(line.strip())
            or _NEW_TRACK_LABEL.match(line.strip())
        )
        if heading:
            track_name = heading.group(1).strip().casefold()
            in_fields = False
            continue
        if line.lstrip().startswith("#"):
            in_fields = False
            track_name = None
            continue
        section = line.strip().casefold()
        if re.match(r"^-\s+(?:\*\*)?fields:(?:\*\*)?", section):
            in_fields = bool(track_name)
            continue
        if in_fields and re.match(r"^-\s+(?:\*\*)?views:(?:\*\*)?", section):
            in_fields = False
            continue
        if in_fields and track_name:
            field = _DESIGN_FIELD.match(line)
            if field:
                proposal_fields.setdefault(track_name, set()).add(
                    field.group(1).strip().casefold()
                )
    # Prose assertions may compress several concrete fields ("last/next
    # service", "rental start/end"). Where the approved proposal spells out
    # the fields, its exact labels replace that compressed set.
    required.update(proposal_fields)
    return required


def _track_declared_field_names(params: Dict[str, Any]) -> set[str]:
    field_sets = [params.get("fields") or []]
    field_sets.extend(
        entry_type.get("fields") or []
        for entry_type in (params.get("entry_types") or [])
        if isinstance(entry_type, dict)
    )
    return {
        str(field.get("name") or field.get("key") or "").strip().casefold()
        for fields in field_sets
        for field in fields
        if isinstance(field, dict)
    }


def _structured_seed(
    params: Dict[str, Any],
    track_fields: Dict[str, list[Dict[str, Any]]],
    seed_titles: Dict[str, str],
) -> Dict[str, Any]:
    """Turn labelled demo text into typed fields; never silently file empty rows."""
    entry = dict(params)
    track_ref = str(entry.get("track_id") or "")
    specs = track_fields.get(track_ref)
    if not specs or entry.get("fields"):
        if specs and entry.get("fields"):
            entry["strict_fields"] = True
        return entry
    text = str(entry.get("text") or "").strip()
    if not text:
        raise ValueError("A typed seed entry needs structured fields.")
    labels = {
        str(field.get("name") or field.get("key") or "").strip().casefold(): field
        for field in specs
        if isinstance(field, dict) and field.get("key")
    }
    values: Dict[str, Any] = {}
    for line in text.splitlines():
        label, separator, raw_value = line.partition(":")
        field = labels.get(label.strip().casefold())
        if not separator or field is None or not raw_value.strip():
            raise ValueError(f"Seed line {line!r} does not match a declared field.")
        value = raw_value.strip()
        if field.get("type") == "relation":
            if value.casefold() not in seed_titles:
                raise ValueError(f"Relation target {value!r} is not a planned seed.")
            value = f"{{{{entry.id:{seed_titles[value.casefold()]}}}}}"
        elif field.get("type") == "member" and value.casefold() in seed_titles:
            raise ValueError(
                f"Member field {field['key']!r} cannot point to planned entry {value!r}; "
                "use a relation field for a Technician record."
            )
        values[str(field["key"])] = value
    if not values:
        raise ValueError("A typed seed entry needs structured fields.")
    entry["fields"] = values
    entry["strict_fields"] = True
    entry.pop("text", None)
    return entry


def _field_path(key: str) -> str:
    return key if key.startswith("custom_fields.") else f"custom_fields.{key}"


def _expand_track(
    params: Dict[str, Any], *, include_default_view: bool = False
) -> list[tuple[str, Dict[str, Any]]]:
    """Compile common design shorthand into the published track/view tools."""
    track = dict(params)
    fields = track.pop("fields", None)
    if fields and not track.get("entry_types"):
        name = str(track.get("name") or "Record")
        singular = name[:-1] if name.endswith("s") else name
        track["entry_types"] = [{"name": singular, "fields": fields}]
    views = track.pop("views", None)
    # A table is added only when the caller already decided the plan or the
    # approved design asked for one. Feed is a substrate default elsewhere.
    if views is None:
        views = (
            [{"name": f"All {track.get('name')}", "type": "table"}]
            if include_default_view
            else []
        )
    operations: list[tuple[str, Dict[str, Any]]] = [
        ("integral_create_app_track", track)
    ]
    for view in views:
        if not isinstance(view, dict):
            raise ValueError("each track view must be an object")
        view_type = view.get("type") or view.get("view_type") or "table"
        config = dict(view.get("config") or {})
        if view_type == "table" and not config.get("columns"):
            source_fields = fields or (
                (track.get("entry_types") or [{}])[0].get("fields") or []
            )
            config["columns"] = [
                _field_path(str(field["key"]))
                for field in source_fields
                if isinstance(field, dict) and field.get("key")
            ]
        if view_type == "kanban":
            group = view.get("group_by") or config.get("group_by")
            if group:
                config["group_by"] = _field_path(str(group))
                source_fields = fields or (
                    (track.get("entry_types") or [{}])[0].get("fields") or []
                )
                selected = next(
                    (field for field in source_fields if field.get("key") == group),
                    None,
                )
                if selected and selected.get("enum"):
                    config["kanban_columns"] = list(selected["enum"])
        if view_type == "calendar":
            date_field = view.get("date_field") or config.get("date_field")
            if date_field:
                config["calendar_mapping"] = {"dateField": _field_path(str(date_field))}
        if view_type == "wiki":
            parent_field = view.get("parent_field") or config.get("parent_field")
            if parent_field:
                config["parent_field"] = str(parent_field).removeprefix(
                    "custom_fields."
                )
        operations.append(
            (
                "integral_save_view",
                {
                    "track_id": f"{{{{track.id:{track['name']}}}}}",
                    "name": view.get("name") or f"All {track['name']}",
                    "view_type": view_type,
                    "config": config,
                },
            )
        )
    return operations


def _normalize_dashboard(params: Dict[str, Any]) -> Dict[str, Any]:
    dashboard = dict(params)
    widgets = []
    operators = {
        "$eq": "eq",
        "$ne": "neq",
        "$gt": "gt",
        "$lt": "lt",
        "$gte": "gte",
        "$lte": "lte",
        "$nin": "not_in",
    }
    for raw in dashboard.get("widgets") or []:
        if not isinstance(raw, dict):
            widgets.append(raw)
            continue
        widget = dict(raw)
        if widget.get("type") == "list":
            widget["type"] = "recent_entries"
        widget.setdefault("title", widget.pop("name", ""))
        source = dict(widget.get("data_source") or {})
        if widget.get("track_id") and not source.get("track_id"):
            source["track_id"] = widget.pop("track_id")
        filters = widget.pop("filters", None)
        if isinstance(filters, dict):
            expressions = []
            for field, predicate in filters.items():
                path = _field_path(str(field))
                if isinstance(predicate, dict):
                    for op, value in predicate.items():
                        if op not in operators:
                            raise ValueError(f"unsupported dashboard filter {op}")
                        expressions.extend(
                            _expanded_filter(
                                {"field": path, "op": operators[op], "value": value},
                                operator_key="op",
                            )
                        )
                else:
                    expressions.append({"field": path, "op": "eq", "value": predicate})
            source["filters"] = expressions
        elif isinstance(source.get("filters"), list):
            source["filters"] = [
                expanded
                for item in source["filters"]
                for expanded in (
                    _expanded_filter(item, operator_key="op")
                    if isinstance(item, dict)
                    else [item]
                )
            ]
        widget["data_source"] = source
        widgets.append(widget)
    dashboard["widgets"] = widgets
    return dashboard


def _normalize_view(params: Dict[str, Any]) -> Dict[str, Any]:
    view = dict(params)
    config = dict(view.get("config") or {})
    filters = config.pop("filter", None)
    if filters is not None and "filters" not in config:
        config["filters"] = filters
    if isinstance(config.get("filters"), list):
        config["filters"] = [
            expanded
            for item in config["filters"]
            for expanded in (
                _expanded_filter(item, operator_key="operator")
                if isinstance(item, dict)
                else [item]
            )
        ]
    view["config"] = config
    return view


def _dashboard_from_date_views(
    operations: list[tuple[str, Dict[str, Any]]], app_name: str
) -> Dict[str, Any]:
    widgets = []
    for tool, params in operations:
        if tool != "integral_save_view":
            continue
        name = str(params.get("name") or "")
        if not (name.startswith("Overdue ") or name.startswith("Upcoming ")):
            continue
        filters = [
            {
                "field": item.get("field"),
                "op": _canonical_operator(item.get("operator")),
                "value": item.get("value"),
            }
            for item in (params.get("config") or {}).get("filters", [])
        ]
        widgets.append(
            {
                "type": "recent_entries",
                "title": name,
                "data_source": {
                    "track_id": params.get("track_id"),
                    "filters": filters,
                    "limit": 10,
                },
            }
        )
    return {"app_id": "{{app.id}}", "name": f"{app_name} Dashboard", "widgets": widgets}


def _invalid(code: str, message: str) -> ToolResult:
    return ToolResult(is_error=True, error_code=code, message=message)


def _relation_type_slug(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().casefold()).strip("-")


def _annotate_plan_cross_track_relations(operations: list[Any]) -> list[Any]:
    """Name sibling tracks on relations that target entry types from other tracks.

    Approved multi-track designs often declare ``target_entry_types: ["business"]``
    on Expenses while Business lives on a sibling track. Runtime then rejects
    seed ``create_entry`` with "cannot reference entries across tracks" unless
    ``target_track_types`` is set (see ``relation_allows_cross_track``).
    """
    type_to_tracks: Dict[str, set[str]] = {}
    for item in operations:
        if (
            not isinstance(item, dict)
            or item.get("tool") != "integral_create_app_track"
        ):
            continue
        args = item.get("args") if isinstance(item.get("args"), dict) else {}
        track_name = str(args.get("name") or "").strip()
        if not track_name:
            continue
        for entry_type in args.get("entry_types") or []:
            if not isinstance(entry_type, dict):
                continue
            for label in (entry_type.get("key"), entry_type.get("name")):
                slug = _relation_type_slug(label)
                if slug:
                    type_to_tracks.setdefault(slug, set()).add(track_name)

    annotated: list[Any] = []
    for item in operations:
        if (
            not isinstance(item, dict)
            or item.get("tool") != "integral_create_app_track"
        ):
            annotated.append(item)
            continue
        args = dict(item.get("args") or {})
        track_name = str(args.get("name") or "").strip()
        entry_types: list[Any] = []
        for entry_type in args.get("entry_types") or []:
            if not isinstance(entry_type, dict):
                entry_types.append(entry_type)
                continue
            fields: list[Any] = []
            for field in entry_type.get("fields") or []:
                if not isinstance(field, dict) or field.get("type") != "relation":
                    fields.append(field)
                    continue
                relation = field.get("relation")
                if not isinstance(relation, dict):
                    fields.append(field)
                    continue
                targets = relation.get("target_entry_types") or []
                if not isinstance(targets, (list, tuple)):
                    fields.append(field)
                    continue
                foreign_tracks: set[str] = set()
                for target in targets:
                    for owner in type_to_tracks.get(_relation_type_slug(target), ()):
                        if owner != track_name:
                            foreign_tracks.add(owner)
                if not foreign_tracks:
                    fields.append(field)
                    continue
                existing = {
                    str(x).strip()
                    for x in (relation.get("target_track_types") or [])
                    if str(x).strip()
                }
                fields.append(
                    {
                        **field,
                        "relation": {
                            **relation,
                            "target_track_types": sorted(existing | foreign_tracks),
                            "allow_cross_track": True,
                        },
                    }
                )
            entry_types.append({**entry_type, "fields": fields})
        annotated.append({**item, "args": {**args, "entry_types": entry_types}})
    return annotated


def _approved_plan_item(item: Any, *, only_track_name: str = "") -> Any:
    """Accept familiar display-name shorthand but stage published tool args."""
    if not isinstance(item, dict) or not isinstance(item.get("args"), dict):
        return item
    tool = item.get("tool")
    params = dict(item["args"])
    if tool == "integral_create_app_track":
        if "track_name" in params:
            params.setdefault("name", params.pop("track_name"))
        if "track_description" in params:
            params.setdefault("description", params.pop("track_description"))
        normalized_types = []
        for entry_type in params.get("entry_types") or []:
            if not isinstance(entry_type, dict):
                normalized_types.append(entry_type)
                continue
            fields = []
            for field in entry_type.get("fields") or []:
                if not isinstance(field, dict) or field.get("type") != "relation":
                    fields.append(field)
                    continue
                relation = field.get("relation") or {}
                if isinstance(relation, dict):
                    related_type = relation.get("to_entry_type") or relation.get(
                        "entry_type"
                    )
                    parent_shorthand = relation.get("target") != "entry" and (
                        relation.get("mode") == "parent"
                        or relation.get("track") in {"self", "same"}
                    )
                    if parent_shorthand:
                        types = (
                            [related_type]
                            if isinstance(related_type, str) and related_type
                            else [str(item) for item in related_type or [] if item]
                        )
                        if types:
                            field = {
                                **field,
                                "relation": {
                                    "target": "entry",
                                    "target_entry_types": types,
                                    "allow_cross_track": False,
                                    "many": False,
                                },
                            }
                fields.append(field)
            normalized_types.append({**entry_type, "fields": fields})
        if "entry_types" in params:
            params["entry_types"] = normalized_types
    if tool == "integral_save_view":
        if "view_name" in params:
            params.setdefault("name", params.pop("view_name"))
        config = dict(params.get("config") or {})
        if config.get("name"):
            params.setdefault("name", config.pop("name"))
        if config.get("type"):
            params.setdefault("view_type", config.pop("type"))
        for alias in ("hierarchy_field", "parent"):
            alias_value = config.get(alias)
            if (
                isinstance(alias_value, str)
                and alias_value.strip()
                and not config.get("parent_field")
            ):
                config["parent_field"] = alias_value.strip()
                config.pop(alias, None)
        hint = str(config.pop("track_hint", "") or "").strip()
        if not params.get("track_id") and (hint or only_track_name):
            params["track_id"] = f"{{{{track.id:{hint or only_track_name}}}}}"
        mapping = params.pop("field_mapping", None)
        if mapping is not None:
            if params.get("view_type") != "wiki" or not isinstance(mapping, dict):
                raise ValueError("field_mapping is only supported for a Wiki view")
            for source, target in (
                ("parent", "parent_field"),
                ("title", "title_field"),
                ("body", "body_field"),
            ):
                if mapping.get(source) and not config.get(target):
                    config[target] = str(mapping[source])
        params["config"] = config
    if tool == "integral_create_entry":
        hint = ""
        for key in ("track_hint", "track_name"):
            raw = params.get(key)
            if isinstance(raw, str) and raw.strip():
                hint = raw.strip()
                params.pop(key, None)
                break
        track_id = str(params.get("track_id") or "").strip()
        if track_id and not (track_id.startswith("{{") or track_id.startswith("n.")):
            hint = hint or track_id
            track_id = ""
        if not track_id and (hint or only_track_name):
            params["track_id"] = f"{{{{track.id:{hint or only_track_name}}}}}"
    return {**item, "args": params}


def _name_in_proposal(name: str, proposal: str) -> bool:
    """True when ``name`` is its own token in the already-folded proposal."""
    folded = name.strip().casefold()
    if not folded:
        return False
    return re.search(rf"(?<![\w]){re.escape(folded)}(?![\w])", proposal) is not None


def _positively_requested(proposal: str, term: str) -> bool:
    """True when ``term`` is named and not negated nearby."""
    if re.search(
        rf"\b(?:no|without|not)\b.{{0,40}}\b{re.escape(term)}\b",
        proposal,
        re.IGNORECASE,
    ):
        return False
    return re.search(rf"\b{re.escape(term)}\b", proposal, re.IGNORECASE) is not None


def _named_seed_titles(proposal: str) -> list[str]:
    titles = []
    for match in re.finditer(r"\btitled\s+([^,.\n]+)", proposal, re.IGNORECASE):
        title = match.group(1).strip(" *\"'")
        if title:
            titles.append(title)
    return titles


def _explicitly_empty_design(proposal: str) -> bool:
    """True when the compiler must not invent Example records."""
    if _named_seed_titles(proposal):
        return True
    return bool(
        re.search(
            r"\b(no demo entries|without demo entries|leave (?:the )?track empty|no other entries|without other entries)\b",
            proposal,
            re.IGNORECASE,
        )
    )


def _existing_app_design(marker: Dict[str, Any]) -> bool:
    """Recognize the explicit target, including proposals saved before that field existed."""
    if marker.get("target_app_id"):
        return True
    summary = str(marker.get("summary") or "")
    proposal = str(marker.get("proposal") or "")
    return bool(
        re.search(r"^\s*add\b.*\btrack\b", summary, re.IGNORECASE)
        and re.search(r"\*\*New Track:\*\*", proposal, re.IGNORECASE)
    )


def _plan_binding_error(
    operations: list[tuple[str, Dict[str, Any]]],
    track_fields: Dict[str, list[Dict[str, Any]]],
    proposal: str,
) -> str | None:
    """Check requested Wiki views against the schema before staging writes."""
    wiki_views = 0
    for tool, params in operations:
        if tool != "integral_save_view" or params.get("view_type") != "wiki":
            continue
        wiki_views += 1
        track_ref = str(params.get("track_id") or "")
        fields = track_fields.get(track_ref)
        if fields is None:
            return f"Wiki view targets an undeclared Track: {track_ref}."
        config = params.get("config") or {}
        if not isinstance(config, dict):
            return "Wiki view config must be an object."
        parent = str(config.get("parent_field") or "").removeprefix("custom_fields.")
        if not any(
            isinstance(field, dict)
            and field.get("key") == parent
            and field.get("type") == "relation"
            for field in fields
        ):
            return "Wiki config.parent_field must name a relation field on its Track."
    if _positively_requested(proposal, "wiki") and not wiki_views:
        if len(track_fields) == 1:
            track_ref, fields = next(iter(track_fields.items()))
            parent = next(
                (
                    str(field.get("key"))
                    for field in fields
                    if isinstance(field, dict) and field.get("type") == "relation"
                ),
                "<relation-field-key>",
            )
            return (
                "The approved Wiki view is missing. Add integral_save_view "
                f"with track_id={track_ref!r}, name='Wiki', view_type='wiki', "
                f"config={{'parent_field': {parent!r}}}; retry this approved build "
                "now without asking the user again."
            )
        return (
            "The approved Wiki view is missing. Add integral_save_view with "
            "view_type='wiki' and config.parent_field; retry this approved "
            "build now without asking the user again."
        )
    return None


async def build_approved_design(
    args: Dict[str, Any],
    *,
    principal_id: str,
    scope: Optional[str],
    session_id: Optional[str],
    interaction_id: Optional[str],
) -> ToolResult:
    """Stage a model-authored plan and commit its single affirmed batch.

    The plan is data, not authority. The conversation marker supplies the
    approval; every operation still goes through the published tool dispatcher
    and the existing batch executor applies each operation under policy.
    """
    if not session_id or not await design_chat_affirmed_for_build(session_id):
        return _invalid(
            "design_approval_required",
            "A saved design must be affirmed in this conversation before building.",
        )
    thread = await get_thread_by_session(session_id)
    marker = getattr(thread, "design_proposed", None) or {}
    if isinstance(marker, dict) and marker.get("build_receipt"):
        return _invalid(
            "design_already_applied",
            "This approved design already has an applied build receipt.",
        )
    if isinstance(marker, dict) and marker.get("partial_build"):
        return _invalid(
            "partial_build_requires_repair",
            "This design has a partially applied batch. Inspect its existing App and repair the failed remainder; do not create another App.",
        )
    if not isinstance(marker, dict) or not marker.get("approved"):
        return _invalid("design_approval_required", "The design is not approved.")
    if getattr(thread, "user_id", None) != principal_id:
        return _invalid("design_owner_mismatch", "The design belongs to another user.")
    existing_batch = peek_open_batch(principal_id, session_id)
    if existing_batch and existing_batch.get("op_count"):
        return _invalid(
            "scaffold_batch_already_open",
            "An unfinished scaffold batch already exists; resume or cancel it first.",
        )

    raw_operations = args.get("operations")
    if isinstance(raw_operations, list):
        # The resident sometimes includes the manual-batch terminator in a
        # one-call plan. This tool commits itself; a terminal control step is
        # redundant, not a second mutation or a reason to abandon the design.
        raw_operations = list(raw_operations)
        if raw_operations and isinstance(raw_operations[-1], dict):
            if raw_operations[-1].get("tool") == "integral_commit_batch":
                raw_operations.pop()
        try:
            track_names = {
                str(
                    (item.get("args") or {}).get("name")
                    or (item.get("args") or {}).get("track_name")
                    or ""
                ).strip()
                for item in raw_operations
                if isinstance(item, dict)
                and item.get("tool") == "integral_create_app_track"
                and isinstance(item.get("args"), dict)
            }
            track_names.discard("")
            only_track_name = next(iter(track_names)) if len(track_names) == 1 else ""
            raw_operations = [
                _approved_plan_item(item, only_track_name=only_track_name)
                for item in raw_operations
            ]
            raw_operations = _coalesce_plan_operations(raw_operations)
            raw_operations = _annotate_plan_cross_track_relations(raw_operations)
        except ValueError as exc:
            return _invalid("invalid_scaffold_plan", str(exc))
    if (
        not isinstance(raw_operations, list)
        or not 1 <= len(raw_operations) <= _MAX_OPERATIONS
    ):
        return _invalid(
            "invalid_scaffold_plan",
            f"Supply 1 to {_MAX_OPERATIONS} ordered scaffold operations.",
        )
    proposal = str(marker.get("proposal") or "").casefold()
    if not proposal:
        return _invalid("invalid_scaffold_plan", "The approved design has no preview.")
    # A typed blueprint replaces every prose heuristic below with a
    # structural comparison (plan_fidelity_errors) after expansion.
    blueprint = (
        marker.get("blueprint") if isinstance(marker.get("blueprint"), dict) else None
    )
    first = raw_operations[0]
    first_tool = first.get("tool") if isinstance(first, dict) else None
    target_app_id = str(args.get("target_app_id") or "").strip()
    if not target_app_id and first_tool == "integral_create_app_track":
        first_args = first.get("args") or {}
        if isinstance(first_args, dict):
            target_app_id = str(first_args.get("app_id") or "").strip()
    approved_target = str(marker.get("target_app_id") or "").strip()
    if _existing_app_design(marker) and first_tool == "integral_create_app":
        return _invalid(
            "plan_differs_from_design",
            "The approved design adds a Track to an existing App; do not create another App. Use its real app_id.",
        )
    if approved_target and target_app_id != approved_target:
        return _invalid(
            "plan_differs_from_design",
            "The build target differs from the App in the approved design.",
        )
    existing_app = None
    if target_app_id:
        from app.models.nodes import App
        from app.services.permissions import can_admin_app

        if first_tool != "integral_create_app_track":
            return _invalid(
                "invalid_scaffold_plan",
                "An existing-App plan must start with integral_create_app_track, not create another App.",
            )
        if target_app_id.startswith("{{"):
            return _invalid(
                "invalid_scaffold_plan",
                "An existing-App plan needs the real app_id from integral_list_apps, not {{app.id}}.",
            )
        existing_app = await App.get(target_app_id)
        if existing_app is None or existing_app.workspace_id != scope:
            return _invalid(
                "target_app_unavailable",
                "The target App is not in the active workspace.",
            )
        if not await can_admin_app(principal_id, target_app_id):
            return _invalid(
                "target_app_forbidden", "You cannot configure the target App."
            )
        if (
            blueprint["app"]["name"].casefold() != existing_app.name.casefold()
            if blueprint
            else not _name_in_proposal(existing_app.name, proposal)
        ):
            return _invalid(
                "plan_differs_from_design",
                "The target App name is absent from the approved preview.",
            )
    elif first_tool != "integral_create_app":
        return _invalid(
            "invalid_scaffold_plan",
            "Start a new-App plan with integral_create_app, or provide the real app_id on the first integral_create_app_track for an existing App.",
        )
    if existing_app is None and len(raw_operations) < 2:
        return _invalid(
            "invalid_scaffold_plan",
            "A new App plan needs an App and at least one Track.",
        )
    operations = []
    required_fields = {} if blueprint else _approved_field_requirements(marker)
    track_fields: Dict[str, list[Dict[str, Any]]] = {}
    seed_titles = {
        str(item.get("args", {}).get("title") or "")
        .strip()
        .casefold(): str(item.get("args", {}).get("title") or "")
        .strip()
        for item in raw_operations
        if isinstance(item, dict)
        and item.get("tool") == "integral_create_entry"
        and isinstance(item.get("args"), dict)
    }
    missing_titles = [
        title
        for title in (
            [] if blueprint else _named_seed_titles(str(marker.get("proposal") or ""))
        )
        if title.casefold() not in seed_titles
    ]
    if missing_titles:
        listed = ", ".join(missing_titles)
        return _invalid(
            "plan_differs_from_design",
            "The approved design names entries missing from the plan: "
            f"{listed}. Add integral_create_entry with those exact titles and "
            "structured fields. Do not add Example records. Retry this approved "
            "build now without asking the user again.",
        )
    explicit_view_tracks = {
        str(item.get("args", {}).get("track_id"))
        for item in raw_operations
        if isinstance(item, dict)
        and item.get("tool") == "integral_save_view"
        and isinstance(item.get("args"), dict)
    }
    track_count = 0
    for index, item in enumerate(raw_operations):
        # `depends_on` is explanatory planner metadata. The actual ordered
        # payload references are still checked by validate_batch_references.
        if (
            not isinstance(item, dict)
            or not {"tool", "args"} <= set(item)
            or (set(item) - {"tool", "args"} not in (set(), {"depends_on"}))
        ):
            return _invalid(
                "invalid_scaffold_plan",
                f"Operation {index + 1} must have tool and args (optional depends_on).",
            )
        tool = item["tool"]
        params = item["args"]
        if tool not in _PLAN_TOOLS or not isinstance(params, dict):
            hint = (
                " For an App Track, use integral_create_app_track with inline entry_types."
                if tool == "integral_author_model"
                else " Allowed writes: " + ", ".join(sorted(_PLAN_TOOLS)) + "."
            )
            return _invalid(
                "invalid_scaffold_plan",
                f"Operation {index + 1} ({tool}) is not an allowed scaffold write.{hint}",
            )
        if {"user_id", "principal_id", "workspace_id"} & set(params):
            return _invalid(
                "invalid_scaffold_plan",
                f"Operation {index + 1} contains identity or scope arguments.",
            )
        if index == 0 and tool != (
            "integral_create_app_track" if existing_app else "integral_create_app"
        ):
            return _invalid(
                "invalid_scaffold_plan",
                "The first operation does not match the approved App build mode.",
            )
        if index > 0 and tool == "integral_create_app":
            return _invalid(
                "invalid_scaffold_plan", "One design can create only one App."
            )
        if tool in {"integral_create_app", "integral_create_app_track"}:
            name = str(params.get("name") or "").strip()
            if not name or (not blueprint and not _name_in_proposal(name, proposal)):
                return _invalid(
                    "plan_differs_from_design",
                    f"Operation {index + 1} names an App or Track absent from the approved preview.",
                )
            if tool == "integral_create_app_track":
                track_count += 1
                missing_fields = sorted(
                    required_fields.get(name.casefold(), set())
                    - _track_declared_field_names(params)
                )
                if missing_fields:
                    return _invalid(
                        "plan_differs_from_design",
                        f"Track {name!r} omits approved fields: {', '.join(missing_fields)}.",
                    )
                expected_app_id = target_app_id if existing_app else "{{app.id}}"
                if params.get("app_id") != expected_app_id:
                    return _invalid(
                        "invalid_scaffold_plan",
                        f"Tracks in this plan must use app_id={expected_app_id}.",
                    )
                if not params.get("entry_types") and not params.get("fields"):
                    return _invalid(
                        "invalid_scaffold_plan",
                        f"Track {name!r} needs inline entry_types or fields.",
                    )
                for entry_type in params.get("entry_types") or []:
                    for field in (
                        (entry_type.get("fields") or [])
                        if isinstance(entry_type, dict)
                        else []
                    ):
                        if isinstance(field, dict) and field.get("type") == "relation":
                            relation = field.get("relation") or {}
                            if isinstance(relation, dict) and {
                                "track",
                                "entry_type",
                            } & set(relation):
                                return _invalid(
                                    "invalid_scaffold_plan",
                                    "Relation fields use relation.target='entry' and relation.target_entry_types, not relation.track or relation.entry_type.",
                                )
        params = normalize_relative_date(params)
        try:
            if tool == "integral_create_app_track":
                track_ref = f"{{{{track.id:{params['name']}}}}}"
                track_fields[track_ref] = list(params.get("fields") or []) + [
                    field
                    for entry_type in params.get("entry_types") or []
                    if isinstance(entry_type, dict)
                    for field in entry_type.get("fields") or []
                ]
                operations.extend(
                    _expand_track(
                        params,
                        include_default_view=(
                            not blueprint
                            and _positively_requested(proposal, "table")
                            and track_ref not in explicit_view_tracks
                        ),
                    )
                )
            elif tool == "integral_save_view":
                operations.append((tool, _normalize_view(params)))
            elif tool == "integral_create_dashboard":
                operations.append((tool, _normalize_dashboard(params)))
            elif tool == "integral_create_entry":
                operations.append(
                    (tool, _structured_seed(params, track_fields, seed_titles))
                )
            else:
                operations.append((tool, params))
        except (KeyError, TypeError, ValueError) as exc:
            return _invalid("invalid_scaffold_plan", f"Operation {index + 1}: {exc}")
    if track_count < 1:
        return _invalid(
            "invalid_scaffold_plan", "The plan needs at least one shaped Track."
        )
    binding_error = _plan_binding_error(
        operations, track_fields, "" if blueprint else proposal
    )
    if binding_error:
        return _invalid("plan_differs_from_design", binding_error)
    if blueprint:
        from app.services.design_blueprint import plan_fidelity_errors

        fidelity = plan_fidelity_errors(
            blueprint, operations, new_app=existing_app is None
        )
        if fidelity:
            return _invalid(
                "plan_differs_from_design",
                " ".join(fidelity) + " Build exactly the approved blueprint (revision "
                f"{marker.get('blueprint_revision')}); retry now without asking "
                "the user again.",
            )
    if (
        not blueprint
        and _positively_requested(proposal, "dashboard")
        and not any(tool == "integral_create_dashboard" for tool, _ in operations)
    ):
        app_name = (
            existing_app.name
            if existing_app
            else str(operations[0][1].get("name") or "App")
        )
        generated_dashboard = _dashboard_from_date_views(operations, app_name)
        if existing_app:
            generated_dashboard["app_id"] = target_app_id
        if "overdue" in proposal and not generated_dashboard["widgets"]:
            return _invalid(
                "plan_differs_from_design",
                "The approved overdue dashboard needs date-filtered views or explicit widgets.",
            )
        operations.append(
            (
                "integral_create_dashboard",
                generated_dashboard,
            )
        )
    if len(operations) > _MAX_OPERATIONS:
        return _invalid("invalid_scaffold_plan", "Expanded plan exceeds 64 writes.")

    # A member field points to a real workspace User, never to a seed Entry.
    # Check it before any write: the batch executor is not transactional and a
    # bad late seed would otherwise strand a partial App.
    from app.services.operational_model_member_field import validate_member_value

    for tool, params in operations:
        if tool != "integral_create_entry":
            continue
        specs = track_fields.get(str(params.get("track_id") or "")) or []
        fields = params.get("fields") or {}
        if not isinstance(fields, dict):
            continue
        for spec in specs:
            if not isinstance(spec, dict) or spec.get("type") != "member":
                continue
            field_key = str(spec.get("key") or "")
            value = fields.get(field_key, fields.get(str(spec.get("name") or "")))
            if value is None:
                continue
            if not scope:
                return _invalid(
                    "invalid_scaffold_plan", "A member seed needs workspace scope."
                )
            if str(value).startswith("{{entry.id:"):
                return _invalid(
                    "invalid_scaffold_plan",
                    f"Member field {field_key!r} cannot point to a planned Entry; use a relation field.",
                )
            try:
                await validate_member_value(
                    value,
                    field_key=field_key,
                    source_track=SimpleNamespace(workspace_id=scope),
                )
            except Exception as exc:
                return _invalid(
                    "invalid_scaffold_plan",
                    f"Member seed {field_key!r} is invalid before build: {exc}",
                )

    # Validate dashboard data sources before opening a batch. Dashboard CRUD
    # applies late in the executor; an invalid filter must not leave a partial
    # App, Tracks and entries behind.
    from app.schemas.dashboards import DataSourceSpec

    for tool, params in operations:
        if tool != "integral_create_dashboard":
            continue
        for widget in params.get("widgets") or []:
            try:
                DataSourceSpec.model_validate(widget.get("data_source") or {})
            except Exception as exc:
                return _invalid(
                    "invalid_scaffold_plan",
                    f"Dashboard {widget.get('title') or widget.get('name') or 'widget'} has an invalid data source: {exc}",
                )

    # Import here to avoid a module import cycle with dispatch._dispatch_propose.
    from app.agentive.tooling.dispatch import _dispatch_batch_control, dispatch_tool

    await open_batch(
        user_id=principal_id,
        session_id=session_id,
        label=str(marker.get("summary") or "Build app"),
    )
    for index, (tool, params) in enumerate(operations):
        result = await dispatch_tool(
            tool,
            params,
            principal_id=principal_id,
            scope=scope,
            session_id=session_id,
            interaction_id=interaction_id,
        )
        if (
            result.is_error
            or not isinstance(result.data, dict)
            or not result.data.get("batched")
        ):
            await cancel_batch(user_id=principal_id, session_id=session_id)
            return _invalid(
                "scaffold_plan_stage_failed",
                f"Operation {index + 1} ({tool}) was refused: {result.error_code or result.message or result.data}",
            )

    committed = await _dispatch_batch_control(
        "integral_commit_batch",
        {
            "summary": str(marker.get("summary") or "Build app"),
            "allow_empty": (
                not blueprint.get("seeds")
                if blueprint
                else _explicitly_empty_design(proposal)
            ),
        },
        principal_id=principal_id,
        scope=scope,
        session_id=session_id,
        interaction_id=interaction_id,
        approved_extension=existing_app is not None,
    )
    if committed.is_error:
        # An incomplete plan has not written anything; allow a corrected plan
        # to retry from a clean batch. Applied/partial executor outcomes retain
        # their own durable receipt and are not erased here.
        if is_batch_open(principal_id, session_id):
            await cancel_batch(user_id=principal_id, session_id=session_id)
        if committed.error_code == "batch_partial_failure":
            partial_data = committed.data if isinstance(committed.data, dict) else {}
            await record_design_partial_build(
                session_id=session_id,
                user_id=principal_id,
                batch_token=str(partial_data.get("batch_token") or ""),
            )
        return committed
    data = committed.data if isinstance(committed.data, dict) else {}
    if data.get("_kind") != "batch_applied" or data.get("applied") is not True:
        # A partial executor outcome must be an error to the resident. The
        # previous successful envelope let it narrate an unfinished App as
        # verified while dashboard creation had failed.
        execution = data.get("execute_result") or {}
        if not isinstance(execution, dict):
            execution = {}
        failures = [
            item
            for item in execution.get("results") or []
            if isinstance(item, dict)
            and isinstance(item.get("result"), dict)
            and item["result"].get("error")
        ]
        await record_design_partial_build(
            session_id=session_id,
            user_id=principal_id,
            batch_token=str(data.get("token") or ""),
        )
        return ToolResult(
            data={
                "_kind": "batch_apply_failed",
                "applied": False,
                "batch_token": data.get("token"),
                "completed": execution.get("completed"),
                "total": execution.get("total"),
                "failed_step": failures[-1] if failures else None,
                "next": "Partial writes may exist. Inspect the existing App and repair only the failed remainder; do not create a second App or claim completion.",
            },
            is_error=True,
            error_code=str(execution.get("error_code") or "batch_apply_failed"),
            message=str(execution.get("message") or "Batch did not fully apply."),
        )
    executed = data.get("execute_result") or {}
    applied = {
        "_kind": "batch_applied",
        "applied": True,
        "batch_token": data.get("token"),
        "completed": executed.get("completed"),
        "total": executed.get("total"),
        "next": "In this same turn, tell the user what was built: the App, each Track, its fields, and its views. Say whether demo entries were created. Do not ask for approval again and do not end on the system marker.",
    }
    if blueprint:
        applied["blueprint_revision"] = marker.get("blueprint_revision")
        applied["blueprint_digest"] = marker.get("blueprint_digest")
        # Operations that need a trusted package are never built here (W1.7).
        applied["not_built"] = [op["id"] for op in blueprint.get("operations") or []]
    return ToolResult(data=applied)
