"""Tag CRUD API endpoints with track scoping and permission checks."""

import logging
from typing import Any, Dict, Optional

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    MissingAuthenticationError,
    ResourceNotFoundError,
)
from app.api.utils import export_node, resolve_principal_id
from app.api.validators_common import (
    compute_fold,
    non_empty_after_strip,
    validate_hex_color,
)
from app.models.nodes import App, Tag, Track
from app.schemas.policy import Resource, Subject
from app.services.app_graph import (
    get_app_attached_content_profile,
    get_track_attached_content_profile,
)
from app.services.change_event import emit_change_event
from app.services.content_profile_runtime import sync_attached_manifest
from app.services.policy_engine import evaluate as policy_evaluate
from app.services.request_scope import resolve_workspace_id_from_request
from app.services.tag_service import create_tag_for_scope
from app.services.uniqueness import assert_unique

logger = logging.getLogger(__name__)


async def _check_tag_view_access(user_id: str, tag: Tag) -> None:
    """Raise permission error if user cannot view the tag's owning track/App."""
    if tag.track_id:
        _decision = await policy_evaluate(
            subject=Subject(kind="human", id=user_id),
            action="track.read",
            resource=Resource(
                kind="track",
                id=tag.track_id,
                scope=f"track:{tag.track_id}",
            ),
        )
        if not _decision.allowed:
            raise InsufficientPermissionsError(message="Access denied")
    app_id = getattr(tag, "app_id", None)
    if app_id:
        _decision = await policy_evaluate(
            subject=Subject(kind="human", id=user_id),
            action="app.read",
            resource=Resource(
                kind="app",
                id=app_id,
                scope=f"app:{app_id}",
            ),
        )
        if not _decision.allowed:
            raise InsufficientPermissionsError(message="Access denied")


async def _check_tag_edit_access(user_id: str, tag: Tag) -> None:
    """Raise permission error if user cannot edit the tag's owning track/App."""
    if tag.track_id:
        _decision = await policy_evaluate(
            subject=Subject(kind="human", id=user_id),
            action="track.update",
            resource=Resource(
                kind="track",
                id=tag.track_id,
                scope=f"track:{tag.track_id}",
            ),
        )
        if not _decision.allowed:
            raise InsufficientPermissionsError(message="Access denied")
    app_id = getattr(tag, "app_id", None)
    if app_id:
        _decision = await policy_evaluate(
            subject=Subject(kind="human", id=user_id),
            action="app.update",
            resource=Resource(
                kind="app",
                id=app_id,
                scope=f"app:{app_id}",
            ),
        )
        if not _decision.allowed:
            raise InsufficientPermissionsError(message="Access denied")


async def _validate_parent_tag_no_cycle(
    parent_tag_id: str, new_tag_id: Optional[str] = None
) -> None:
    """Validate parent_tag_id exists and won't create a circular reference.

    If ``new_tag_id`` is given (update scenario), walk up from the parent
    to ensure the new tag is not already an ancestor.
    If ``new_tag_id`` is None (create scenario, tag not yet saved), no
    upward walk is needed beyond confirming the parent itself is valid.
    """
    parent = await Tag.get(parent_tag_id)
    if not parent:
        raise BadRequestError(message=f"Parent tag '{parent_tag_id}' not found")
    if new_tag_id is None:
        return
    visited: set = set()
    current_id: Optional[str] = parent_tag_id
    while current_id:
        if current_id == new_tag_id:
            raise BadRequestError(
                message="Circular parent reference: parent tag is already a descendant"
            )
        if current_id in visited:
            break
        visited.add(current_id)
        current_node = await Tag.get(current_id)
        current_id = (
            getattr(current_node, "parent_tag_id", None) if current_node else None
        )


