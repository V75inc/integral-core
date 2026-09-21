"""Governed query engine — QuerySpec v1 locked C (ADR-012)."""

from __future__ import annotations

import base64
import json
from typing import Any, Dict, List, Optional

from app.api.errors import BadRequestError, InsufficientPermissionsError
from app.schemas.capabilities import Evidence, ObjectRef
from app.schemas.governed_query import QueryResult, QuerySpec
from app.services.capability_catalogue.compile import (
    get_or_compile_catalogue,
    require_generation,
)
from app.services.permissions import resolve_role
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


async def _run_core_open(
    *,
    user_id: str,
    workspace_id: str,
    spec: QuerySpec,
) -> QueryResult:
    from app.models.nodes import App, Entry, Track

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
        tracks = await Track.find({"context.workspace_id": workspace_id})
        candidates: List[Any] = []
        for track in tracks:
            if await resolve_role(user_id, "track", track.id) is None:
                continue
            if await _track_is_app_domain(track):
                # Locked C: no generic App-domain open scans
                continue
            try:
                entries = await track.nodes(
                    edge=["CONTAINS"], direction="out", node=["Entry"], limit=500
                )
            except Exception:  # noqa: BLE001
                continue
            for e in entries:
                if not isinstance(e, Entry):
                    continue
                if await resolve_role(user_id, "entry", e.id) is None:
                    continue
                # Apply simple filters
                ok = True
                cf = getattr(e, "custom_fields", None) or {}
                for f in spec.filters:
                    if f.field == "track_id" and f.op == "eq":
                        if str(getattr(e, "track_id", "")) != str(f.value):
                            ok = False
                    elif f.field == "title" and f.op == "eq":
                        if str(getattr(e, "title", "")) != str(f.value):
                            ok = False
                    elif f.field.startswith("custom_fields."):
                        key = f.field.split(".", 1)[1]
                        val = cf.get(key)
                        if (f.op == "eq" and str(val) != str(f.value)) or (
                            f.op == "exists"
                            and (
                                (f.value and val is None)
                                or (not f.value and val is not None)
                            )
                        ):
                            ok = False
                if ok:
                    candidates.append(e)

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
        page_rows = []
        for track in tracks:
            if await resolve_role(user_id, "track", track.id) is None:
                continue
            if await _track_is_app_domain(track):
                continue
            page_rows.append(
                {
                    "id": track.id,
                    "kind": "track",
                    "title": getattr(track, "title", "") or "",
                }
            )
            refs.append(
                ObjectRef(
                    kind="track",
                    id=track.id,
                    workspace_id=workspace_id,
                    title=getattr(track, "title", None),
                )
            )
        sliced = page_rows[offset : offset + limit]
        return QueryResult(
            mode="core_open",
            rows=sliced,
            object_refs=refs[offset : offset + limit],
            evidence=Evidence(
                object_refs=refs[offset : offset + limit],
                freshness=utc_now_iso(),
                applied_scope=f"ws:{workspace_id}",
            ),
            total_estimate=len(page_rows),
            explain={"resource": "track"},
        )

    if spec.resource == "app":
        apps = await App.find({"workspace_id": workspace_id})
        page_rows = []
        for app in apps:
            if await resolve_role(user_id, "app", app.id) is None:
                continue
            page_rows.append(
                {
                    "id": app.id,
                    "kind": "app",
                    "title": getattr(app, "title", "") or "",
                    "lifecycle_state": getattr(app, "lifecycle_state", None),
                    "package_slug": getattr(app, "installed_package_slug", None),
                }
            )
            refs.append(
                ObjectRef(
                    kind="app",
                    id=app.id,
                    workspace_id=workspace_id,
                    title=getattr(app, "title", None),
                )
            )
        sliced = page_rows[offset : offset + limit]
        return QueryResult(
            mode="core_open",
            rows=sliced,
            object_refs=refs[offset : offset + limit],
            evidence=Evidence(
                object_refs=refs[offset : offset + limit],
                freshness=utc_now_iso(),
                applied_scope=f"ws:{workspace_id}",
            ),
            total_estimate=len(page_rows),
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
