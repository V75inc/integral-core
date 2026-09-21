"""App CRUD API endpoints with collaborator and track management."""

import logging
from typing import Any, Dict, List, Optional

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    MissingAuthenticationError,
    ResourceNotFoundError,
)
from app.api.utils import export_node, public_user_view, resolve_principal_id
from app.api.validators import (
    validate_app_visibility_workspace,
)
from app.api.validators_common import (
    compute_fold,
    non_empty_after_strip,
    validate_hex_color,
)
from app.models.edges import (
    COLLABORATES_ON,
    CONTAINS,
    DEFINES_TRACK_PROFILE,
)
from app.models.nodes import (
    App,
    ApplicationDefinition,
    EntryType,
    OperationalModel,
    Tag,
    Track,
    View,
    Workspace,
)
from app.schemas.policy import Resource, Subject
from app.services.app_graph import (
    get_app_attached_operational_model,
    get_or_create_views_registry_for_operational_model,
)
from app.services.change_event import emit_change_event
from app.services.entry_context import (
    attach_anchor_source_to_track_data,
    attach_operational_model_defaults_to_track_data,
)
from app.services.notification_paths import resolve_resource_action_url
from app.services.operational_model_merge import (
    merge_library_manifest_into_operational_model,
    merge_template_operational_model_into_track,
    provision_prescribed_tracks_from_app_manifest,
    verify_track_template_in_app,
)
from app.services.operational_model_runtime import (
    compile_canonical_manifest,
    invalidate_manifest_cache,
    normalize_entry_type_form_schema,
    normalize_view_config,
)
from app.services.ownership_transfer import transfer_app_ownership
from app.services.permissions import (
    can_create_app_under_workspace,
    get_user_accessible_apps,
    get_user_node,
)
from app.services.policy_engine import evaluate as policy_evaluate
from app.services.sharing import add_collaborator as sharing_add_collaborator
from app.services.sharing import remove_collaborator as sharing_remove_collaborator
from app.services.sharing import (
    update_collaborator_role as sharing_update_collaborator_role,
)
from app.services.uniqueness import assert_unique
from app.utils.time import utc_now_iso
from app.views import operational_model_view_types as _view_type_registry

logger = logging.getLogger(__name__)


def _allowed_view_types() -> set:
    """Snapshot of registered view types (built-ins + plugins)."""
    return set(_view_type_registry.allowed_keys())


@endpoint("/apps", methods=["GET"], auth=True, tags=["Apps"])
async def list_apps(
    request: Request,
    cursor: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """List Apps the user can access (owned, collaborator, public, org-visible)."""
    from app.models.nodes import App
    from app.services.pagination import DEFAULT_ENTITY_SORT, paginate_nodes_by_ids

    user_id = resolve_principal_id(request)
    if not user_id:
        # Peer handlers raise here. These four used to return an empty
        # collection. auth=True means an authenticated request whose
        # principal still failed to resolve — surface it (see
        # test_list_endpoints_auth_envelope).
        raise MissingAuthenticationError(message="Authentication required")
    if not await get_user_node(user_id):
        # Same empty-page footgun as an unresolvable principal: do not
        # answer 200 with {apps: []} for a consistency failure.
        raise MissingAuthenticationError(message="Authentication required")

    result = await get_user_accessible_apps(user_id)
    result = [
        s
        for s in result
        if str(getattr(s, "lifecycle_state", "") or "active") == "active"
    ]
    # W5: server-enforce the active workspace scope from
    # X-Integral-Scope (fail-closed → user's Personal Workspace).
    from app.services.request_scope import (
        matches_workspace,
        resolve_workspace_id_from_request,
    )

    target_ws = await resolve_workspace_id_from_request(request, user_id)
    if target_ws:
        result = [s for s in result if matches_workspace(s, target_ws)]
    # Phase 36 — workspace-scoped order: sort by App.position (asc,
    # null-last) so PATCH /workspaces/{id}/apps/order is the source of
    # truth for the listing order. Across-workspace listings (no
    # target_ws) fall back to recency since position only makes sense
    # within one workspace.
    if target_ws:
        app_sort = [
            ("context.position", 1),
            ("context.updated_at", -1),
            ("id", -1),
        ]
    else:
        app_sort = list(DEFAULT_ENTITY_SORT)

    app_ids = [s.id for s in result]
    page_apps, response = await paginate_nodes_by_ids(
        App,
        app_ids,
        cursor,
        limit,
        sort=app_sort,
        include_total=True,
    )
    response["apps"] = [
        {
            **(await export_node(s)),
            "action_url": resolve_resource_action_url("app", s.id),
        }
        for s in page_apps
    ]
    response["scope_workspace_id"] = target_ws
    return response


def _iso_to_sort_key(iso: str) -> int:
    """Return an integer derivable from an ISO timestamp for stable sort.

    The list sort needs a numeric tie-breaker; ISO 8601 strings sort
    lexicographically but combining with the position int requires
    matching types. Convert by stripping the non-digit chars; falls
    back to 0 on empty.
    """
    if not iso:
        return 0
    digits = "".join(c for c in iso if c.isdigit())
    try:
        return int(digits[:14] or 0)
    except ValueError:
        return 0


@endpoint("/apps", methods=["POST"], auth=True, tags=["Apps"])
async def create_app(
    request: Request,
    name: str = "",
    description: Optional[str] = None,
    workspace_id: Optional[str] = None,
    visibility: Optional[str] = None,
    library_operational_model_id: Optional[str] = None,
    accent_color: Optional[str] = None,
    type_hint: Optional[str] = None,
    include_seed_data: bool = True,
) -> Dict[str, Any]:
    """Create a new App owned by the current user inside a Workspace.

    Phase 6 Plan 06-04 (MCP-04 AC#5): ``type_hint`` resolves a free-text
    hint via ``resolve_type_hint`` (direct service call — Pitfall 6: no MCP
    recursion). Same rules as ``create_track``: zero-match → warning;
    tied top score → 400 disambiguation; single best match → adopt as
    ``library_operational_model_id``. Mutually exclusive with explicit
    ``library_operational_model_id``.

    Phase 9 Plan 09-05 (B4): the post-auth creation body now lives in
    ``app/services/space_service.py::create_app_for_user`` so agentive
    tools can drive App creation in-process (no HTTP self-call, no MCP
    recursion). This handler is the thin auth + request-parsing wrapper
    that resolves ``type_hint`` and formats the response.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    # ---- Phase 6 Plan 06-04 — type_hint resolution ----
    type_hint_warning: Optional[str] = None
    if type_hint:
        if library_operational_model_id:
            raise BadRequestError(
                message=("type_hint cannot combine with explicit picker fields"),
                details={
                    "error_code": "operational_model.conflicting_picker",
                },
            )
        from app.api.operational_models import resolve_type_hint

        matches = await resolve_type_hint(type_hint)
        if not matches:
            type_hint_warning = (
                "type_hint did not resolve to any library package; "
                "using default profile"
            )
        elif len(matches) > 1 and matches[0]["score"] == matches[1]["score"]:
            raise BadRequestError(
                message="type_hint matches multiple library packages",
                details={
                    "error_code": "operational_model.type_hint_ambiguous",
                    "candidates": matches[:10],
                },
            )
        else:
            library_operational_model_id = matches[0]["operational_model_id"]

    from app.services.app_service import (
        create_app_for_user,
        create_app_response_payload,
    )

    sp = await create_app_for_user(
        user_id=user_id,
        name=name,
        description=description,
        workspace_id=workspace_id,
        visibility=visibility,
        library_package_id=library_operational_model_id,
        accent_color=accent_color,
        include_seed_data=include_seed_data,
    )

    return await create_app_response_payload(sp, type_hint_warning=type_hint_warning)


@endpoint("/apps/{app_id}", methods=["GET"], auth=True, tags=["Apps"])
async def get_app(request: Request, app_id: str) -> Dict[str, Any]:
    """Get an App by ID.

    F1: also returns ``operations`` from the attached Operational Model
    (``app.operations[]``) so admins can see named authority surfaces.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    sp = await App.get(app_id)
    if not sp:
        raise ResourceNotFoundError(message="App not found")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.read",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")
    payload: Dict[str, Any] = {"app": await export_node(sp)}
    try:
        cp = await get_app_attached_operational_model(sp)
        if cp is not None:
            from app.services.operational_model_runtime import (
                compile_canonical_manifest,
            )

            canonical = compile_canonical_manifest(manifest=cp.manifest or {})
            ops = (canonical.get("app") or {}).get("operations") or []
            if isinstance(ops, list):
                payload["operations"] = ops
    except Exception:  # noqa: BLE001
        logger.debug("get_app: operations extract failed for %s", app_id, exc_info=True)
    return payload