@endpoint("/tags", methods=["GET"], auth=True, tags=["Tags"])
async def list_tags(
    request: Request,
    track_id: Optional[str] = None,
    app_id: Optional[str] = None,
) -> Dict[str, Any]:
    """List tags, optionally scoped to a track or app content profile."""
    user_id = resolve_principal_id(request)
    if not user_id:
        # Peer handlers raise here. These four used to return an empty
        # collection. auth=True means an authenticated request whose
        # principal still failed to resolve — surface it (see
        # test_list_endpoints_auth_envelope).
        raise MissingAuthenticationError(message="Authentication required")
    if track_id and app_id:
        raise BadRequestError(message="Specify only one of track_id or app_id")
    if track_id:
        _decision = await policy_evaluate(
            subject=Subject(kind="human", id=user_id),
            action="track.read",
            resource=Resource(kind="track", id=track_id, scope=f"track:{track_id}"),
        )
        if not _decision.allowed:
            raise InsufficientPermissionsError(message="Access denied")
        tags = await Tag.find({"context.track_id": track_id})
    elif app_id:
        _decision = await policy_evaluate(
            subject=Subject(kind="human", id=user_id),
            action="app.read",
            resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
        )
        if not _decision.allowed:
            raise InsufficientPermissionsError(message="Access denied")
        tags = await Tag.find({"context.app_id": app_id})
    else:
        # No filter supplied. This branch used to be a bare ``Tag.find()`` --
        # every tag in every workspace on the deployment, with none of the
        # ``policy_evaluate`` gating its two siblings above apply. Scope it to
        # the caller's workspace instead of dumping the table, then post-filter
        # by the same track.read / app.read policy the filtered branches use.
        #
        # ``Tag`` carries ``app_id`` / ``track_id`` but no ``workspace_id``, so
        # the workspace filter has to go through the containers.
        # ``resolve_workspace_id_from_request`` is fail-closed and validates
        # membership, so reaching here means the caller can read this workspace.
        #
        # Callers that land here: the ``integral_list_tags`` agent tool invoked
        # with neither argument, and direct API consumers. Returning 400 would
        # be tighter but breaks both, so scope + policy-filter rather than reject.
        workspace_id = await resolve_workspace_id_from_request(request, user_id)
        if not workspace_id:
            return {"tags": [], "total": 0}
        ws_apps = await App.find({"context.workspace_id": workspace_id})
        ws_tracks = await Track.find({"context.workspace_id": workspace_id})
        app_ids = [str(a.id) for a in ws_apps or []]
        track_ids = [str(t.id) for t in ws_tracks or []]
        if not app_ids and not track_ids:
            return {"tags": [], "total": 0}
        tags = await Tag.find(
            {
                "$or": [
                    {"context.app_id": {"$in": app_ids}},
                    {"context.track_id": {"$in": track_ids}},
                ]
            }
        )
        # Per-parent policy cache — workspace membership ≠ track/app read.
        track_ok: Dict[str, bool] = {}
        app_ok: Dict[str, bool] = {}
        filtered = []
        for tag in tags:
            tid = getattr(tag, "track_id", None) or ""
            aid = getattr(tag, "app_id", None) or ""
            allowed = True
            if tid:
                if tid not in track_ok:
                    decision = await policy_evaluate(
                        subject=Subject(kind="human", id=user_id),
                        action="track.read",
                        resource=Resource(kind="track", id=tid, scope=f"track:{tid}"),
                    )
                    track_ok[tid] = bool(decision.allowed)
                allowed = allowed and track_ok[tid]
            if aid and allowed:
                if aid not in app_ok:
                    decision = await policy_evaluate(
                        subject=Subject(kind="human", id=user_id),
                        action="app.read",
                        resource=Resource(kind="app", id=aid, scope=f"app:{aid}"),
                    )
                    app_ok[aid] = bool(decision.allowed)
                allowed = allowed and app_ok[aid]
            if allowed and (tid or aid):
                filtered.append(tag)
        tags = filtered

    items = [await export_node(t) for t in tags]
    return {"tags": items, "total": len(items)}


@endpoint("/tags", methods=["POST"], auth=True, tags=["Tags"])
async def create_tag(
    request: Request,
    name: str,
    track_id: str = "",
    app_id: str = "",
    color: str = "#6B7280",
    group_key: Optional[str] = None,
    aliases: Optional[list[str]] = None,
    parent_tag_id: Optional[str] = None,
    applies_to_entry_types: Optional[list[str]] = None,
) -> Dict[str, Any]:
    """Create a tag under a track or app-attached content profile (editor/owner)."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    tid = str(track_id or "").strip()
    sid = str(app_id or "").strip()
    if bool(tid) == bool(sid):
        raise BadRequestError(message="Provide exactly one of track_id or app_id")

    if parent_tag_id:
        await _validate_parent_tag_no_cycle(parent_tag_id)

    safe_name = non_empty_after_strip(name, "name")
    if len(safe_name) > 80:
        raise BadRequestError(message="name must be 80 characters or fewer")
    name_fold = compute_fold(safe_name)
    safe_color = validate_hex_color(color, allow_empty=False) if color else "#6b7280"

    tag = await create_tag_for_scope(
        user_id,
        name=safe_name,
        name_fold=name_fold,
        color=safe_color,
        track_id=tid,
        app_id=sid,
        group_key=group_key,
        aliases=aliases,
        parent_tag_id=parent_tag_id,
        applies_to_entry_types=applies_to_entry_types,
    )
    return {"tag": await export_node(tag), "message": "Tag created successfully"}


@endpoint("/tags/{tag_id}", methods=["GET"], auth=True, tags=["Tags"])
async def get_tag(request: Request, tag_id: str) -> Dict[str, Any]:
    """Get a specific tag by ID."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    tag = await Tag.get(tag_id)
    if not tag:
        raise ResourceNotFoundError(message="Tag not found")

    await _check_tag_view_access(user_id, tag)

    return {"tag": await export_node(tag)}


