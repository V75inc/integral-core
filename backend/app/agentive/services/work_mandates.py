"""Read and verify mandate review bindings in the existing durable work store.

This does not dispatch work, grant current permissions or reserve a budget.
Only authenticated host code may call it; model output is never authority.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import ValidationError

from app.agentive.services.work_approvals import get_work_approval
from app.agentive.work_models import WorkApproval, WorkItem
from app.schemas.agentive.work import WorkError
from app.schemas.agentive.work_mandate import WorkMandateRevision


async def propose_work_mandate_review(
    *,
    revision: WorkMandateRevision,
    principal_id: str,
    workspace_id: str,
    thread_id: str,
) -> tuple[WorkItem, WorkApproval]:
    """Atomically persist one pending review, never runnable work.

    Host code supplies authenticated scope; no public tool calls this service.
    Current resource/capability authorization and execution admission must be
    added before exposing approval. Same mandate/revision retries reconcile
    stored values; changed values require a new explicit revision.
    PostgreSQL is required, with no process-local atomicity substitute.
    """
    from app.agentive.services.work_approvals import (
        _hydrate_approval,
        _refresh_work_approval_cache,
        build_work_approval_document,
        deterministic_work_approval_id,
    )
    from app.agentive.services.work_items import work_item_object_id
    from app.agentive.services.work_outbox import (
        OBJECT_COLLECTION,
        _active_database,
        _hydrate_work_item,
        _is_postgres_txn_db,
        _refresh_work_item_cache,
        _txn_database,
        build_outbox_document,
        build_work_item_document,
        outbox_id_for,
    )

    # Revalidate even a model constructed with unchecked copy/update helpers.
    try:
        revision = WorkMandateRevision.model_validate(revision.model_dump(mode="json"))
    except ValidationError as exc:
        raise WorkError("work.mandate_invalid") from exc
    if not all((principal_id, workspace_id, thread_id)) or (
        revision.principal_id != principal_id
        or revision.workspace_id != workspace_id
        or revision.thread_id != thread_id
    ):
        raise WorkError("work.mandate_scope_denied")
    now = datetime.now(timezone.utc)
    if revision.limits.deadline_at <= now:
        raise WorkError("work.mandate_expired")
    db = _active_database()
    if not _is_postgres_txn_db(db):
        raise WorkError("work.transaction_required")
    db = _txn_database(db)
    digest = revision.review_digest()
    # Thread is in the immutable digest; a same-revision cross-thread retry
    # conflicts instead of producing a second independently approvable review.
    identity = f"mandate:{revision.mandate_id}:{revision.revision}"
    object_id = work_item_object_id(
        kind="capability",
        origin="mandate_review",
        principal_id=principal_id,
        workspace_id=workspace_id,
        idempotency_key=identity,
    )
    work_id = object_id.removeprefix("o.WorkItem.")
    approval_id = deterministic_work_approval_id(
        work_item_id=work_id, policy_approval_id=digest
    )
    timestamp = now.isoformat()
    snapshot = revision.canonical_review_payload()
    deadline = snapshot["limits"]["deadline_at"]
    work_doc = build_work_item_document(
        object_id=object_id,
        work_item_id=work_id,
        kind="capability",
        origin="mandate_review",
        principal_id=principal_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
        idempotency_key=identity,
        input_payload={},
        input_fingerprint=digest,
        status="waiting_for_human",
        attempt=0,
        transition_seq=0,
        next_attempt_at="",
        created_at=timestamp,
        updated_at=timestamp,
        retry_policy={"max_attempts": revision.max_attempts},
        deadline_at=deadline,
        plan_revision=digest,
        plan={
            "mandate_revision": snapshot,
            "mandate_approval_id": approval_id,
        },
    )
    approval_doc = build_work_approval_document(
        work_approval_id=approval_id,
        work_item_id=work_id,
        staging_token="",
        policy_approval_id="",
        run_id="",
        run_step_id="",
        authority_digest=digest,
        expires_at=deadline,
        created_at=timestamp,
    )
    topic = "work.mandate_review_proposed"
    outbox_doc = build_outbox_document(
        outbox_id=outbox_id_for(work_item_id=work_id, topic=topic, seq=0),
        work_item_id=work_id,
        topic=topic,
        payload={"review_digest": digest, "work_approval_id": approval_id},
        created_at=timestamp,
        deadline_at=deadline,
    )
    txn = await db.begin_transaction()
    try:
        inserted = await txn.insert_if_absent(OBJECT_COLLECTION, work_doc)
        if not inserted.created:
            stored = inserted.record or await txn.get(OBJECT_COLLECTION, object_id)
            if stored is None:
                raise WorkError("work.cas_conflict")
            item = _hydrate_work_item(stored)
            if (
                item.principal_id != principal_id
                or item.workspace_id != workspace_id
                or item.thread_id != thread_id
                or item.origin != "mandate_review"
                or item.input_fingerprint != digest
                or item.plan_revision != digest
                or item.plan != work_doc["context"]["plan"]
            ):
                raise WorkError("work.idempotency_conflict")
            # Existing review must have its original atomic binding. Do not
            # reconstruct missing authority or reset a decided approval.
            stored_approval = await txn.get(OBJECT_COLLECTION, approval_doc["id"])
            if stored_approval is None:
                raise WorkError("work.mandate_approval_required")
            approval = _hydrate_approval(stored_approval)
            if (
                approval.work_item_id != work_id
                or approval.authority_digest != digest
                or approval.expires_at != deadline
            ):
                raise WorkError("work.mandate_revision_conflict")
        else:
            approval_insert = await txn.insert_if_absent(
                OBJECT_COLLECTION, approval_doc
            )
            if not approval_insert.created:
                raise WorkError("work.cas_conflict")
            await txn.insert_if_absent(OBJECT_COLLECTION, outbox_doc)
            item = _hydrate_work_item(work_doc)
            approval = _hydrate_approval(approval_doc)
        await db.commit_transaction(txn)
    except Exception:
        await db.rollback_transaction(txn)
        raise
    await _refresh_work_item_cache(item)
    await _refresh_work_approval_cache(approval)
    return item, approval


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
    return await _validate_approved_mandate_item(
        item=item,
        work_item_id=work_item_id,
        principal_id=principal_id,
        workspace_id=workspace_id,
    )


async def _validate_approved_mandate_item(
    *,
    item: WorkItem,
    work_item_id: str,
    principal_id: str,
    workspace_id: str,
    fresh_approval: bool = False,
) -> WorkMandateRevision:
    """Verify the exact supplied durable snapshot and its approval binding."""
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
    approval = (
        await _load_lineage_approval(approval_id)
        if fresh_approval
        else await get_work_approval(approval_id)
    )
    if approval is None or (
        approval.work_approval_id != approval_id
        or approval.work_item_id != item.work_item_id
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


async def _load_lineage_item(work_item_id: str) -> WorkItem:
    """Read durable lineage without an identity-cache authority shortcut."""
    from app.agentive.services.work_outbox import (
        OBJECT_COLLECTION,
        _active_database,
        _hydrate_work_item,
    )

    document = await _active_database().get(
        OBJECT_COLLECTION, f"o.WorkItem.{work_item_id}"
    )
    if document is None:
        raise WorkError("work.not_found")
    return _hydrate_work_item(document)


async def _load_lineage_approval(approval_id: str) -> WorkApproval | None:
    from app.agentive.services.work_approvals import (
        _hydrate_approval,
        work_approval_object_id,
    )
    from app.agentive.services.work_outbox import OBJECT_COLLECTION, _active_database

    document = await _active_database().get(
        OBJECT_COLLECTION, work_approval_object_id(approval_id)
    )
    return _hydrate_approval(document) if document is not None else None


async def load_approved_mandate_lineage(
    *, work_item_id: str, principal_id: str, workspace_id: str, thread_id: str
) -> tuple[WorkItem, WorkMandateRevision]:
    """Resolve a root approval through exact durable parent links.

    Every child carries only ``plan.mandate_root_work_item_id`` and the root
    review digest, never a copied snapshot/approval. All ancestors must retain
    authenticated owner/workspace/thread, the same root and digest, and active
    uncancelled state. Cycles and more than 32 ancestors fail closed.

    This read is not dispatch authority: admission must lock/fence the root
    and executing child, recheck current grants, and reserve shared limits in
    the effect transaction. It does not create or enqueue a child.
    """
    if not all((work_item_id, principal_id, workspace_id, thread_id)):
        raise WorkError("work.mandate_scope_denied")
    seen: set[str] = set()
    ancestors: list[WorkItem] = []
    current_id = work_item_id
    for _ in range(32):
        if current_id in seen:
            raise WorkError("work.mandate_lineage_invalid")
        seen.add(current_id)
        item = await _load_lineage_item(current_id)
        if (
            item.work_item_id != current_id
            or item.principal_id != principal_id
            or item.workspace_id != workspace_id
            or item.thread_id != thread_id
        ):
            raise WorkError("work.mandate_scope_denied")
        if item.cancel_requested_at or item.status not in {
            "queued",
            "running",
            "waiting_for_human",
            "waiting_for_event",
            "retry_wait",
        }:
            raise WorkError("work.mandate_inactive")
        ancestors.append(item)
        if not item.parent_work_item_id:
            root = item
            break
        if (
            "mandate_revision" in item.plan
            or "mandate_approval_id" in item.plan
            or not item.plan.get("mandate_root_work_item_id")
        ):
            raise WorkError("work.mandate_lineage_invalid")
        current_id = item.parent_work_item_id
    else:
        raise WorkError("work.mandate_lineage_invalid")
    if root.plan.get("mandate_root_work_item_id") not in (None, root.work_item_id):
        raise WorkError("work.mandate_lineage_invalid")
    try:
        stored_revision = WorkMandateRevision.model_validate(
            root.plan.get("mandate_revision")
        )
    except ValidationError as exc:
        raise WorkError("work.mandate_invalid") from exc
    digest = stored_revision.review_digest()
    parent_deadline = stored_revision.limits.deadline_at
    for child in reversed(ancestors[:-1]):
        if (
            child.plan.get("mandate_root_work_item_id") != root.work_item_id
            or child.plan_revision != digest
        ):
            raise WorkError("work.mandate_lineage_invalid")
        # A child may shorten, never extend or omit, the reviewed deadline.
        try:
            deadline = datetime.fromisoformat(child.deadline_at.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise WorkError("work.mandate_lineage_invalid") from exc
        if deadline.utcoffset() is None or deadline > parent_deadline:
            raise WorkError("work.mandate_lineage_invalid")
        if deadline <= datetime.now(timezone.utc):
            raise WorkError("work.mandate_expired")
        parent_deadline = deadline
    revision = await _validate_approved_mandate_item(
        item=root,
        work_item_id=root.work_item_id,
        principal_id=principal_id,
        workspace_id=workspace_id,
        fresh_approval=True,
    )
    if (
        revision.review_digest() != digest
        or root.plan_revision != digest
        or revision.thread_id != thread_id
    ):
        raise WorkError("work.mandate_revision_conflict")
    return root, revision
