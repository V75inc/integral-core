"""Owner-scoped discovery and explicit retirement of saved chat designs.

A design decision is the durable ChatThread marker, not a staging token. This
surface never grants build authority: continuation uses the existing native
run/revision approval path. Partially applied or unsettled work requires
reconciliation in its original conversation.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.api.errors import InsufficientPermissionsError, ResourceConflictError
from app.models.nodes import ChatThread
from app.schemas.agentive.work import TERMINAL_WORK_STATUSES
from app.services import chat_threads
from app.services.app_operations.transaction_scope import postgres_graph_transaction
from app.services.release_admission import release_admission
from app.utils.time import utc_now_iso


def _pending(marker: dict[str, Any]) -> bool:
    return bool(
        marker
        and not marker.get("discarded_at")
        and not marker.get("build_receipt")
        and isinstance(marker.get("proposed_at_user_turn"), int)
    )


async def list_design_decisions(*, user_id: str, workspace_id: str) -> list[dict]:
    """Project current markers, including native designs with no legacy token."""
    async with release_admission(user_id, workspace_id):
        threads = await chat_threads.list_threads(
            user_id=user_id, workspace_id=workspace_id, include_archived=True
        )
        rows = []
        for thread in threads:
            marker = dict(thread.design_proposed or {})
            if not _pending(marker):
                continue
            rows.append(
                {
                    "thread_id": thread.id,
                    "workspace_id": workspace_id,
                    "thread_title": thread.title,
                    "summary": str(marker.get("summary") or "Saved App design"),
                    "design_id": str(marker.get("design_id") or ""),
                    "blueprint_revision": int(marker.get("blueprint_revision") or 0),
                    "proposed_at": str(marker.get("proposed_at") or ""),
                    "approved": bool(marker.get("approved")),
                    "partial_build": bool(marker.get("partial_build")),
                }
            )
        return rows


async def discard_design(
    *,
    user_id: str,
    workspace_id: str,
    thread_id: str,
    design_id: str,
    blueprint_revision: int,
    proposed_at: str,
) -> dict:
    """Retire precisely the reviewed design after all work has settled.

    The full marker CAS fences revision, approval, and any late build receipt.
    Retain a tombstone so visible-chat legacy recovery cannot resurrect the
    discarded design on reload. No successful or uncertain effects are erased.
    """
    thread = await ChatThread.get(thread_id)
    if (
        thread is None
        or thread.user_id != user_id
        or thread.workspace_id != workspace_id
    ):
        raise InsufficientPermissionsError(message="Design conversation unavailable")
    marker = deepcopy(thread.design_proposed or {})
    if (
        marker.get("discarded_at")
        and marker.get("discarded_by") == user_id
        and str(marker.get("design_id") or "") == design_id
        and int(marker.get("blueprint_revision") or 0) == blueprint_revision
        and str(marker.get("proposed_at") or "") == proposed_at
    ):
        # A committed retirement may lose its notification/readback response.
        # Retry only this tombstone's captured IDs, never a new session sweep.
        async with release_admission(user_id, workspace_id):
            await _retire_design_projections(
                list(marker.get("retired_token_ids") or []), user_id
            )
        return {"ok": True, "thread_id": thread_id, "discarded": True}
    if (
        not _pending(marker)
        or marker.get("partial_build")
        or str(marker.get("design_id") or "") != design_id
        or int(marker.get("blueprint_revision") or 0) != blueprint_revision
        or str(marker.get("proposed_at") or "") != proposed_at
    ):
        raise ResourceConflictError(
            message="Design changed or has build effects. Open its conversation to reconcile it."
        )
    now = utc_now_iso()
    async with (
        postgres_graph_transaction() as transaction,
        release_admission(user_id, workspace_id),
    ):
        # Lock the same thread row used by chat-turn admission before checking
        # its active work slot. A new submission cannot race a discard commit.
        locked = await transaction.find_one_and_update(
            "node",
            {
                "id": thread_id,
                "context.user_id": user_id,
                "context.workspace_id": workspace_id,
                "context.updated_at": thread.updated_at,
            },
            {"$set": {}},
        )
        if locked is None:
            raise ResourceConflictError(
                message="Design changed; refresh before discarding."
            )
        current = locked["context"]
        if current.get("design_proposed") != marker:
            raise ResourceConflictError(
                message="Design changed; refresh before discarding."
            )
        # Enroll the graph cache key on this transaction without writing an old
        # hydrated thread over the freshly locked record.
        await ChatThread.get(thread_id)
        active_work = str(current.get("active_work_item_id") or "")
        if active_work:
            work = await transaction.get("object", f"o.WorkItem.{active_work}")
            context = (work or {}).get("context") or {}
            if (
                context.get("status") not in TERMINAL_WORK_STATUSES
                or context.get("principal_id") != user_id
                or context.get("workspace_id") != workspace_id
                or context.get("thread_id") != thread_id
            ):
                raise ResourceConflictError(
                    message="This conversation has unsettled work. Stop or finish it before discarding."
                )
        await _assert_settled_build(marker, thread)
        queue = deepcopy(current.get("prompt_queue") or {})
        for item in queue.get("items") or []:
            if item.get("write_kind") == "design_proposal" and item.get("status") in {
                "pending",
                "approved",
            }:
                item.update(status="cancelled", decision_source="design_discard")
        if queue and not any(
            item.get("status") == "pending" for item in queue.get("items") or []
        ):
            queue.update(status="closed", closed_at=now, close_reason="cancelled")
        artifacts = deepcopy(current.get("artifacts") or {})
        for key in ("app_design_blueprint", "operation_bridge"):
            artifacts.pop(key, None)
        await _assert_no_ambiguous_draft(thread, user_id)
        retired_token_ids = await _capture_legacy_design_tokens(thread, user_id)
        await _retire_authoritative_tokens(retired_token_ids, user_id)
        tombstone = {
            "design_id": design_id,
            "blueprint_revision": blueprint_revision,
            "proposed_at": proposed_at,
            "discarded_at": now,
            "discarded_by": user_id,
            "retired_token_ids": retired_token_ids,
        }
        updated = await transaction.find_one_and_update(
            "node",
            {"id": thread_id},
            {
                "$set": {
                    "context.design_proposed": tombstone,
                    "context.prompt_queue": queue,
                    "context.artifacts": artifacts,
                    "context.updated_at": now,
                }
            },
        )
        if updated is None:
            raise ResourceConflictError(
                message="Design changed; refresh before discarding."
            )
    await _retire_design_projections(retired_token_ids, user_id)
    return {"ok": True, "thread_id": thread_id, "discarded": True}


async def _capture_legacy_design_tokens(thread: ChatThread, user_id: str) -> list[str]:
    """Capture immutable token identities while the original thread is locked."""
    from app.agentive.staging import get_pending_for_user

    session_ids = {thread.id, thread.provider_session_id} - {None, ""}
    return [
        change.token
        for change in await get_pending_for_user(user_id)
        if change.kind == "design_proposal"
        and change.session_id in session_ids
        and change.workspace_id == thread.workspace_id
    ]


async def _retire_authoritative_tokens(token_ids: list[str], user_id: str) -> None:
    """Commit exact durable token retirement with the design marker."""
    from app.agentive.services.staging_apply import _maybe_decide_linked_work_approval
    from app.agentive.services.staging_decision_store import transition_staged_decision

    for token_id in token_ids:
        await transition_staged_decision(
            token=token_id,
            user_id=user_id,
            target="revoked",
            expected_kind="design_proposal",
            decision_source="design_discard",
        )
        await _maybe_decide_linked_work_approval(
            token=token_id,
            user_id=user_id,
            decision="rejected",
            reason="design_discard",
        )


async def _retire_design_projections(token_ids: list[str], user_id: str) -> None:
    """Refresh terminal presentation only; never select or mutate fresh work."""
    from app.agentive.staging import _push_state_and_record_closure, get_token

    for token_id in token_ids:
        change = await get_token(token_id)
        if (
            change is not None
            and change.user_id == user_id
            and change.state == "revoked"
        ):
            await _push_state_and_record_closure(change)


async def _assert_settled_build(marker: dict, thread: ChatThread) -> None:
    """An approved but incomplete build requires original no-effect receipts."""
    if not marker.get("approved"):
        return
    from app.agentive.harness.effect_reconciliation import is_pre_effect_rejection
    from app.agentive.services.execution_runs import RunStep

    run_id = str(marker.get("affirm_run_id") or "")
    receipts = await RunStep.find({"run_id": run_id}) if run_id else []
    build_receipts = [
        r for r in receipts if r.capability_key == "integral_build_approved_design"
    ]
    if not build_receipts or any(
        r.principal_id != thread.user_id
        or r.workspace_id != thread.workspace_id
        or r.capability_source != "core"
        or not (is_pre_effect_rejection(r) or r.status == "denied")
        for r in build_receipts
    ):
        raise ResourceConflictError(
            message="This confirmed design has no proven no-effect build outcome. Resume or reconcile it in the original conversation before discarding."
        )


async def _assert_no_ambiguous_draft(thread: ChatThread, user_id: str) -> None:
    """Preserve drafts whose design ownership and revision cannot be proven.

    OpenBatchRecord currently carries no design identity. Even a scaffold-like
    label or operations list cannot prove ownership by this selected revision.
    Refuse discard rather than deleting unrelated or mixed resource work. A
    later turn can create a fresh batch after commit; no postcommit batch sweep
    is allowed on this path.
    """
    from app.agentive.staging import peek_open_batch
    from app.agentive.staging_store import OpenBatchRecord, _open_batch_record_id

    for session_id in {thread.id, thread.provider_session_id} - {None, ""}:
        durable = await OpenBatchRecord.get(_open_batch_record_id(user_id, session_id))
        if durable is not None or peek_open_batch(user_id, session_id) is not None:
            raise ResourceConflictError(
                message="This conversation has a draft change whose design association cannot be verified. Review or cancel that draft in its conversation before discarding the design."
            )
