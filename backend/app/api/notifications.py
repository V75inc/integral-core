"""Notifications API endpoints.

Provides notification management for user alerts, invites, and system messages.

Phase 9 Plan 09-03a (NOTIF-02) — :func:`create_notification` was refactored
to delegate via :func:`app.services.notification_router.dispatch`. The
previous direct ``emit_change_event`` call for the notification-create
action (formerly at L297-L306 of this file) has been DELETED — the single
notification-create emission per D-05 now lives ONLY in
:class:`app.services.notification_channels.in_app_channel.InAppChannel`.
The acceptance-criteria grep gate asserts 0 occurrences of the literal
emit-action string for notification-create in this file (preserved across
future edits — search ``in_app_channel.py`` to find the canonical site).
"""

from typing import Any, Dict, List, Optional

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    MissingAuthenticationError,
    ResourceNotFoundError,
)
from app.api.utils import export_node, require_platform_admin, resolve_principal_id
from app.models.nodes import Notification, Track, User, Workspace
from app.services import notification_router
from app.services.app_graph import link_notification
from app.services.change_event import emit_change_event
from app.services.permissions import get_user_node
from app.utils.time import utc_now_iso


@endpoint("/notifications", methods=["GET"], auth=True, tags=["Notifications"])
async def get_notifications(
    request: Request,
    # Injected by auth
    read: Optional[bool] = None,
    page: int = 1,
    per_page: int = 20,
) -> Dict[str, Any]:
    """Get notifications for the current user.

    Args:
        user_id: ID of the authenticated user (injected by auth)
        read: Optional filter for read/unread notifications
        page: Page number (1-indexed)
        per_page: Number of notifications per page

    Returns:
        List of notifications
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    # Clamp the page window. ``per_page`` was unbounded, so a single request
    # could serialize every notification the user has ever received. The rows
    # are already user-scoped by the query below, so this is a resource bound
    # rather than an access control -- but it is the only bound there is.
    try:
        per_page = int(per_page)
    except (TypeError, ValueError):
        per_page = 20
    per_page = max(1, min(per_page, 100))
    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1

    # Find notifications for this user
    query: Dict[str, Any] = {"context.user_id": user_id}
    if read is not None:
        query["context.read"] = read

    # NOTE: still loads the full user-scoped set because ``total`` and
    # ``unread_count`` below are computed over it. Moving those to DB-side
    # counts (and the page to a keyset slice via app/services/pagination.py) is
    # the follow-up; the clamp above bounds the serialization cost meanwhile.
    notifications = await Notification.find(query)

    # Sort by created_at (most recent first)
    notifications.sort(key=lambda n: n.created_at or "", reverse=True)

    # Apply pagination
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    paginated = notifications[start_idx:end_idx]

    # Convert to dictionaries
    notifications_list = [await export_node(n) for n in paginated]

    return {
        "notifications": notifications_list,
        "total": len(notifications),
        "page": page,
        "per_page": per_page,
        "total_pages": (len(notifications) + per_page - 1) // per_page,
        "unread_count": len([n for n in notifications if not n.read]),
    }


@endpoint(
    "/notifications/{notification_id}/read",
    methods=["PUT"],
    auth=True,
    tags=["Notifications"],
)
async def mark_notification_as_read(
    request: Request,
    notification_id: str,
    # Injected by auth
) -> Dict[str, Any]:
    """Mark a notification as read.

    Args:
        notification_id: ID of the notification
        user_id: ID of the authenticated user

    Returns:
        Updated notification
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    notification = await Notification.get(notification_id)
    if not notification:
        raise ResourceNotFoundError(message="Notification not found")

    # Verify ownership
    if notification.user_id != user_id:
        raise InsufficientPermissionsError(
            message="You can only mark your own notifications as read"
        )

    notification.read = True
    await notification.save()

    # D-05 single emission path. Phase 9 Plan 09-02 (NOTIF-01) — closes one of
    # the two deferred mark-read emission gaps (the other is
    # mark_all_notifications_as_read below). Emit AFTER save() succeeds and
    # BEFORE the HTTP response; sync inline (D-06). The before/after pair
    # captures the read-flag state transition only — the rest of the
    # Notification body is unchanged.
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="notification.read",
        resource_type="Notification",
        resource_id=notification.id,
        before={"read": False},
        after={"read": True},
        scope=f"user:{user_id}",
    )

    notif_data = await export_node(notification)

    return {
        "notification": notif_data,
        "message": "Notification marked as read",
    }


