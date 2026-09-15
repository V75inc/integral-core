"""Track CRUD API endpoints with permission checking."""

import asyncio
import logging
from typing import Any, Dict, List, Optional, cast

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
    normalize_track_accent_color,
    normalize_track_visibility_input,
    validate_id,
    validate_track_visibility_workspace,
)
from app.api.validators_common import compute_fold, non_empty_after_strip
from app.models.edges import (
    COLLABORATES_ON,
    EXCLUDED_FROM,
    WATCHES,
)
from app.models.nodes import ContentProfile, Track, User
from app.schemas.policy import Resource, Subject
from app.services.app_graph import (
    get_track_attached_content_profile,
)
from app.services.change_event import emit_change_event
from app.services.content_profile_merge import (
    merge_library_manifest_into_content_profile,
)
from app.services.content_profile_runtime import (
    compile_canonical_manifest,
    invalidate_manifest_cache,
    synchronize_track_view_default_flags,
)
from app.services.entry_context import (
    attach_anchor_source_to_track_data,
    attach_content_profile_defaults_to_track_data,
    attach_parent_app_to_track_data,
    attach_track_and_space,
)
from app.services.notification_paths import resolve_resource_action_url
from app.services.ownership_transfer import transfer_track_ownership
from app.services.pagination import DEFAULT_ENTITY_SORT, paginate_nodes_by_ids
from app.services.permissions import (
    count_user_accessible_entries,
    get_user_accessible_tracks,
    get_user_node,
    resolve_role,
)
from app.services.policy_engine import evaluate as policy_evaluate
from app.services.sharing import add_collaborator as sharing_add_collaborator
from app.services.sharing import add_exclusion as sharing_add_exclusion
from app.services.sharing import remove_collaborator as sharing_remove_collaborator
from app.services.sharing import remove_exclusion as sharing_remove_exclusion
from app.services.sharing import (
    update_collaborator_role as sharing_update_collaborator_role,
)
from app.services.uniqueness import assert_unique
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)


@endpoint("/tracks", methods=["GET"], auth=True, tags=["Tracks"])
async def list_tracks(
    request: Request,
    cursor: Optional[str] = None,
    limit: int = 20,
    app_id: Optional[str] = None,
    include_total: bool = True,
) -> Dict[str, Any]:
    """List all tracks accessible to the current user (cursor pagination)."""
    user_id = resolve_principal_id(request)
    if not user_id:
        # Peer handlers raise here. These four used to return an empty
        # collection. auth=True means an authenticated request whose
        # principal still failed to resolve — surface it (see
        # test_list_endpoints_auth_envelope).
        raise MissingAuthenticationError(message="Authentication required")
    tracks = await get_user_accessible_tracks(user_id)
    # W5: server-enforce the active workspace scope from
    # X-Integral-Scope (fail-closed → user's Personal Workspace).
    from app.services.request_scope import (
        matches_workspace,
        resolve_workspace_id_from_request,
    )

    target_ws = await resolve_workspace_id_from_request(request, user_id)
    if target_ws:
        tracks = [t for t in tracks if matches_workspace(t, target_ws)]

    if app_id:
        # Distinguish "app exists but has no tracks" from "app_id does not
        # resolve to any app visible in this scope". Without this, a bogus or
        # hallucinated app_id silently returns {total: 0, tracks: []}, which an
        # agent misreads as "the app is empty" and cannot self-correct. Signal
        # the unresolved id explicitly so the caller re-grounds (list_apps).
        from app.models.nodes import App as _App

        target_app = await _App.get(app_id)
        if target_app is None or (
            target_ws and not matches_workspace(target_app, target_ws)
        ):
            return {
                "tracks": [],
                "total": 0,
                "next_cursor": None,
                "has_more": False,
                "scope_workspace_id": target_ws,
                "app_id_unresolved": True,
                "error_hint": (
                    f"No app with id '{app_id}' is visible in this workspace. "
                    "Resolve the app by name via list_apps before listing its "
                    "tracks; do not construct or guess an app id."
                ),
            }

        # I-PERF: parent lookup was sequential across all tracks. With the
        # idx_target_entity edge index in place, each call is O(log N); run
        # them in parallel so wall time scales with the slowest, not the sum.
        async def _parents_of(t: Track) -> List[Any]:
            try:
                return await t.nodes(
                    edge=["CONTAINS"], direction="in", node=["WorkspaceApp"]
                )
            except Exception:
                return []

        parent_lists = await asyncio.gather(*(_parents_of(t) for t in tracks))
        tracks = [
            t
            for t, parents in zip(tracks, parent_lists)
            if any(getattr(p, "id", None) == app_id for p in parents)
        ]

    track_ids = [t.id for t in tracks]
    page_tracks, response = await paginate_nodes_by_ids(
        Track,
        track_ids,
        cursor,
        limit,
        sort=list(DEFAULT_ENTITY_SORT),
        include_total=include_total,
    )
    response["scope_workspace_id"] = target_ws

    # I-PERF: enrichment was 4 sequential awaits per track (export_node +
    # count + parent_app + content_profile + anchor). Lift to per-track
    # parallel: each track's 4 attaches run together (asyncio.gather),
    # and all N tracks run in parallel against the now-indexed substrate.
    async def _enrich_one(t: Track) -> Dict[str, Any]:
        data = await export_node(t)
        count, _, _, _ = await asyncio.gather(
            count_user_accessible_entries(user_id, t.id),
            attach_parent_app_to_track_data(data, t),
            attach_content_profile_defaults_to_track_data(data, t),
            attach_anchor_source_to_track_data(data, t),
        )
        data["entry_count"] = count
        data["action_url"] = resolve_resource_action_url("track", t.id)
        return data

    enriched = await asyncio.gather(*(_enrich_one(t) for t in page_tracks))
    response["tracks"] = list(enriched)
    return response