@endpoint(
    "/apps/{app_id}/definition",
    methods=["GET"],
    auth=True,
    tags=["Apps"],
)
async def get_app_definition(request: Request, app_id: str) -> Dict[str, Any]:
    """Return the active, compiler-validated App contract revision."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    app_node = await App.get(app_id)
    if app_node is None:
        raise ResourceNotFoundError(message="App not found")
    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.read",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")
    definition_id = str(getattr(app_node, "active_definition_id", "") or "")
    definition = (
        await ApplicationDefinition.get(definition_id) if definition_id else None
    )
    if definition is None:
        raise ResourceNotFoundError(message="Active application definition not found")
    return {
        "app_id": app_id,
        "definition": await export_node(definition),
    }


@endpoint(
    "/apps/{app_id}/definition/verify",
    methods=["POST"],
    auth=True,
    tags=["Apps"],
)
async def verify_app_definition(request: Request, app_id: str) -> Dict[str, Any]:
    """Refresh persisted evidence for the active App contract.

    Verification is an explicit write, rather than a side effect of the
    definition read endpoint. It lets an App owner reconcile durable evidence
    after a runtime or dependency change without minting a new revision.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    app_node = await App.get(app_id)
    if app_node is None:
        raise ResourceNotFoundError(message="App not found")
    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.update",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")
    definition_id = str(getattr(app_node, "active_definition_id", "") or "")
    definition = (
        await ApplicationDefinition.get(definition_id) if definition_id else None
    )
    if definition is None:
        raise ResourceNotFoundError(message="Active application definition not found")
    from app.services.application_definitions import verify_definition_materialization

    verification = await verify_definition_materialization(
        app_node=app_node,
        definition=definition,
    )
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="app.definition_verified",  # type: ignore[arg-type]
        resource_type="App",
        resource_id=app_id,
        before=None,
        after={"active_definition_id": definition.id},
        scope=f"app:{app_id}",
        details={"verification": verification},
    )
    return {
        "app_id": app_id,
        "definition": await export_node(definition),
        "verification": verification,
    }


@endpoint(
    "/apps/{app_id}/definition/preview",
    methods=["GET"],
    auth=True,
    tags=["Apps"],
)
async def preview_app_definition(request: Request, app_id: str) -> Dict[str, Any]:
    """Preview attached-profile drift against the active App definition.

    This endpoint is deliberately read-only. It helps an author decide whether
    the current draft-shaped profile needs a new authorized definition revision.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    app_node, attached_profile = await _require_app_attached_cp(app_id)
    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.read",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")
    definition_id = str(getattr(app_node, "active_definition_id", "") or "")
    definition = (
        await ApplicationDefinition.get(definition_id) if definition_id else None
    )
    if definition is None:
        raise ResourceNotFoundError(message="Active application definition not found")
    from app.services.application_definitions import preview_application_definition

    return {
        "app_id": app_id,
        "active_definition_id": definition.id,
        "preview": preview_application_definition(
            before_manifest=definition.canonical_manifest,
            candidate_manifest=attached_profile.manifest or {},
        ),
    }


@endpoint("/apps/{app_id}/export", methods=["GET"], auth=True, tags=["Apps"])
async def export_app(request: Request, app_id: str) -> Dict[str, Any]:
    """Generic Core JSON export of App data (active or paused — entitlement loss)."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.read",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")
    from app.services.app_export import export_app_bundle

    return await export_app_bundle(app_id=app_id)


