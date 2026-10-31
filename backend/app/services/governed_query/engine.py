"""Governed query engine — QuerySpec v1 locked C (ADR-012)."""

from __future__ import annotations

import base64
import bisect
import hashlib
import json
import logging
from datetime import date, datetime
from numbers import Real
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    QueryUnavailableError,
)
from app.schemas.capabilities import Evidence, ObjectRef
from app.schemas.governed_query import QueryResult, QuerySpec
from app.services.capability_catalogue.compile import (
    get_or_compile_catalogue,
    require_generation,
)
from app.services.computed_fields import (
    assert_stored_query_field,
    project_computed_values,
)
from app.services.notification_paths import resolve_resource_action_url
from app.services.permissions import resolve_role
from app.services.query_boundary import generic_entry_read
from app.services.query_filters import entry_field_value, filter_matches
from app.services.workspace_permissions import can_access_workspace
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

TargetGate = Callable[[str, str], Awaitable[bool]]


def _encode_cursor(offset: int, binding: Optional[str] = None) -> str:
    payload: Dict[str, Any] = {"o": offset}
    if binding is not None:
        payload.update({"v": 2, "q": binding})
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def _decode_cursor(cursor: Optional[str], binding: Optional[str] = None) -> int:
    if not cursor:
        return 0
    try:
        raw = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        if not isinstance(raw, dict) or type(raw.get("o")) is not int or raw["o"] < 0:
            raise ValueError("Invalid offset")
        if "v" in raw and (
            type(raw["v"]) is not int
            or raw["v"] != 2
            or not isinstance(raw.get("q"), str)
        ):
            raise ValueError("Invalid cursor version")
        if binding is not None and "v" in raw and raw["q"] != binding:
            raise ValueError("Cursor does not match query scope")
        # Original offset-only cursors remain readable during migration. All
        # audience, target and field checks still run for every requested page.
        return raw["o"]
    except Exception as exc:
        raise BadRequestError(message="Invalid query cursor") from exc


def _entry_type_key(entry_type) -> str:
    if entry_type is None:
        return ""
    manifest_key = str(
        (getattr(entry_type, "form_schema", None) or {}).get("_manifest_entry_type_key")
        or ""
    ).strip()
    from app.services.operational_model_compile import _slug

    return _slug(manifest_key or getattr(entry_type, "name", "") or "")