@endpoint(
    "/notifications/mark-all-read", methods=["PUT"], auth=True, tags=["Notifications"]
)
async def mark_all_notifications_as_read(
    request: Request,
    # Injected by auth
) -> Dict[str, Any]:
    """Mark all notifications as read for the current user.

    Args:
        user_id: ID of the authenticated user

    Returns:
        Count of notifications marked as read
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    # Find unread notifications for this user
    notifications = await Notification.find(
        {
            "context.user_id": user_id,
            "context.read": False,
        }
    )

    # Mark all as read. Capture each affected id so the batch ChangeEvent
    # can record the exact set the user cleared (T-09-02-R02 mitigation —
    # repudiation defense; T-09-02-I01 — the query above already scopes to
    # caller-owned notifications by construction, so this list is safe to
    # persist verbatim).
    count = 0
    affected_ids: List[str] = []
    for notification in notifications:
        notification.read = True
        await notification.save()
        affected_ids.append(notification.id)
        count += 1

    # D-05 single emission path. Phase 9 Plan 09-02 (NOTIF-01) — closes the
    # second deferred mark-read emission gap. SINGLE emit for the entire
    # batch (not one per notification — that would inflate the audit log
    # for a mass mark-clear; details.notification_ids preserves the per-id
    # enumeration so the audit trail is lossless). Skip emission when count
    # == 0 — emitting a no-op ChangeEvent would clutter the audit log
    # without representing any state transition.
    if count > 0:
        await emit_change_event(
            actor_kind="human",
            actor_id=user_id,
            action="notification.mark_all_read",
            resource_type="User",
            resource_id=user_id,
            before=None,
            after=None,
            scope=f"user:{user_id}",
            details={"count": count, "notification_ids": affected_ids},
        )

    return {
        "message": f"Marked {count} notifications as read",
        "count": count,
    }


@endpoint(
    "/notifications/{notification_id}/respond",
    methods=["POST"],
    auth=True,
    tags=["Notifications"],
)
async def respond_to_notification(
    request: Request,
    notification_id: str,
    # Injected by auth
    action: str = "accept",  # accept or decline (from request body)
) -> Dict[str, Any]:
    """Respond to an invite notification.

    Args:
        notification_id: ID of the notification
        user_id: ID of the authenticated user
        action: Response action ('accept' or 'decline')

    Returns:
        Updated notification with response
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    notification = await Notification.get(notification_id)
    if not notification:
        raise ResourceNotFoundError(message="Notification not found")

    # Verify ownership
    if notification.user_id != user_id:
        raise InsufficientPermissionsError(
            message="You can only respond to your own notifications"
        )

    # Verify it's an invite type
    if notification.type != "invite":
        raise BadRequestError(message="Only invite notifications can be responded to")

    # Validate action
    if action not in ["accept", "decline"]:
        raise BadRequestError(message="Action must be 'accept' or 'decline'")

    # Process the invite response
    if action == "accept":
        # Get the track or organization ID from metadata
        if "track_id" in notification.metadata:
            track_id = notification.metadata["track_id"]
            role = notification.metadata.get("role", "viewer")

            # Add user as collaborator
            track = await Track.get(track_id)
            if track:
                user = await get_user_node(user_id)
                if user:
                    from app.models.edges import COLLABORATES_ON

                    await user.connect(
                        track,
                        edge=COLLABORATES_ON,
                        role=role,
                        invited_at=notification.created_at or utc_now_iso(),
                    )

        elif "workspace_id" in notification.metadata:
            workspace_id = notification.metadata["workspace_id"]
            role = notification.metadata.get("role", "member")

            workspace = await Workspace.get(workspace_id)
            from app.services.workspace_kind import is_collaborative_kind

            if workspace and is_collaborative_kind(workspace.kind):
                user = await get_user_node(user_id)
                if user:
                    from app.models.edges import IS_MEMBER_OF

                    await user.connect(
                        workspace,
                        edge=IS_MEMBER_OF,
                        role=role,
                        joined_at=utc_now_iso(),
                    )

    # Mark as read and update metadata
    notification.read = True
    notification.metadata["response"] = action
    notification.metadata["responded_at"] = utc_now_iso()
    await notification.save()

    notif_data = await export_node(notification)

    return {
        "notification": notif_data,
        "message": f"Invite {action}ed successfully",
    }