@endpoint("/apps/{app_id}", methods=["PUT"], auth=True, tags=["Apps"])
async def update_app(
    request: Request,
    app_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    visibility: Optional[str] = None,
    accent_color: Optional[str] = None,
) -> Dict[str, Any]:
    """Update an App (editor or owner only).

    ``workspace_id`` is immutable after create — App → Workspace transfer
    is a separate feature (deferred).
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.update",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    sp = await App.get(app_id)
    if not sp:
        raise ResourceNotFoundError(message="App not found")

    prior_snapshot = await export_node(sp)  # D-03 before-snapshot

    if name is not None:
        safe_name = non_empty_after_strip(name, "name")
        if len(safe_name) > 200:
            raise BadRequestError(message="name must be 200 characters or fewer")
        new_fold = compute_fold(safe_name)
        if new_fold != (getattr(sp, "name_fold", "") or ""):
            await assert_unique(
                App,
                {
                    "context.workspace_id": sp.workspace_id,
                    "context.name_fold": new_fold,
                },
                entity="app",
                field_label="name",
                value=safe_name,
                scope_label="in this workspace",
                exclude_id=sp.id,
            )
        sp.name = safe_name
        sp.name_fold = new_fold
    if description is not None:
        sp.description = description
    if accent_color is not None:
        sp.accent_color = validate_hex_color(accent_color, allow_empty=True)
    if visibility is not None:
        v = visibility.strip()
        if v == "inherit":
            from app.services.workspace_kind import workspace_is_collaborative

            workspace_id = sp.workspace_id
            ws = await Workspace.get(workspace_id) if workspace_id else None
            sp.visibility = "workspace" if workspace_is_collaborative(ws) else "private"
        else:
            if v not in ("private", "workspace", "public"):
                raise BadRequestError(
                    message="visibility must be private, workspace, public, or inherit",
                )
            sp.visibility = v
    vis = getattr(sp, "visibility", None) or "private"
    await validate_app_visibility_workspace(vis, sp.workspace_id)
    sp.updated_at = utc_now_iso()
    await sp.save()

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="app.update",
        resource_type="App",
        resource_id=sp.id,
        before=prior_snapshot,
        after=await export_node(sp),
        scope=f"app:{sp.id}",
    )

    return {
        "app": await export_node(sp),
        "message": "App updated successfully",
    }


@endpoint("/apps/{app_id}", methods=["DELETE"], auth=True, tags=["Apps"])
async def delete_app(request: Request, app_id: str) -> Dict[str, Any]:
    """Delete an App (owner only) and cascade contained tracks and App operational model.

    Tracks that are also linked to another App are unlinked from this App only.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.delete",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Only the App owner can delete it")

    sp = await App.get(app_id)
    if not sp:
        raise ResourceNotFoundError(message="App not found")

    # Library bundle Apps uninstall via the canonical path (I-APP-06).
    if getattr(sp, "installed_from_library_id", None):
        from app.services.app_lifecycle import uninstall_app

        return await uninstall_app(
            app_id=app_id,
            actor_id=user_id,
            force=True,
            archive=True,
        )

    prior_snapshot = await export_node(sp)  # D-03 before-snapshot
    # Not ``delete_app_cascade`` directly: a non-library App can still carry
    # live bundle registrations (``sync_operational_layer_from_manifest``
    # registers hooks/tools for Apps with no ``installed_from_library_id``),
    # which would keep dispatching for a deleted App until restart.
    from app.services.app_lifecycle import purge_app_with_bundle_teardown

    deleted_tracks, unlinked_tracks = await purge_app_with_bundle_teardown(sp)

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="app.delete",
        resource_type="App",
        resource_id=app_id,
        before=prior_snapshot,
        after=None,
        scope=f"app:{app_id}",
    )

    return {
        "message": "App deleted successfully",
        "deleted_app_id": app_id,
        "deleted_tracks": deleted_tracks,
        "unlinked_tracks": unlinked_tracks,
    }


@endpoint(
    "/apps/{app_id}/tracks",
    methods=["GET"],
    auth=True,
    tags=["Apps"],
)
async def list_app_tracks(
    request: Request,
    app_id: str,
) -> Dict[str, Any]:
    """List all Tracks in an App."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.read",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    sp = await App.get(app_id)
    if not sp:
        raise ResourceNotFoundError(message="App not found")

    tracks, _next = await sp.nodes_page(
        edge=["CONTAINS"],
        node=["Track"],
        limit=500,
    )
    track_ids = [t.id for t in tracks if getattr(t, "id", None)]
    entry_counts: Dict[str, int] = {}
    track_positions: Dict[str, int] = {}
    if track_ids:
        import asyncio

        from app.models.edges import CONTAINS

        ctx = await sp.get_context()

        async def _count_for_track(tid: str) -> tuple:
            c = await ctx.database.count(
                "node", {"entity": "Entry", "context.track_id": tid}
            )
            return (tid, c)

        async def _position_for_track(tid: str) -> tuple:
            edges = await ctx.find_edges_between(sp.id, tid, edge_class=CONTAINS)
            pos: Optional[int] = None
            for e in edges:
                p = getattr(e, "position", None)
                if isinstance(p, int):
                    pos = p
                    break
            return (tid, pos)

        count_results, pos_results = await asyncio.gather(
            asyncio.gather(*[_count_for_track(tid) for tid in track_ids]),
            asyncio.gather(*[_position_for_track(tid) for tid in track_ids]),
        )
        entry_counts = dict(count_results)
        track_positions = {tid: p for tid, p in pos_results if p is not None}

    # Phase 34 — order by CONTAINS edge position (asc). Tracks without a
    # position default to a large sentinel so they sort to the end;
    # ties break by created_at (then by id) so the ordering stays
    # stable across reads.
    sentinel = 1_000_000_000
    sorted_tracks = sorted(
        tracks,
        key=lambda t: (
            track_positions.get(t.id, sentinel),
            getattr(t, "created_at", "") or "",
            getattr(t, "id", "") or "",
        ),
    )

    items = []
    for t in sorted_tracks:
        row = await export_node(t)
        row["entry_count"] = entry_counts.get(t.id, 0)
        row["position"] = track_positions.get(t.id)
        await attach_operational_model_defaults_to_track_data(row, t)
        await attach_anchor_source_to_track_data(row, t)
        items.append(row)
    return {"tracks": items, "total": len(items), "app_id": app_id}


@endpoint(
    "/apps/{app_id}/tracks",
    methods=["POST"],
    auth=True,
    tags=["Apps"],
)
async def add_track_to_app(
    request: Request,
    app_id: str,
    track_id: str = "",
) -> Dict[str, Any]:
    """Add an existing Track to an App (editor or owner only)."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.update",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    sp = await App.get(app_id)
    if not sp:
        raise ResourceNotFoundError(message="App not found")

    track = await Track.get(track_id)
    if not track:
        raise ResourceNotFoundError(message="Track not found")

    prior_snapshot = await export_node(sp)  # D-03 before-snapshot
    ctx = await sp.get_context()
    existing = await ctx.find_edges_between(sp.id, track.id, edge_class=CONTAINS)
    if not existing:
        await sp.connect(track, edge=CONTAINS, added_at=utc_now_iso())

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="app.update",
        resource_type="App",
        resource_id=sp.id,
        before=prior_snapshot,
        after=await export_node(sp),
        scope=f"app:{sp.id}",
    )

    return {
        "message": "Track added to App",
        "app_id": app_id,
        "track_id": track_id,
    }


