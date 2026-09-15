"""In-app channel adapter (Phase 9 Plan 09-03a, NOTIF-02).

The in-app channel is the canonical Notification entity — the Notification
Node IS the in-app artifact. This adapter is responsible for emitting the
single ``notification.create`` ChangeEvent that audits the entity into the
change-event log (D-05 single-emission invariant).

Per locked decision D-05 (single-emission), the duplicate
``emit_change_event`` call for the notification-create action at the old
``api/notifications.py::create_notification`` L297-L306 is DELETED in this
plan. Emission lives ONLY in this adapter. The acceptance-criteria grep
gate asserts 0 occurrences of the literal emit-action string in
``api/notifications.py`` and exactly 1 occurrence (the real call below)
in this file.

Per locked decision A1, NO new ChangeEventAction Literal members are
introduced. Idempotency on retry is tracked via
``Notification.metadata.dispatched_to[]`` (the router's responsibility — see
``app.services.notification_router``).
"""

from app.services.change_event import emit_change_event
from app.services.notification_channels.types import ChannelDispatchResult


class InAppChannel:
    """In-app channel — Notification node IS the artifact.

    The Notification row is already created by the router caller before this
    adapter runs. This adapter's job is to emit the single
    ``notification.create`` ChangeEvent that audits the row into the
    change-event log.

    Idempotency on retry is router-side: the router checks
    ``notif.metadata.dispatched_to[]`` for a prior
    ``{channel: "in_app", status: "sent"}`` entry and skips the channel call
    when present. So this adapter unconditionally emits — by the time it
    runs, the router has already cleared the idempotency guard.
    """

    name = "in_app"

    async def dispatch(
        self, user, kind, payload, notification, actor_id, actor_kind
    ) -> ChannelDispatchResult:
        # D-05 single emission — the ONE place ``notification.create`` is
        # emitted from. The duplicate site at api/notifications.py L297-L306
        # was deleted in this plan; the create_notification handler now
        # delegates via notification_router.dispatch which routes through
        # this adapter.
        await emit_change_event(
            actor_kind=actor_kind,
            actor_id=actor_id,
            action="notification.create",
            resource_type="Notification",
            resource_id=notification.id,
            before=None,
            after={
                "id": notification.id,
                "type": kind,
                "user_id": user.id,
            },
            scope=f"user:{user.id}",
        )
        return ChannelDispatchResult(
            channel="in_app",
            status="sent",
            external_ref=notification.id,
        )