@endpoint("/notifications", methods=["POST"], auth=True, tags=["Notifications"])
async def create_notification(
    request: Request,
    target_user_id: str,
    type: str,
    content: str,
    # Injected by auth (sender)
    action_url: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create a new notification (internal use and admins).

    Args:
        user_id: ID of the user creating the notification
        target_user_id: ID of the user to notify
        type: Notification type (invite, update, system)
        content: Notification message
        action_url: Optional URL for action
        metadata: Additional metadata

    Returns:
        Created notification
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    require_platform_admin(request)

    # Phase 9 Plan 09-03a (NOTIF-02) — delegate to the notification router
    # (single-entry discipline, mirrors policy_engine.evaluate). The previous
    # direct Notification.create + change-event-emit-for-notification-create
    # block at L297-L306 is DELETED — the emission lives ONLY in
    # InAppChannel.dispatch (D-05 single-emission invariant). This handler
    # explicitly fires only the in_app channel; full fan-out (mention emails,
    # WhatsApp opt-in welcomes) goes through ``notification_router.dispatch``
    # directly from the originating service (sharing, mentions, agent
    # approvals), bypassing this admin/internal endpoint.
    actor_name: Optional[str] = None
    actor_user = await User.get(user_id)
    if actor_user is not None:
        actor_name = actor_user.display_name or None

    result = await notification_router.dispatch(
        user_id=target_user_id,
        kind=type,
        payload={
            "actor_name": actor_name,
            "content": content,
            "action_url": action_url,
            **(metadata or {}),
        },
        actor_id=user_id,
        actor_kind="human",
        channels=["in_app"],
    )

    notification = await Notification.get(result["notification_id"])
    if notification is None:
        # Defensive — router returned a notification_id but the row vanished.
        raise ResourceNotFoundError(message="Notification creation failed")

    # Preserve the legacy ``link_notification`` cataloging side-effect that
    # the prior implementation performed — keeps the User -> HAS_NOTIFICATION
    # edge consistent for downstream consumers that walk the User node.
    recipient = await get_user_node(target_user_id)
    if recipient:
        await link_notification(recipient, notification)

    # Backfill the ``action_url`` from the request (the router persists the
    # rendered summary into ``content``; ``action_url`` is a Notification
    # node field, not part of the router's payload). The router-created row
    # doesn't carry action_url; mirror the prior endpoint's contract here.
    if action_url is not None and notification.action_url != action_url:
        notification.action_url = action_url
        await notification.save()

    notif_data = await export_node(notification)
    return {
        "notification": notif_data,
        "message": "Notification created successfully",
    }


@endpoint(
    "/notifications/{notification_id}",
    methods=["DELETE"],
    auth=True,
    tags=["Notifications"],
)
async def delete_notification(
    request: Request,
    notification_id: str,
    # Injected by auth
) -> Dict[str, Any]:
    """Delete a notification.

    Args:
        notification_id: ID of the notification
        user_id: ID of the authenticated user

    Returns:
        Deletion confirmation
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    notification = await Notification.get(notification_id)
    if not notification:
        raise ResourceNotFoundError(message="Notification not found")

    # Verify ownership
    if notification.user_id != user_id:
        raise InsufficientPermissionsError(
            message="You can only delete your own notifications"
        )

    prior_snapshot = await export_node(notification)  # D-03 before-snapshot
    await notification.delete()

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="notification.delete",
        resource_type="Notification",
        resource_id=notification_id,
        before=prior_snapshot,
        after=None,
        scope=f"user:{user_id}",
    )

    return {
        "message": "Notification deleted successfully",
        "deleted_notification_id": notification_id,
    }