@endpoint("/tags/{tag_id}", methods=["PUT"], auth=True, tags=["Tags"])
async def update_tag(
    request: Request,
    tag_id: str,
    name: Optional[str] = None,
    color: Optional[str] = None,
    group_key: Optional[str] = None,
    aliases: Optional[list[str]] = None,
    parent_tag_id: Optional[str] = None,
    applies_to_entry_types: Optional[list[str]] = None,
) -> Dict[str, Any]:
    """Update a tag (editor or owner of the owning track only)."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    tag = await Tag.get(tag_id)
    if not tag:
        raise ResourceNotFoundError(message="Tag not found")

    await _check_tag_edit_access(user_id, tag)

    prior_snapshot = await export_node(tag)  # D-03 before-snapshot

    if name is not None:
        safe_name = non_empty_after_strip(name, "name")
        if len(safe_name) > 80:
            raise BadRequestError(message="name must be 80 characters or fewer")
        new_fold = compute_fold(safe_name)
        if new_fold != (getattr(tag, "name_fold", "") or ""):
            scope_query = (
                {"context.track_id": tag.track_id}
                if tag.track_id
                else {"context.app_id": tag.app_id}
            )
            scope_query["context.name_fold"] = new_fold
            await assert_unique(
                Tag,
                scope_query,
                entity="tag",
                field_label="name",
                value=safe_name,
                scope_label="in this " + ("track" if tag.track_id else "app"),
                exclude_id=tag.id,
            )
        tag.name = safe_name
        tag.name_fold = new_fold
    if color is not None:
        tag.color = validate_hex_color(color, allow_empty=False)
    if group_key is not None:
        tag.group_key = group_key
    if aliases is not None:
        tag.aliases = aliases
    if parent_tag_id is not None:
        if parent_tag_id:
            await _validate_parent_tag_no_cycle(parent_tag_id, new_tag_id=tag_id)
        tag.parent_tag_id = parent_tag_id
    if applies_to_entry_types is not None:
        tag.applies_to_entry_types = applies_to_entry_types

    await tag.save()

    # Sync the attached profile manifest
    sync_cp = None
    if tag.track_id:
        t = await Track.get(tag.track_id)
        if t:
            sync_cp = await get_track_attached_content_profile(t)
    elif tag.app_id:
        s = await App.get(tag.app_id)
        if s:
            sync_cp = await get_app_attached_content_profile(s)
    if sync_cp:
        await sync_attached_manifest(sync_cp)

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    scope_str = f"track:{tag.track_id}" if tag.track_id else f"app:{tag.app_id or ''}"
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="tag.update",
        resource_type="Tag",
        resource_id=tag.id,
        before=prior_snapshot,
        after=await export_node(tag),
        scope=scope_str,
    )

    return {"tag": await export_node(tag), "message": "Tag updated successfully"}


@endpoint("/tags/{tag_id}", methods=["DELETE"], auth=True, tags=["Tags"])
async def delete_tag(request: Request, tag_id: str) -> Dict[str, Any]:
    """Delete a tag (editor or owner of the owning track only)."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    tag = await Tag.get(tag_id)
    if not tag:
        raise ResourceNotFoundError(message="Tag not found")

    await _check_tag_edit_access(user_id, tag)

    prior_snapshot = await export_node(tag)  # D-03 before-snapshot
    scope_str = f"track:{tag.track_id}" if tag.track_id else f"app:{tag.app_id or ''}"

    # Sync manifest before deleting the node
    sync_cp = None
    if tag.track_id:
        t = await Track.get(tag.track_id)
        if t:
            sync_cp = await get_track_attached_content_profile(t)
    elif tag.app_id:
        s = await App.get(tag.app_id)
        if s:
            sync_cp = await get_app_attached_content_profile(s)

    await tag.delete()

    if sync_cp:
        await sync_attached_manifest(sync_cp)

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="tag.delete",
        resource_type="Tag",
        resource_id=tag_id,
        before=prior_snapshot,
        after=None,
        scope=scope_str,
    )

    return {"message": "Tag deleted successfully", "deleted_tag_id": tag_id}


@endpoint("/tags/{tag_id}/children", methods=["GET"], auth=True, tags=["Tags"])
async def list_tag_children(request: Request, tag_id: str) -> Dict[str, Any]:
    """Get immediate children of a tag (tags where parent_tag_id == tag_id)."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    tag = await Tag.get(tag_id)
    if not tag:
        raise ResourceNotFoundError(message="Tag not found")
    await _check_tag_view_access(user_id, tag)

    children = await Tag.find({"context.parent_tag_id": tag_id})
    items = [await export_node(c) for c in children]
    return {"tags": items, "total": len(items)}


@endpoint("/tags/{tag_id}/tree", methods=["GET"], auth=True, tags=["Tags"])
async def get_tag_tree(request: Request, tag_id: str) -> Dict[str, Any]:
    """Get full tag subtree starting from a tag (recursive, max depth 10)."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    tag = await Tag.get(tag_id)
    if not tag:
        raise ResourceNotFoundError(message="Tag not found")
    await _check_tag_view_access(user_id, tag)

    async def _build_tree(current_id: str, depth: int) -> Dict[str, Any]:
        current = await Tag.get(current_id)
        node_data = await export_node(current) if current else {}
        result: Dict[str, Any] = {"tag": node_data, "children": []}
        if depth >= 10:
            return result
        child_tags = await Tag.find({"context.parent_tag_id": current_id})
        for child in child_tags:
            result["children"].append(await _build_tree(child.id, depth + 1))
        return result

    tree = await _build_tree(tag_id, 0)
    return tree
