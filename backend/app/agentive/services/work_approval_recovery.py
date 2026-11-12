"""Reconcile a consumed staged capability before dispatching another attempt."""

from __future__ import annotations

import json

from jvspatial.core.context import get_default_context

from app.agentive import staging_store
from app.agentive.services import work_execution, work_items
from app.agentive.services.staging_execution import execution_record_id
from app.agentive.work_models import WorkItem
from app.schemas.agentive.work import WorkError, WorkExecutionContext
from app.services.app_operations.transaction_scope import (
    current_graph_transaction_database,
    graph_transaction_available,
)


async def recover_staged_capability(
    item: WorkItem, context: WorkExecutionContext
) -> WorkItem | None:
    """Complete only an exactly bound, applied command; never replay a proposal."""
    database = get_default_context().database
    query = {
        "entity": "WorkApproval",
        "context.work_item_id": item.work_item_id,
        "context.staging_token": {"$ne": ""},
    }
    candidates = await database.find("object", query, limit=2)
    if not candidates:
        return None
    if not graph_transaction_available(database):
        raise WorkError("work.non_replayable_effect", "Approval needs reconciliation")

    from app.agentive.services.capability_broker import _fingerprint
    from app.agentive.services.work_approvals import (
        deterministic_work_approval_id,
        work_approval_object_id,
    )
    from app.services.credential_crypto import decrypt_secret_from_storage

    await work_execution.assert_effect_boundary_allowed(context)
    async with work_items.authorized_work_item_effect(context):
        held = current_graph_transaction_database()
        links = await held.find("object", query, limit=2)
        if len(links) != 1:
            raise WorkError("work.non_replayable_effect", "Approval link is ambiguous")
        link = links[0].get("context") or {}
        token = str(link.get("staging_token") or "")
        intent = str(link.get("staging_intent_fingerprint") or "")
        receipt_id = str(link.get("staging_execution_id") or "")
        request_hash = str(link.get("staging_request_hash") or "")
        marker = await held.get("object", staging_store._decision_record_id(token))
        marker_data = (marker or {}).get("context") or {}
        receipt = await held.get("object", receipt_id) if receipt_id else None
        facts = (receipt or {}).get("context") or {}
        steps = await held.find(
            "object",
            {
                "entity": "RunStep",
                "context.run_id": link.get("run_id"),
                "context.step_key": link.get("run_step_id"),
            },
            limit=2,
        )
        step = (steps[0].get("context") or {}) if len(steps) == 1 else {}
        payload = dict(item.input_payload or {})
        valid = (
            link.get("status") == "approved"
            and link.get("work_approval_id")
            == deterministic_work_approval_id(
                work_item_id=item.work_item_id, staging_token=token
            )
            and links[0].get("id") == work_approval_object_id(link["work_approval_id"])
            and link.get("authority_digest") == context.effect_key
            and bool(intent and request_hash and receipt_id)
            and receipt_id == execution_record_id(token)
            and (marker or {}).get("entity") == "StagingDecisionRecord"
            and marker_data.get("user_id") == item.principal_id
            and marker_data.get("state") == "consumed"
            and marker_data.get("intent_fingerprint") == intent
            and (receipt or {}).get("entity") == "StagingExecutionRecord"
            and facts.get("principal_id") == item.principal_id
            and facts.get("workspace_id") == item.workspace_id
            and facts.get("kind") == marker_data.get("kind")
            and facts.get("request_hash") == request_hash
            and facts.get("state") == "applied"
            and facts.get("local_effect_state") == "applied"
            and facts.get("external_outcome") in {None, "not_dispatched", "returned"}
            and step.get("work_item_id") == item.work_item_id
            and step.get("principal_id") == item.principal_id
            and step.get("workspace_id") == item.workspace_id
            and step.get("capability_key") == payload.get("capability_key")
            and step.get("capability_source") == (payload.get("source") or "core")
            and step.get("capability_op_class") == (payload.get("op_class") or "read")
            and (step.get("app_id") or "") == (item.app_id or "")
            and step.get("input_fingerprint")
            == _fingerprint(payload.get("arguments") or {})
            and step.get("approval_ref") == token
            and step.get("status") == "waiting_for_human"
        )
        if not valid:
            raise WorkError(
                "work.non_replayable_effect", "Approval outcome needs reconciliation"
            )
        try:
            decoded = decrypt_secret_from_storage(
                facts.get("result_ciphertext"), aad=receipt_id
            )
            if not isinstance(json.loads(decoded), dict):
                raise ValueError("not a result object")
        except Exception as exc:
            raise WorkError(
                "work.non_replayable_effect", "Saved effect receipt is unavailable"
            ) from exc
        return await work_items.transition_leased(
            item.work_item_id,
            lease_token=context.lease_token,
            lease_fence=context.lease_fence,
            expected_status="running",
            target="succeeded",
            fields={
                "result_fingerprint": context.effect_key,
                "receipt_refs": [f"staging_execution:{receipt_id}"],
            },
            transaction=held,
        )