@endpoint(
    "/apps/{app_id}/tracks/order",
    methods=["PATCH"],
    auth=True,
    tags=["Apps"],
)
async def reorder_app_tracks(
    request: Request,
    app_id: str,
    track_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Phase 34 — set the display order of Tracks under an App.

    Body shape::

        {"track_ids": ["n.Track.A", "n.Track.B", ...]}

    Updates the ``position`` field on each ``App-CONTAINS-Track`` edge
    so subsequent ``GET /apps/{id}/tracks`` returns them in this order.
    The provided list does not need to contain every Track under the
    App — any unspecified Track keeps its existing position (and sorts
    after the explicit set in the listing endpoint).

    Editor + owner only.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.update",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    if track_ids is None:
        track_ids = []
    if not isinstance(track_ids, list):
        raise BadRequestError(message="track_ids must be a list")

    sp = await App.get(app_id)
    if not sp:
        raise ResourceNotFoundError(message="App not found")

    from app.models.edges import CONTAINS

    ctx = await sp.get_context()
    # Build a {track_id → set of CONTAINS edges from this app} map so
    # we can write the position on the exact edge that anchors the
    # track to this App. (Anchored child tracks may have multiple
    # CONTAINS in-edges; we update only the App→Track edge.)
    updated: List[str] = []
    skipped: List[str] = []
    for idx, tid in enumerate(track_ids):
        edges = await ctx.find_edges_between(sp.id, tid, edge_class=CONTAINS)
        if not edges:
            skipped.append(tid)
            continue
        for e in edges:
            e.position = idx
            await e.save()
        updated.append(tid)

    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="app.tracks_reorder",
        resource_type="App",
        resource_id=sp.id,
        before=None,
        after={"track_ids": track_ids, "updated": updated, "skipped": skipped},
        scope=f"app:{sp.id}",
    )

    return {
        "message": "Tracks reordered",
        "app_id": app_id,
        "updated": updated,
        "skipped": skipped,
    }


@endpoint(
    "/apps/{app_id}/tracks/{track_id}",
    methods=["DELETE"],
    auth=True,
    tags=["Apps"],
)
async def remove_track_from_app(
    request: Request,
    app_id: str,
    track_id: str,
) -> Dict[str, Any]:
    """Remove a Track from an App (editor or owner only)."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.update",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    sp = await App.get(app_id)
    if not sp:
        raise ResourceNotFoundError(message="App not found")

    track = await Track.get(track_id)
    if not track:
        raise ResourceNotFoundError(message="Track not found")

    prior_snapshot = await export_node(sp)  # D-03 before-snapshot
    ctx = await sp.get_context()
    edges = await ctx.find_edges_between(sp.id, track.id, edge_class=CONTAINS)
    for edge in edges:
        await edge.delete()

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="app.update",
        resource_type="App",
        resource_id=sp.id,
        before=prior_snapshot,
        after=await export_node(sp),
        scope=f"app:{sp.id}",
    )

    return {
        "message": "Track removed from App",
        "app_id": app_id,
        "track_id": track_id,
    }


@endpoint(
    "/apps/{app_id}/collaborators",
    methods=["GET"],
    auth=True,
    tags=["Apps"],
)
async def list_app_collaborators(request: Request, app_id: str) -> Dict[str, Any]:
    """List direct collaborators for an App.

    Legacy surface (direct + owner only). Prefer ``GET /apps/{id}/access`` for
    unified permission buckets via ``services/sharing.list_access``.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.read",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    sp = await App.get(app_id)
    if not sp:
        raise ResourceNotFoundError(message="App not found")

    collaborators = await sp.nodes(
        edge=["COLLABORATES_ON"],
        direction="in",
        node=["User"],
        limit=500,
    )
    owner_node = await get_user_node(sp.owner_user_id) if sp.owner_user_id else None

    role_by_user: Dict[str, str] = {}
    if collaborators:
        ctx = await sp.get_context()
        collab_edges = await ctx.find_edges_between(
            None, app_id, edge_class=COLLABORATES_ON
        )
        for ce in collab_edges:
            role_by_user[ce.source] = getattr(ce, "role", "viewer")

    result = []
    if owner_node:
        # Never full-export User — preferences hold reset/OTP hashes.
        owner_data = await public_user_view(owner_node)
        owner_data["role"] = "owner"
        result.append(owner_data)

    for collab in collaborators:
        data = await public_user_view(collab)
        data["role"] = role_by_user.get(collab.id, "viewer")
        result.append(data)

    return {
        "collaborators": result,
        "total": len(result),
        "app_id": app_id,
    }


@endpoint(
    "/apps/{app_id}/collaborators",
    methods=["POST"],
    auth=True,
    tags=["Apps"],
)
async def add_app_collaborator(
    request: Request,
    app_id: str,
    collaborator_user_id: str,
    role: str = "editor",
) -> Dict[str, Any]:
    """Add a collaborator to an App (owner only)."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    result = await sharing_add_collaborator(
        user_id, "app", app_id, collaborator_user_id, role
    )

    return {
        "message": "Collaborator added successfully",
        "app_id": app_id,
        "collaborator_user_id": collaborator_user_id,
        "role": role,
        "auto_added_to_org_pool": bool(result.get("auto_added_to_workspace_pool")),
    }


@endpoint(
    "/apps/{app_id}/collaborators/{collaborator_user_id}",
    methods=["DELETE"],
    auth=True,
    tags=["Apps"],
)
async def remove_app_collaborator(
    request: Request,
    app_id: str,
    collaborator_user_id: str,
) -> Dict[str, Any]:
    """Remove a collaborator from an App (owner only)."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    await sharing_remove_collaborator(user_id, "app", app_id, collaborator_user_id)

    return {
        "message": "Collaborator removed",
        "app_id": app_id,
        "removed_collaborator_id": collaborator_user_id,
    }


@endpoint(
    "/apps/{app_id}/collaborators/{collaborator_user_id}",
    methods=["PATCH"],
    auth=True,
    tags=["Apps"],
)
async def update_app_collaborator_role(
    request: Request,
    app_id: str,
    collaborator_user_id: str,
    role: str,
) -> Dict[str, Any]:
    """Change an existing direct collaborator's role on an App (owner only).

    Accepts ``admin | editor | commenter | viewer`` (owner via transfer
    flow). Emits ``app.collaborator_role_update`` ChangeEvent + notifies
    the collaborator.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    result = await sharing_update_collaborator_role(
        user_id, "app", app_id, collaborator_user_id, role
    )
    return {
        "message": "Collaborator role updated",
        "app_id": app_id,
        **result,
    }


@endpoint(
    "/apps/{app_id}/transfer-ownership",
    methods=["POST"],
    auth=True,
    tags=["Apps"],
)
async def post_transfer_app_ownership(
    request: Request,
    app_id: str,
    new_owner_user_id: str,
) -> Dict[str, Any]:
    """Transfer App ownership to an existing collaborator (current owner only)."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.delete",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(
            message="Only the App owner can transfer ownership",
        )
    prior_sp = await App.get(app_id)
    prior_snapshot = await export_node(prior_sp) if prior_sp else None  # D-03
    sp = await transfer_app_ownership(
        app_id=app_id,
        acting_user_id=user_id,
        new_owner_user_id=new_owner_user_id,
    )

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="app.update",
        resource_type="App",
        resource_id=sp.id,
        before=prior_snapshot,
        after=await export_node(sp),
        scope=f"app:{sp.id}",
    )

    return {
        "message": "Ownership transferred",
        "app": await export_node(sp),
        "app_id": app_id,
        "new_owner_user_id": new_owner_user_id,
    }