@endpoint("/tracks", methods=["POST"], auth=True, tags=["Tracks"])
async def create_track(
    request: Request,
    title: str = "",
    purpose: Optional[str] = None,
    icon: Optional[str] = None,
    visibility: Optional[str] = None,
    template_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    app_id: Optional[str] = None,
    library_content_profile_id: Optional[str] = None,
    app_track_template_content_profile_id: Optional[str] = None,
    app_track_type_key: Optional[str] = None,
    accent_color: Optional[str] = None,
    type_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new track owned by the current user.

    Phase 6 Plan 06-04 (MCP-04 AC#5): ``type_hint`` resolves a free-text hint
    via ``resolve_type_hint`` (direct service call — Pitfall 6: no MCP
    recursion) to one of the seeded library packages. Resolution rules:

      * Zero matches → fall back to default empty profile + warning in response.
      * Tied top scores → 400 ``content_profile.type_hint_ambiguous``.
      * Single best match (top-1 by score, all others strictly lower) →
        adopt as ``library_content_profile_id``.

    ``type_hint`` is mutually exclusive with explicit picker fields
    (``library_content_profile_id`` / ``app_track_template_content_profile_id`` /
    ``app_track_type_key``); combining → 400
    ``content_profile.conflicting_picker``.

    Phase 9 Plan 09-05 (B4): the post-auth creation body now lives in
    ``app/services/track_service.py::create_track_in_space`` so agentive
    tools can drive Track creation in-process (no HTTP self-call, no MCP
    recursion). This handler is the thin auth + request-parsing wrapper
    that resolves ``type_hint`` and formats the response.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    stk_raw = str(app_track_type_key or "").strip()

    # ---- Phase 6 Plan 06-04 — type_hint resolution (BEFORE single-picker check) ----
    type_hint_warning: Optional[str] = None
    if type_hint:
        if (
            stk_raw
            or app_track_template_content_profile_id
            or library_content_profile_id
        ):
            raise BadRequestError(
                message=("type_hint cannot combine with explicit picker fields"),
                details={
                    "error_code": "content_profile.conflicting_picker",
                },
            )
        # Pitfall 6: direct service call, NOT an MCP-wrapped re-dispatch.
        from app.api.content_profiles import resolve_type_hint

        matches = await resolve_type_hint(type_hint)
        if not matches:
            type_hint_warning = (
                "type_hint did not resolve to any library package; "
                "using default profile"
            )
        elif len(matches) > 1 and matches[0]["score"] == matches[1]["score"]:
            # Tied top score — 400 with disambiguation list (locked-post #8).
            raise BadRequestError(
                message="type_hint matches multiple library packages",
                details={
                    "error_code": "content_profile.type_hint_ambiguous",
                    "candidates": matches[:10],
                },
            )
        else:
            # Single best match — adopt as library_content_profile_id.
            library_content_profile_id = matches[0]["content_profile_id"]

    from app.services.track_service import (
        create_track_in_space,
        create_track_response_payload,
    )

    track = await create_track_in_space(
        user_id=user_id,
        title=title,
        purpose=purpose,
        icon=icon,
        visibility=visibility,
        template_id=template_id,
        workspace_id=workspace_id,
        app_id=app_id,
        library_content_profile_id=library_content_profile_id,
        app_track_template_content_profile_id=app_track_template_content_profile_id,
        app_track_type_key=stk_raw or None,
        accent_color=accent_color,
    )
    return await create_track_response_payload(
        track, type_hint_warning=type_hint_warning
    )


@endpoint("/tracks/{track_id}", methods=["GET"], auth=True, tags=["Tracks"])
async def get_track(request: Request, track_id: str) -> Dict[str, Any]:
    """Get a specific track by ID."""
    validate_id(track_id, "track_id")
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    track = await Track.get(track_id)
    if not track:
        raise ResourceNotFoundError(message="Track not found")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="track.read",
        resource=Resource(kind="track", id=track_id, scope=f"track:{track_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")
    track_data = await export_node(track)
    track_data["entry_count"] = await count_user_accessible_entries(user_id, track_id)
    await attach_parent_app_to_track_data(track_data, track)
    await attach_content_profile_defaults_to_track_data(track_data, track)
    await attach_anchor_source_to_track_data(track_data, track)
    return {"track": track_data}


@endpoint("/tracks/{track_id}/detail", methods=["GET"], auth=True, tags=["Tracks"])
async def get_track_detail_bundle(request: Request, track_id: str) -> Dict[str, Any]:
    """Composite read for Track detail page bootstrap.

    Returns track metadata, collaborators, entry types, and saved views in a
    single round-trip.
    """
    validate_id(track_id, "track_id")
    # Reuse existing endpoint-level contracts to keep payload semantics aligned.
    track_resp = await get_track(request, track_id)
    from app.api.entry_types import list_entry_types as _list_entry_types
    from app.api.views import list_track_views as _list_track_views

    # These three reads are independent after the track-level permission
    # check above. Running them together removes two serial graph/policy
    # round-trips from every track navigation without changing their response
    # contracts.
    collab_resp, entry_types_resp, views_resp = await asyncio.gather(
        list_collaborators(request, track_id),
        _list_entry_types(request, track_id=track_id),
        _list_track_views(request, track_id),
    )

    return {
        "track": track_resp.get("track"),
        "collaborators": collab_resp.get("collaborators", []),
        "collaborator_effective_total": collab_resp.get("effective_total", 0),
        "collaborator_inherited_truncated": collab_resp.get(
            "inherited_truncated", False
        ),
        "collaborator_visibility_grant": collab_resp.get("visibility_grant"),
        "caller_role": collab_resp.get("caller_role"),
        "entry_types": entry_types_resp.get("entry_types", []),
        "views": views_resp.get("views", []),
    }


@endpoint(
    "/tracks/{track_id}/content-profile",
    methods=["GET"],
    auth=True,
    tags=["Tracks"],
)
async def get_track_content_profile(
    request: Request,
    track_id: str,
) -> Dict[str, Any]:
    """Return the ContentProfile attached to a track."""
    validate_id(track_id, "track_id")
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="track.read",
        resource=Resource(kind="track", id=track_id, scope=f"track:{track_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")
    track = await Track.get(track_id)
    if not track:
        raise ResourceNotFoundError(message="Track not found")
    cp = await get_track_attached_content_profile(track)
    if not cp:
        raise ResourceNotFoundError(message="Content profile not found")
    return {
        "content_profile": await export_node(cp),
        "track_id": track_id,
    }


@endpoint(
    "/tracks/{track_id}/content-profile",
    methods=["PATCH"],
    auth=True,
    tags=["Tracks"],
)
async def patch_track_content_profile(
    request: Request,
    track_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    version: Optional[str] = None,
    manifest: Optional[Dict[str, Any]] = None,
    manifest_yaml: Optional[str] = None,
    scope: Optional[str] = None,
) -> Dict[str, Any]:
    """Update fields on a track's attached ContentProfile."""
    validate_id(track_id, "track_id")
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="track.update",
        resource=Resource(kind="track", id=track_id, scope=f"track:{track_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")
    track = await Track.get(track_id)
    if not track:
        raise ResourceNotFoundError(message="Track not found")
    cp = await get_track_attached_content_profile(track)
    if not cp:
        raise ResourceNotFoundError(message="Content profile not found")
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
            scope_hint=scope or "track",
        )
        cp.scope = str(cp.manifest.get("scope") or scope or "track")
    elif scope is not None:
        cp.scope = scope
    cp.updated_at = utc_now_iso()
    await cp.save()
    if manifest_updated:
        invalidate_manifest_cache()
        await synchronize_track_view_default_flags(track)

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="content_profile.update",
        resource_type="ContentProfile",
        resource_id=cp.id,
        before=prior_snapshot,
        after=await export_node(cp),
        scope=f"track:{track.id}",
    )

    return {
        "content_profile": await export_node(cp),
        "message": "Content profile updated",
    }


