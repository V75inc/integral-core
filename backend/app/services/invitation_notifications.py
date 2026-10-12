"""Private invitation delivery responses use the canonical lifecycle writer.

PostgreSQL joins grant, terminal state, delivery and durable audit intent.
JSON only orders participating writers in this process; it cannot roll back.
"""

import logging
from copy import deepcopy
from typing import Any

from jvspatial.core.context import get_default_context

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.exceptions import AuthorizationUnavailableError
from app.models.edges import HAS_NOTIFICATION
from app.models.nodes import Invitation, Notification
from app.services.authority_facts import primary_node_record, required_node_text
from app.services.permissions import get_user_node
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)


async def _current_delivery(
    notification_id: str, user: Any, *, lock: bool = False
) -> tuple[Notification, dict]:
    database = get_default_context().database
    raw = await primary_node_record(database, Notification, notification_id)
    if raw is None:
        raise ResourceNotFoundError(message="Notification not found")
    if required_node_text(raw, "user_id") not in {user.id, user.user_id}:
        raise ResourceNotFoundError(message="Notification not found")
    edges = await database.find(
        "edge",
        {"target": notification_id, "entity": HAS_NOTIFICATION._entity_name()},
        limit=2,
    )
    if len(edges) != 1 or edges[0].get("source") != user.id:
        raise AuthorizationUnavailableError()
    if required_node_text(raw, "type") != "invitation":
        raise ResourceConflictError(message="Reissue this invitation before responding")
    metadata = raw["context"].get("metadata")
    payload = metadata.get("payload") if type(metadata) is dict else None
    if type(payload) is not dict:
        raise AuthorizationUnavailableError()
    pointer = payload.get("invitation_id")
    revision = payload.get("invitation_revision")
    if (
        type(pointer) is not str
        or not pointer
        or pointer != pointer.strip()
        or type(revision) is not int
        or revision < 0
    ):
        raise ResourceConflictError(message="Refresh this invitation before responding")
    if type(raw["context"].get("read")) is not bool:
        raise AuthorizationUnavailableError()
    if lock:
        # Lock the complete observed delivery image before granting rights.
        # The no-op write cannot replace a concurrent change silently.
        locked = await database.find_one_and_update(
            "node",
            {
                "id": notification_id,
                "entity": Notification._entity_name(),
                "context": {"$eq": raw["context"]},
            },
            {"$set": {"context.read": raw["context"]["read"]}},
        )
        if locked is None:
            raise ResourceConflictError(
                message="Notification changed; refresh before responding"
            )
    notice = await Notification.find_one({"id": notification_id})
    if notice is None or notice.metadata != metadata:
        raise AuthorizationUnavailableError()
    return notice, payload


