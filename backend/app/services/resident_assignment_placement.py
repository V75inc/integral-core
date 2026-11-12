"""Prepare an explicitly reviewed placement of a private assignment draft."""

from __future__ import annotations

from typing import Any

from jvspatial.core.context import get_default_context

from app.agentive import staging, staging_store
from app.agentive.services.work_mandates import _load_lineage_item
from app.api.errors import ResourceConflictError, ResourceNotFoundError
from app.contracts.information import schema_revision_from_profile_version
from app.models.edges import CONTAINS
from app.models.nodes import ChatMessage, Entry
from app.schemas.agentive.work import WorkError
from app.schemas.agentive.work_mandate import WorkMandateRevision
from app.services.app_operations.transaction_scope import (
    current_graph_transaction_database,
    postgres_graph_transaction,
)
from app.services.resident_assignment import (
    assert_assignment_model_authority,
    get_resident_assignment_review,
)


async def _placement_payload(
    *,
    transaction: Any,
    user_id: str,
    workspace_id: str,
    entry_id: str,
    work_item_id: str,
    review_digest: str,
) -> tuple[str, dict[str, Any], str]:
    item = await _load_lineage_item(work_item_id)
    revision = WorkMandateRevision.model_validate(item.plan.get("mandate_revision"))
    source = revision.assignment_source
    if (
        source is None
        or source.entry_id != entry_id
        or item.kind != "resident_assignment"
        or item.origin != "mandate_review"
        or item.status != "succeeded"
        or item.principal_id != user_id
        or item.workspace_id != workspace_id
        or revision.principal_id != user_id
        or revision.workspace_id != workspace_id
        or revision.thread_id != item.thread_id
        or item.plan_revision != review_digest
        or revision.review_digest() != review_digest
        or len(item.result_refs) != 1
    ):
        raise WorkError("work.assignment_result_invalid")

    task = await assert_assignment_model_authority(revision, transaction=transaction)
    if task is None:
        raise WorkError("work.assignment_stale")
    message = await ChatMessage.find_one({"id": item.result_refs[0]})
    entry = await Entry.find_one({"id": entry_id})
    if (
        message is None
        or message.role != "assistant"
        or message.thread_id != item.thread_id
        or entry is None
        or entry.record_revision != task.entry_revision
        or len(message.parts) != 1
        or message.parts[0].get("type") != "text"
        or len(
            await get_default_context().find_edges_between(
                item.thread_id, message.id, edge_class=CONTAINS
            )
        )
        != 1
    ):
        raise WorkError("work.assignment_result_invalid")
    text = message.parts[0].get("text")
    if not isinstance(text, str) or not text.strip() or len(text.encode()) > 64_000:
        raise WorkError("work.assignment_result_invalid")
    section = "## Resident preparation draft\n\n" + text
    return (
        item.thread_id,
        {
            "entry_id": entry_id,
            "body": f"{entry.body}\n\n{section}" if entry.body else section,
            "expected_record_revision": task.entry_revision,
            "expected_schema_revision": schema_revision_from_profile_version(
                task.definition_revision
            ),
            "idempotency_key": f"resident-placement:{work_item_id}",
        },
        section,
    )


async def stage_assignment_placement(
    *, user_id: str, workspace_id: str, entry_id: str, review_digest: str
) -> staging.StagedChange:
    """Stage an append to the same Entry. The sponsor must approve the write."""
    view = await get_resident_assignment_review(
        user_id=user_id, workspace_id=workspace_id, entry_id=entry_id
    )
    if view is None or not view.preparation_thread_id:
        raise ResourceNotFoundError(message="Private assignment draft not found")
    if view.review_digest != review_digest:
        raise ResourceConflictError(message="The assignment review has changed")
    try:
        async with postgres_graph_transaction(
            get_default_context().database
        ) as transaction:
            thread_id, payload, section = await _placement_payload(
                transaction=transaction,
                user_id=user_id,
                workspace_id=workspace_id,
                entry_id=entry_id,
                work_item_id=view.work_item_id,
                review_digest=review_digest,
            )
    except WorkError as exc:
        raise ResourceConflictError(
            message="The assignment or private draft has changed"
        ) from exc

    # Mint outside the Entry lock. Apply will recheck the private draft and
    # current assignment inside the canonical staging/Entry transaction.
    staged = await staging.create_staged_change(
        user_id=user_id,
        session_id=thread_id,
        kind="update_entry",
        summary="Append the private preparation draft to the assigned Entry",
        diff_human=section,
        diff_machine={"op": "update_entry", **payload},
        payload=payload,
        workspace_id=workspace_id,
        entry_edit_fences={
            "resident_assignment": {
                "work_item_id": view.work_item_id,
                "review_digest": review_digest,
            }
        },
    )
    durable = await staging_store.load_current(staged.token)
    if durable is None or durable.entry_edit_fences != staged.entry_edit_fences:
        raise ResourceConflictError(message="The placement review could not be saved")
    return durable


async def validate_assignment_placement(decision: dict, fence: dict) -> None:
    """Check the host-bound assignment fence before the canonical Entry write."""
    payload = decision.get("payload") or {}
    try:
        _, expected, _ = await _placement_payload(
            transaction=current_graph_transaction_database(),
            user_id=decision["user_id"],
            workspace_id=decision["workspace_id"],
            entry_id=payload["entry_id"],
            work_item_id=fence["work_item_id"],
            review_digest=fence["review_digest"],
        )
        if decision.get("kind") != "update_entry" or payload != expected:
            raise WorkError("work.assignment_result_invalid")
    except (WorkError, KeyError, ValueError) as exc:
        raise staging.StagingError(
            "proposal_changed",
            "The assignment or private draft changed. Prepare a fresh placement review.",
        ) from exc