@endpoint(
    "/tracks/{track_id}/content-profile/merge-library",
    methods=["POST"],
    auth=True,
    tags=["Tracks"],
)
async def merge_library_into_track_content_profile(
    request: Request,
    track_id: str,
    library_content_profile_id: str,
) -> Dict[str, Any]:
    """Merge a library ContentProfile package into a track's profile."""
    validate_id(track_id, "track_id")
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="track.update",
        resource=Resource(kind="track", id=track_id, scope=f"track:{track_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")
    track = await Track.get(track_id)
    if not track:
        raise ResourceNotFoundError(message="Track not found")
    lib = await ContentProfile.get(library_content_profile_id)
    if not lib or not getattr(lib, "library_package", False):
        raise ResourceNotFoundError(message="Library package not found")
    tcp = await get_track_attached_content_profile(track)
    if not tcp:
        raise ResourceNotFoundError(message="Content profile not found")
    prior_snapshot = await export_node(tcp)  # D-03 before-snapshot
    await merge_library_manifest_into_content_profile(lib, tcp, track, for_space=False)
    track.library_merge_source_id = library_content_profile_id
    await track.save()

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="content_profile.merge_library",
        resource_type="ContentProfile",
        resource_id=tcp.id,
        before=prior_snapshot,
        after=await export_node(tcp),
        scope=f"track:{track.id}",
    )

    return {
        "message": "Library merged into track content profile",
        "track_id": track_id,
        "library_content_profile_id": library_content_profile_id,
    }


@endpoint("/tracks/{track_id}", methods=["PUT"], auth=True, tags=["Tracks"])
async def update_track(
    request: Request,
    track_id: str,
    title: Optional[str] = None,
    purpose: Optional[str] = None,
    icon: Optional[str] = None,
    visibility: Optional[str] = None,
    accent_color: Optional[str] = None,
) -> Dict[str, Any]:
    """Update a track (editor or owner only)."""
    validate_id(track_id, "track_id")
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="track.update",
        resource=Resource(kind="track", id=track_id, scope=f"track:{track_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    track = await Track.get(track_id)
    if not track:
        raise ResourceNotFoundError(message="Track not found")

    prior_snapshot = await export_node(track)  # D-03 before-snapshot

    if title is not None:
        safe_title = non_empty_after_strip(title, "title")
        if len(safe_title) > 200:
            raise BadRequestError(message="title must be 200 characters or fewer")
        new_fold = compute_fold(safe_title)
        if new_fold != (getattr(track, "title_fold", "") or ""):
            await assert_unique(
                Track,
                {
                    "context.workspace_id": track.workspace_id,
                    "context.title_fold": new_fold,
                },
                entity="track",
                field_label="title",
                value=safe_title,
                scope_label="in this workspace",
                exclude_id=track.id,
            )
        track.title = safe_title
        track.title_fold = new_fold
    if purpose is not None:
        track.purpose = purpose
    if icon is not None:
        track.icon = icon
    if visibility is not None:
        explicit_vis = normalize_track_visibility_input(visibility)
        if explicit_vis is None:
            track.visibility = "inherit"
        else:
            await validate_track_visibility_workspace(explicit_vis, track.workspace_id)
            track.visibility = explicit_vis
    if accent_color is not None:
        track.accent_color = normalize_track_accent_color(accent_color)

    track.updated_at = utc_now_iso()
    await track.save()

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="track.update",
        resource_type="Track",
        resource_id=track.id,
        before=prior_snapshot,
        after=await export_node(track),
        scope=f"track:{track.id}",
    )

    return {"track": await export_node(track), "message": "Track updated successfully"}


