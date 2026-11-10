"""Read and verify mandate review bindings in the existing durable work store.

This does not dispatch work, grant current permissions or reserve a budget.
Only authenticated host code may call it; model output is never authority.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import ValidationError

from app.agentive.work_models import WorkApproval, WorkItem
from app.schemas.agentive.work import WorkError
from app.schemas.agentive.work_mandate import (
    MandateEntrySourceRevision,
    ProposeEntryReadMandateRequest,
    ProposeMandateReviewRequest,
    WorkMandateRevision,
)


async def propose_work_mandate_review(
    *,
    revision: WorkMandateRevision,
    principal_id: str,
    workspace_id: str,
    thread_id: str,
    transaction: Any = None,
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

    if transaction is not None:
        from app.services.app_operations.transaction_scope import (
            current_graph_transaction_database,
        )

        if transaction is not current_graph_transaction_database():
            raise WorkError("work.transaction_required")

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
    db = None
    if transaction is None:
        db = _active_database()
        if not _is_postgres_txn_db(db):
            raise WorkError("work.transaction_required")
        db = _txn_database(db)
    digest = revision.review_digest()
    # Thread is in the immutable digest; a same-revision cross-thread retry
    # conflicts instead of producing a second independently approvable review.
    identity = f"mandate:{revision.mandate_id}:{revision.revision}"
    kind = "resident_assignment" if revision.assignment_source else "capability"
    object_id = work_item_object_id(
        kind=kind,
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
        kind=kind,
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
    owns_txn = transaction is None
    txn = await db.begin_transaction() if owns_txn else transaction
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
                or item.plan.get("mandate_revision") != snapshot
                or item.plan.get("mandate_approval_id") != approval_id
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
        if owns_txn:
            await db.commit_transaction(txn)
    except BaseException:
        if owns_txn:
            await db.rollback_transaction(txn)
        raise
    if owns_txn:
        await _refresh_work_item_cache(item)
        await _refresh_work_approval_cache(approval)
    return item, approval


async def load_approved_work_mandate(
    *, work_item_id: str, principal_id: str, workspace_id: str
) -> WorkMandateRevision:
    """Verify a stored revision against its exact durable approval and scope.

    ``principal_id``/``workspace_id`` must come from authenticated host scope.
    Read the review and approval from durable storage. A parked review still
    needs admission after approval.
    Child work must resolve its same approved root lineage before using this
    reader; copying a parent's approval id onto a child is deliberately denied.
    """
    item = await _load_lineage_item(work_item_id)
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
    allow_parked_for_admission: bool = False,
) -> WorkMandateRevision:
    """Verify the exact supplied durable snapshot and its approval binding."""
    revision, approval = await _read_mandate_binding(
        item=item,
        work_item_id=work_item_id,
        principal_id=principal_id,
        workspace_id=workspace_id,
    )
    if (
        approval.status != "approved"
        or approval.decision != "approved"
        or not approval.decider_id
        or not approval.decided_at
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
    if item.status == "waiting_for_human" and not allow_parked_for_admission:
        raise WorkError("work.mandate_admission_required")
    return revision


async def _read_mandate_binding(
    *, item: WorkItem, work_item_id: str, principal_id: str, workspace_id: str
) -> tuple[WorkMandateRevision, WorkApproval]:
    """Read and verify the immutable binding independently of its decision."""
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
    approval = await _load_lineage_approval(approval_id)
    if approval is None or (
        approval.work_approval_id != approval_id
        or approval.work_item_id != item.work_item_id
        or approval.authority_digest != digest
        or approval.definition_id != item.definition_id
    ):
        raise WorkError("work.mandate_approval_required")
    return revision, approval


async def _read_general_mandate_review(
    *, work_item_id: str, principal_id: str, workspace_id: str, thread_id: str
) -> tuple[WorkItem, WorkMandateRevision, WorkApproval]:
    item = await _load_lineage_item(work_item_id)
    if (
        not thread_id
        or item.thread_id != thread_id
        or item.origin != "mandate_review"
        or item.kind not in {"capability", "mandate_entry_read"}
        or item.parent_work_item_id
    ):
        raise WorkError("work.mandate_scope_denied")
    revision, approval = await _read_mandate_binding(
        item=item,
        work_item_id=work_item_id,
        principal_id=principal_id,
        workspace_id=workspace_id,
    )
    if revision.assignment_source is not None:
        raise WorkError("work.mandate_scope_denied")
    return item, revision, approval


async def check_mandate_entry_read_grants(
    revision: WorkMandateRevision,
    *,
    transaction: Any = None,
) -> tuple[
    Literal["current", "denied", "unsupported"], tuple[MandateEntrySourceRevision, ...]
]:
    """Check exact Core Entry reads against current declarations and policy."""
    import asyncio

    from app.agentive.services.execution_runs import build_core_capability_snapshot
    from app.schemas.policy import Resource, Subject
    from app.services.policy_engine import evaluate
    from app.services.release_admission import release_admission
    from app.services.resource_location import locate_resources

    if revision.external_grants or any(
        grant.capability_key != "integral_resolve_entry"
        or grant.operation != "read"
        or grant.app_id
        for grant in revision.grants
    ):
        return "unsupported", ()
    resources = {resource for grant in revision.grants for resource in grant.resources}
    if not resources or any(resource.kind != "entry" for resource in resources):
        return "unsupported", ()
    if not set(revision.inputs).issubset(resources) or len(resources) > min(
        revision.limits.max_tool_calls, revision.grants[0].max_calls
    ):
        return "denied", ()

    catalogue = await asyncio.to_thread(build_core_capability_snapshot)
    if any(
        grant.capability_version != catalogue["manifest_version"]
        for grant in revision.grants
    ) or not any(
        item["name"] == "integral_resolve_entry"
        and item["op_class"] == "read"
        and item["policy_action"] == "entry.read"
        for item in catalogue["capabilities"]
    ):
        return "denied", ()

    entry_ids = sorted(resource.resource_id for resource in resources)
    async with release_admission(revision.principal_id, revision.workspace_id):
        records: dict[str, dict] = {}
        if transaction is not None:
            for entry_id in entry_ids:
                await transaction.lock_record("node", entry_id, shared=True)
        placements = await locate_resources(
            "entry",
            entry_ids,
            workspace_hints=[revision.workspace_id],
            records_out=records,
        )
        source_revisions = []
        subject = Subject(kind="human", id=revision.principal_id)
        for entry_id in entry_ids:
            placement = placements.get(entry_id)
            if placement is None or placement.workspace_id != revision.workspace_id:
                return "denied", ()
            decision = await evaluate(
                subject=subject,
                action="entry.read",
                resource=Resource(
                    kind="entry", id=entry_id, scope=f"track:{placement.track_id}"
                ),
            )
            if not decision.allowed:
                return "denied", ()
            record = records.get(entry_id)
            if record is None:
                return "denied", ()
            version = (record.get("context") or {}).get("record_revision", 1)
            if type(version) is not int or version < 1:
                return "denied", ()
            source_revisions.append(
                MandateEntrySourceRevision(entry_id=entry_id, record_revision=version)
            )
    snapshot = tuple(source_revisions)
    if revision.entry_source_revisions and revision.entry_source_revisions != snapshot:
        return "denied", ()
    return "current", snapshot


async def _model_route_preflight(
    revision: WorkMandateRevision,
) -> Literal["current", "stale", "unavailable"]:
    from app.agentive.harness.model_routing import resolve_reviewed_conversation_route
    from app.api.errors import (
        InsufficientPermissionsError,
        QuotaExceededError,
        ServiceUnavailableError,
    )
    from app.models.nodes import ChatThread

    thread = await ChatThread.find_one({"id": revision.thread_id})
    if (
        thread is None
        or thread.archived
        or thread.provider_id != "integral_native"
        or thread.user_id != revision.principal_id
        or thread.workspace_id != revision.workspace_id
    ):
        return "unavailable"
    try:
        await resolve_reviewed_conversation_route(
            workspace_id=revision.workspace_id,
            profile_id=thread.model_profile_id,
            reasoning_efforts=thread.model_reasoning_efforts,
            reviewed_routes=revision.model_routes,
        )
    except WorkError as exc:
        if exc.code == "work.model_route_stale":
            return "stale"
        if exc.code != "work.mandate_model_unavailable":
            raise
        return "unavailable"
    except (InsufficientPermissionsError, QuotaExceededError, ServiceUnavailableError):
        return "unavailable"
    return "current"


async def list_general_mandate_reviews(
    *, principal_id: str, workspace_id: str, thread_id: str
) -> list[dict[str, str]]:
    """List the newest owned root reviews without resolving their model routes."""
    from app.agentive.services.work_outbox import OBJECT_COLLECTION, _active_database

    rows = await _active_database().find(
        OBJECT_COLLECTION,
        {
            "entity": "WorkItem",
            "context.principal_id": principal_id,
            "context.workspace_id": workspace_id,
            "context.thread_id": thread_id,
            "context.origin": "mandate_review",
            "context.kind": {"$in": ["capability", "mandate_entry_read"]},
            "context.parent_work_item_id": "",
        },
        sort=[("context.created_at", -1), ("id", -1)],
        limit=20,
    )
    reviews = []
    for row in rows:
        data = row.get("context") or {}
        terms = (data.get("plan") or {}).get("mandate_revision") or {}
        if terms.get("assignment_source") is None and isinstance(
            terms.get("goal"), str
        ):
            reviews.append(
                {
                    "work_item_id": data["work_item_id"],
                    "goal": terms["goal"],
                    "work_status": data["status"],
                }
            )
    return reviews


async def _entry_mandate_accounting(item: WorkItem) -> dict[str, Any] | None:
    """Expose only accounting state and amounts from this owned work item."""
    from app.agentive.services.work_outbox import OBJECT_COLLECTION, _active_database

    if item.kind != "mandate_entry_read":
        return None
    rows = await _active_database().find(
        OBJECT_COLLECTION,
        {
            "entity": "WorkBudgetReservation",
            "context.root_work_item_id": item.work_item_id,
            "context.work_item_id": item.work_item_id,
            "context.review_digest": item.plan_revision,
            "context.principal_id": item.principal_id,
            "context.workspace_id": item.workspace_id,
            "context.thread_id": item.thread_id,
        },
        limit=2,
    )
    if not rows:
        return {"status": "not_recorded"}
    if len(rows) != 1:
        return {"status": "unavailable"}
    record = rows[0].get("context") or {}
    status = record.get("status")
    dispatch = record.get("dispatch_state")
    if status == "reserved" and dispatch in {"intent", "unknown", "legacy_untracked"}:
        status = "awaiting_receipt"
    if status not in {"awaiting_receipt", "reserved", "settled", "overrun", "released"}:
        return {"status": "unavailable"}

    def usd(units: Any) -> str | None:
        if type(units) is not int or units < 0:
            return None
        dollars, fraction = divmod(units, 100_000_000)
        return f"{dollars}.{fraction:08d}".rstrip("0").rstrip(".")

    return {
        "status": status,
        "reserved_usd": (
            usd(record.get("upper_units"))
            if status in {"reserved", "awaiting_receipt"}
            else None
        ),
        "charged_usd": (
            usd(record.get("charged_units"))
            if status in {"settled", "overrun"}
            else None
        ),
    }


async def read_general_mandate_review(
    *, work_item_id: str, principal_id: str, workspace_id: str, thread_id: str
) -> dict[str, Any]:
    """Project an owned review for inspection without granting authority."""
    item, revision, approval = await _read_general_mandate_review(
        work_item_id=work_item_id,
        principal_id=principal_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
    )
    entry_read_status, _ = await check_mandate_entry_read_grants(revision)
    if entry_read_status == "current" and not revision.entry_source_revisions:
        entry_read_status = "denied"
    terms = revision.canonical_review_payload()
    for grant in (*terms["model_routes"], *terms["external_grants"]):
        grant.pop("credential_ref")
    return {
        "work_item_id": item.work_item_id,
        "work_status": item.status,
        "review_digest": item.plan_revision,
        "requires_reconciliation": (item.failure or {}).get("code")
        == "work.outcome_reconciliation_required",
        "accounting": await _entry_mandate_accounting(item),
        "approval_status": approval.status,
        "expires_at": approval.expires_at,
        "model_route_preflight": await _model_route_preflight(revision),
        "entry_read_preflight": entry_read_status,
        "terms": terms,
    }


async def build_entry_read_mandate_terms(
    request: ProposeEntryReadMandateRequest, *, workspace_id: str, thread: Any
) -> ProposeMandateReviewRequest:
    """Derive capability and model attribution for the structured proposal form."""
    import asyncio

    from app.agentive.harness.model_route import resolve_native_model_route
    from app.agentive.harness.model_routing import conversation_turn_routes
    from app.agentive.services.execution_runs import build_core_capability_snapshot

    entry_ids = tuple(dict.fromkeys(request.entry_ids))
    resources = [{"kind": "entry", "resource_id": entry_id} for entry_id in entry_ids]
    catalogue = await asyncio.to_thread(build_core_capability_snapshot)
    plan = await conversation_turn_routes(
        workspace_id=workspace_id,
        profile_id=thread.model_profile_id,
        reasoning_efforts=thread.model_reasoning_efforts,
        heavy_only=True,
        resolver=resolve_native_model_route,
    )
    route = plan.heavy
    if not route.credential_ref:
        raise WorkError("work.mandate_model_unavailable")
    return ProposeMandateReviewRequest.model_validate(
        {
            "mandate_id": request.mandate_id,
            "revision": 1,
            "goal": request.goal,
            "inputs": resources,
            "grants": [
                {
                    "capability_key": "integral_resolve_entry",
                    "capability_version": catalogue["manifest_version"],
                    "operation": "read",
                    "resources": resources,
                    "max_calls": len(resources),
                }
            ],
            "model_routes": [
                {
                    "provider": route.provider,
                    "model": route.model,
                    "credential_source": route.credential_source,
                    "credential_ref": route.credential_ref,
                }
            ],
            "limits": {
                "deadline_at": request.deadline_at,
                "max_model_requests": 1,
                "max_tool_calls": len(resources),
                "max_internal_writes": 0,
                "max_external_effects": 0,
                "max_spend": request.max_spend,
            },
            "success_criteria": [
                "Answer the stated goal from the reviewed Entries and cite the sources used."
            ],
            "stop_conditions": [
                "Stop if a source is unavailable or the reviewed limits cannot be met."
            ],
            "result_obligations": [
                "Save the answer in this private conversation and identify any evidence gaps."
            ],
        }
    )


async def propose_general_mandate_review(
    *,
    terms: ProposeMandateReviewRequest,
    principal_id: str,
    workspace_id: str,
    thread_id: str,
    require_current_entries: bool = False,
) -> dict[str, Any]:
    """Persist requested terms for review. Approval and grant resolution follow separately."""
    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
        postgres_graph_transaction,
    )
    from app.services.release_admission import release_admission

    if not graph_transaction_available():
        raise WorkError("work.transaction_required")
    try:
        revision = WorkMandateRevision(
            **terms.model_dump(),
            principal_id=principal_id,
            workspace_id=workspace_id,
            thread_id=thread_id,
        )
    except ValidationError as exc:
        raise WorkError("work.mandate_invalid") from exc

    async with (
        postgres_graph_transaction() as txn,
        release_admission(principal_id, workspace_id),
    ):
        thread = await txn.lock_record("node", thread_id)
        context = (thread or {}).get("context") or {}
        if (
            (thread or {}).get("entity") != "ChatThread"
            or context.get("user_id") != principal_id
            or context.get("workspace_id") != workspace_id
            or context.get("provider_id") != "integral_native"
            or context.get("archived")
        ):
            raise WorkError("work.mandate_scope_denied")
        status, sources = await check_mandate_entry_read_grants(
            revision, transaction=txn
        )
        if require_current_entries and status != "current":
            raise WorkError("work.mandate_grants_unavailable")
        if sources:
            revision = revision.model_copy(update={"entry_source_revisions": sources})
        item, _ = await propose_work_mandate_review(
            revision=revision,
            principal_id=principal_id,
            workspace_id=workspace_id,
            thread_id=thread_id,
            transaction=txn,
        )
    return await read_general_mandate_review(
        work_item_id=item.work_item_id,
        principal_id=principal_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
    )


async def decide_general_mandate_review(
    *,
    work_item_id: str,
    principal_id: str,
    workspace_id: str,
    thread_id: str,
    review_digest: str,
    decision: Literal["approved", "rejected"],
) -> dict[str, str]:
    """Decide the exact owned review. Execution admission is separate."""
    from app.agentive.services.work_approvals import reject_work_approval
    from app.agentive.services.work_outbox import (
        OBJECT_COLLECTION,
        build_outbox_document,
        outbox_id_for,
    )
    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
        postgres_graph_transaction,
    )
    from app.services.release_admission import release_admission

    if not graph_transaction_available():
        raise WorkError("work.transaction_required")
    scope = {
        "work_item_id": work_item_id,
        "principal_id": principal_id,
        "workspace_id": workspace_id,
        "thread_id": thread_id,
    }
    item, _, approval = await _read_general_mandate_review(**scope)
    async with (
        postgres_graph_transaction() as txn,
        release_admission(principal_id, workspace_id),
    ):
        if decision == "approved":
            await txn.lock_record("node", thread_id)
        await txn.lock_record(OBJECT_COLLECTION, approval.id)
        await txn.lock_record(OBJECT_COLLECTION, item.id)
        current, revision, bound_approval = await _read_general_mandate_review(**scope)
        if current.plan_revision != review_digest or bound_approval.id != approval.id:
            raise WorkError("work.mandate_revision_conflict")
        if (
            bound_approval.status == decision
            and bound_approval.decider_id == principal_id
        ):
            return {"approval_status": decision, "work_status": current.status}
        if (
            bound_approval.status != "pending"
            or current.status != "waiting_for_human"
            or current.cancel_requested_at
        ):
            raise WorkError("work.approval_decided")
        if decision == "rejected":
            _, current = await reject_work_approval(
                work_approval_id=bound_approval.work_approval_id,
                decider_id=principal_id,
                transaction=txn,
            )
        else:
            status, _ = await check_mandate_entry_read_grants(revision, transaction=txn)
            if status != "current" or not revision.entry_source_revisions:
                raise WorkError("work.mandate_grants_unavailable")
            if await _model_route_preflight(revision) != "current":
                raise WorkError("work.mandate_model_unavailable")
            now = datetime.now(timezone.utc)
            deadline = revision.canonical_review_payload()["limits"]["deadline_at"]
            if (
                bound_approval.expires_at != deadline
                or revision.limits.deadline_at <= now
            ):
                raise WorkError("work.mandate_expired")
            timestamp = now.isoformat()
            approved = await txn.find_one_and_update(
                OBJECT_COLLECTION,
                {"id": bound_approval.id, "context.status": "pending"},
                {
                    "$set": {
                        "context.status": "approved",
                        "context.decision": "approved",
                        "context.decider_id": principal_id,
                        "context.decided_at": timestamp,
                        "context.updated_at": timestamp,
                    }
                },
            )
            if approved is None:
                raise WorkError("work.approval_decided")
            topic = "work.mandate_review_approved"
            await txn.insert_if_absent(
                OBJECT_COLLECTION,
                build_outbox_document(
                    outbox_id=outbox_id_for(
                        work_item_id=work_item_id, topic=topic, seq=0
                    ),
                    work_item_id=work_item_id,
                    topic=topic,
                    payload={
                        "review_digest": review_digest,
                        "work_approval_id": bound_approval.work_approval_id,
                    },
                    created_at=timestamp,
                    deadline_at=deadline,
                ),
            )
        return {"approval_status": decision, "work_status": current.status}


async def admit_entry_read_mandate(
    *,
    work_item_id: str,
    principal_id: str,
    workspace_id: str,
    thread_id: str,
    review_digest: str,
) -> dict[str, str]:
    """Promote one exact owner-approved review to the dedicated worker."""
    from app.agentive.services.work_items import transition_work_item
    from app.agentive.services.work_outbox import OBJECT_COLLECTION
    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
        postgres_graph_transaction,
    )
    from app.services.release_admission import release_admission
    from app.utils.time import utc_now_iso

    if not graph_transaction_available():
        raise WorkError("work.transaction_required")
    scope = {
        "work_item_id": work_item_id,
        "principal_id": principal_id,
        "workspace_id": workspace_id,
        "thread_id": thread_id,
    }
    item, _, approval = await _read_general_mandate_review(**scope)
    async with (
        postgres_graph_transaction() as txn,
        release_admission(principal_id, workspace_id),
    ):
        await txn.lock_record("node", thread_id)
        await txn.lock_record(OBJECT_COLLECTION, approval.id)
        await txn.lock_record(OBJECT_COLLECTION, item.id)
        current, revision, decision = await _read_general_mandate_review(**scope)
        if current.plan_revision != review_digest or decision.id != approval.id:
            raise WorkError("work.mandate_revision_conflict")
        if (
            decision.status != "approved"
            or decision.decision != "approved"
            or decision.decider_id != principal_id
        ):
            raise WorkError("work.mandate_approval_required")
        if current.kind == "mandate_entry_read":
            return {"work_item_id": work_item_id, "work_status": current.status}
        if current.status != "waiting_for_human" or current.attempt:
            raise WorkError("work.mandate_admission_required")
        await _validate_approved_mandate_item(
            item=current,
            work_item_id=work_item_id,
            principal_id=principal_id,
            workspace_id=workspace_id,
            allow_parked_for_admission=True,
        )
        status, _ = await check_mandate_entry_read_grants(revision, transaction=txn)
        if status != "current" or not revision.entry_source_revisions:
            raise WorkError("work.mandate_grants_unavailable")
        if await _model_route_preflight(revision) != "current":
            raise WorkError("work.mandate_model_unavailable")
        if revision.limits.deadline_at <= datetime.now(timezone.utc):
            raise WorkError("work.mandate_expired")
        current = await transition_work_item(
            work_item_id,
            expected_status="waiting_for_human",
            target="queued",
            fields={
                "kind": "mandate_entry_read",
                "next_attempt_at": utc_now_iso(),
                "retry_policy": {"max_attempts": 1},
            },
            transaction=txn,
        )
        return {"work_item_id": work_item_id, "work_status": current.status}


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


async def is_mandate_work(work: str | WorkItem) -> bool:
    """Identify reviewed lineage before choosing an execution admission path."""
    seen: set[str] = set()
    for _ in range(32):
        item = await _load_lineage_item(work) if isinstance(work, str) else work
        if item.work_item_id in seen:
            raise WorkError("work.mandate_lineage_invalid")
        seen.add(item.work_item_id)
        if item.origin == "mandate_review" or any(
            key in item.plan
            for key in (
                "mandate_revision",
                "mandate_approval_id",
                "mandate_root_work_item_id",
            )
        ):
            return True
        if not item.parent_work_item_id:
            return False
        work = item.parent_work_item_id
    raise WorkError("work.mandate_lineage_invalid")


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


async def load_approved_mandate_path(
    *, work_item_id: str, principal_id: str, workspace_id: str, thread_id: str
) -> tuple[WorkItem, WorkMandateRevision, tuple[WorkItem, ...]]:
    """Resolve a root approval and root-to-leaf durable path.

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
    if stored_revision.assignment_source is not None and len(ancestors) != 1:
        raise WorkError("work.mandate_lineage_invalid")
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
    )
    if (
        revision.review_digest() != digest
        or root.plan_revision != digest
        or revision.thread_id != thread_id
    ):
        raise WorkError("work.mandate_revision_conflict")
    return root, revision, tuple(reversed(ancestors))


async def load_approved_mandate_lineage(
    *, work_item_id: str, principal_id: str, workspace_id: str, thread_id: str
) -> tuple[WorkItem, WorkMandateRevision]:
    """Read approved root intent; the returned values are not dispatch authority."""
    root, revision, _ = await load_approved_mandate_path(
        work_item_id=work_item_id,
        principal_id=principal_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
    )
    return root, revision
