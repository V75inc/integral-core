"""Queue and run reviewed resident preparation against one canonical Entry."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone

from jvspatial.core.context import get_default_context

from app.agentive.harness.contracts import ResolvedModelRoute
from app.agentive.harness.model_route import resolve_native_model_route
from app.agentive.harness.model_routing import (
    conversation_turn_routes,
    resolve_reviewed_conversation_route,
)
from app.agentive.services import work_items
from app.agentive.services.work_mandates import (
    _load_lineage_item,
    _validate_approved_mandate_item,
    load_approved_mandate_lineage,
)
from app.agentive.work_models import WorkItem
from app.api.errors import ServiceUnavailableError
from app.models.nodes import ChatThread
from app.schemas.agentive.work import WorkError, WorkExecutionContext
from app.schemas.agentive.work_mandate import MandateModelRoute, WorkMandateRevision
from app.schemas.resident_assignment import ResidentAssignmentTaskSnapshot
from app.services.app_operations.transaction_scope import (
    graph_transaction_available,
    postgres_graph_transaction,
)
from app.services.chat_threads import append_message
from app.services.resident_assignment import (
    ASSIGNMENT_REVIEW_APPROVED,
    assert_assignment_model_authority,
)
from app.utils.time import utc_now_iso

log = logging.getLogger(__name__)


async def resolve_assignment_review_route(workspace_id: str) -> MandateModelRoute:
    """Bind the default conversation model without exposing credentials."""
    plan = await conversation_turn_routes(
        workspace_id=workspace_id,
        heavy_only=True,
        resolver=resolve_native_model_route,
    )
    route = plan.heavy
    if not route.credential_ref:
        raise ServiceUnavailableError(
            message="Ask your operator to configure a credential reference for the assignment model."
        )
    return MandateModelRoute(
        provider=route.provider,
        model=route.model,
        credential_source=route.credential_source,
        credential_ref=route.credential_ref,
    )


async def _resolve_assignment_route(
    revision: WorkMandateRevision,
) -> ResolvedModelRoute:
    thread = await ChatThread.find_one({"id": revision.thread_id})
    if thread is None or thread.archived:
        raise WorkError("work.assignment_stale")
    try:
        return await resolve_reviewed_conversation_route(
            workspace_id=revision.workspace_id,
            profile_id=thread.model_profile_id,
            reasoning_efforts=thread.model_reasoning_efforts,
            reviewed_routes=revision.model_routes,
        )
    except WorkError as exc:
        if exc.code == "work.mandate_model_unavailable":
            raise WorkError("work.assignment_model_unavailable") from exc
        raise


async def enqueue_approved_resident_assignment(
    *,
    work_item_id: str,
    workspace_id: str,
    expected_review_digest: str,
    approval_event_id: str = "",
) -> WorkItem:
    """Promote one exact approved review to the existing durable worker queue."""
    item = await _load_lineage_item(work_item_id)
    if item.kind != "resident_assignment" or item.workspace_id != workspace_id:
        raise WorkError("work.mandate_scope_denied")

    async def validated(current: WorkItem) -> WorkMandateRevision:
        if (
            current.kind != "resident_assignment"
            or current.origin != "mandate_review"
            or current.parent_work_item_id
            or current.plan_revision != expected_review_digest
        ):
            raise WorkError("work.mandate_revision_conflict")
        revision = await _validate_approved_mandate_item(
            item=current,
            work_item_id=work_item_id,
            principal_id=item.principal_id,
            workspace_id=workspace_id,
            allow_parked_for_admission=True,
        )
        if revision.assignment_source is None or len(revision.model_routes) != 1:
            raise WorkError("work.mandate_invalid")
        return revision

    revision = await validated(item)
    if item.status == "waiting_for_human":
        await _resolve_assignment_route(revision)
    async with postgres_graph_transaction(
        get_default_context().database
    ) as transaction:
        await assert_assignment_model_authority(revision, transaction=transaction)
        await transaction.lock_record("object", item.id)
        current = await _load_lineage_item(work_item_id)
        if await validated(current) != revision:
            raise WorkError("work.mandate_revision_conflict")
        if current.status == "waiting_for_human":
            source = revision.assignment_source
            assert source is not None
            slot_id = f"o.ResidentAssignmentPreparation.{source.intake_fact_id}"
            slot = {
                "workspace_id": workspace_id,
                "entry_id": source.entry_id,
                "work_item_id": current.work_item_id,
                "review_digest": current.plan_revision,
            }
            acquired = await transaction.insert_if_absent(
                "object",
                {
                    "id": slot_id,
                    "entity": "ResidentAssignmentPreparation",
                    "context": slot,
                },
            )
            if not acquired.created:
                raise WorkError("work.assignment_already_admitted")
            current = await work_items.transition_work_item(
                work_item_id,
                expected_status="waiting_for_human",
                target="queued",
                fields={"next_attempt_at": utc_now_iso()},
                transaction=transaction,
            )
        if approval_event_id:
            await _complete_approval_event(transaction, approval_event_id, current)
        return current


async def _complete_approval_event(transaction, event_id: str, item: WorkItem) -> None:
    event = await transaction.lock_record("object", event_id)
    data = (event or {}).get("context") or {}
    payload = data.get("payload") or {}
    if (
        (event or {}).get("entity") != "WorkOutboxEntry"
        or data.get("topic") != ASSIGNMENT_REVIEW_APPROVED
        or data.get("work_item_id") != item.work_item_id
        or payload.get("review_digest") != item.plan_revision
        or payload.get("work_approval_id") != item.plan.get("mandate_approval_id")
    ):
        raise WorkError("work.assignment_event_invalid")
    if data.get("status") == "delivered":
        return
    result = await transaction.find_one_and_update(
        "object",
        {"id": event_id, "context.status": "pending"},
        {"$set": {"context.status": "delivered", "context.updated_at": utc_now_iso()}},
    )
    if result is None:
        raise WorkError("work.assignment_event_invalid")


async def deliver_resident_assignment_approvals(*, limit: int = 20) -> int:
    """Recover due approved assignments through the durable WorkOutbox."""
    database = get_default_context().database
    if not graph_transaction_available(database):
        return 0
    rows = await database.find(
        "object",
        {
            "entity": "WorkOutboxEntry",
            "context.topic": ASSIGNMENT_REVIEW_APPROVED,
            "context.status": "pending",
            "context.available_at": {"$lte": utc_now_iso()},
        },
        sort=[("context.available_at", 1), ("id", 1)],
        limit=limit,
    )
    delivered = 0
    for row in rows:
        data = row["context"]
        now = datetime.now(timezone.utc)
        attempt = int(data.get("attempt") or 0)
        try:
            deadline = datetime.fromisoformat(data.get("deadline_at") or "")
            if deadline.utcoffset() is None or deadline <= now:
                raise WorkError("work.mandate_expired")
            item = await _load_lineage_item(data["work_item_id"])
            await enqueue_approved_resident_assignment(
                work_item_id=item.work_item_id,
                workspace_id=item.workspace_id,
                expected_review_digest=data["payload"]["review_digest"],
                approval_event_id=row["id"],
            )
        except Exception as exc:
            code = (
                exc.code
                if isinstance(exc, WorkError)
                else "work.assignment_delivery_retry"
            )
            terminal = code not in {
                "work.assignment_delivery_retry",
                "work.assignment_model_unavailable",
                "work.model_route_stale",
                "work.assignment_stale",
            }
            await database.find_one_and_update(
                "object",
                {
                    "id": row["id"],
                    "context.status": "pending",
                    "context.attempt": attempt,
                },
                {
                    "$set": {
                        "context.status": "dead_letter" if terminal else "pending",
                        "context.attempt": attempt + 1,
                        "context.available_at": (
                            now + timedelta(seconds=min(300, 2 ** min(attempt + 1, 8)))
                        ).isoformat(),
                        "context.updated_at": now.isoformat(),
                    }
                },
            )
            if terminal:
                log.warning(
                    "Assignment approval delivery stopped (event=%s, code=%s)",
                    row["id"],
                    code,
                )
        else:
            delivered += 1
    return delivered


async def _generate_preparation(
    context: WorkExecutionContext,
    revision: WorkMandateRevision,
    task: ResidentAssignmentTaskSnapshot,
) -> str:
    from app.agentive.services.work_mandate_inference import (
        run_reviewed_mandate_inference,
    )

    route = await _resolve_assignment_route(revision)
    try:
        return await run_reviewed_mandate_inference(
            context,
            revision,
            route=route,
            resolve_route=lambda: _resolve_assignment_route(revision),
            instructions=(
                "Prepare a private draft for the sponsor of this assigned task. "
                "Use only the supplied task data. Identify missing information, "
                "avoid unsupported claims, and never claim to have taken an action."
            ),
            prompt={
                "outcome": task.outcome,
                "acceptance_criteria": task.acceptance_criteria,
                "due_date": task.due_date,
                "success_criteria": revision.success_criteria,
                "stop_conditions": revision.stop_conditions,
                "result_obligations": revision.result_obligations,
            },
            capability_version="resident-assignment-preparation-v1",
        )
    except TimeoutError as exc:
        raise WorkError("work.assignment_model_timeout") from exc
    except WorkError:
        raise
    except Exception as exc:
        # Transport may already have been entered. Leave the ledger to
        # reconcile its receipt and never retry inference blindly.
        raise WorkError("work.assignment_model_failed") from exc


async def prepare_resident_assignment(
    context: WorkExecutionContext,
) -> tuple[WorkMandateRevision, str]:
    """Generate a private preparation under the reviewed assignment mandate."""
    root, revision = await load_approved_mandate_lineage(
        work_item_id=context.work_item_id,
        principal_id=context.principal_id,
        workspace_id=context.workspace_id,
        thread_id=context.thread_id,
    )
    if root.kind != "resident_assignment" or revision.assignment_source is None:
        raise WorkError("work.mandate_invalid")
    async with postgres_graph_transaction(
        get_default_context().database
    ) as transaction:
        task = await assert_assignment_model_authority(
            revision, transaction=transaction
        )
    if task is None:
        raise WorkError("work.mandate_invalid")
    result = await _generate_preparation(context, revision, task)
    if not result or len(result.encode()) > 64_000:
        raise WorkError("work.assignment_result_invalid")
    return revision, result


async def publish_resident_assignment_preparation(
    context: WorkExecutionContext, revision: WorkMandateRevision, result: str
) -> WorkItem:
    """Commit the private draft and leased completion in one transaction."""
    async with postgres_graph_transaction(
        get_default_context().database
    ) as transaction:
        task = await assert_assignment_model_authority(
            revision, transaction=transaction, shared_destination=False
        )
        if task is None:
            raise WorkError("work.mandate_invalid")
        async with work_items.authorized_work_item_effect(context):
            _, current = await load_approved_mandate_lineage(
                work_item_id=context.work_item_id,
                principal_id=context.principal_id,
                workspace_id=context.workspace_id,
                thread_id=context.thread_id,
            )
            if current != revision:
                raise WorkError("work.mandate_revision_conflict")
            thread = await ChatThread.find_one({"id": context.thread_id})
            if thread is None:
                raise WorkError("work.assignment_stale")
            digest = hashlib.sha256(
                f"resident-assignment:{context.work_item_id}".encode()
            ).hexdigest()
            message = await append_message(
                thread=thread,
                role="assistant",
                parts=[{"type": "text", "text": result}],
                provider_metadata={"resident_assignment_draft": True},
                message_id=f"n.ChatMessage.{digest}",
            )
            return await work_items.transition_leased(
                context.work_item_id,
                lease_token=context.lease_token,
                lease_fence=context.lease_fence,
                expected_status="running",
                target="succeeded",
                fields={"result_refs": [message.id]},
                transaction=transaction,
            )