async def _require_app_attached_cp(app_id: str) -> tuple[App, OperationalModel]:
    sp = await App.get(app_id)
    if not sp:
        raise ResourceNotFoundError(message="App not found")
    cp = await get_app_attached_operational_model(sp)
    if not cp:
        raise ResourceNotFoundError(message="Operational Model not found")
    return sp, cp


async def _record_effective_definition_after_library_apply(
    *,
    app_node: App,
    attached_profile: OperationalModel,
    library_profile: OperationalModel,
) -> ApplicationDefinition:
    """Append the effective App contract after an explicit library apply.

    The attached operational model is the tenant's actual materialized specification.
    Compiling the library source here would discard preserved local choices
    from the definition authority, even though the merge keeps them at
    runtime.
    """
    from app.services.application_definitions import (
        compile_application_definition,
        verify_definition_materialization,
    )

    definition = await compile_application_definition(
        app_node=app_node,
        manifest=dict(attached_profile.manifest or {}),
        source_operational_model_id=library_profile.id,
        source_kind="package",
        base_package_manifest=compile_canonical_manifest(
            manifest=dict(library_profile.manifest or {})
        ),
    )
    if str(getattr(app_node, "lifecycle_state", "") or "") == "active":
        await verify_definition_materialization(
            app_node=app_node,
            definition=definition,
        )
    return definition


async def _preview_app_library_apply(
    app_node: App, library_cp: OperationalModel
) -> Dict[str, Any]:
    canonical = compile_canonical_manifest(manifest=library_cp.manifest or {})
    scope = str(canonical.get("scope") or "")
    preview: Dict[str, Any] = {
        "scope": scope,
        "library_operational_model_id": library_cp.id,
        "tracks": [],
        "relations": [],
        "would_create_tracks": [],
        "counts": {
            "tracks": 0,
            "entry_types": 0,
            "views": 0,
            "tags": 0,
            "relations": 0,
        },
    }
    from app.services.application_definitions import (
        get_active_application_definition,
        preview_three_way_package_upgrade,
    )

    definition = await get_active_application_definition(app_node)
    if definition is None or not definition.base_package_manifest:
        preview["definition_upgrade"] = {
            "status": "not_available",
            "reason": "The active definition has no immutable package base.",
        }
    else:
        preview["definition_upgrade"] = preview_three_way_package_upgrade(
            base_package_manifest=dict(definition.base_package_manifest),
            effective_manifest=dict(definition.canonical_manifest or {}),
            incoming_package_manifest=canonical,
        )
    # Preview the same effective post-merge schema used by the commit gate.
    # A package may be structurally conflict-free yet still invalidate rows
    # already held in an installed App.
    attached_profile = await get_app_attached_operational_model(app_node)
    if attached_profile is not None:
        from app.exceptions import OperationalModelValidationError
        from app.services.application_upgrade_safety import (
            assert_package_upgrade_migration_safe,
        )

        try:
            preview["migration_safety"] = await assert_package_upgrade_migration_safe(
                attached_profile=attached_profile,
                library_profile=library_cp,
            )
        except OperationalModelValidationError as exc:
            preview["migration_safety"] = {
                "status": "blocked",
                **(exc.details or {}),
            }
    if scope != "app":
        return preview
    tracks = list((canonical.get("app") or {}).get("tracks") or [])
    preview["relations"] = list((canonical.get("app") or {}).get("relations") or [])
    existing = await app_node.nodes(edge=["CONTAINS"], node=["Track"])
    existing_keys = {str(getattr(t, "template_id", "") or "") for t in existing}
    track_rows: List[Dict[str, Any]] = []
    for tr in tracks:
        td = tr if isinstance(tr, dict) else {}
        key = str(td.get("key") or "")
        entry_types = list(td.get("entry_types") or [])
        views = list(td.get("views") or [])
        tag_count = 0
        taxonomy = td.get("taxonomy") or {}
        for grp in list(taxonomy.get("tag_groups") or []):
            gd = grp if isinstance(grp, dict) else {}
            tag_count += len(list(gd.get("tags") or []))
        row = {
            "key": key,
            "name": str(td.get("name") or key),
            "provision_on_create": bool(td.get("provision_on_create", True)),
            "entry_type_count": len(entry_types),
            "view_count": len(views),
            "tag_count": tag_count,
            "already_exists": key in existing_keys if key else False,
        }
        track_rows.append(row)
        if (
            row["provision_on_create"]
            and key
            and not row["already_exists"]
            and bool(
                (canonical.get("app") or {})
                .get("defaults", {})
                .get("provision_prescribed_tracks", True)
            )
        ):
            preview["would_create_tracks"].append(key)
    preview["tracks"] = track_rows
    preview["counts"] = {
        "tracks": len(track_rows),
        "entry_types": sum(r["entry_type_count"] for r in track_rows),
        "views": sum(r["view_count"] for r in track_rows),
        "tags": sum(r["tag_count"] for r in track_rows),
        "relations": len(preview["relations"]),
    }
    return preview


@endpoint(
    "/apps/{app_id}/operational-model",
    methods=["GET"],
    auth=True,
    tags=["Apps"],
)
async def get_app_operational_model(
    request: Request,
    app_id: str,
) -> Dict[str, Any]:
    """Get the App-attached OperationalModel."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.read",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")
    _, cp = await _require_app_attached_cp(app_id)
    return {
        "operational_model": await export_node(cp),
        "app_id": app_id,
    }


@endpoint(
    "/apps/{app_id}/operational-model",
    methods=["PATCH"],
    auth=True,
    tags=["Apps"],
)
async def patch_app_operational_model(
    request: Request,
    app_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    version: Optional[str] = None,
    manifest: Optional[Dict[str, Any]] = None,
    manifest_yaml: Optional[str] = None,
    scope: Optional[str] = None,
) -> Dict[str, Any]:
    """Update metadata on the App-attached OperationalModel."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.update",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")
    _, cp = await _require_app_attached_cp(app_id)
    prior_snapshot = await export_node(cp)  # D-03 before-snapshot
    if name is not None:
        cp.name = name
    if description is not None:
        cp.description = description
    if version is not None:
        cp.version = version
    manifest_updated = manifest is not None or manifest_yaml is not None
    if manifest_updated:
        cp.manifest = compile_canonical_manifest(
            manifest=manifest,
            manifest_yaml=manifest_yaml,
            scope_hint=scope or "app",
        )
        cp.scope = str(cp.manifest.get("scope") or scope or "app")
    elif scope is not None:
        cp.scope = scope
    cp.updated_at = utc_now_iso()
    await cp.save()
    if manifest_updated:
        invalidate_manifest_cache()

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="operational_model.update",
        resource_type="OperationalModel",
        resource_id=cp.id,
        before=prior_snapshot,
        after=await export_node(cp),
        scope=f"app:{app_id}",
    )

    return {
        "operational_model": await export_node(cp),
        "message": "Operational Model updated",
    }