def _serialize_entry(
    entry,
    projection: List[str],
    fields: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    cf = project_computed_values(
        getattr(entry, "custom_fields", None) or {}, fields or []
    )
    row: Dict[str, Any] = {
        "id": entry.id,
        "kind": "entry",
        "title": getattr(entry, "title", "") or "",
        "track_id": getattr(entry, "track_id", None),
        "type_id": getattr(entry, "type_id", None),
    }
    if not projection or "custom_fields" in projection:
        row["custom_fields"] = dict(cf)
    else:
        row["custom_fields"] = {k: cf.get(k) for k in projection if k in cf}
    for key in projection:
        if key in ("id", "title", "track_id", "type_id"):
            continue
        if key.startswith("custom_fields."):
            field = key.split(".", 1)[1]
            row[key] = cf.get(field)
    return row


_CORE_RESOURCE_FIELDS = {
    "track": frozenset(
        {
            "id",
            "title",
            "workspace_id",
            "owner_id",
            "kind",
            "created_at",
            "updated_at",
        }
    ),
    "app": frozenset(
        {
            "id",
            "name",
            "workspace_id",
            "owner_user_id",
            "lifecycle_state",
            "installed_package_slug",
            "active_definition_revision",
            "created_at",
            "updated_at",
        }
    ),
}


def _core_value(resource: str, node: Any, field: str) -> Any:
    """Read a field from one Core resource without a title/name fallback."""
    if resource == "entry":
        return entry_field_value(node, field)
    if field not in _CORE_RESOURCE_FIELDS.get(resource, frozenset()):
        raise ValueError(f"unsupported {resource} filter field {field!r}")
    return getattr(node, field, None)


def _serialize_core_node(
    resource: str, node: Any, projection: List[str]
) -> Dict[str, Any]:
    """Return exactly the requested Core fields, or the safe default shape."""
    if resource == "entry":
        return _serialize_entry(node, projection)

    fields = projection or (
        ["id", "title", "workspace_id", "kind"]
        if resource == "track"
        else [
            "id",
            "name",
            "workspace_id",
            "lifecycle_state",
            "installed_package_slug",
        ]
    )
    row: Dict[str, Any] = {"kind": resource}
    for field in fields:
        try:
            row[field] = _core_value(resource, node, field)
        except ValueError as exc:
            raise BadRequestError(message=str(exc)) from exc
    return row


def _sort_key(value: Any) -> tuple[int, Any]:
    """Make mixed optional values sortable without coercing their meaning."""
    if value is None:
        return (5, "")
    if isinstance(value, bool):
        return (0, value)
    if isinstance(value, Real):
        return (1, value)
    if isinstance(value, (datetime, date)):
        return (2, value.isoformat())
    if isinstance(value, str):
        return (3, value.casefold())
    return (4, json.dumps(value, sort_keys=True, default=str, separators=(",", ":")))


def _sort_core_nodes(resource: str, nodes: List[Any], sort: Optional[str]) -> List[Any]:
    """Sort a Core result deterministically; ``-field`` means descending."""
    requested = (sort or "id").strip()
    descending = requested.startswith("-")
    field = requested[1:] if descending else requested
    if not field:
        raise BadRequestError(message="sort field cannot be empty")
    try:
        return sorted(
            nodes,
            key=lambda node: (_sort_key(_core_value(resource, node, field)), node.id),
            reverse=descending,
        )
    except ValueError as exc:
        raise BadRequestError(message=str(exc)) from exc


def _matches_core_filters(resource: str, node: Any, filters: List[Any]) -> bool:
    """Apply every declared filter or fail explicitly on an invalid path/value."""
    try:
        return all(
            _filter_matches(
                _core_value(resource, node, filter_expr.field),
                op=filter_expr.op,
                expected=filter_expr.value,
            )
            for filter_expr in filters
        )
    except ValueError as exc:
        raise BadRequestError(message=str(exc)) from exc


async def _iter_track_entries(
    track,
    candidate: Optional[Dict[str, Any]] = None,
    *,
    user_id: str,
    workspace_id: str,
) -> AsyncIterator[Any]:
    """Use the same raw-fact admission and payload checks as page/count reads."""
    from app.services.permissions import iter_user_accessible_entry_batches

    persisted = {
        key if key == "id" else f"context.{key}": value
        for key, value in (candidate or {}).items()
    }
    async for batch in iter_user_accessible_entry_batches(
        user_id,
        track.id,
        candidate_query=persisted,
        workspace_id=workspace_id,
        batch_size=200,
    ):
        for entry in batch:
            yield entry


def _filter_matches(value: Any, *, op: str, expected: Any) -> bool:
    """Apply the QuerySpec comparison vocabulary without silent fallthrough."""
    try:
        return filter_matches(value, op=op, expected=expected)
    except ValueError as exc:
        raise BadRequestError(message=str(exc)) from exc


async def _run_core_open(
    *,
    user_id: str,
    workspace_id: str,
    spec: QuerySpec,
    target_gate: Optional[TargetGate] = None,
) -> QueryResult:
    from app.models.nodes import App, Track

    if spec.max_depth > 2:
        raise BadRequestError(
            message="max_depth exceeds budget",
            details={"error_code": "query_budget_depth"},
        )

    binding = hashlib.sha256(
        json.dumps(
            {
                "workspace": workspace_id,
                "principal": user_id,
                "query": spec.model_dump(mode="json", exclude={"cursor", "limit"}),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    offset = _decode_cursor(spec.cursor, binding)
    limit = spec.limit
    warnings: List[str] = []
    rows: List[Dict[str, Any]] = []
    refs: List[ObjectRef] = []

    if spec.resource == "entry":
        # Collect candidate entries via workspace tracks — never raw cross-ws find.
        try:
            tracks = await Track.find({"context.workspace_id": workspace_id})
        except Exception as exc:  # noqa: BLE001
            raise QueryUnavailableError() from exc
        candidates: List[Any] = []
        retained: List[Any] = []
        total_matched = 0
        requested_sort = (spec.sort or "id").strip()
        descending = requested_sort.startswith("-")
        sort_field = requested_sort[1:] if descending else requested_sort
        if not sort_field:
            raise BadRequestError(message="sort field cannot be empty")
        capacity = offset + limit
        fields_by_type: Dict[str, List[Dict[str, Any]]] = {}

        async def fields_for(entry: Any) -> List[Dict[str, Any]]:
            type_id = str(entry.type_id or "")
            if not type_id:
                return []
            if type_id not in fields_by_type:
                from app.models.nodes import EntryType

                entry_type = await EntryType.get(type_id)
                schema = entry_type.form_schema if entry_type is not None else {}
                raw_fields = schema.get("fields") if isinstance(schema, dict) else []
                fields_by_type[type_id] = (
                    list(raw_fields) if isinstance(raw_fields, list) else []
                )
            return fields_by_type[type_id]

        # Exact string identity predicates can narrow the persisted neighbours.
        # The canonical evaluator and current target/role checks still decide
        # every returned row. Custom/computed fields stay on the existing path.
        # Empty strings and status retain canonical hydration defaults: a missing
        # persisted value may hydrate to an empty string or active status.
        stable_fields = {"id", "title", "track_id", "type_id"}
        candidate = (
            {
                f.field: f.value
                for f in spec.filters
                if f.field in stable_fields
                and f.op == "eq"
                and isinstance(f.value, str)
                and bool(f.value)
            }
            if all(f.field in stable_fields for f in spec.filters)
            and (not spec.sort or spec.sort.lstrip("-") in stable_fields)
            else {}
        )
        # Repeated field predicates must remain a conjunction; one must never
        # overwrite another and remove an otherwise valid candidate.
        if len({f.field for f in spec.filters}) != len(spec.filters):
            candidate = {}

        # Exact persisted filters with ID ordering can share page/count reads.
        # An additional per-entry target gate or custom sort needs the complete
        # admitted stream so denied rows never consume a result-page position.
        use_page = (
            target_gate is None
            and sort_field == "id"
            and len(candidate) == len(spec.filters)
        )

        async def entries_for_track(track):
            nonlocal total_matched
            if use_page:
                from app.services.permissions import get_user_accessible_entry_page

                persisted = {
                    key if key == "id" else f"context.{key}": value
                    for key, value in candidate.items()
                }
                page = await get_user_accessible_entry_page(
                    user_id,
                    track.id,
                    workspace_id=workspace_id,
                    limit=capacity,
                    candidate_query=persisted,
                    sort=[("id", -1 if descending else 1)],
                )
                total_matched += page["total"]
                for entry in page["entries"]:
                    yield entry
            else:
                async for entry in _iter_track_entries(
                    track, candidate, user_id=user_id, workspace_id=workspace_id
                ):
                    yield entry

        for track in tracks:
            if target_gate is not None and not await target_gate("track", track.id):
                continue
            if await resolve_role(user_id, "track", track.id) is None:
                continue
            if not (await generic_entry_read(track)).allowed:
                # Locked C / W3.0: no generic App-domain or inactive-App scans
                continue
            async for e in entries_for_track(track):
                if target_gate is not None and not await target_gate("entry", e.id):
                    continue
                entry_fields = await fields_for(e)
                for filt in spec.filters:
                    assert_stored_query_field(filt.field, entry_fields)
                assert_stored_query_field(spec.sort or "", entry_fields)
                ok = True
                for f in spec.filters:
                    try:
                        value = _core_value("entry", e, f.field)
                    except ValueError as exc:
                        raise BadRequestError(message=str(exc)) from exc
                    if not _filter_matches(value, op=f.op, expected=f.value):
                        ok = False
                        break
                if ok:
                    if not use_page:
                        total_matched += 1
                    try:
                        key = (_sort_key(_core_value("entry", e, sort_field)), e.id)
                    except ValueError as exc:
                        raise BadRequestError(message=str(exc)) from exc
                    # Retain only the requested offset/page while counting the
                    # complete authorized audience. The ordinal avoids comparing
                    # Node objects if a legacy source repeats the same ID.
                    bisect.insort(retained, (key, total_matched, e))
                    if len(retained) > capacity:
                        retained.pop(0 if descending else -1)

        candidates = _sort_core_nodes(
            "entry", [item[2] for item in retained], spec.sort
        )
        page = candidates[offset : offset + limit]
        if total_matched > offset + limit:
            warnings.append("result truncated by limit budget")
        for e in page:
            rows.append(
                _serialize_entry(
                    e,
                    spec.projection,
                    fields_by_type.get(str(e.type_id or "")),
                )
            )
            refs.append(
                ObjectRef(
                    kind="entry",
                    id=e.id,
                    url_path=resolve_resource_action_url(
                        "entry", e.id, track_id=e.track_id
                    ),
                    workspace_id=workspace_id,
                    title=getattr(e, "title", None),
                )
            )
        next_cursor = (
            _encode_cursor(offset + limit, binding)
            if offset + limit < total_matched
            else None
        )
        return QueryResult(
            mode="core_open",
            rows=rows,
            object_refs=refs,
            evidence=Evidence(
                object_refs=refs,
                freshness=utc_now_iso(),
                applied_scope=f"ws:{workspace_id}",
                catalogue_generation=None,
                warnings=warnings,
            ),
            cursor=next_cursor,
            total_estimate=total_matched,
            warnings=warnings,
            explain={"resource": "entry", "app_domain_tracks_skipped": True},
        )

    if spec.resource == "track":
        tracks = await Track.find({"context.workspace_id": workspace_id})
        candidates = []
        for track in tracks:
            if target_gate is not None and not await target_gate("track", track.id):
                continue
            if await resolve_role(user_id, "track", track.id) is None:
                continue
            if not (await generic_entry_read(track)).allowed:
                continue
            if _matches_core_filters("track", track, spec.filters):
                candidates.append(track)
        candidates = _sort_core_nodes("track", candidates, spec.sort)
        page = candidates[offset : offset + limit]
        page_rows = [
            _serialize_core_node("track", track, spec.projection) for track in page
        ]
        for track in page:
            refs.append(
                ObjectRef(
                    kind="track",
                    id=track.id,
                    url_path=resolve_resource_action_url("track", track.id),
                    workspace_id=workspace_id,
                    title=getattr(track, "title", None),
                )
            )
        return QueryResult(
            mode="core_open",
            rows=page_rows,
            object_refs=refs,
            evidence=Evidence(
                object_refs=refs,
                freshness=utc_now_iso(),
                applied_scope=f"ws:{workspace_id}",
            ),
            cursor=(
                _encode_cursor(offset + limit, binding)
                if offset + limit < len(candidates)
                else None
            ),
            total_estimate=len(candidates),
            explain={"resource": "track"},
        )

    if spec.resource == "app":
        apps = await App.find({"workspace_id": workspace_id})
        candidates = []
        for app in apps:
            if target_gate is not None and not await target_gate("app", app.id):
                continue
            if await resolve_role(user_id, "app", app.id) is None:
                continue
            if _matches_core_filters("app", app, spec.filters):
                candidates.append(app)
        candidates = _sort_core_nodes("app", candidates, spec.sort)
        page = candidates[offset : offset + limit]
        page_rows = [_serialize_core_node("app", app, spec.projection) for app in page]
        for app in page:
            refs.append(
                ObjectRef(
                    kind="app",
                    id=app.id,
                    url_path=resolve_resource_action_url("app", app.id),
                    workspace_id=workspace_id,
                    title=getattr(app, "name", None),
                )
            )
        return QueryResult(
            mode="core_open",
            rows=page_rows,
            object_refs=refs,
            evidence=Evidence(
                object_refs=refs,
                freshness=utc_now_iso(),
                applied_scope=f"ws:{workspace_id}",
            ),
            cursor=(
                _encode_cursor(offset + limit, binding)
                if offset + limit < len(candidates)
                else None
            ),
            total_estimate=len(candidates),
            explain={"resource": "app"},
        )

    raise BadRequestError(message=f"unsupported core resource {spec.resource!r}")


def _rows_from_output(output: Dict[str, Any]) -> List[Any]:
    """Prefer standard collections; retain a single legacy named collection."""
    for key in ("rows", "items", "entries"):
        if isinstance(output.get(key), list):
            return list(output[key])
    collections = [
        value
        for key, value in output.items()
        if key not in {"object_refs", "warnings"}
        and isinstance(value, list)
        and all(isinstance(item, dict) for item in value)
    ]
    return list(collections[0]) if len(collections) == 1 else [output]


def _refs_from_output(
    output: Dict[str, Any], *, workspace_id: str, app_id: str
) -> List[ObjectRef]:
    """Canonical local links from typed IDs, never provider-supplied URLs.

    References describe an already-governed result; they confer no authority.
    Navigation resolves the current object and permissions again.
    """
    from urllib.parse import quote

    from app.models.nodes import App, Entry, Track, Workspace

    kinds = {
        f"n.{cls._entity_name()}.": kind
        for kind, cls in (
            ("app", App),
            ("entry", Entry),
            ("track", Track),
            ("workspace", Workspace),
        )
    }
    candidates = [output]
    for value in output.values():
        if isinstance(value, dict):
            candidates.append(value)
        elif isinstance(value, list):
            candidates.extend(item for item in value if isinstance(item, dict))
    refs = []
    seen = set()
    for item in candidates:
        identity = item.get("entry_id") or item.get("id")
        if not isinstance(identity, str) or identity in seen:
            continue
        kind = next(
            (
                kind
                for prefix, kind in kinds.items()
                if identity.startswith(prefix) and len(identity) > len(prefix)
            ),
            None,
        )
        if kind is None or (item.get("kind") and item["kind"] != kind):
            continue
        if item.get("workspace_id") not in (None, "", workspace_id):
            continue
        track_id = item.get("track_id")
        path = resolve_resource_action_url(
            kind, identity, track_id=track_id if isinstance(track_id, str) else None
        )
        if path is None and kind == "entry":
            path = f"/entries/{quote(identity, safe='')}"
        seen.add(identity)
        refs.append(
            ObjectRef(
                kind=kind,
                id=identity,
                workspace_id=workspace_id,
                app_id=app_id,
                title=item.get("title") if isinstance(item.get("title"), str) else None,
                url_path=path,
            )
        )
    return refs


async def _run_declared(
    *,
    user_id: str,
    workspace_id: str,
    spec: QuerySpec,
    catalogue_generation: Optional[str],
    target_gate: Optional[TargetGate] = None,
) -> QueryResult:
    from app.services.app_queries.dispatch import invoke_app_query

    key = str(spec.capability_key or "").strip()
    app_id = str(spec.app_id or "").strip()
    if app_id and target_gate is not None and not await target_gate("app", app_id):
        raise InsufficientPermissionsError(message="Access denied")
    snap = await get_or_compile_catalogue(workspace_id)
    candidates = [
        cap
        for cap in snap.capabilities
        if cap.kind == "query"
        and cap.app_id
        and cap.availability == "active"
        and key in (cap.key, cap.capability_id)
        and (not app_id or cap.app_id == app_id)
    ]
    matches = []
    allowed_apps = {}
    for cap in candidates:
        aid = cap.app_id
        if aid not in allowed_apps:
            allowed_apps[aid] = target_gate is None or await target_gate("app", aid)
            if allowed_apps[aid] and not app_id:
                allowed_apps[aid] = await resolve_role(user_id, "app", aid) is not None
        if allowed_apps[aid]:
            matches.append(cap)
    if not matches:
        raise BadRequestError(
            message=f"declared query {key!r} not found",
            details={"error_code": "capability_not_found"},
        )
    if len(matches) != 1:
        raise BadRequestError(
            message=f"declared query {key!r} is ambiguous; pass app_id",
            details={
                "error_code": "capability_ambiguous",
                "apps": sorted({cap.app_id for cap in matches}),
            },
        )
    selected = matches[0]
    app_id = selected.app_id
    invoked = await invoke_app_query(
        user_id=user_id,
        workspace_id=workspace_id,
        app_id=app_id,
        query_key=selected.key,
        params=spec.params,
    )
    output = dict(invoked.get("output") or {})
    refs = _refs_from_output(output, workspace_id=workspace_id, app_id=app_id)
    rows = _rows_from_output(output)
    source_warnings = output.get("warnings")
    warnings = (
        [item for item in source_warnings if isinstance(item, str)]
        if isinstance(source_warnings, list)
        else []
    )
    degraded = output.get("degraded") is True or output.get("complete") is False
    if output.get("complete") is False:
        warnings.append("The source returned incomplete results.")
    return QueryResult(
        mode="declared_capability",
        rows=rows,
        object_refs=refs,
        evidence=Evidence(
            object_refs=refs,
            freshness=utc_now_iso(),
            applied_scope=f"app:{app_id}",
            catalogue_generation=catalogue_generation or snap.generation_id,
            policy_decision_id=invoked.get("policy_decision_id"),
            policy_revision=invoked.get("policy_revision"),
            package_slug=selected.owner_package,
            warnings=warnings,
        ),
        cursor=output.get("next_cursor"),
        total_estimate=(
            output["total_estimate"]
            if "total_estimate" in output
            else (
                output["total"]
                if "total" in output
                else None if degraded else len(rows)
            )
        ),
        degraded=degraded,
        warnings=warnings,
        explain={"capability_key": selected.key, "app_id": app_id},
    )


async def execute_query(
    *,
    user_id: str,
    workspace_id: str,
    spec: QuerySpec,
    catalogue_generation: Optional[str] = None,
    target_gate: Optional[TargetGate] = None,
) -> QueryResult:
    """Dispatch a query, applying a trusted execution gate before selection.

    The gate is a server-side constraint, never a QuerySpec field. It must run
    before pagination and aggregates so excluded targets cannot affect counts,
    cursors, ambiguity responses, or handler selection.
    """
    if not workspace_id:
        raise BadRequestError(message="no active workspace")
    ws_role = await can_access_workspace(user_id, workspace_id)
    if ws_role == "none":
        raise InsufficientPermissionsError(message="Access denied")

    if catalogue_generation:
        # Refresh definitions before accepting a supplied generation, including
        # after process restart. A cache miss isn't permission to skip it.
        await get_or_compile_catalogue(workspace_id)
    require_generation(workspace_id, catalogue_generation)
    if spec.retrieval_mode != "deterministic":
        raise BadRequestError(
            message="Ranked retrieval is not supported by structured QuerySpec; use the retrieval service",
            details={"error_code": "unsupported_retrieval_mode"},
        )

    if spec.mode == "declared_capability":
        return await _run_declared(
            user_id=user_id,
            workspace_id=workspace_id,
            spec=spec,
            catalogue_generation=catalogue_generation,
            target_gate=target_gate,
        )
    if spec.mode == "core_open":
        # Hard reject filters that name App package entry types via resource misuse
        return await _run_core_open(
            user_id=user_id,
            workspace_id=workspace_id,
            spec=spec,
            target_gate=target_gate,
        )
    raise BadRequestError(message=f"unknown query mode {spec.mode!r}")
