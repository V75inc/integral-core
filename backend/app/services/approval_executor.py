"""Atomic human decisions replay exact intent under the original current authority."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from jvspatial.core.context import get_default_context

from app.exceptions import AuthorizationUnavailableError
from app.models.nodes import Approval, Policy
from app.schemas.policy import Resource, Subject
from app.services.approved_effect import approved_effect_scope, fact_fingerprint
from app.services.authority_facts import primary_node_record, required_node_text
from app.services.resource_location import locate_resource
from app.utils.time import utc_now_iso


# Call-time imports avoid the API registry importing a partially initialized service.
async def _entry_create(
    *,
    actor_kind: str,
    actor_id: str,
    payload: dict,
    _internal_actor: Subject | None = None,
):
    from app.services.entry_writer import create_entry_internal

    return await create_entry_internal(
        actor_kind=actor_kind,
        actor_id=actor_id,
        payload=payload,
        _internal_actor=_internal_actor,
    )


async def _entry_update(
    *,
    actor_kind: str,
    actor_id: str,
    payload: dict,
    _internal_actor: Subject | None = None,
):
    from app.services.entry_writer import update_entry_internal

    return await update_entry_internal(
        actor_kind=actor_kind,
        actor_id=actor_id,
        payload=payload,
        _internal_actor=_internal_actor,
    )


async def _entry_delete(
    *,
    actor_kind: str,
    actor_id: str,
    payload: dict,
    _internal_actor: Subject | None = None,
):
    from app.services.entry_writer import delete_entry_internal

    return await delete_entry_internal(
        actor_kind=actor_kind,
        actor_id=actor_id,
        payload=payload,
        _internal_actor=_internal_actor,
    )


async def _track_create(
    *,
    actor_kind: str,
    actor_id: str,
    payload: dict,
    _internal_actor: Subject | None = None,
):
    from app.services.track_writer import create_track_internal

    return await create_track_internal(
        actor_kind=actor_kind,
        actor_id=actor_id,
        payload=payload,
        _internal_actor=_internal_actor,
    )


async def _app_create(
    *,
    actor_kind: str,
    actor_id: str,
    payload: dict,
    _internal_actor: Subject | None = None,
):
    from app.services.app_writer import create_app_internal

    return await create_app_internal(
        actor_kind=actor_kind,
        actor_id=actor_id,
        payload=payload,
        _internal_actor=_internal_actor,
    )


async def _comment_create(
    *,
    actor_kind: str,
    actor_id: str,
    payload: dict,
    _internal_actor: Subject | None = None,
):
    from app.services.comment_writer import create_comment_internal

    return await create_comment_internal(
        actor_kind=actor_kind,
        actor_id=actor_id,
        payload=payload,
        _internal_actor=_internal_actor,
    )


WRITE_ACTION_DISPATCH: dict[str, Callable[..., Awaitable[dict[str, Any]]]] = {
    "entry.create": _entry_create,
    "entry.update": _entry_update,
    "entry.delete": _entry_delete,
    "track.create": _track_create,
    "app.create": _app_create,
    "comment.create": _comment_create,
}


class UnsupportedApprovalAction(Exception):
    """The captured action has no exact supported replay adapter."""


class ApprovalAlreadyDecided(Exception):
    """The pending intent was already claimed or decided."""


async def _facts(approval_id: str) -> tuple[dict, dict, Any]:
    """Read complete intent, structural issuer and live original subject."""
    from app.models.edges import HAS_APPROVAL
    from app.services.policy_registry import _resolve_subject_node

    database = get_default_context().database
    row = await primary_node_record(database, Approval, approval_id)
    if row is None:
        raise ValueError("Approval not found")
    data = row["context"]
    for key in (
        "actor_kind",
        "actor_id",
        "action",
        "resource_kind",
        "policy_id",
        "created_at",
        "expires_at",
        "status",
    ):
        required_node_text(row, key)
    required_node_text(row, "resource_id", allow_empty=True)
    if data["actor_kind"] not in {"human", "agent", "connector"} or not isinstance(
        data.get("payload"), dict
    ):
        raise AuthorizationUnavailableError()
    for key in ("created_at", "expires_at"):
        try:
            if datetime.fromisoformat(data[key]).tzinfo is None:
                raise ValueError("Missing timezone")
        except ValueError as exc:
            raise AuthorizationUnavailableError() from exc
    if data["status"] != "pending":
        raise ApprovalAlreadyDecided(
            "Approval is no longer pending (expected 'pending')"
        )
    if datetime.fromisoformat(data["expires_at"]) <= datetime.now(timezone.utc):
        raise ApprovalAlreadyDecided("Approval expired")
    if "decision_revision" in data and (
        type(data["decision_revision"]) is not int or data["decision_revision"] < 0
    ):
        raise AuthorizationUnavailableError()
    roots = await database.find(
        "edge", {"target": approval_id, "entity": HAS_APPROVAL._entity_name()}, limit=2
    )
    policy = await primary_node_record(database, Policy, data["policy_id"])
    if (
        len(roots) != 1
        or roots[0].get("source") != data["policy_id"]
        or policy is None
        or type(policy["context"].get("is_active")) is not bool
    ):
        raise AuthorizationUnavailableError()
    required_node_text(policy, "created_by")
    if data["actor_kind"] == "human":
        from app.services.identity_admission import resolve_current_profile

        subject = await resolve_current_profile(data["actor_id"], require_active=False)
    else:
        subject = await _resolve_subject_node(data["actor_kind"], data["actor_id"])
    if subject is None:
        raise AuthorizationUnavailableError()
    return row, policy, subject


async def _target(
    data: dict, *, policy_scope: str = "", require_current_definition: bool = True
) -> tuple[str, Resource]:
    """Resolve a supported replay's exact actual parent/target before locks."""
    action, payload = data["action"], data["payload"]
    if action.startswith("app:"):
        from app.services.app_operation_approval import validate_app_approval

        await validate_app_approval(
            data, require_current_definition=require_current_definition
        )
    if action == "entry.create":
        kind, identity = "track", payload.get("track_id")
        resource = Resource(kind="entry", id="", scope=f"track:{identity}")
    elif action in {"entry.update", "entry.delete", "comment.create"}:
        kind, identity = "entry", payload.get("entry_id")
        location = (
            await locate_resource(kind, identity)
            if isinstance(identity, str) and identity
            else None
        )
        if location is None:
            raise AuthorizationUnavailableError()
        resource = Resource(
            kind="entry", id=identity, scope=f"track:{location.track_id}"
        )
    elif action in {"app.create", "track.create"}:
        kind, identity = "workspace", payload.get("workspace_id")
        resource = Resource(
            kind="app" if action == "app.create" else "track",
            id="",
            scope=f"workspace:{identity}",
        )
        if action == "track.create" and payload.get("app_id"):
            parent = await locate_resource("app", payload["app_id"])
            if parent is None or parent.workspace_id != identity:
                raise AuthorizationUnavailableError()
    else:
        scope = data.get("resource_scope") or policy_scope
        parts = scope.split(":", 1) if isinstance(scope, str) else []
        if (
            len(parts) != 2
            or parts[0] not in {"app", "track", "entry", "workspace"}
            or not parts[1]
        ):
            raise UnsupportedApprovalAction(f"Unsupported approval target {action}")
        kind, identity = parts
        resource = Resource(
            kind=data["resource_kind"], id=data["resource_id"], scope=scope
        )
    if not isinstance(identity, str) or not identity:
        raise AuthorizationUnavailableError()
    location = await locate_resource(kind, identity)
    if (
        location is None
        or data["resource_kind"] != resource.kind
        or data["resource_id"] != resource.id
    ):
        raise AuthorizationUnavailableError()
    if data.get("resource_scope") and data["resource_scope"] != resource.scope:
        raise AuthorizationUnavailableError()
    return location.workspace_id, resource