@endpoint(
    "/apps/{app_id}/operational-model/merge-library",
    methods=["POST"],
    auth=True,
    tags=["Apps"],
)
async def merge_library_into_app_operational_model(
    request: Request,
    app_id: str,
    library_operational_model_id: str,
) -> Dict[str, Any]:
    """Merge a library package manifest into the App-attached OperationalModel."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.update",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")
    sp, sacp = await _require_app_attached_cp(app_id)
    lib = await OperationalModel.get(library_operational_model_id)
    if not lib or not getattr(lib, "library_package", False):
        raise ResourceNotFoundError(message="Library package not found")
    from app.services.package_trust import assert_library_artifact_trusted

    assert_library_artifact_trusted(lib)
    from app.services.application_definitions import (
        assert_package_upgrade_conflict_free,
        get_active_application_definition,
    )

    definition = await get_active_application_definition(sp)
    if definition is not None:
        assert_package_upgrade_conflict_free(definition, lib.manifest or {})
    from app.services.application_upgrade_safety import (
        assert_package_upgrade_migration_safe,
    )

    migration_safety = await assert_package_upgrade_migration_safe(
        attached_profile=sacp,
        library_profile=lib,
    )
    prior_snapshot = await export_node(sacp)  # D-03 before-snapshot
    await merge_library_manifest_into_operational_model(
        lib, sacp, track=None, for_space=True
    )
    sp.library_merge_source_id = lib.id
    await sp.save()
    await provision_prescribed_tracks_from_app_manifest(sp, user_id)
    definition = await _record_effective_definition_after_library_apply(
        app_node=sp,
        attached_profile=sacp,
        library_profile=lib,
    )
    from app.services.application_upgrade_safety import start_package_upgrade_migrations

    migration_tracker = await start_package_upgrade_migrations(
        attached_profile=sacp,
        safety=migration_safety,
        actor_id=user_id,
    )

    if getattr(sp, "lifecycle_state", None) == "active":
        from app.services.app_lifecycle import sync_operational_layer_from_manifest
        from app.services.operational_model_runtime import compile_canonical_manifest

        canonical = compile_canonical_manifest(manifest=lib.manifest or {})
        await sync_operational_layer_from_manifest(sp, canonical, actor_id=user_id)

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="operational_model.merge_library",
        resource_type="OperationalModel",
        resource_id=sacp.id,
        before=prior_snapshot,
        after=await export_node(sacp),
        scope=f"app:{app_id}",
    )

    return {
        "message": "Library merged into App operational model",
        "app_id": app_id,
        "library_operational_model_id": library_operational_model_id,
        "definition_id": definition.id,
        "definition_revision": definition.revision,
        "migration_tracker": migration_tracker,
    }


@endpoint(
    "/apps/{app_id}/operational-model/preview",
    methods=["POST"],
    auth=True,
    tags=["Apps"],
)
async def preview_app_operational_model_merge(
    request: Request,
    app_id: str,
    library_operational_model_id: str,
) -> Dict[str, Any]:
    """Preview impact of merging a library package into an App profile."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.update",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")
    sp, _ = await _require_app_attached_cp(app_id)
    lib = await OperationalModel.get(library_operational_model_id)
    if not lib or not getattr(lib, "library_package", False):
        raise ResourceNotFoundError(message="Library package not found")
    from app.services.package_trust import assert_library_artifact_trusted

    assert_library_artifact_trusted(lib)
    return {
        "app_id": app_id,
        "preview": await _preview_app_library_apply(sp, lib),
    }


@endpoint(
    "/apps/{app_id}/operational-model/apply",
    methods=["POST"],
    auth=True,
    tags=["Apps"],
)
async def apply_app_operational_model_library(
    request: Request,
    app_id: str,
    library_operational_model_id: str,
) -> Dict[str, Any]:
    """Apply library package to App with preview summary in response."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.update",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")
    sp, sacp = await _require_app_attached_cp(app_id)
    lib = await OperationalModel.get(library_operational_model_id)
    if not lib or not getattr(lib, "library_package", False):
        raise ResourceNotFoundError(message="Library package not found")
    from app.services.package_trust import assert_library_artifact_trusted

    assert_library_artifact_trusted(lib)
    preview = await _preview_app_library_apply(sp, lib)
    if (preview.get("definition_upgrade") or {}).get("status") == "conflicts":
        from app.exceptions import ApplicationDefinitionUpgradeConflictError

        raise ApplicationDefinitionUpgradeConflictError(
            message="Package upgrade requires conflict resolution before it can apply.",
            details={"conflicts": preview["definition_upgrade"]["conflicts"]},
        )
    from app.services.application_upgrade_safety import (
        assert_package_upgrade_migration_safe,
    )

    migration_safety = await assert_package_upgrade_migration_safe(
        attached_profile=sacp,
        library_profile=lib,
    )
    prior_snapshot = await export_node(sacp)  # D-03 before-snapshot
    await merge_library_manifest_into_operational_model(
        lib, sacp, track=None, for_space=True
    )
    sp.library_merge_source_id = lib.id
    await sp.save()
    await provision_prescribed_tracks_from_app_manifest(sp, user_id)
    definition = await _record_effective_definition_after_library_apply(
        app_node=sp,
        attached_profile=sacp,
        library_profile=lib,
    )
    from app.services.application_upgrade_safety import start_package_upgrade_migrations

    migration_tracker = await start_package_upgrade_migrations(
        attached_profile=sacp,
        safety=migration_safety,
        actor_id=user_id,
    )
    tracks_after = await sp.nodes(edge=["CONTAINS"], node=["Track"])

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="operational_model.merge_library",
        resource_type="OperationalModel",
        resource_id=sacp.id,
        before=prior_snapshot,
        after=await export_node(sacp),
        scope=f"app:{app_id}",
    )

    return {
        "message": "Operational Model applied",
        "app_id": app_id,
        "library_operational_model_id": library_operational_model_id,
        "preview": preview,
        "applied": {"space_track_count": len(tracks_after)},
        "definition_id": definition.id,
        "definition_revision": definition.revision,
        "migration_tracker": migration_tracker,
    }


@endpoint(
    "/apps/{app_id}/operational-model/track-templates",
    methods=["GET"],
    auth=True,
    tags=["Apps"],
)
async def list_app_track_templates(
    request: Request,
    app_id: str,
) -> Dict[str, Any]:
    """List track-template OperationalModels defined under the App attached CP."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.read",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")
    _, sacp = await _require_app_attached_cp(app_id)
    templates = await sacp.nodes(
        edge=[DEFINES_TRACK_PROFILE], node=["OperationalModel"]
    )
    items = [await export_node(t) for t in templates]
    return {"track_templates": items, "total": len(items), "app_id": app_id}


