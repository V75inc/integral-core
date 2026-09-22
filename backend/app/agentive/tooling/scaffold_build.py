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
        heading = _TRACK_HEADING.match(line.strip()) or _NAMED_TRACK_HEADING.match(
            line.strip()
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
    # the fields, its exact labels are the checkable contract.
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
    params: Dict[str, Any], *, include_default_view: bool = True
) -> list[tuple[str, Dict[str, Any]]]:
    """Compile common design shorthand into the published track/view tools."""
    track = dict(params)
    fields = track.pop("fields", None)
    if fields and not track.get("entry_types"):
        name = str(track.get("name") or "Record")
        singular = name[:-1] if name.endswith("s") else name
        track["entry_types"] = [{"name": singular, "fields": fields}]
    views = track.pop("views", None)
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
            raw_operations = _coalesce_plan_operations(raw_operations)
        except ValueError as exc:
            return _invalid("invalid_scaffold_plan", str(exc))
    if (
        not isinstance(raw_operations, list)
        or not 2 <= len(raw_operations) <= _MAX_OPERATIONS
    ):
        return _invalid(
            "invalid_scaffold_plan",
            f"Supply 2 to {_MAX_OPERATIONS} ordered scaffold operations.",
        )
    proposal = str(marker.get("proposal") or "").casefold()
    if not proposal:
        return _invalid("invalid_scaffold_plan", "The approved design has no preview.")
    operations = []
    required_fields = _approved_field_requirements(marker)
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
        if index == 0 and tool != "integral_create_app":
            return _invalid(
                "invalid_scaffold_plan",
                "The first operation must create the approved App.",
            )
        if index > 0 and tool == "integral_create_app":
            return _invalid(
                "invalid_scaffold_plan", "One design can create only one App."
            )
        if tool in {"integral_create_app", "integral_create_app_track"}:
            name = str(params.get("name") or "").strip()
            if not name or name.casefold() not in proposal:
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
                if params.get("app_id") != "{{app.id}}":
                    return _invalid(
                        "invalid_scaffold_plan",
                        "New tracks must use app_id={{app.id}}.",
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
                        include_default_view=track_ref not in explicit_view_tracks,
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
    if "dashboard" in proposal and not any(
        tool == "integral_create_dashboard" for tool, _ in operations
    ):
        app_name = str(operations[0][1].get("name") or "App")
        generated_dashboard = _dashboard_from_date_views(operations, app_name)
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
        {"summary": str(marker.get("summary") or "Build app")},
        principal_id=principal_id,
        scope=scope,
        session_id=session_id,
        interaction_id=interaction_id,
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
    return ToolResult(
        data={
            "_kind": "batch_applied",
            "applied": True,
            "batch_token": data.get("token"),
            "completed": executed.get("completed"),
            "total": executed.get("total"),
            "next": "Read back each Track schema, saved views, dashboard data and seeded entries against the approved assertions before saying verified.",
        }
    )