@endpoint("/tracks/{track_id}", methods=["DELETE"], auth=True, tags=["Tracks"])
async def delete_track(request: Request, track_id: str) -> Dict[str, Any]:
    """Delete a track (owner only)."""
    validate_id(track_id, "track_id")
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="track.delete",
        resource=Resource(kind="track", id=track_id, scope=f"track:{track_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Only the track owner can delete it")

    track = await Track.get(track_id)
    if not track:
        raise ResourceNotFoundError(message="Track not found")

    prior_snapshot = await export_node(track)  # D-03 before-snapshot
    # Route through delete_track_and_nested_content so the contained
    # entries go through delete_entry_fast (cascade=False + targeted
    # comment cleanup). The default ``track.delete()`` invokes
    # jvspatial's recursive cascade, which on a track with tagged
    # entries would explode to O(entries × tag-fanout) DB round-trips.
    # See app/services/entry_deletion.py for the rationale.
    from app.services.app_deletion import delete_track_and_nested_content

    await delete_track_and_nested_content(track)

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="track.delete",
        resource_type="Track",
        resource_id=track_id,
        before=prior_snapshot,
        after=None,
        scope=f"track:{track_id}",
    )

    return {"message": "Track deleted successfully", "deleted_track_id": track_id}


@endpoint("/tracks/{track_id}/entries", methods=["GET"], auth=True, tags=["Tracks"])
async def get_track_entries(
    request: Request,
    track_id: str,
    cursor: Optional[str] = None,
    limit: int = 20,
    q: Optional[str] = None,
    view_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Get entries in a track, filtered by the user's visibility access.

    When ``view_id`` is provided, applies that view's entry-type constraint
    (``View.entry_type_keys``) so view tabs hide entries of unrelated types.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="track.read",
        resource=Resource(kind="track", id=track_id, scope=f"track:{track_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    track = await Track.get(track_id)
    if not track:
        raise ResourceNotFoundError(message="Track not found")

    from app.api.entries import enrich_entry_page_for_response
    from app.models.nodes import View as ViewNode
    from app.services.entry_listing import fetch_accessible_entries_page

    view_node = None
    if view_id:
        view_node = await ViewNode.get(view_id)

    page_entries, response = await fetch_accessible_entries_page(
        user_id,
        track_id=track_id,
        view_node=view_node,
        q=q,
        cursor=cursor,
        limit=limit,
        include_total=True,
    )
    enriched = await enrich_entry_page_for_response(page_entries)
    response["entries"] = enriched
    response["track_id"] = track_id
    return response


@endpoint(
    "/tracks/{track_id}/collaborators", methods=["POST"], auth=True, tags=["Tracks"]
)
async def add_collaborator(
    request: Request,
    track_id: str,
    collaborator_user_id: str,
    role: str = "editor",
) -> Dict[str, Any]:
    """Add a collaborator to a track (owner only)."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    result = await sharing_add_collaborator(
        user_id, "track", track_id, collaborator_user_id, role
    )
    return {
        "message": "Collaborator added successfully",
        "track_id": track_id,
        "collaborator_user_id": collaborator_user_id,
        "role": role,
        "auto_added_to_org_pool": bool(result.get("auto_added_to_workspace_pool")),
    }


@endpoint(
    "/tracks/{track_id}/collaborators", methods=["GET"], auth=True, tags=["Tracks"]
)
async def list_collaborators(request: Request, track_id: str) -> Dict[str, Any]:
    """List all collaborators for a track, including app-inherited members.

    Legacy enriched surface for the track-detail modal (full user exports).
    For permission UI that only needs role buckets, prefer the canonical
    ``GET /tracks/{id}/access`` snapshot via ``services/sharing.list_access``.

    Integral's access model is inheritance with explicit deny. The response
    enumerates every user that has effective access to the track:

    * the track owner (``source="owner"``);
    * direct ``COLLABORATES_ON`` users (``source="direct"``);
    * users inheriting via a parent App (``source="app"`` with
      ``source_app_id`` set), capped at a sensible limit.

    Each row carries ``excluded`` (true when an ``EXCLUDED_FROM`` edge
    blocks the inherited path) and ``effective_access`` (``False`` only for
    excluded inherited rows — direct collaborators and the owner are never
    denied).

    Workspace-visibility members (``track.visibility="workspace"``) and public
    visibility are signaled via the ``visibility_grant`` field rather than
    enumerated, because those membership pools can be large and per-user
    enumeration is not the right surface here.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="track.read",
        resource=Resource(kind="track", id=track_id, scope=f"track:{track_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    track = await Track.get(track_id)
    if not track:
        raise ResourceNotFoundError(message="Track not found")

    ctx = await track.get_context()

    # Collect exclusion edges for this track, keyed by user_id.
    excluded_edges = await ctx.find_edges_between(
        None, track.id, edge_class=EXCLUDED_FROM
    )
    excluded_by_user: Dict[str, Any] = {e.source: e for e in excluded_edges}

    # Direct COLLABORATES_ON edges. `track.nodes(edge=[...])` does not
    # reliably filter by edge type in the underlying jvspatial query path,
    # so derive the authoritative set from the typed edges instead.
    direct_role_by_user: Dict[str, str] = {}
    collab_edges = await ctx.find_edges_between(
        None, track.id, edge_class=COLLABORATES_ON
    )
    for ce in collab_edges:
        direct_role_by_user[ce.source] = getattr(ce, "role", "viewer")
    direct_collab_ids = set(direct_role_by_user.keys())
    raw_direct_collabs = await track.nodes(
        edge=["COLLABORATES_ON"],
        direction="in",
        node=["User"],
        limit=max(len(direct_collab_ids), 1) + 50,
    )
    direct_collabs = [c for c in raw_direct_collabs if c.id in direct_collab_ids]

    # Parent apps of this track. App-cascade inherits role.
    parent_spaces = await track.nodes(
        edge=["CONTAINS"], direction="in", node=["WorkspaceApp"], limit=50
    )

    # Build the unified row map keyed by user id. Direct/owner rows beat
    # inherited rows for the same user.
    rows: Dict[str, Dict[str, Any]] = {}

    owner_node = await get_user_node(track.owner_id)
    if owner_node:
        # Never full-export User — preferences hold reset/OTP hashes.
        data = await public_user_view(owner_node)
        data["role"] = "owner"
        data["source"] = "owner"
        data["excluded"] = False
        data["effective_access"] = True
        rows[owner_node.id] = data

    for collab in direct_collabs:
        if collab.id in rows:
            continue
        data = await public_user_view(collab)
        data["role"] = direct_role_by_user.get(collab.id, "viewer")
        data["source"] = "direct"
        data["excluded"] = False
        data["effective_access"] = True
        rows[collab.id] = data

    # Cap on the number of inherited rows surfaced. The cascade can include
    # large org-attached apps; the modal isn't the right place to render
    # hundreds. Direct/owner rows are never capped.
    inherited_cap = 500
    inherited_count = 0
    inherited_truncated = False

    for sp in parent_spaces:
        space_owner_id = getattr(sp, "owner_id", None) or getattr(
            sp, "owner_user_id", None
        )
        # App owner inherits as editor unless already represented.
        if space_owner_id and space_owner_id not in rows:
            if inherited_count >= inherited_cap:
                inherited_truncated = True
                break
            owner = await get_user_node(space_owner_id)
            if owner:
                data = await public_user_view(owner)
                data["role"] = "editor"
                # ``source_role`` carries the uncapped parent role for
                # display only — effective auth still uses capped ``role``
                # per I-ROLE-02. Lets the UI label the App owner as
                # "Owner (via App)" instead of "Editor", restoring
                # ownership lineage without granting silent track-config
                # rights.
                data["source_role"] = "owner"
                data["source"] = "app"
                data["source_app_id"] = sp.id
                data["source_app_name"] = getattr(sp, "name", None)
                data["source_space_name"] = data["source_app_name"]
                excl = excluded_by_user.get(owner.id)
                data["excluded"] = excl is not None
                data["effective_access"] = excl is None
                rows[owner.id] = data
                inherited_count += 1

        # App collaborators inherit per their App role.
        space_collabs = await sp.nodes(
            edge=["COLLABORATES_ON"],
            direction="in",
            node=["User"],
            limit=500,
        )
        sp_role_edges = await ctx.find_edges_between(
            None, sp.id, edge_class=COLLABORATES_ON
        )
        sp_role_by_user = {
            e.source: getattr(e, "role", "viewer") for e in sp_role_edges
        }
        # Filter to true COLLABORATES_ON users; track.nodes(edge=) does not
        # reliably restrict by edge type (see direct_collabs above).
        sp_collab_ids = set(sp_role_by_user.keys())
        space_collabs = [c for c in space_collabs if c.id in sp_collab_ids]
        for sc in space_collabs:
            if sc.id in rows:
                continue
            if inherited_count >= inherited_cap:
                inherited_truncated = True
                break
            sp_role = sp_role_by_user.get(sc.id, "viewer")
            # Inherited-role mapping mirrors _cap_inherited_role in
            # services/permissions.py — track-config authority NEVER
            # cascades (I-ROLE-02). Anything above editor caps to editor;
            # roles at or below editor pass through unchanged:
            #   App owner     → Track editor (entry CRUD only)
            #   App admin     → Track editor
            #   App editor    → Track editor
            #   App commenter → Track commenter (comment + react)
            #   App viewer    → Track viewer
            # To grant track-config rights, add the user as a direct
            # admin/owner on this Track via the collaborators surface.
            if sp_role in ("owner", "admin"):
                inherited_role = "editor"
            elif sp_role in ("editor", "commenter", "viewer"):
                inherited_role = sp_role
            else:
                inherited_role = "viewer"
            data = await public_user_view(sc)
            data["role"] = inherited_role
            # ``source_role`` carries the uncapped App-side role for
            # display; ``role`` (capped) is the effective inherited
            # role per I-ROLE-02.
            data["source_role"] = sp_role
            data["source"] = "app"
            data["source_app_id"] = sp.id
            data["source_app_name"] = getattr(sp, "name", None)
            data["source_space_name"] = data["source_app_name"]
            excl = excluded_by_user.get(sc.id)
            data["excluded"] = excl is not None
            data["effective_access"] = excl is None
            rows[sc.id] = data
            inherited_count += 1
        if inherited_truncated:
            break

    result = list(rows.values())
    effective_count = sum(1 for r in result if r.get("effective_access"))

    visibility_grant: Optional[str] = None
    track_vis = getattr(track, "visibility", None)
    if track_vis == "public":
        visibility_grant = "public"
    elif track_vis in ("workspace", "organization") and bool(
        getattr(track, "workspace_id", None)
    ):
        visibility_grant = "workspace"

    # Effective role for the authenticated caller — includes staff implicit
    # grants and App cascade that are not always enumerated in ``collaborators``
    # (visibility pools are summarized via ``visibility_grant`` only). Frontend
    # permission gates must prefer this over scanning the list so org staff /
    # App editors can comment on public/workspace-visible tracks.
    caller_role = await resolve_role(user_id, "track", track_id)

    return {
        "collaborators": result,
        "total": len(result),
        "effective_total": effective_count,
        "inherited_truncated": inherited_truncated,
        "visibility_grant": visibility_grant,
        "caller_role": caller_role,
        "track_id": track_id,
    }


@endpoint(
    "/tracks/{track_id}/mention-candidates",
    methods=["GET"],
    auth=True,
    tags=["Tracks"],
)
async def list_mention_candidates(
    request: Request,
    track_id: str,
    q: str = "",
    limit: int = 8,
) -> Dict[str, Any]:
    """List users eligible to be ``@``-mentioned on this track.

    Mirrors the access-model cascade so the @-picker can never surface a
    user that lacks effective visibility on the track:

      * ``track.visibility == "public"`` → fall through to the global user
        search the picker used pre-scoping (anyone authenticated can read).
      * Otherwise → owner + direct ``COLLABORATES_ON`` + app-inherited
        ``COLLABORATES_ON``, minus ``EXCLUDED_FROM`` (deny overrides
        inherited paths only — direct/owner survive). Workspace-visibility
        tracks do NOT enumerate the workspace member pool: visibility on a
        track is not the same as a working relationship with it, and the
        picker stays tight to keep drive-by mentions off shared boards.

    Query ``q`` is case-insensitive contains against display_name and the
    email local-part. Cap defaults to 8, clamps to [1, 50].
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="track.read",
        resource=Resource(kind="track", id=track_id, scope=f"track:{track_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    track_raw = await Track.get(track_id)
    if not track_raw:
        raise ResourceNotFoundError(message="Track not found")
    # ``Node.get`` is typed ``Object | None``; the registry partitioning
    # guarantees the row is a ``Track`` if non-None, so narrow once for
    # the rest of the handler.
    track = cast(Track, track_raw)

    try:
        cap = int(limit or 8)
    except (TypeError, ValueError):
        cap = 8
    cap = max(1, min(cap, 50))
    q_norm = (q or "").strip().casefold()
    track_vis = getattr(track, "visibility", None)

    async def _hydrate(u: Any) -> Dict[str, Any]:
        # Projection, not a raw export: this is a collaborator picker, so it
        # needs id/display_name/avatar plus ``email`` to disambiguate people
        # with the same name — but never ``preferences`` (holds the
        # password-reset slot) or ``notification_preferences`` (holds the
        # verified WhatsApp phone). See app/api/utils.py::public_user_view.
        data = await public_user_view(u)
        if getattr(u, "user_id", None):
            try:
                from jvspatial.api.auth.models import User as AuthUser

                au = await AuthUser.get(u.user_id)
                if au:
                    data["email"] = getattr(au, "email", "") or ""
            except Exception:
                pass
        return data

    def _q_match(data: Dict[str, Any]) -> bool:
        if not q_norm:
            return True
        dn = (data.get("display_name") or "").casefold()
        if q_norm in dn:
            return True
        email = (data.get("email") or "").casefold()
        local = email.split("@", 1)[0] if email else ""
        return bool(local and q_norm in local)

    caller_node = await get_user_node(user_id)
    caller_node_id = caller_node.id if caller_node else None

    def _is_caller(u: Any) -> bool:
        return u.id == caller_node_id or getattr(u, "user_id", None) == user_id

    if track_vis == "public":
        import re as _re

        from jvspatial.core.pager import ObjectPager

        filters: Dict[str, Any] = {}
        if q_norm:
            filters["context.display_name"] = {
                "$regex": _re.escape(q),
                "$options": "i",
            }
        # Pull a wider page than the cap so an email-local-part match can
        # bubble in even when the display_name DB filter eliminates it.
        pager = ObjectPager(User, page_size=max(cap * 4, 32), filters=filters)
        users: List[Any] = await pager.get_page(page=1)
        out: List[Dict[str, Any]] = []
        for u in users:
            if _is_caller(u):
                continue
            data = await _hydrate(u)
            if not _q_match(data):
                continue
            out.append(data)
            if len(out) >= cap:
                break
        return {
            "users": out,
            "track_id": track_id,
            "visibility_grant": "public",
            "total": len(out),
        }

    ctx = await track.get_context()

    # ``find_edges_between`` accepts ``None`` for either endpoint at runtime
    # (wildcard match) but the jvspatial stub declares the parameters as
    # ``str``. Silence the stub-bug at each call site rather than rewrite
    # the upstream typing.
    excluded_edges = await ctx.find_edges_between(
        None,  # type: ignore[arg-type]
        track.id,
        edge_class=EXCLUDED_FROM,
    )
    excluded_user_ids = {e.source for e in excluded_edges}

    candidates: Dict[str, Any] = {}  # User.id → User node, insertion-ordered
    direct_user_ids: set = set()
    owner_id = getattr(track, "owner_id", None)

    if owner_id:
        owner = await get_user_node(owner_id)
        if owner:
            candidates[owner.id] = owner

    direct_collab_edges = await ctx.find_edges_between(
        None,  # type: ignore[arg-type]
        track.id,
        edge_class=COLLABORATES_ON,
    )
    direct_collab_ids = {e.source for e in direct_collab_edges}
    inherited_cap = 500
    raw_direct = await track.nodes(
        edge=["COLLABORATES_ON"],
        direction="in",
        node=["User"],
        limit=inherited_cap,
    )
    for u in raw_direct:
        if u.id in direct_collab_ids:
            candidates[u.id] = u
            direct_user_ids.add(u.id)

    inherited_count = 0
    parent_spaces = await track.nodes(
        edge=["CONTAINS"], direction="in", node=["WorkspaceApp"], limit=50
    )
    for sp in parent_spaces:
        if inherited_count >= inherited_cap:
            break
        sp_owner_id = getattr(sp, "owner_id", None) or getattr(
            sp, "owner_user_id", None
        )
        if sp_owner_id and sp_owner_id not in candidates:
            sp_owner = await get_user_node(sp_owner_id)
            if sp_owner:
                candidates[sp_owner.id] = sp_owner
                inherited_count += 1
        sp_collab_edges = await ctx.find_edges_between(
            None,  # type: ignore[arg-type]
            sp.id,
            edge_class=COLLABORATES_ON,
        )
        sp_collab_ids = {e.source for e in sp_collab_edges}
        sp_users = await sp.nodes(
            edge=["COLLABORATES_ON"],
            direction="in",
            node=["User"],
            limit=inherited_cap,
        )
        for u in sp_users:
            if u.id in candidates or u.id not in sp_collab_ids:
                continue
            if inherited_count >= inherited_cap:
                break
            candidates[u.id] = u
            inherited_count += 1

    def _has_direct_path(uid: str) -> bool:
        return uid in direct_user_ids or uid == owner_id

    out = []
    for uid, u in candidates.items():
        if _is_caller(u):
            continue
        if uid in excluded_user_ids and not _has_direct_path(uid):
            continue
        data = await _hydrate(u)
        if not _q_match(data):
            continue
        out.append(data)
        if len(out) >= cap:
            break

    return {
        "users": out,
        "track_id": track_id,
        "visibility_grant": None,
        "total": len(out),
    }


@endpoint(
    "/tracks/{track_id}/collaborators/{collaborator_user_id}",
    methods=["DELETE"],
    auth=True,
    tags=["Tracks"],
)
async def remove_collaborator(
    request: Request,
    track_id: str,
    collaborator_user_id: str,
) -> Dict[str, Any]:
    """Remove a collaborator from a track (owner only)."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    await sharing_remove_collaborator(user_id, "track", track_id, collaborator_user_id)

    return {
        "message": "Collaborator removed successfully",
        "track_id": track_id,
        "removed_collaborator_id": collaborator_user_id,
    }


@endpoint(
    "/tracks/{track_id}/collaborators/{collaborator_user_id}",
    methods=["PATCH"],
    auth=True,
    tags=["Tracks"],
)
async def update_collaborator_role(
    request: Request,
    track_id: str,
    collaborator_user_id: str,
    role: str,
) -> Dict[str, Any]:
    """Change an existing direct collaborator's role (owner only).

    Body accepts a role from the substrate ladder
    (``admin | editor | commenter | viewer`` — ``owner`` is handled by
    the separate transfer-ownership flow). Emits
    ``track.collaborator_role_update`` ChangeEvent with before/after
    role snapshots and notifies the collaborator.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    result = await sharing_update_collaborator_role(
        user_id, "track", track_id, collaborator_user_id, role
    )
    return {
        "message": "Collaborator role updated",
        "track_id": track_id,
        **result,
    }


@endpoint(
    "/tracks/{track_id}/exclusions",
    methods=["POST"],
    auth=True,
    tags=["Tracks"],
)
async def add_exclusion(
    request: Request,
    track_id: str,
    user_id_to_exclude: str,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Explicitly deny a user's inherited access to this track.

    Integral's access model is inheritance with explicit deny: app and
    organization cascades automatically grant access to contained tracks,
    and this endpoint creates an ``EXCLUDED_FROM`` edge that overrides that
    inherited grant for a specific user. Direct collaborators are never
    blocked by an exclusion — exclusion is intended for inherited users only.

    Owner-only.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    await sharing_add_exclusion(user_id, "track", track_id, user_id_to_exclude, reason)

    return {
        "message": "User excluded successfully",
        "track_id": track_id,
        "excluded_user_id": user_id_to_exclude,
    }


@endpoint(
    "/tracks/{track_id}/exclusions/{user_id_to_restore}",
    methods=["DELETE"],
    auth=True,
    tags=["Tracks"],
)
async def remove_exclusion(
    request: Request,
    track_id: str,
    user_id_to_restore: str,
) -> Dict[str, Any]:
    """Restore a previously excluded user's inherited access.

    Deletes the ``EXCLUDED_FROM`` edge between user and track. The user
    will regain whatever inherited access their app/org cascade grants.
    Owner-only.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    await sharing_remove_exclusion(user_id, "track", track_id, user_id_to_restore)

    return {
        "message": "User restored successfully",
        "track_id": track_id,
        "restored_user_id": user_id_to_restore,
    }


@endpoint(
    "/tracks/{track_id}/transfer-ownership",
    methods=["POST"],
    auth=True,
    tags=["Tracks"],
)
async def post_transfer_track_ownership(
    request: Request,
    track_id: str,
    new_owner_user_id: str,
) -> Dict[str, Any]:
    """Transfer track ownership to an existing collaborator (current owner only)."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="track.delete",
        resource=Resource(kind="track", id=track_id, scope=f"track:{track_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(
            message="Only the track owner can transfer ownership",
        )
    prior_track = await Track.get(track_id)
    prior_snapshot = await export_node(prior_track) if prior_track else None  # D-03
    track = await transfer_track_ownership(
        track_id=track_id,
        acting_user_id=user_id,
        new_owner_user_id=new_owner_user_id,
    )

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="track.update",
        resource_type="Track",
        resource_id=track.id,
        before=prior_snapshot,
        after=await export_node(track),
        scope=f"track:{track.id}",
    )

    return {
        "message": "Ownership transferred",
        "track": await export_node(track),
        "track_id": track_id,
        "new_owner_user_id": new_owner_user_id,
    }


# ---------------------------------------------------------------------------
# Phase 4 (MEM-03) — promote a scratch entry into a domain Track.
#
# CONTEXT lock #7: endpoint path is EXACTLY
# ``POST /api/tracks/{target_track_id}/promote-scratch-entry``. Do NOT
# rename to ``/promote`` or any variant. The Phase-3 mcp_adapter walk
# auto-registers this as ``integral_create_promotescratchentry`` (locked
# auto-derived name; see CONTEXT lock #10).
# ---------------------------------------------------------------------------


@endpoint(
    "/tracks/{target_track_id}/promote-scratch-entry",
    methods=["POST"],
    auth=True,
    tags=["Tracks", "AgentScratch"],
)
async def post_promote_scratch_entry(
    request: Request,
    target_track_id: str,
    source_entry_id: str = "",
) -> Dict[str, Any]:
    """MEM-03 — promote a scratch Entry to a domain Track.

    Body: ``{"source_entry_id": str}``
    Returns: ``{"entry": <new entry>, "message": "Entry promoted successfully"}``

    Permission gates: the source MUST live in the caller's OWN
    ``kind='agent_scratch'`` Track (I-SCRATCH-01/02 — otherwise 403), then
    ``policy_engine.evaluate(entry.read on source)`` AND
    ``policy_engine.evaluate(entry.create on target)``. The source entry
    is archived (``status='archived'``) — NOT hard-deleted (CONTEXT lock #2).
    """
    from app.services.agent_scratch import promote_scratch_entry

    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    if not source_entry_id:
        raise BadRequestError(message="source_entry_id is required")

    new_entry = await promote_scratch_entry(
        user_id=user_id,
        entry_id=source_entry_id,
        target_track_id=target_track_id,
    )

    entry_data = await export_node(new_entry)
    await attach_track_and_space(entry_data, new_entry)

    return {"entry": entry_data, "message": "Entry promoted successfully"}


@endpoint("/tracks/{track_id}/watchers", methods=["GET"], auth=True, tags=["Tracks"])
async def get_track_watchers(request: Request, track_id: str) -> Dict[str, Any]:
    """Get the list of watchers for a track."""
    validate_id(track_id, "track_id")
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    track = await Track.get(track_id)
    if not track:
        raise ResourceNotFoundError(message="Track not found")

    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="track.read",
        resource=Resource(kind="track", id=track_id, scope=f"track:{track_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    ctx = await track.get_context()
    watches_edges = await ctx.find_edges_between(None, track.id, edge_class=WATCHES)
    watcher_ids = {e.source for e in watches_edges}
    raw_watchers = await track.nodes(
        edge=[WATCHES], direction="in", node=["User"], limit=max(len(watcher_ids), 1)
    )
    watchers = [w for w in raw_watchers if w.id in watcher_ids]

    user_node = await get_user_node(user_id)
    user_node_id = user_node.id if user_node else None

    watcher_exports = []
    is_watching = False
    for w in watchers:
        # Never full-export User in a list other users can read: a raw export
        # carries `preferences` (the email-verification OTP slot),
        # `notification_preferences` (phone_e164), `email_verified` and
        # `active_workspace_id`. The watcher UI reads only display_name,
        # avatar_url, avatar_attachment_id and id — all inside the allowlist —
        # so unlike the members list this needs no field restored.
        watcher_exports.append(await public_user_view(w))
        if user_node_id and w.id == user_node_id:
            is_watching = True

    return {
        "watchers": watcher_exports,
        "is_watching": is_watching,
        "count": len(watchers),
    }


@endpoint("/tracks/{track_id}/watch", methods=["POST"], auth=True, tags=["Tracks"])
async def watch_track(request: Request, track_id: str) -> Dict[str, Any]:
    """Watch a track for updates."""
    validate_id(track_id, "track_id")
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    track = await Track.get(track_id)
    if not track:
        raise ResourceNotFoundError(message="Track not found")

    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="track.read",
        resource=Resource(kind="track", id=track_id, scope=f"track:{track_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    user_node = await get_user_node(user_id)
    if not user_node:
        raise ResourceNotFoundError(message="User not found")

    from app.services.watchers import ensure_watch_edge

    await ensure_watch_edge(user_node, track)

    return {"message": "Watching track", "is_watching": True}


@endpoint("/tracks/{track_id}/unwatch", methods=["POST"], auth=True, tags=["Tracks"])
async def unwatch_track(request: Request, track_id: str) -> Dict[str, Any]:
    """Stop watching a track."""
    validate_id(track_id, "track_id")
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    track = await Track.get(track_id)
    if not track:
        raise ResourceNotFoundError(message="Track not found")

    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="track.read",
        resource=Resource(kind="track", id=track_id, scope=f"track:{track_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    user_node = await get_user_node(user_id)
    if not user_node:
        raise ResourceNotFoundError(message="User not found")

    ctx = await track.get_context()
    existing_edges = await ctx.find_edges_between(
        user_node.id, track.id, edge_class=WATCHES
    )
    for edge in existing_edges:
        await edge.delete()

    return {"message": "Stopped watching track", "is_watching": False}
