"""Durable approval capture for an exact declared App function invocation."""

import json
from datetime import timedelta

from jvspatial.core.context import get_default_context

from app.exceptions import AuthorizationUnavailableError
from app.models.edges import HAS_APPROVAL
from app.models.nodes import Approval, Policy
from app.schemas.policy import Subject
from app.services.access_delegation import current_definition
from app.services.app_operation_authority import operation_decision
from app.services.authority_facts import primary_node_record
from app.utils.time import utc_now


async def app_approval_binding(
    app_id: str,
    operation_key: str,
    workspace_id: str,
    original_input: dict,
    idempotency_key: str,
) -> dict:
    """Bind the current canonical definition and exact original selector input."""
    app, row = await current_definition(app_id)
    if app["context"].get("workspace_id") != workspace_id:
        raise AuthorizationUnavailableError()
    return {
        "version": 1,
        "app_id": app_id,
        "operation_key": operation_key,
        "workspace_id": workspace_id,
        "definition_id": row["id"],
        "definition_fingerprint": row["context"]["manifest_fingerprint"],
        "original_input": dict(original_input),
        "idempotency_key": idempotency_key,
    }


async def validate_app_approval(
    data: dict, *, require_current_definition: bool = True
) -> dict:
    """Reject altered or obsolete declared-function replay envelopes."""
    binding = data.get("app_operation")
    if (
        not isinstance(binding, dict)
        or set(binding)
        != {
            "version",
            "app_id",
            "operation_key",
            "workspace_id",
            "definition_id",
            "definition_fingerprint",
            "original_input",
            "idempotency_key",
        }
        or type(binding["version"]) is not int
        or binding["version"] != 1
        or not isinstance(binding["original_input"], dict)
    ):
        raise AuthorizationUnavailableError()
    if any(
        not isinstance(binding[key], str) or not binding[key]
        for key in (
            "app_id",
            "operation_key",
            "workspace_id",
            "definition_id",
            "definition_fingerprint",
            "idempotency_key",
        )
    ):
        raise AuthorizationUnavailableError()
    if (
        data["action"] != f"app:{binding['app_id']}:{binding['operation_key']}"
        or data["resource_kind"] != "app"
        or data["resource_id"] != binding["app_id"]
        or data["resource_scope"] != f"app:{binding['app_id']}"
    ):
        raise AuthorizationUnavailableError()
    if not require_current_definition:
        return binding
    current = await app_approval_binding(
        binding["app_id"],
        binding["operation_key"],
        binding["workspace_id"],
        binding["original_input"],
        binding["idempotency_key"],
    )
    if current != binding:
        raise AuthorizationUnavailableError()
    return binding


async def defer_app_operation(
    *,
    subject: Subject,
    app_id: str,
    operation_key: str,
    workspace_id: str,
    payload: dict,
    original_input: dict,
    policy_id: str,
    idempotency_key: str,
) -> dict:
    """Recheck original authority inside the same identity/workspace capture unit."""
    from app.api.errors import OperationIdempotencyConflictError
    from app.exceptions import PayloadTooLargeError
    from app.services.approval_executor import _approval_write_scope
    from app.services.service_delegation import delegated_principal_ids

    if (
        len(json.dumps({"input": original_input, "resolved": payload}).encode())
        > 1048576
    ):
        raise PayloadTooLargeError(message="Payload exceeds approval cap")
    participants = set(await delegated_principal_ids(subject))
    row = await primary_node_record(get_default_context().database, Policy, policy_id)
    if row is None:
        raise AuthorizationUnavailableError()
    participants.add(row["context"]["created_by"])
    async with _approval_write_scope(
        workspace_id, participants, subject, tuple(participants)
    ):
        held_policy = await primary_node_record(
            get_default_context().database, Policy, policy_id
        )
        if (
            held_policy is None
            or held_policy["context"].get("created_by") != row["context"]["created_by"]
        ):
            raise AuthorizationUnavailableError()
        decision, _ = await operation_decision(
            subject=subject, app_id=app_id, operation_key=operation_key, payload=payload
        )
        if (
            decision.reason != "requires_human_approval"
            or decision.matched_policy_id != policy_id
        ):
            raise AuthorizationUnavailableError()
        binding = await app_approval_binding(
            app_id, operation_key, workspace_id, original_input, idempotency_key
        )
        existing = await get_default_context().database.find(
            "node",
            {
                "entity": Approval._entity_name(),
                "context.actor_kind": subject.kind,
                "context.actor_id": subject.id,
                "context.action": f"app:{app_id}:{operation_key}",
                "context.app_operation.workspace_id": workspace_id,
                "context.app_operation.idempotency_key": idempotency_key,
            },
            limit=2,
        )
        if len(existing) > 1:
            raise AuthorizationUnavailableError()
        if existing:
            data = existing[0]["context"]
            if (
                data.get("payload") != payload
                or data.get("app_operation") != binding
                or data.get("policy_id") != policy_id
            ):
                raise OperationIdempotencyConflictError(
                    message="Approval request key already binds another intent"
                )
            return {
                "app_id": app_id,
                "operation_key": operation_key,
                "output": {
                    "status": data["status"],
                    "approval_id": existing[0]["id"],
                    "expires_at": data["expires_at"],
                },
                "object_refs": [],
            }
        now = utc_now()
        approval = await Approval.create(
            actor_kind=subject.kind,
            actor_id=subject.id,
            action=f"app:{app_id}:{operation_key}",
            resource_kind="app",
            resource_id=app_id,
            resource_scope=f"app:{app_id}",
            payload=dict(payload),
            app_operation=binding,
            policy_id=policy_id,
            created_at=now.isoformat(),
            expires_at=(now + timedelta(days=7)).isoformat(),
        )
        policy = await Policy.get(policy_id)
        if policy is None:
            raise AuthorizationUnavailableError()
        await policy.connect(approval, edge=HAS_APPROVAL, created_at=now.isoformat())
        return {
            "app_id": app_id,
            "operation_key": operation_key,
            "output": {
                "status": "pending",
                "approval_id": approval.id,
                "expires_at": approval.expires_at,
            },
            "object_refs": [],
        }
