"""Governed query engine — QuerySpec v1 locked C (ADR-012)."""

from __future__ import annotations

import base64
import json
from datetime import date, datetime
from numbers import Real
from typing import Any, Dict, List, Optional

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
from app.services.permissions import resolve_role
from app.services.query_filters import entry_field_value, filter_matches
from app.services.workspace_permissions import can_access_workspace
from app.utils.time import utc_now_iso


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"o": offset}).encode()).decode()


def _decode_cursor(cursor: Optional[str]) -> int:
    if not cursor:
        return 0
    try:
        raw = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        return max(0, int(raw.get("o") or 0))
    except Exception:  # noqa: BLE001
        return 0


def _entry_type_key(entry_type) -> str:
    if entry_type is None:
        return ""
    manifest_key = str(
        (getattr(entry_type, "form_schema", None) or {}).get("_manifest_entry_type_key")
        or ""
    ).strip()
    from app.services.operational_model_compile import _slug

    return _slug(manifest_key or getattr(entry_type, "name", "") or "")


async def _parent_app_for_track(track) -> Any:
    from app.models.edges import CONTAINS

    try:
        parents = await track.nodes(
            edge=[CONTAINS], node=["WorkspaceApp"], direction="in", limit=4
        )
    except Exception:  # noqa: BLE001
        return None
    return parents[0] if parents else None


async def _track_is_app_domain(track) -> bool:
    """True when track hangs under an installed packaged App."""
    app = await _parent_app_for_track(track)
    if app is None:
        return False
    slug = str(getattr(app, "installed_package_slug", "") or "").strip()
    return bool(slug)


def _serialize_entry(entry, projection: List[str]) -> Dict[str, Any]:
    cf = getattr(entry, "custom_fields", None) or {}
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


async def _track_entries(track) -> List[Any]:
    """Read a track completely through keyset pagination, never a fixed cap."""
    from app.models.nodes import Entry

    cursor: Optional[str] = None
    entries: List[Any] = []
    while True:
        page, cursor = await track.nodes_page(
            edge=["CONTAINS"], direction="out", node=["Entry"], cursor=cursor, limit=200
        )
        entries.extend(item for item in page if isinstance(item, Entry))
        if not cursor:
            return entries


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
) -> QueryResult:
    from app.models.nodes import App, Track

    if spec.max_depth > 2:
        raise BadRequestError(
            message="max_depth exceeds budget",
            details={"error_code": "query_budget_depth"},
        )

    offset = _decode_cursor(spec.cursor)
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
        for track in tracks:
            if await resolve_role(user_id, "track", track.id) is None:
                continue
            if await _track_is_app_domain(track):
                # Locked C: no generic App-domain open scans
                continue
            try:
                entries = await _track_entries(track)
            except Exception as exc:  # noqa: BLE001
                raise QueryUnavailableError() from exc
            for e in entries:
                if await resolve_role(user_id, "entry", e.id) is None:
                    continue
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
                    candidates.append(e)

        candidates = _sort_core_nodes("entry", candidates, spec.sort)
        page = candidates[offset : offset + limit]
        if len(candidates) > offset + limit:
            warnings.append("result truncated by limit budget")
        for e in page:
            rows.append(_serialize_entry(e, spec.projection))
            refs.append(
                ObjectRef(
                    kind="entry",
                    id=e.id,
                    workspace_id=workspace_id,
                    title=getattr(e, "title", None),
                )
            )
        next_cursor = (
            _encode_cursor(offset + limit) if offset + limit < len(candidates) else None
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
            total_estimate=len(candidates),
            warnings=warnings,
            explain={"resource": "entry", "app_domain_tracks_skipped": True},
        )

    if spec.resource == "track":
        tracks = await Track.find({"context.workspace_id": workspace_id})
        candidates = []
        for track in tracks:
            if await resolve_role(user_id, "track", track.id) is None:
                continue
            if await _track_is_app_domain(track):
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
                _encode_cursor(offset + limit)
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
                _encode_cursor(offset + limit)
                if offset + limit < len(candidates)
                else None
            ),
            total_estimate=len(candidates),
            explain={"resource": "app"},
        )

    raise BadRequestError(message=f"unsupported core resource {spec.resource!r}")


