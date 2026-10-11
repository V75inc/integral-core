"""Unified sharing primitives for App / Track / Entry.

Phase 2 — central helpers that every collaborator-add, exclusion, and
access-listing endpoint delegates to. Eliminates the per-resource
duplication that grew up during the legacy track-only era.

Surface:

* ``add_collaborator``      — materialize ``COLLABORATES_ON{role}`` edge,
                              auto-grant guest workspace membership when
                              the recipient isn't a member, emit change
                              event + recipient notification.
* ``remove_collaborator``   — drop the edge; emit events.
* ``add_exclusion``         — materialize ``EXCLUDED_FROM`` edge to override
                              cascade for a single user; emit events.
* ``remove_exclusion``      — drop the exclusion.
* ``list_access``           — return ``{direct, inherited, excluded, links}``
                              snapshot for the Manage Access modal.
* ``ensure_guest_membership`` — idempotent IS_MEMBER_OF{role:"guest"} on a
                              workspace; the cross-workspace share primitive.

Role enum accepted on grants: ``owner | admin | editor | commenter | viewer``.
``owner`` represents a transfer-of-ownership grant; the primary OWNS edge
still represents the canonical owner and is not modified by this module.
``admin`` is the curator tier — full track-config authority (schema, views,
tags, library) plus entry CRUD, but NOT collaborator management,
share-link mint, or delete (owner-only). ``editor`` is entry CRUD only —
no track-config rights.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Literal, Optional, Tuple, cast

from app.models.edges import (
    COLLABORATES_ON,
    EXCLUDED_FROM,
    IS_MEMBER_OF,
    OWNS,
)
from app.models.nodes import (
    App,
    Entry,
    Track,
    User,
    Workspace,
)
from app.schemas.audit import ChangeEventAction
from app.services.change_event import emit_change_event
from app.services.grant_history import grant_baseline, record_grant_change
from app.services.permissions import (
    get_user_node,
    resolve_role,
)
from app.services.permissions_process_cache import (
    invalidate_user_aliases as _invalidate_perm_cache,
)
from app.services.sharing_write_scope import (
    collaboration_mutation,
    emit_sharing_change,
    sharing_write_scope,
)
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

ResourceType = Literal["app", "track", "entry"]
Role = Literal["owner", "admin", "editor", "commenter", "viewer"]

VALID_ROLES: Tuple[str, ...] = ("owner", "admin", "editor", "commenter", "viewer")


# ---------------------------------------------------------------------------
# Resource loading + workspace resolution
# ---------------------------------------------------------------------------


async def _load_resource(resource_type: ResourceType, resource_id: str) -> Any:
    if resource_type == "app":
        return await App.find_one({"id": resource_id})
    if resource_type == "track":
        return await Track.find_one({"id": resource_id})
    if resource_type == "entry":
        return await Entry.find_one({"id": resource_id})
    return None


async def _resource_workspace_id(
    resource_type: ResourceType, resource: Any
) -> Optional[str]:
    """Resolve canonical rooted containment, never scalar-cache fallback."""
    from app.services.resource_location import MANAGEMENT_APP_STATES, locate_resource

    location = await locate_resource(
        resource_type, resource.id, allowed_app_states=MANAGEMENT_APP_STATES
    )
    return location.workspace_id if location is not None else None


async def _resource_label(resource_type: ResourceType, resource: Any) -> str:
    if resource_type == "app":
        return getattr(resource, "name", "") or "this app"
    if resource_type == "track":
        return getattr(resource, "title", "") or "this track"
    if resource_type == "entry":
        return getattr(resource, "title", "") or "this entry"
    return "this resource"


# ---------------------------------------------------------------------------
# Auto-guest workspace membership
# ---------------------------------------------------------------------------


async def _materialize_guest_membership(
    collaborator: User, workspace_id: str, inviter_user_id: str, source_label: str = ""
) -> bool:
    if not workspace_id:
        return False
    async with sharing_write_scope(workspace_id):
        current = await get_user_node(collaborator.id)
        if current is None:
            from app.api.errors import ResourceNotFoundError

            raise ResourceNotFoundError(message="User not found")
        return await _materialize_guest_membership_unlocked(
            current, workspace_id, inviter_user_id, source_label
        )


async def _materialize_guest_membership_unlocked(
    collaborator: User,
    workspace_id: str,
    inviter_user_id: str,
    source_label: str = "",
) -> bool:
    """Core guest-membership materialization — shared by add_collaborator."""
    if not workspace_id:
        return False
    ws = await Workspace.find_one({"id": workspace_id})
    if not ws:
        return False
    now = utc_now_iso()
    from app.services.invitation_grants import _current_grant

    existing = await _current_grant(collaborator.id, ws.id, IS_MEMBER_OF)
    await grant_baseline(
        source_id=collaborator.id,
        target_id=ws.id,
        grant_class=IS_MEMBER_OF,
        grant=existing,
    )
    if existing is not None:
        return False
    await collaborator.connect(
        ws,
        edge=IS_MEMBER_OF,
        role="guest",
        joined_at=now,
        can_create_apps=False,
        can_create_tracks=False,
    )
    current = await _current_grant(collaborator.id, ws.id, IS_MEMBER_OF)
    clock = await record_grant_change(
        source=collaborator,
        target=ws,
        grant_class=IS_MEMBER_OF,
        before=None,
        after=current,
    )
    if current is None or clock is None:
        from app.exceptions import AuthorizationUnavailableError

        raise AuthorizationUnavailableError()
    await emit_sharing_change(
        clock=clock,
        emitter=emit_change_event,
        actor_kind="human",
        actor_id=inviter_user_id,
        action="workspace.member_add",
        resource_type="Workspace",
        resource_id=ws.id,
        before=None,
        after={
            "workspace_id": ws.id,
            "member_user_id": collaborator.id,
            "role": "guest",
            "auto_added_from": source_label or "share",
        },
        scope=f"user:{inviter_user_id}",
    )
    _invalidate_perm_cache(collaborator)
    return True


async def ensure_guest_membership(
    collaborator: User,
    workspace_id: str,
    inviter_user_id: str,
    source_label: str = "",
) -> bool:
    """Idempotently materialize ``IS_MEMBER_OF{role:"guest"}`` on a workspace.

    Thin wrapper over ``_materialize_guest_membership`` for share-link and
    invitation call sites. ``add_collaborator`` inlines the same logic.
    """
    return await _materialize_guest_membership(
        collaborator,
        workspace_id,
        inviter_user_id,
        source_label=source_label,
    )


async def revoke_workspace_resource_grants(
    user: User,
    workspace_id: str,
) -> Dict[str, int]:
    """Drop in-workspace ``COLLABORATES_ON`` / ``EXCLUDED_FROM`` for ``user``.

    Called when membership ends (self-leave or admin remove). Does not touch
    ``OWNS``. Callers reassign those edges to the workspace owner first via
    ``reassign_departing_member_ownership`` so a removed creator does not
    leave an unmanageable resource behind.
    """
    deleted = {"collaborates_on": 0, "excluded_from": 0}
    if not user or not workspace_id:
        return deleted

    from app.services.walkers.workspace_departure import (
        workspace_departure_grant_targets,
    )

    targets = await workspace_departure_grant_targets(user, workspace_id)
    ctx = await user.get_context()
    for resource in targets:
        for edge_class, key in (
            (COLLABORATES_ON, "collaborates_on"),
            (EXCLUDED_FROM, "excluded_from"),
        ):
            edges = await ctx.find_edges_between(
                user.id, resource.id, edge_class=edge_class
            )
            before = None
            current_resource = resource
            if edge_class is COLLABORATES_ON:
                from app.exceptions import AuthorizationUnavailableError

                rows = await ctx.database.find(
                    "edge",
                    {
                        "source": user.id,
                        "target": resource.id,
                        "entity": edge_class.__name__,
                    },
                    limit=2,
                )
                if len(rows) > 1:
                    raise AuthorizationUnavailableError()
                before = rows[0] if rows else None
                await grant_baseline(
                    source_id=user.id,
                    target_id=resource.id,
                    grant_class=edge_class,
                    grant=before,
                )
                current_resource = await type(resource).find_one({"id": resource.id})
                if current_resource is None:
                    raise AuthorizationUnavailableError()
            if edge_class is COLLABORATES_ON and before is not None:
                # The complete primary image is authoritative. A malformed
                # legacy grant can disappear from edge hydration; do not
                # advance negative history while leaving its raw grant alive.
                await ctx.database.delete("edge", before["id"])
                remaining = await ctx.database.find(
                    "edge",
                    {
                        "source": user.id,
                        "target": resource.id,
                        "entity": edge_class.__name__,
                    },
                    limit=1,
                )
                if remaining:
                    raise AuthorizationUnavailableError()
                deleted[key] += 1
                await record_grant_change(
                    source=user,
                    target=current_resource,
                    grant_class=edge_class,
                    before=before,
                    after=None,
                )
            elif edge_class is EXCLUDED_FROM:
                for edge in edges:
                    await edge.delete()
                    deleted[key] += 1

    if deleted["collaborates_on"] or deleted["excluded_from"]:
        _invalidate_perm_cache(user)
    return deleted


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


async def _entry_track_id(entry_id: str) -> Optional[str]:
    entry = await Entry.get(entry_id)
    if entry is None:
        return None
    track_id = getattr(entry, "track_id", "") or ""
    if not track_id:
        tracks = await entry.nodes(edge=["CONTAINS"], direction="in", node=["Track"])
        if tracks:
            track_id = tracks[0].id
    return track_id or None


async def _share_notification_action_url(
    resource_type: ResourceType,
    resource_id: str,
) -> tuple[Optional[str], Optional[str]]:
    """Resolve the frontend route (+ track id for entries) for share notifications."""
    from app.services.notification_paths import resolve_resource_action_url

    track_id: Optional[str] = None
    if resource_type == "entry":
        track_id = await _entry_track_id(resource_id)
    action_url = resolve_resource_action_url(
        resource_type,
        resource_id,
        track_id=track_id,
    )
    return action_url, track_id


async def _emit_share_notification(
    recipient_user_id: str,
    kind: str,
    title: str,
    body: str,
    resource_type: ResourceType,
    resource_id: str,
) -> None:
    """Best-effort recipient notification. Failures do not block the grant.

    Phase 10.5 Plan 10.5-01: routed through ``create_notification`` so
    the User —HAS_NOTIFICATION→ Notification edge is wired in the same
    transaction (I-GRAPH-01).
    """
    try:
        from app.services.app_graph import create_notification

        now = utc_now_iso()
        action_url, track_id = await _share_notification_action_url(
            resource_type,
            resource_id,
        )

        await create_notification(
            user_id=recipient_user_id,
            type=kind,
            content=f"{title}\n{body}".strip(),
            read=False,
            metadata={
                "resource_type": resource_type,
                "resource_id": resource_id,
                "track_id": track_id,
                "title": title,
                "body": body,
            },
            action_url=action_url,
            created_at=now,
        )
    except Exception:
        logger.exception(
            "failed to emit %s notification to %s for %s %s",
            kind,
            recipient_user_id,
            resource_type,
            resource_id,
        )


# ---------------------------------------------------------------------------
# Permission gates
# ---------------------------------------------------------------------------


async def _require_policy_allowed(
    actor_user_id: str,
    action: str,
    resource_type: ResourceType,
    resource_id: str,
    *,
    denied_message: str = "Access denied.",
) -> None:
    from app.api.errors import InsufficientPermissionsError
    from app.schemas.policy import Resource, Subject
    from app.services.policy_engine import evaluate as policy_evaluate

    decision = await policy_evaluate(
        subject=Subject(kind="human", id=actor_user_id),
        action=action,
        resource=Resource(
            kind=resource_type,
            id=resource_id,
            scope=f"{resource_type}:{resource_id}",
        ),
    )
    if not decision.allowed:
        raise InsufficientPermissionsError(message=denied_message)


async def _require_collaborator_manage_authority(
    actor_user_id: str,
    resource_type: ResourceType,
    resource_id: str,
    *,
    policy_action: Optional[str] = None,
) -> None:
    """Owner or admin may add/remove/change direct collaborators."""
    action = policy_action or f"{resource_type}.collaborator_add"
    await _require_policy_allowed(
        actor_user_id,
        action,
        resource_type,
        resource_id,
        denied_message="Only the resource owner or admin may manage collaborators.",
    )


async def _require_share_authority(
    actor_user_id: str,
    resource_type: ResourceType,
    resource_id: str,
    *,
    policy_action: Optional[str] = None,
) -> None:
    """Owner or admin check for share links, exclusions, and invitations."""
    action = policy_action or f"{resource_type}.share_link.mint"
    await _require_policy_allowed(
        actor_user_id,
        action,
        resource_type,
        resource_id,
        denied_message="Only the resource owner or admin may perform this action.",
    )


async def _has_owns_edge(
    actor_user_id: str,
    resource_type: ResourceType,
    resource_id: str,
) -> bool:
    """True when the actor holds a direct OWNS edge (not COLLABORATES_ON owner)."""
    user = await get_user_node(actor_user_id)
    if user is None:
        return False
    ctx = await user.get_context()
    owns_edges = await ctx.find_edges_between(user.id, resource_id, edge_class=OWNS)
    return bool(owns_edges)


async def _require_true_owner_for_owner_grant(
    actor_user_id: str,
    resource_type: ResourceType,
    resource_id: str,
    role: str,
) -> None:
    """Only true OWNS holders may grant or invite role=owner."""
    from app.api.errors import InsufficientPermissionsError

    if role != "owner":
        return
    if not await _has_owns_edge(actor_user_id, resource_type, resource_id):
        raise InsufficientPermissionsError(
            message="Only the resource owner may grant or invite the owner role."
        )


# ---------------------------------------------------------------------------
# Collaborator add / remove
# ---------------------------------------------------------------------------


@collaboration_mutation
async def add_collaborator(
    actor_user_id: str,
    resource_type: ResourceType,
    resource_id: str,
    collaborator_user_id: str,
    role: str = "editor",
) -> Dict[str, Any]:
    """Add a ``COLLABORATES_ON{role}`` edge from user → resource.

    Owner or admin (``OWNS`` or ``COLLABORATES_ON{role:"owner"|"admin"}``).
    Auto-creates ``IS_MEMBER_OF{role:"guest"}`` on the resource's workspace
    if the collaborator isn't a member yet. Emits a recipient notification.
    """
    from app.api.errors import BadRequestError, ResourceNotFoundError

    if role not in VALID_ROLES:
        raise BadRequestError(
            message=f"Role must be one of {VALID_ROLES}; got '{role}'."
        )

    await _require_collaborator_manage_authority(
        actor_user_id,
        resource_type,
        resource_id,
        policy_action=f"{resource_type}.collaborator_add",
    )
    await _require_true_owner_for_owner_grant(
        actor_user_id, resource_type, resource_id, role
    )

    resource = await _load_resource(resource_type, resource_id)
    if resource is None:
        raise ResourceNotFoundError(message=f"{resource_type.capitalize()} not found.")
    collaborator = await get_user_node(collaborator_user_id)
    if collaborator is None:
        raise ResourceNotFoundError(message="User not found.")

    workspace_id = await _resource_workspace_id(resource_type, resource)
    auto_added_guest = False
    if workspace_id:
        auto_added_guest = await _materialize_guest_membership(
            collaborator,
            workspace_id,
            actor_user_id,
            source_label=f"{resource_type}:{resource_id}",
        )

    from app.services.invitation_grants import _current_grant

    before = await _current_grant(collaborator.id, resource_id, COLLABORATES_ON)
    await grant_baseline(
        source_id=collaborator.id,
        target_id=resource_id,
        grant_class=COLLABORATES_ON,
        grant=before,
    )
    if before is not None:
        # Add is not an implicit role replacement; report the actual stored grant.
        actual_role = before["context"].get("role", before.get("role"))
        _invalidate_perm_cache(collaborator)
        return {
            "resource_type": resource_type,
            "resource_id": resource_id,
            "collaborator_user_id": collaborator.id,
            "role": actual_role,
            "auto_added_to_workspace_pool": auto_added_guest,
            "workspace_id": workspace_id,
            "outcome": "unchanged",
            "grant_id": before["id"],
            "grant_revision": before["context"].get("grant_revision", 0),
        }
    await collaborator.connect(
        resource,
        edge=COLLABORATES_ON,
        role=role,
        invited_at=utc_now_iso(),
        invited_by=actor_user_id,
    )
    current = await _current_grant(collaborator.id, resource_id, COLLABORATES_ON)
    clock = await record_grant_change(
        source=collaborator,
        target=resource,
        grant_class=COLLABORATES_ON,
        before=None,
        after=current,
    )
    if current is None or clock is None:
        from app.exceptions import AuthorizationUnavailableError

        raise AuthorizationUnavailableError()
    label = await _resource_label(resource_type, resource)
    await _emit_share_notification(
        recipient_user_id=collaborator.id,
        kind="share.collaborator_added",
        title=f"You were added to {label}",
        body=f"Role: {role}",
        resource_type=resource_type,
        resource_id=resource_id,
    )

    await emit_sharing_change(
        clock=clock,
        emitter=emit_change_event,
        actor_kind="human",
        actor_id=actor_user_id,
        action=cast(ChangeEventAction, f"{resource_type}.collaborator_add"),
        resource_type=resource_type.capitalize(),
        resource_id=resource_id,
        before=None,
        after={
            "resource_type": resource_type,
            "resource_id": resource_id,
            "collaborator_user_id": collaborator.id,
            "role": role,
            "grant_id": current["id"],
            "grant_revision": current["context"].get("grant_revision", 0),
            "history_revision": clock["context"]["revision"],
        },
        scope=f"{resource_type}:{resource_id}",
    )

    # Access changed for this user — drop their cached access aggregates so the
    # next read recomputes (the per-user process cache; see permissions_process_cache).
    _invalidate_perm_cache(collaborator)
    return {
        "resource_type": resource_type,
        "resource_id": resource_id,
        "collaborator_user_id": collaborator.id,
        "role": role,
        "auto_added_to_workspace_pool": auto_added_guest,
        "workspace_id": workspace_id,
    }


@collaboration_mutation
async def update_collaborator_role(
    actor_user_id: str,
    resource_type: ResourceType,
    resource_id: str,
    collaborator_user_id: str,
    role: str,
) -> Dict[str, Any]:
    """Update an existing ``COLLABORATES_ON{role}`` edge's role field.

    Owner-only. Idempotent — if the edge already carries ``role``, the
    edge is touched (``invited_at`` refreshed) but no diff event fires.
    Distinct from ``add_collaborator`` so the audit trail captures
    re-grants vs. role changes separately; the existing add path remains
    upsert-shaped via ``ensure_edge`` for back-compat callers, but the
    intent-specific surface is preferred.
    """
    from app.api.errors import BadRequestError, ResourceNotFoundError

    if role not in VALID_ROLES:
        raise BadRequestError(
            message=f"Role must be one of {VALID_ROLES}; got '{role}'."
        )

    await _require_collaborator_manage_authority(
        actor_user_id,
        resource_type,
        resource_id,
        policy_action=f"{resource_type}.collaborator_role_update",
    )
    await _require_true_owner_for_owner_grant(
        actor_user_id, resource_type, resource_id, role
    )

    resource = await _load_resource(resource_type, resource_id)
    if resource is None:
        raise ResourceNotFoundError(message=f"{resource_type.capitalize()} not found.")
    collaborator = await get_user_node(collaborator_user_id)
    if collaborator is None:
        raise ResourceNotFoundError(message="User not found.")

    from jvspatial.core.context import get_default_context

    from app.exceptions import AuthorizationUnavailableError
    from app.services.authority_facts import relationship_literal
    from app.services.invitation_grants import _current_grant

    before = await _current_grant(collaborator.id, resource_id, COLLABORATES_ON)
    if before is None:
        raise ResourceNotFoundError(
            message="No direct collaborator grant exists to update."
        )
    await grant_baseline(
        source_id=collaborator.id,
        target_id=resource_id,
        grant_class=COLLABORATES_ON,
        grant=before,
    )
    prior_role = relationship_literal(before, "role", VALID_ROLES)
    if prior_role == role:
        _invalidate_perm_cache(collaborator)
        return {
            "resource_type": resource_type,
            "resource_id": resource_id,
            "collaborator_user_id": collaborator.id,
            "prior_role": prior_role,
            "role": prior_role,
            "outcome": "unchanged",
            "grant_id": before["id"],
            "grant_revision": before["context"].get("grant_revision", 0),
        }
    values = {"context.role": role}
    if "role" in before:
        values["role"] = role
    if "grant_revision" in before:
        values["grant_revision"] = before["context"].get("grant_revision", 0) + 1
    result = await get_default_context().database.find_one_and_update(
        "edge",
        {
            "id": before["id"],
            "source": collaborator.id,
            "target": resource_id,
            "entity": COLLABORATES_ON.__name__,
        },
        {"$set": values, "$inc": {"context.grant_revision": 1}},
    )
    if result is None:
        raise AuthorizationUnavailableError()
    current = await _current_grant(collaborator.id, resource_id, COLLABORATES_ON)
    clock = await record_grant_change(
        source=collaborator,
        target=resource,
        grant_class=COLLABORATES_ON,
        before=before,
        after=current,
    )
    if current is None or clock is None:
        raise AuthorizationUnavailableError()
    if prior_role != role:
        label = await _resource_label(resource_type, resource)
        await _emit_share_notification(
            recipient_user_id=collaborator.id,
            kind="share.collaborator_role_changed",
            title=f"Your role on {label} changed",
            body=f"New role: {role} (was {prior_role})",
            resource_type=resource_type,
            resource_id=resource_id,
        )

    await emit_sharing_change(
        clock=clock,
        emitter=emit_change_event,
        actor_kind="human",
        actor_id=actor_user_id,
        action=cast(ChangeEventAction, f"{resource_type}.collaborator_role_update"),
        resource_type=resource_type.capitalize(),
        resource_id=resource_id,
        before={
            "resource_type": resource_type,
            "resource_id": resource_id,
            "collaborator_user_id": collaborator.id,
            "role": prior_role,
            "grant_id": before["id"],
            "grant_revision": before["context"].get("grant_revision", 0),
        },
        after={
            "resource_type": resource_type,
            "resource_id": resource_id,
            "collaborator_user_id": collaborator.id,
            "role": role,
            "grant_id": current["id"],
            "grant_revision": current["context"].get("grant_revision", 0),
            "history_revision": clock["context"]["revision"],
        },
        scope=f"{resource_type}:{resource_id}",
    )

    _invalidate_perm_cache(collaborator)
    return {
        "resource_type": resource_type,
        "resource_id": resource_id,
        "collaborator_user_id": collaborator.id,
        "prior_role": prior_role,
        "role": role,
    }


@collaboration_mutation
async def remove_collaborator(
    actor_user_id: str,
    resource_type: ResourceType,
    resource_id: str,
    collaborator_user_id: str,
) -> Dict[str, Any]:
    """Drop the COLLABORATES_ON edge from user → resource (owner-only)."""
    from app.api.errors import ResourceNotFoundError

    await _require_collaborator_manage_authority(
        actor_user_id,
        resource_type,
        resource_id,
        policy_action=f"{resource_type}.collaborator_remove",
    )

    resource = await _load_resource(resource_type, resource_id)
    if resource is None:
        raise ResourceNotFoundError(message=f"{resource_type.capitalize()} not found.")
    collaborator = await get_user_node(collaborator_user_id)
    if collaborator is None:
        raise ResourceNotFoundError(message="User not found.")

    from jvspatial.core.context import get_default_context

    from app.exceptions import AuthorizationUnavailableError
    from app.services.invitation_grants import _current_grant

    before = await _current_grant(collaborator.id, resource_id, COLLABORATES_ON)
    await grant_baseline(
        source_id=collaborator.id,
        target_id=resource_id,
        grant_class=COLLABORATES_ON,
        grant=before,
    )
    if before is None:
        return {
            "resource_type": resource_type,
            "resource_id": resource_id,
            "collaborator_user_id": collaborator.id,
            "removed_edges": 0,
        }
    await get_default_context().database.delete("edge", before["id"])
    if await _current_grant(collaborator.id, resource_id, COLLABORATES_ON) is not None:
        raise AuthorizationUnavailableError()
    clock = await record_grant_change(
        source=collaborator,
        target=resource,
        grant_class=COLLABORATES_ON,
        before=before,
        after=None,
    )
    if clock is None:
        raise AuthorizationUnavailableError()
    removed = 1
    label = await _resource_label(resource_type, resource)
    await _emit_share_notification(
        recipient_user_id=collaborator.id,
        kind="share.collaborator_removed",
        title=f"You were removed from {label}",
        body="Your access has been revoked.",
        resource_type=resource_type,
        resource_id=resource_id,
    )

    await emit_sharing_change(
        clock=clock,
        emitter=emit_change_event,
        actor_kind="human",
        actor_id=actor_user_id,
        action=cast(ChangeEventAction, f"{resource_type}.collaborator_remove"),
        resource_type=resource_type.capitalize(),
        resource_id=resource_id,
        before={
            "resource_type": resource_type,
            "resource_id": resource_id,
            "collaborator_user_id": collaborator.id,
            "grant_id": before["id"],
            "grant_revision": before["context"].get("grant_revision", 0),
        },
        after=None,
        scope=f"{resource_type}:{resource_id}",
    )

    _invalidate_perm_cache(collaborator)
    return {
        "resource_type": resource_type,
        "resource_id": resource_id,
        "collaborator_user_id": collaborator.id,
        "removed_edges": removed,
    }


# ---------------------------------------------------------------------------
# Exclusion add / remove
# ---------------------------------------------------------------------------


async def add_exclusion(
    actor_user_id: str,
    resource_type: ResourceType,
    resource_id: str,
    user_id_to_exclude: str,
    reason: str = "",
) -> Dict[str, Any]:
    """Add EXCLUDED_FROM edge to override inherited access for one user.

    Direct COLLABORATES_ON / OWNS grants still beat exclusion — to block a
    direct collaborator you remove their edge instead.
    """
    from app.api.errors import ResourceNotFoundError

    await _require_share_authority(
        actor_user_id,
        resource_type,
        resource_id,
        policy_action=f"{resource_type}.exclusion_add",
    )

    resource = await _load_resource(resource_type, resource_id)
    if resource is None:
        raise ResourceNotFoundError(message=f"{resource_type.capitalize()} not found.")
    target = await get_user_node(user_id_to_exclude)
    if target is None:
        raise ResourceNotFoundError(message="User not found.")

    from app.services.edge_upsert import ensure_edge

    await ensure_edge(
        target,
        resource,
        EXCLUDED_FROM,
        excluded_at=utc_now_iso(),
        excluded_by=actor_user_id,
        reason=reason or None,
    )

    await emit_change_event(
        actor_kind="human",
        actor_id=actor_user_id,
        action=cast(ChangeEventAction, f"{resource_type}.exclusion_add"),
        resource_type=resource_type.capitalize(),
        resource_id=resource_id,
        before=None,
        after={
            "resource_type": resource_type,
            "resource_id": resource_id,
            "excluded_user_id": target.id,
            "reason": reason or None,
        },
        scope=f"{resource_type}:{resource_id}",
    )

    _invalidate_perm_cache(target)
    return {
        "resource_type": resource_type,
        "resource_id": resource_id,
        "excluded_user_id": target.id,
    }


async def remove_exclusion(
    actor_user_id: str,
    resource_type: ResourceType,
    resource_id: str,
    user_id_to_restore: str,
) -> Dict[str, Any]:
    """Drop EXCLUDED_FROM edge — restore inherited access path."""
    from app.api.errors import ResourceNotFoundError

    await _require_share_authority(
        actor_user_id,
        resource_type,
        resource_id,
        policy_action=f"{resource_type}.exclusion_remove",
    )

    target = await get_user_node(user_id_to_restore)
    if target is None:
        raise ResourceNotFoundError(message="User not found.")

    ctx = await target.get_context()
    edges = await ctx.find_edges_between(
        target.id, resource_id, edge_class=EXCLUDED_FROM
    )
    for edge in edges:
        await edge.delete()
    removed = len(edges)

    await emit_change_event(
        actor_kind="human",
        actor_id=actor_user_id,
        action=cast(ChangeEventAction, f"{resource_type}.exclusion_remove"),
        resource_type=resource_type.capitalize(),
        resource_id=resource_id,
        before={
            "resource_type": resource_type,
            "resource_id": resource_id,
            "restored_user_id": target.id,
        },
        after=None,
        scope=f"{resource_type}:{resource_id}",
    )

    _invalidate_perm_cache(target)
    return {
        "resource_type": resource_type,
        "resource_id": resource_id,
        "restored_user_id": target.id,
        "removed_edges": removed,
    }


# ---------------------------------------------------------------------------
# Unified /access listing
# ---------------------------------------------------------------------------


async def _direct_collaborators(
    resource_type: ResourceType, resource_id: str
) -> List[Dict[str, Any]]:
    """List users with direct OWNS or COLLABORATES_ON on the resource."""
    rows: List[Dict[str, Any]] = []
    resource = await _load_resource(resource_type, resource_id)
    if resource is None:
        return rows
    ctx = await resource.get_context()

    owns_edges = await ctx.database.find(
        "edge", {"target": resource_id, "entity": OWNS.__name__}
    )
    for data in owns_edges:
        uid = data.get("source") or ""
        if uid:
            rows.append({"user_id": uid, "role": "owner", "source": "owns"})

    collab_edges = await ctx.database.find(
        "edge", {"target": resource_id, "entity": COLLABORATES_ON.__name__}
    )
    seen_users = {r["user_id"] for r in rows}
    for data in collab_edges:
        uid = data.get("source") or ""
        if not uid or uid in seen_users:
            continue
        row_ctx = data.get("context") if isinstance(data, dict) else None
        role_val = (
            row_ctx.get("role") if isinstance(row_ctx, dict) else None
        ) or data.get("role")
        rv = str(role_val or "viewer").strip().lower()
        if rv not in VALID_ROLES:
            rv = "viewer"
        rows.append({"user_id": uid, "role": rv, "source": "direct"})
        seen_users.add(uid)
    return rows


async def _excluded_users(
    resource_type: ResourceType, resource_id: str
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    resource = await _load_resource(resource_type, resource_id)
    if resource is None:
        return rows
    ctx = await resource.get_context()
    edges = await ctx.database.find(
        "edge", {"target": resource_id, "entity": EXCLUDED_FROM.__name__}
    )
    for data in edges:
        uid = data.get("source") or ""
        if not uid:
            continue
        row_ctx = data.get("context") if isinstance(data, dict) else None
        reason = (
            row_ctx.get("reason") if isinstance(row_ctx, dict) else None
        ) or data.get("reason")
        rows.append({"user_id": uid, "reason": reason})
    return rows


async def _inherited_collaborators(
    resource_type: ResourceType, resource: Any
) -> List[Dict[str, Any]]:
    """Best-effort enumeration of inherited collaborators via parent chain."""
    inherited: List[Dict[str, Any]] = []
    if resource_type == "app":
        return inherited
    parent_type: Optional[ResourceType]
    parent_id: Optional[str]
    if resource_type == "track":
        apps = await resource.nodes(
            edge=["CONTAINS"], direction="in", node=["WorkspaceApp"]
        )
        if not apps:
            return inherited
        parent_type = "app"
        parent_id = apps[0].id
    else:  # entry
        track_id = getattr(resource, "track_id", "") or ""
        if not track_id:
            tracks = await resource.nodes(
                edge=["CONTAINS"], direction="in", node=["Track"]
            )
            if tracks:
                track_id = tracks[0].id
        if not track_id:
            return inherited
        parent_type = "track"
        parent_id = track_id

    parent_direct = await _direct_collaborators(parent_type, parent_id)
    # Inherited cap = "editor" (matches permissions._cap_inherited_role).
    # Any parent role above editor (owner / admin) caps to editor on the
    # child — track-config authority NEVER cascades; it must be granted
    # directly per resource. See INVARIANTS.md I-ROLE-02.
    cap = "editor"
    for row in parent_direct:
        capped_role = cap if row["role"] in ("owner", "admin") else row["role"]
        inherited.append(
            {
                "user_id": row["user_id"],
                "role": capped_role,
                "source": "inherited",
                "source_type": parent_type,
                "source_id": parent_id,
            }
        )
    # Recurse to grandparent
    parent_resource = await _load_resource(parent_type, parent_id)
    if parent_resource is not None:
        grand = await _inherited_collaborators(parent_type, parent_resource)
        seen = {r["user_id"] for r in inherited}
        for row in grand:
            if row["user_id"] in seen:
                continue
            inherited.append(row)
            seen.add(row["user_id"])
    return inherited


async def list_access(
    actor_user_id: str,
    resource_type: ResourceType,
    resource_id: str,
) -> Dict[str, Any]:
    """Return the access snapshot for the Manage Access modal.

    Canonical permission-inspection surface — prefer
    ``GET /{apps|tracks|entries}/{id}/access`` over legacy
    ``GET .../collaborators`` list endpoints for UI that only needs role
    buckets. Legacy collaborator list routes remain for track-detail modals
    that require full user exports.

    Requires read access to the resource. Managers (``owner`` / ``admin``)
    get the full graph:
    ``{resource_type, resource_id, direct, inherited, excluded, links,
    effective_role, links_visible, effective_total}``.

    Everyone else gets a reduced snapshot carrying only their own standing —
    ``{resource_type, resource_id, effective_role, links_visible: False}`` —
    so a viewer or commenter cannot enumerate who else holds access.
    Mutating endpoints enforce owner-or-admin separately.
    """
    from app.api.errors import ResourceNotFoundError

    read_action = f"{resource_type}.read"
    await _require_policy_allowed(
        actor_user_id,
        read_action,
        resource_type,
        resource_id,
        denied_message="Access denied.",
    )

    resource = await _load_resource(resource_type, resource_id)
    if resource is None:
        raise ResourceNotFoundError(message=f"{resource_type.capitalize()} not found.")

    actor_role = await resolve_role(actor_user_id, resource_type, resource_id)
    from app.services.workspace_governance import resolve_owner_governance

    governance = await resolve_owner_governance(
        actor_user_id, resource_type, resource_id, "resource.access.review"
    )
    if actor_role not in ("owner", "admin") and governance is None:
        return {
            "resource_type": resource_type,
            "resource_id": resource_id,
            "effective_role": actor_role,
            "links_visible": False,
        }

    direct = await _direct_collaborators(resource_type, resource_id)
    excluded = await _excluded_users(resource_type, resource_id)
    inherited = await _inherited_collaborators(resource_type, resource)
    # Strip inherited rows that have a direct grant on this resource — the
    # direct row is the authoritative one.
    direct_uids = {r["user_id"] for r in direct}
    inherited = [r for r in inherited if r["user_id"] not in direct_uids]
    # Mark inherited rows that are excluded.
    excluded_uids = {r["user_id"] for r in excluded}
    for row in inherited:
        row["excluded"] = row["user_id"] in excluded_uids
        row["effective"] = not row["excluded"]

    links: List[Dict[str, Any]] = []
    links_visible = actor_role in ("owner", "admin") or governance is not None
    if links_visible:
        from app.services.share_links import list_active_links

        links = await list_active_links(actor_user_id, resource_type, resource_id)

    return {
        "resource_type": resource_type,
        "resource_id": resource_id,
        "direct": direct,
        "inherited": inherited,
        "excluded": excluded,
        "links": links,
        "governance": (
            {"source": governance.source, "workspace_id": governance.workspace_id}
            if governance is not None
            else None
        ),
        "links_visible": links_visible,
        # Present in BOTH shapes so a caller can read the viewer's own standing
        # without first branching on whether it got the manager response. The
        # reduced branch above has always carried it; omitting it here made the
        # key conditional on the caller's role, which the docstring did not say.
        "effective_role": actor_role,
    }