async def respond_to_invitation_notification(
    *, notification_id: str, user_id: str, action: str, invitation_revision: int
) -> dict:
    """Apply explicit recipient consent through the held canonical invitation."""
    if (
        action not in {"accept", "decline"}
        or type(invitation_revision) is not int
        or invitation_revision < 0
    ):
        raise BadRequestError(message="Invalid invitation response")
    user = await get_user_node(user_id)
    if user is None:
        raise InsufficientPermissionsError(message="Access denied")
    _, payload = await _current_delivery(notification_id, user)
    invitation_id = payload["invitation_id"]
    raw = await primary_node_record(
        get_default_context().database, Invitation, invitation_id
    )
    if raw is None:
        raise ResourceNotFoundError(message="Invitation not found")
    workspace_id = required_node_text(raw, "workspace_id")
    from app.services.entry_write_scope import (
        entry_write_scope,
        is_postgres_entry_transaction,
    )
    from app.services.invitation_lifecycle import invitation_lifecycle_scope
    from app.services.invitations import (
        _accept_admitted_invitation,
        _decline_admitted_invitation,
        _deliver_invitation_audit,
        _is_past,
        _verified_invitee_for_id,
    )

    caller_context = get_default_context()
    outbox_id = None
    async with (
        invitation_lifecycle_scope(
            invitation_id,
            workspace_id,
            principal_ids=(user_id,),
            include_issuer=action == "accept",
        ) as current,
        entry_write_scope(f"notification-response:{notification_id}"),
    ):
        user = await get_user_node(user_id)
        if user is None:
            raise InsufficientPermissionsError(message="Access denied")
        notice, payload = await _current_delivery(notification_id, user, lock=True)
        if (
            payload["invitation_id"] != current.id
            or payload["invitation_revision"] != invitation_revision
        ):
            raise ResourceConflictError(
                message="Refresh this invitation before responding"
            )
        _, error = await _verified_invitee_for_id(current, user.id)
        if error:
            raise InsufficientPermissionsError(
                message="Verify the current invitation mailbox",
                details={"error_code": error},
            )
        prior_response = notice.metadata.get("invitation_response")
        terminal = "accepted" if action == "accept" else "declined"
        same_response = {
            "action": action,
            "invitation_id": current.id,
            "proposal_revision": invitation_revision,
            "terminal_revision": current.invitation_revision,
            "recipient_id": user.id,
        }
        if (
            prior_response == same_response
            and current.status == terminal
            and current.invited_user_id == user.id
        ):
            changed = False
        else:
            if (
                current.status != "pending"
                or current.invitation_revision != invitation_revision
                or _is_past(current.expires_at)
            ):
                raise ResourceConflictError(
                    message="This invitation proposal is no longer actionable"
                )
            if action == "accept":
                result, error = await _accept_admitted_invitation(
                    invitation=current, user=user, accepting_user_id=user.id
                )
            else:
                result, error = await _decline_admitted_invitation(
                    invitation=current, user=user, declining_user_id=user.id
                )
            if result is None or error:
                raise ResourceConflictError(message="Invitation could not be applied")
            before = {"read": notice.read, "metadata": deepcopy(notice.metadata)}
            notice.read = True
            notice.metadata = {
                **notice.metadata,
                "response": action,
                "responded_at": utc_now_iso(),
                "invitation_response": {
                    **same_response,
                    "terminal_revision": current.invitation_revision,
                },
            }
            await notice.save()
            outbox_id = await _record_response_fact(
                notice=notice, current=current, actor_id=user.id, before=before
            )
            changed = True
        from app.services.invitation_grants import invitation_effective_role

        effective_role = await invitation_effective_role(current, user.id)
    await current.set_context(caller_context)
    await notice.set_context(caller_context)
    await _deliver_invitation_audit(current)
    if outbox_id and not is_postgres_entry_transaction(get_default_context().database):
        from app.services.app_operations.event_outbox import deliver_operation_event

        try:
            await deliver_operation_event(
                outbox_id=outbox_id, database=get_default_context().database
            )
        except Exception:
            logger.exception(
                "Notification response audit delivery pending: %s", notification_id
            )
    return {
        "notification": await notice.export(flat=True),
        "invitation": {
            "id": current.id,
            "status": current.status,
            "grant_outcome": current.grant_outcome,
            "granted_role": current.granted_role,
            "granted_can_create_apps": current.granted_can_create_apps,
            "granted_can_create_tracks": current.granted_can_create_tracks,
            "invitation_revision": current.invitation_revision,
            "target_resource_type": current.target_resource_type,
            "target_resource_id": current.target_resource_id,
            "effective_role": effective_role,
        },
        "changed": changed,
    }


async def _record_response_fact(
    *, notice: Notification, current: Invitation, actor_id: str, before: dict
) -> str | None:
    """Store one response fact per actual invitation terminal revision."""
    from app.services.change_event import emit_change_event
    from app.services.entry_write_scope import is_postgres_entry_transaction

    database = get_default_context().database
    event = {
        "actor_kind": "human",
        "actor_id": actor_id,
        "action": "notification.respond",
        "resource_type": "Notification",
        "resource_id": notice.id,
        "scope": f"user:{notice.user_id}",
        "before": before,
        "after": {"read": notice.read, "metadata": deepcopy(notice.metadata)},
    }
    if is_postgres_entry_transaction(database):
        from app.services.app_operations.event_outbox import insert_revision_event

        return await insert_revision_event(
            transaction=database,
            workspace_id=current.workspace_id,
            namespace="invitation-notification-response-v1",
            subject_id=notice.id,
            revision=current.invitation_revision,
            event=event,
        )
    await emit_change_event(**event)
    return None