@asynccontextmanager
async def _approval_write_scope(
    workspace_id: str,
    participants: set[str],
    subject: Subject,
    active: tuple[str, ...],
    cleanup_model: type | None = None,
):
    """Lock current human identities and service identity before the workspace."""
    from app.services.app_operations.transaction_scope import postgres_graph_transaction
    from app.services.identity_admission import identity_admission
    from app.services.membership_write_scope import membership_write_scope
    from app.services.service_delegation import delegated_subject_record

    database = get_default_context().database
    async with postgres_graph_transaction(database):
        async with identity_admission(tuple(participants), active_principal_ids=active):
            held = get_default_context().database
            if subject.kind != "human":
                record = (
                    (cleanup_model, "node", subject.id)
                    if cleanup_model is not None
                    else await delegated_subject_record(subject)
                )
                if record is None:
                    raise AuthorizationUnavailableError()
                model, collection, identity = record
                if (
                    await held.find_one_and_update(
                        collection,
                        {"id": identity, "entity": model._entity_name()},
                        {"$set": {}},
                    )
                    is None
                ):
                    raise AuthorizationUnavailableError()
            async with membership_write_scope(
                workspace_id,
                principal_ids=tuple(participants),
                active_principal_ids=active,
            ):
                yield


async def _decide(
    *, approval_id: str, decider_id: str, approved: bool, reason: str = ""
) -> dict:
    """Join pending claim, exact effect, decision edge and WorkApproval atomically."""
    from app.api.errors import InsufficientPermissionsError
    from app.models.edges import HAS_APPROVAL_DECISION
    from app.services.app_operations.event_outbox import insert_revision_event
    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
    )
    from app.services.permissions import get_user_node
    from app.services.policy_engine import evaluate
    from app.services.workspace_membership import _deliver

    before, policy, subject_node = await _facts(approval_id)
    if not graph_transaction_available(get_default_context().database):
        raise AuthorizationUnavailableError()
    workspace_id, resource = await _target(
        before["context"],
        policy_scope=policy["context"].get("scope", ""),
        require_current_definition=approved,
    )
    delegator_id = policy["context"]["created_by"]
    participants = {decider_id, delegator_id}
    if before["context"]["actor_kind"] == "human":
        participants.add(before["context"]["actor_id"])
    else:
        raw_subject = await primary_node_record(
            get_default_context().database, type(subject_node), subject_node.id
        )
        if raw_subject is None:
            raise AuthorizationUnavailableError()
        controller = raw_subject["context"].get("user_id") or raw_subject[
            "context"
        ].get("owner")
        if isinstance(controller, str) and controller:
            participants.add(controller)
    original_subject = Subject(
        kind=before["context"]["actor_kind"], id=before["context"]["actor_id"]
    )
    if original_subject.kind != "human" and approved:
        from app.services.service_delegation import delegated_principal_ids

        participants.update(await delegated_principal_ids(original_subject))
    outboxes = []
    captured_events: list[dict[str, Any]] = []
    operation_outboxes: list[str] = []
    async with _approval_write_scope(
        workspace_id,
        participants,
        original_subject,
        tuple(participants) if approved else (decider_id,),
        cleanup_model=(
            type(subject_node)
            if not approved and original_subject.kind != "human"
            else None
        ),
    ):
        # The pinned adapter's JSON predicate fallback is read-then-save.
        # Lock the exact scalar identity first so the full semantic CAS and
        # terminal reread remain atomic against expiration and other decisions.
        held_database = get_default_context().database
        if (
            await held_database.find_one_and_update(
                "node",
                {"id": approval_id, "entity": Approval._entity_name()},
                {"$set": {}},
            )
            is None
        ):
            raise AuthorizationUnavailableError()
        current, current_policy, subject_node = await _facts(approval_id)
        current_workspace, current_resource = await _target(
            current["context"],
            policy_scope=current_policy["context"].get("scope", ""),
            require_current_definition=approved,
        )
        if (
            current_workspace != workspace_id
            or current_resource != resource
            or fact_fingerprint(current) != fact_fingerprint(before)
            or current_policy["context"]["created_by"] != delegator_id
        ):
            raise AuthorizationUnavailableError()
        if before["context"]["actor_kind"] != "human":
            held_subject = await primary_node_record(
                get_default_context().database, type(subject_node), subject_node.id
            )
            if held_subject is None or fact_fingerprint(
                held_subject
            ) != fact_fingerprint(raw_subject):
                raise AuthorizationUnavailableError()
        if approved and current_policy["context"].get("is_active") is not True:
            raise InsufficientPermissionsError(message="Original policy was revoked")
        subject = Subject(
            kind=current["context"]["actor_kind"], id=current["context"]["actor_id"]
        )
        decider = await get_user_node(decider_id)
        if decider is None:
            raise InsufficientPermissionsError(message="Access denied")
        gate = await evaluate(
            subject=Subject(kind="human", id=decider.id),
            action="approval.approve" if approved else "approval.reject",
            resource=Resource(
                kind="policy",
                id=current["context"]["policy_id"],
                scope=f"policy:{current['context']['policy_id']}",
            ),
        )
        if not gate.allowed:
            raise InsufficientPermissionsError(message="Approval decision denied")
        data = current["context"]
        helper = WRITE_ACTION_DISPATCH.get(data["action"])
        if approved and data["action"].startswith("app:"):
            from app.services.app_operation_approval import validate_app_approval

            binding = await validate_app_approval(data)

            async def app_replay(**kwargs):
                from app.services.app_operations.dispatch import invoke_app_operation

                return await invoke_app_operation(
                    user_id=delegator_id,
                    subject=subject,
                    workspace_id=binding["workspace_id"],
                    app_id=binding["app_id"],
                    operation_key=binding["operation_key"],
                    payload=binding["original_input"],
                    idempotency_key=f"approval:{approval_id}",
                )

            helper = app_replay

        if approved and helper is None:
            raise UnsupportedApprovalAction(data["action"])
        database = get_default_context().database
        query = {
            "id": approval_id,
            "entity": Approval._entity_name(),
            "context.status": "pending",
        }
        for key in (
            "actor_kind",
            "actor_id",
            "action",
            "resource_kind",
            "resource_id",
            "payload",
            "policy_id",
            "created_at",
            "expires_at",
            "app_operation",
        ):
            query[f"context.{key}"] = (
                (
                    {"$eq": data[key]}
                    if isinstance(data[key], (dict, list))
                    else data[key]
                )
                if key in data
                else {"$exists": False}
            )
        query["context.resource_scope"] = (
            data["resource_scope"] if "resource_scope" in data else {"$exists": False}
        )
        revision = data.get("decision_revision", 0)
        query["context.decision_revision"] = (
            revision if "decision_revision" in data else {"$exists": False}
        )
        status, decided_at = ("approved" if approved else "rejected"), utc_now_iso()
        claimed = await database.find_one_and_update(
            "node",
            query,
            {
                "$set": {
                    "context.status": status,
                    "context.decider_id": decider.id,
                    "context.decided_at": decided_at,
                    "context.decision_revision": revision + 1,
                }
            },
        )
        if claimed is None:
            raise ApprovalAlreadyDecided("Concurrent approval decision")
        ap = await Approval.find_one({"id": approval_id})
        if ap is None:
            raise AuthorizationUnavailableError()
        await decider.connect(
            ap, edge=HAS_APPROVAL_DECISION, decision=status, decided_at=decided_at
        )
        if approved:
            async with approved_effect_scope(
                approval_id=approval_id,
                approval_fingerprint=fact_fingerprint(claimed),
                original_subject=subject,
                action=data["action"],
                resource=resource,
                payload=data["payload"],
                delegator_id=delegator_id,
                events=captured_events,
                app_operation=data.get("app_operation"),
                operation_outboxes=operation_outboxes,
            ):
                decision = await evaluate(
                    subject=subject,
                    action=data["action"],
                    resource=resource,
                    payload=dict(data["payload"]),
                )
                if not decision.allowed or (
                    subject.kind != "human"
                    and decision.matched_policy_id != data["policy_id"]
                ):
                    raise InsufficientPermissionsError(
                        message="Original authority no longer admits approved effect"
                    )
                result = await helper(
                    actor_kind=subject.kind,
                    actor_id=subject.id,
                    payload=dict(data["payload"]),
                    _internal_actor=None,
                )
        else:
            result = {"approval_id": approval_id, "status": "rejected"}
        from app.agentive.services import work_approvals
        from app.agentive.work_models import WorkApproval, WorkItem
        from app.services.identity_admission import resolve_current_profile

        links = await database.find(
            "object",
            {
                "entity": WorkApproval._entity_name(),
                "context.policy_approval_id": approval_id,
            },
            limit=2,
        )
        if len(links) > 1:
            raise AuthorizationUnavailableError()
        if links:
            raw_link = links[0]
            linked_data = raw_link.get("context")
            if (
                not isinstance(linked_data, dict)
                or linked_data.get("status") != "pending"
            ):
                raise ApprovalAlreadyDecided("Linked work approval already decided")
            work_id = required_node_text(raw_link, "work_item_id")
            linked_id = required_node_text(raw_link, "work_approval_id")
            if raw_link["id"] != work_approvals.work_approval_object_id(linked_id):
                raise AuthorizationUnavailableError()
            work_rows = await database.find(
                "object",
                {"id": f"o.WorkItem.{work_id}", "entity": WorkItem._entity_name()},
                limit=2,
            )
            if (
                len(work_rows) != 1
                or required_node_text(work_rows[0], "workspace_id") != workspace_id
            ):
                raise AuthorizationUnavailableError()
            work_principal = await resolve_current_profile(
                required_node_text(work_rows[0], "principal_id"),
                require_active=approved,
            )
            delegator = await resolve_current_profile(
                delegator_id, require_active=approved
            )
            if (
                work_principal is None
                or delegator is None
                or work_principal.id != delegator.id
            ):
                raise AuthorizationUnavailableError()
            if approved:
                await work_approvals.approve_work_approval(
                    work_approval_id=linked_id,
                    decider_id=decider.id,
                    transaction=database,
                )
            else:
                await work_approvals.reject_work_approval(
                    work_approval_id=linked_id,
                    decider_id=decider.id,
                    reason=reason,
                    transaction=database,
                )
        for index, event in enumerate(captured_events):
            event = {
                **event,
                "details": {
                    **(event.get("details") or {}),
                    "approval_id": approval_id,
                    "decider_id": decider.id,
                },
            }
            outboxes.append(
                await insert_revision_event(
                    transaction=database,
                    workspace_id=workspace_id,
                    namespace=f"approved-effect-v1:{index}",
                    subject_id=approval_id,
                    revision=revision + 1,
                    event=event,
                )
            )
        outboxes.append(
            await insert_revision_event(
                transaction=database,
                workspace_id=workspace_id,
                namespace="approval-decision-v1",
                subject_id=approval_id,
                revision=revision + 1,
                event={
                    "actor_kind": "human",
                    "actor_id": decider.id,
                    "action": "approval.approved" if approved else "policy.deny",
                    "resource_type": "approval",
                    "resource_id": approval_id,
                    "scope": resource.scope,
                    "details": {
                        "approval_id": approval_id,
                        "decision": status,
                        "original_actor_id": subject.id,
                        "reason": reason,
                        "effect_events": len(captured_events),
                    },
                    "before": None,
                    "after": {"status": status},
                },
            )
        )
    for outbox in outboxes:
        await _deliver(outbox)
    from app.services.app_operations.event_outbox import deliver_operation_event

    for outbox_id in operation_outboxes:
        await deliver_operation_event(outbox_id=outbox_id)
    return result


async def execute_approval(*, approval_id: str, approver_user_id: str) -> dict:
    """Execute the exact original supported action with fresh current authority."""
    return await _decide(
        approval_id=approval_id, decider_id=approver_user_id, approved=True
    )


async def reject_approval(
    *, approval_id: str, rejecter_user_id: str, reason: str | None = None
) -> dict:
    """Reject an exact pending intent and its linked work in one durable unit."""
    return await _decide(
        approval_id=approval_id,
        decider_id=rejecter_user_id,
        approved=False,
        reason=reason or "",
    )