@endpoint(
    "/apps/{app_id}/operational-model/track-templates",
    methods=["POST"],
    auth=True,
    tags=["Apps"],
)
async def create_app_track_template(
    request: Request,
    app_id: str,
    name: str = "",
    description: str = "",
) -> Dict[str, Any]:
    """Create a track-template OperationalModel linked from the App attached CP."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.update",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")
    _, sacp = await _require_app_attached_cp(app_id)
    now = utc_now_iso()
    tpl = await OperationalModel.create(
        name=name or "Template",
        app_id=app_id,
        description=description or "",
        library_package=False,
        created_at=now,
        updated_at=now,
    )
    await sacp.connect(tpl, edge=DEFINES_TRACK_PROFILE, defined_at=now)

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="operational_model.create",
        resource_type="OperationalModel",
        resource_id=tpl.id,
        before=None,
        after=await export_node(tpl),
        scope=f"app:{app_id}",
    )

    return {
        "track_template": await export_node(tpl),
        "message": "Track template created",
        "app_id": app_id,
    }


@endpoint(
    "/apps/{app_id}/operational-model/track-templates/{template_id}",
    methods=["DELETE"],
    auth=True,
    tags=["Apps"],
)
async def delete_app_track_template(
    request: Request,
    app_id: str,
    template_id: str,
) -> Dict[str, Any]:
    """Delete a track template from an App operational model."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.update",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")
    await verify_track_template_in_app(app_id, template_id)
    tpl = await OperationalModel.get(template_id)
    prior_snapshot = await export_node(tpl) if tpl else None  # D-03 before-snapshot
    if tpl:
        await tpl.delete()

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="operational_model.delete",
        resource_type="OperationalModel",
        resource_id=template_id,
        before=prior_snapshot,
        after=None,
        scope=f"app:{app_id}",
    )

    return {
        "message": "Track template deleted",
        "app_id": app_id,
        "template_id": template_id,
    }


@endpoint(
    "/apps/{app_id}/operational-model/track-templates/{template_id}/apply-to-track",
    methods=["POST"],
    auth=True,
    tags=["Apps"],
)
async def apply_track_template_to_track(
    request: Request,
    app_id: str,
    template_id: str,
    track_id: str,
) -> Dict[str, Any]:
    """Merge an App track template into an existing track's attached OperationalModel."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.update",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")
    _track_decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="track.update",
        resource=Resource(kind="track", id=track_id, scope=f"track:{track_id}"),
    )
    if not _track_decision.allowed:
        raise InsufficientPermissionsError(message="Access denied for track")
    tpl = await verify_track_template_in_app(app_id, template_id)
    track = await Track.get(track_id)
    if not track:
        raise ResourceNotFoundError(message="Track not found")
    prior_snapshot = await export_node(track)  # D-03 before-snapshot
    await merge_template_operational_model_into_track(tpl, track)

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="operational_model.merge_library",
        resource_type="Track",
        resource_id=track.id,
        before=prior_snapshot,
        after=await export_node(track),
        scope=f"track:{track_id}",
    )

    return {
        "message": "Track template applied",
        "track": await export_node(track),
        "app_id": app_id,
        "template_id": template_id,
    }


@endpoint(
    "/apps/{app_id}/operational-model/track-templates/{template_id}/entry-types",
    methods=["POST"],
    auth=True,
    tags=["Apps"],
)
async def create_track_template_entry_type(
    request: Request,
    app_id: str,
    template_id: str,
    name: str = "",
    icon: str = "document",
    form_schema: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Add an entry type to an App track template."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.update",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")
    tpl = await verify_track_template_in_app(app_id, template_id)
    now = utc_now_iso()
    et = await EntryType.create(
        name=name,
        icon=icon,
        form_schema=normalize_entry_type_form_schema(form_schema),
        track_id="",
        is_template=True,
        created_at=now,
        updated_at=now,
    )
    await tpl.connect(et, edge=CONTAINS, added_at=now)

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="entry_type.create",
        resource_type="EntryType",
        resource_id=et.id,
        before=None,
        after=await export_node(et),
        scope=f"app:{app_id}",
    )

    return {"entry_type": await export_node(et), "message": "Template entry type added"}


@endpoint(
    "/apps/{app_id}/operational-model/track-templates/{template_id}/tags",
    methods=["POST"],
    auth=True,
    tags=["Apps"],
)
async def create_track_template_tag(
    request: Request,
    app_id: str,
    template_id: str,
    name: str = "",
    color: str = "#6B7280",
) -> Dict[str, Any]:
    """Add a tag to an App track template."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.update",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")
    tpl = await verify_track_template_in_app(app_id, template_id)
    now = utc_now_iso()
    tag = await Tag.create(
        name=name,
        color=color or "#6B7280",
        track_id="",
        is_template=True,
        created_at=now,
    )
    await tpl.connect(tag, edge=CONTAINS, added_at=now)

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="tag.create",
        resource_type="Tag",
        resource_id=tag.id,
        before=None,
        after=await export_node(tag),
        scope=f"app:{app_id}",
    )

    return {"tag": await export_node(tag), "message": "Template tag added"}