def _refs_from_output(
    output: Dict[str, Any], *, workspace_id: str, app_id: str
) -> List[ObjectRef]:
    refs: List[ObjectRef] = []
    for key in ("expiring_assets", "assets"):
        for item in output.get(key) or []:
            if not isinstance(item, dict):
                continue
            eid = str(item.get("entry_id") or item.get("id") or "").strip()
            if eid:
                refs.append(
                    ObjectRef(
                        kind="entry",
                        id=eid,
                        workspace_id=workspace_id,
                        app_id=app_id,
                        title=item.get("title"),
                    )
                )
    asset = output.get("asset")
    if isinstance(asset, dict) and asset.get("entry_id"):
        refs.append(
            ObjectRef(
                kind="entry",
                id=str(asset["entry_id"]),
                workspace_id=workspace_id,
                app_id=app_id,
                title=asset.get("title"),
            )
        )
    return refs


async def _run_declared(
    *,
    user_id: str,
    workspace_id: str,
    spec: QuerySpec,
    catalogue_generation: Optional[str],
) -> QueryResult:
    from app.services.app_queries.dispatch import invoke_app_query
    from app.services.app_queries.registry import all_workspace_query_apps

    key = str(spec.capability_key or "").strip()
    # Allow namespace.key or bare key
    bare = key.split(".")[-1] if "." in key else key
    app_id = str(spec.app_id or "").strip()

    if not app_id:
        # Resolve unique app that owns this query key
        matches = []
        for aid, bucket in all_workspace_query_apps(workspace_id).items():
            if bare in bucket or key in bucket:
                matches.append(aid)
        if len(matches) == 1:
            app_id = matches[0]
        elif len(matches) == 0:
            raise BadRequestError(
                message=f"declared query {key!r} not found",
                details={"error_code": "capability_not_found"},
            )
        else:
            raise BadRequestError(
                message=f"declared query {key!r} is ambiguous; pass app_id",
                details={"error_code": "capability_ambiguous", "apps": matches},
            )

    # Reject attempting to use core_open semantics against app records via fake keys
    invoked = await invoke_app_query(
        user_id=user_id,
        workspace_id=workspace_id,
        app_id=app_id,
        query_key=bare,
        params=spec.params,
    )
    output = dict(invoked.get("output") or {})
    refs = _refs_from_output(output, workspace_id=workspace_id, app_id=app_id)
    rows = []
    if "expiring_assets" in output:
        rows = list(output.get("expiring_assets") or [])
    elif "assets" in output:
        rows = list(output.get("assets") or [])
    else:
        rows = [output]

    snap = await get_or_compile_catalogue(workspace_id)
    return QueryResult(
        mode="declared_capability",
        rows=rows,
        object_refs=refs,
        evidence=Evidence(
            object_refs=refs,
            freshness=utc_now_iso(),
            applied_scope=f"ws:{workspace_id}",
            catalogue_generation=catalogue_generation or snap.generation_id,
            policy_decision_id=invoked.get("policy_decision_id"),
            package_slug=None,
            warnings=[],
        ),
        total_estimate=len(rows),
        explain={"capability_key": bare, "app_id": app_id},
    )


async def execute_query(
    *,
    user_id: str,
    workspace_id: str,
    spec: QuerySpec,
    catalogue_generation: Optional[str] = None,
) -> QueryResult:
    """Dispatch a QuerySpec to declared-capability or Core open mode."""
    if not workspace_id:
        raise BadRequestError(message="no active workspace")
    ws_role = await can_access_workspace(user_id, workspace_id)
    if ws_role == "none":
        raise InsufficientPermissionsError(message="Access denied")

    require_generation(workspace_id, catalogue_generation)

    if spec.mode == "declared_capability":
        return await _run_declared(
            user_id=user_id,
            workspace_id=workspace_id,
            spec=spec,
            catalogue_generation=catalogue_generation,
        )
    if spec.mode == "core_open":
        # Hard reject filters that name App package entry types via resource misuse
        return await _run_core_open(
            user_id=user_id, workspace_id=workspace_id, spec=spec
        )
    raise BadRequestError(message=f"unknown query mode {spec.mode!r}")
