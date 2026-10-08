"""Atomic terminal transition for a WorkItem-backed native chat turn."""

from __future__ import annotations

from typing import Any, Literal

from app.agentive.services import work_items
from app.agentive.work_models import WorkItem
from app.schemas.agentive.work import (
    TERMINAL_WORK_STATUSES,
    WorkError,
    WorkExecutionContext,
    WorkFailure,
)
from app.services.app_operations.transaction_scope import (
    OperationTransactionUnavailable,
    graph_transaction_available,
    postgres_graph_transaction,
)
from app.services.chat_turn_admission import release_chat_turn_admission_in_transaction

ChatTerminalStatus = Literal["succeeded", "failed", "cancelled"]


async def terminalize_chat_turn(
    context: WorkExecutionContext,
    *,
    status: ChatTerminalStatus,
    failure: dict[str, Any] | None = None,
    result_fingerprint: str = "",
    receipt_refs: list[str] | None = None,
) -> WorkItem:
    """Finish a claimed chat turn and release admission in one transaction.

    WorkItem status/outbox, thread admission pointer, principal permit, and
    AgentRun terminal status share the transaction. A retry after a committed
    terminal transition is idempotent for the same outcome and repairs a
    leftover admission reservation; a conflicting outcome is rejected.
    """
    if not graph_transaction_available():
        raise OperationTransactionUnavailable(
            "Chat turn terminalization requires a transactional shared store"
        )

    async with postgres_graph_transaction() as transaction:
        item = await WorkItem.get(f"o.WorkItem.{context.work_item_id}")
        if (
            item is None
            or item.kind != "chat_turn"
            or item.work_item_id != context.work_item_id
            or item.principal_id != context.principal_id
            or item.workspace_id != context.workspace_id
            or item.thread_id != context.thread_id
        ):
            raise WorkError("work.policy_denied", "chat terminal scope mismatch")

        if item.status in TERMINAL_WORK_STATUSES:
            if item.status != status:
                raise WorkError(
                    "work.idempotency_conflict",
                    "chat turn already has a different terminal outcome",
                )
            transitioned = item
        else:
            if item.status != "running":
                raise WorkError(
                    "work.invalid_transition",
                    f"chat turn cannot finish from {item.status}",
                )
            fields: dict[str, Any] = {}
            if result_fingerprint:
                fields["result_fingerprint"] = result_fingerprint
            if receipt_refs:
                fields["receipt_refs"] = list(dict.fromkeys(receipt_refs))
            if status in {"failed", "cancelled"}:
                fields["failure"] = (
                    WorkFailure.from_record(
                        failure,
                        default_class=(
                            "cancelled" if status == "cancelled" else "permanent"
                        ),
                    ).model_dump(by_alias=True)
                    if failure
                    else work_items.normalize_failure(
                        class_="cancelled" if status == "cancelled" else "permanent",
                        code=(
                            "work.cancelled" if status == "cancelled" else "work.failed"
                        ),
                        message=(
                            "Turn cancelled" if status == "cancelled" else "Turn failed"
                        ),
                        retryable=False,
                    )
                )
            transitioned = await work_items.transition_leased(
                context.work_item_id,
                lease_token=context.lease_token,
                lease_fence=context.lease_fence,
                expected_status="running",
                target=status,
                fields=fields,
                transaction=transaction,
            )

        thread = None
        if item.thread_id:
            from app.models.nodes import ChatThread

            thread = await ChatThread.get(item.thread_id)
        await release_chat_turn_admission_in_transaction(
            transaction=transaction,
            thread=thread,
            work_item_id=item.work_item_id,
        )

        from app.agentive.services.execution_runs import AgentRun
        from app.utils.time import utc_now_iso

        runs = await AgentRun.find({"run_id": context.run_id})
        if len(runs) > 1:
            raise WorkError("work.idempotency_conflict", "chat run ID is not unique")
        run = runs[0] if runs else None
        if run is not None:
            if (
                run.work_item_id != item.work_item_id
                or run.thread_id != context.thread_id
                or run.user_id != context.principal_id
                or run.workspace_id != context.workspace_id
            ):
                raise WorkError("work.policy_denied", "chat run scope mismatch")
            if run.status not in {"succeeded", "failed", "cancelled"}:
                run.status = status
                run.finished_at = utc_now_iso()
                run.error = transitioned.failure if status != "succeeded" else None
                await run.save()
        return transitioned


__all__ = ["ChatTerminalStatus", "terminalize_chat_turn"]