@endpoint(
    "/apps/{app_id}/operational-model/track-templates/{template_id}/views",
    methods=["POST"],
    auth=True,
    tags=["Apps"],
)
async def create_track_template_view(
    request: Request,
    app_id: str,
    template_id: str,
    name: str = "",
    type: str = "feed",
    view_type: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
    is_default: bool = False,
) -> Dict[str, Any]:
    """Add a view to an App track template."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.update",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")
    tpl = await verify_track_template_in_app(app_id, template_id)
    resolved_type = view_type or type
    valid_types = _allowed_view_types()
    if resolved_type not in valid_types:
        raise BadRequestError(
            message=f"type must be one of: {', '.join(sorted(valid_types))}",
        )
    now = utc_now_iso()
    vreg = await get_or_create_views_registry_for_operational_model(tpl, track=None)
    view = await View.create(
        name=name,
        type=resolved_type or "feed",
        config=normalize_view_config(resolved_type or "feed", config or {}),
        track_id="",
        operational_model_id=tpl.id,
        is_template=True,
        is_default=is_default,
        created_by=user_id or "",
        created_at=now,
        updated_at=now,
    )
    from app.services.app_graph import ensure_catalog_edge

    await ensure_catalog_edge(vreg, view)

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="view.create",
        resource_type="View",
        resource_id=view.id,
        before=None,
        after=await export_node(view),
        scope=f"app:{app_id}",
    )

    return {"view": await export_node(view), "message": "Template view added"}


# ---------------------------------------------------------------------------
# Phase 10 Plan 10-05 — App Bundles v1 install / settings / uninstall lifecycle.
# ---------------------------------------------------------------------------
#
# These endpoints layer on top of the existing CRUD surface above. They
# orchestrate the atomic install transaction (compile manifest → check deps
# → create App → materialize Tracks → register skills + agents → pause for
# settings if needed → plant seeds → emit app.installed ChangeEvent) and
# the uninstall path (dependency check → unregister → archive or hard
# purge → emit app.uninstalled or app.force_uninstalled).
#
# All routes use @endpoint + JVSpatialAPIException subclasses (jvspatial
# convention; AGENTS.md § jvspatial Object-Spatial Contract). Request +
# response shapes live in backend/app/schemas/app_lifecycle.py.


@endpoint(
    "/workspaces/{workspace_id}/apps/install",
    methods=["POST"],
    auth=True,
    tags=["Apps"],
)
async def install_app_endpoint(
    request: Request,
    workspace_id: str,
    library_operational_model_id: str,
    version: Optional[str] = None,
    settings: Optional[Dict[str, Any]] = None,
    include_seed_data: bool = True,
) -> Dict[str, Any]:
    """Install an App into a Workspace from a library OperationalModel.

    Phase 10 Plan 10-05. Drives the 12-step install transaction in
    ``app_lifecycle.install_app``. Returns either:

    - 200 ``{status: "active", app_id, installed_at, version}`` — install
      completed (no ``settings_schema`` declared or caller pre-supplied
      settings).
    - 200 ``{status: "awaiting_settings", app_id, install_token, settings_schema}``
      — install paused at step 9; caller resumes with the install_token
      via POST /api/apps/{app_id}/install/settings. The 202 status code
      is NOT used here because the App row IS persisted in
      awaiting_settings state — the response is a normal success indicating
      the next required step.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    if not await can_create_app_under_workspace(user_id, workspace_id):
        raise InsufficientPermissionsError(
            message="You are not allowed to install apps in this workspace",
        )

    from app.services.app_lifecycle import install_app as _install_app_impl

    return await _install_app_impl(
        workspace_id=workspace_id,
        library_cp_id=library_operational_model_id,
        actor_id=user_id,
        settings=settings,
        include_seed_data=include_seed_data,
    )


@endpoint(
    "/apps/{app_id}/install/settings",
    methods=["POST"],
    auth=True,
    tags=["Apps"],
)
async def finalize_install_endpoint(
    request: Request,
    app_id: str,
    install_token: str = "",
    settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Resume a paused install by submitting settings + the install_token.

    Phase 10 Plan 10-05. Verifies the HMAC-signed install_token, applies
    user-submitted settings (jsonschema-validated against the App's
    settings_schema), plants seeds, and transitions the App to ``active``.
    Emits a single ``app.installed`` ChangeEvent (D-05).
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    if not install_token:
        raise BadRequestError(message="install_token is required")

    from app.services.app_lifecycle import finalize_install as _finalize_install_impl

    return await _finalize_install_impl(
        app_id=app_id,
        install_token=install_token,
        settings=settings or {},
        actor_id=user_id,
    )


@endpoint(
    "/apps/{app_id}/settings",
    methods=["GET"],
    auth=True,
    tags=["Apps"],
)
async def get_app_settings(request: Request, app_id: str) -> Dict[str, Any]:
    """Return current App settings + the App's settings_schema mirror.

    Phase 10 Plan 10-05. The settings_schema is the same shape the install-
    time form rendered against; the frontend re-uses ``AppSettingsForm.tsx``
    for the post-install Settings page.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    app_node = await App.get(app_id)
    if not app_node:
        raise ResourceNotFoundError(message="App not found")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.read",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")
    return {
        "app_id": app_id,
        "settings": dict(app_node.settings or {}),
        "settings_schema": dict(app_node.settings_schema or {}),
        "lifecycle_state": app_node.lifecycle_state,
    }


@endpoint(
    "/apps/{app_id}/settings",
    methods=["PATCH"],
    auth=True,
    tags=["Apps"],
)
async def patch_app_settings(
    request: Request,
    app_id: str,
    settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Update an active App's settings (Settings page edit).

    Phase 10 Plan 10-05. Server-side validation runs against the App's
    persisted settings_schema mirror (T-10-05-05).
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    if settings is None:
        raise BadRequestError(message="settings is required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.update",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(
            message="Only the App owner can update its settings"
        )

    from app.services.app_lifecycle import update_app_settings

    return await update_app_settings(app_id=app_id, settings=settings, actor_id=user_id)


@endpoint(
    "/apps/{app_id}/update-from-library",
    methods=["POST"],
    auth=True,
    tags=["Apps"],
)
async def update_from_library_endpoint(
    request: Request,
    app_id: str,
    version: Optional[str] = None,
) -> Dict[str, Any]:
    """Re-merge from the originating library package (newer version).

    Phase 10 Plan 10-05. Wraps ``app_lifecycle.update_app_from_library``.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.update",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(
            message="Only the App owner can update it from the library"
        )
    from app.services.app_lifecycle import update_app_from_library

    return await update_app_from_library(
        app_id=app_id, version=version, actor_id=user_id
    )


@endpoint(
    "/apps/{app_id}/pause",
    methods=["POST"],
    auth=True,
    tags=["Apps"],
)
async def pause_app_endpoint(request: Request, app_id: str) -> Dict[str, Any]:
    """Pause an active App. Disables scheduled agent runs."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.update",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Only the App owner can pause it")
    from app.services.app_lifecycle import pause_app

    return await pause_app(app_id=app_id, actor_id=user_id)


@endpoint(
    "/apps/{app_id}/resume",
    methods=["POST"],
    auth=True,
    tags=["Apps"],
)
async def resume_app_endpoint(request: Request, app_id: str) -> Dict[str, Any]:
    """Resume a paused App."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.update",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Only the App owner can resume it")
    from app.services.app_lifecycle import resume_app

    return await resume_app(app_id=app_id, actor_id=user_id)


@endpoint(
    "/apps/{app_id}/uninstall",
    methods=["POST"],
    auth=True,
    tags=["Apps"],
)
async def uninstall_app_endpoint(
    request: Request,
    app_id: str,
    force: bool = False,
    archive: bool = True,
) -> Dict[str, Any]:
    """Uninstall an App. ``force=true`` bypasses dependency checks.

    Phase 10 Plan 10-05. Distinct from ``DELETE /api/apps/{id}`` (the
    legacy CRUD delete) so the lifecycle-aware uninstall path is opt-in
    and explicit. The CRUD delete remains for back-compat with pre-10-05
    callers; new callers should prefer this endpoint.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.delete",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(
            message="Only the App owner can uninstall it"
        )
    from app.services.app_lifecycle import uninstall_app

    return await uninstall_app(
        app_id=app_id,
        actor_id=user_id,
        force=force,
        archive=archive,
    )
