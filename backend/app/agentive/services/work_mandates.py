"""Read and verify mandate review bindings in the existing durable work store.

This does not dispatch work, grant current permissions or reserve a budget.
Only authenticated host code may call it; model output is never authority.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import ValidationError

from app.agentive.services.work_approvals import get_work_approval
from app.agentive.work_models import WorkItem
from app.schemas.agentive.work import WorkError
from app.schemas.agentive.work_mandate import WorkMandateRevision


async def load_approved_work_mandate(
    *, work_item_id: str, principal_id: str, workspace_id: str
) -> WorkMandateRevision:
    """Verify a stored revision against its exact durable approval and scope.

    ``principal_id``/``workspace_id`` must come from authenticated host scope.
    A future approval producer must atomically bind ``plan.mandate_revision``,
    ``plan.mandate_approval_id`` and ``plan_revision`` to its reviewed digest.
    Child work must resolve its same approved root lineage before using this
    reader; copying a parent's approval id onto a child is deliberately denied.
    """
    item = await WorkItem.get(f"o.WorkItem.{work_item_id}")
    if item is None:
        raise WorkError("work.not_found")
    if (
        not principal_id
        or not workspace_id
        or item.work_item_id != work_item_id
        or item.principal_id != principal_id
        or item.workspace_id != workspace_id
    ):
        raise WorkError("work.mandate_scope_denied")
    snapshot = item.plan.get("mandate_revision")
    approval_id = item.plan.get("mandate_approval_id")
    if (
        not isinstance(snapshot, dict)
        or not isinstance(approval_id, str)
        or not (approval_id.strip() and approval_id == approval_id.strip())
    ):
        raise WorkError("work.mandate_approval_required")
    try:
        revision = WorkMandateRevision.model_validate(snapshot)
    except ValidationError as exc:
        raise WorkError("work.mandate_invalid") from exc
    if (
        revision.principal_id != principal_id
        or revision.workspace_id != workspace_id
        or revision.thread_id != item.thread_id
    ):
        raise WorkError("work.mandate_scope_denied")
    digest = revision.review_digest()
    if item.plan_revision != digest:
        raise WorkError("work.mandate_revision_conflict")
    approval = await get_work_approval(approval_id)
    if approval is None or (
        approval.work_item_id != item.work_item_id
        or approval.authority_digest != digest
        or approval.status != "approved"
        or approval.decision != "approved"
        or not approval.decider_id
        or not approval.decided_at
        or approval.definition_id != item.definition_id
    ):
        raise WorkError("work.mandate_approval_required")
    now = datetime.now(timezone.utc)
    try:
        expires = datetime.fromisoformat(approval.expires_at.replace("Z", "+00:00"))
        decided = datetime.fromisoformat(approval.decided_at.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise WorkError("work.mandate_approval_invalid") from exc
    if (
        expires.utcoffset() is None
        or decided.utcoffset() is None
        or (decided > now or decided >= expires)
    ):
        raise WorkError("work.mandate_approval_invalid")
    if expires <= now or revision.limits.deadline_at <= now:
        raise WorkError("work.mandate_expired")
    if item.cancel_requested_at or item.status not in {
        "queued",
        "running",
        "waiting_for_human",
        "waiting_for_event",
        "retry_wait",
    }:
        raise WorkError("work.mandate_inactive")
    return revision
