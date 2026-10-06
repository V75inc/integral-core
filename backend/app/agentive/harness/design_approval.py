"""Persist build authority selected by the primary native model run."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from app.agentive.harness.contracts import HarnessExecutionScope
from app.api.errors import InsufficientPermissionsError, ResourceConflictError
from app.models.nodes import ChatThread, HarnessSession
from app.schemas.agentive.work import WorkExecutionContext
from app.services import chat_threads
from app.services.app_operations.transaction_scope import postgres_graph_transaction

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _authority_transaction(work_context: WorkExecutionContext | None):
    if work_context is not None:
        from app.agentive.services.work_items import authorized_work_item_effect

        async with authorized_work_item_effect(work_context) as graph:
            yield graph.database
    else:
        async with postgres_graph_transaction() as transaction:
            yield transaction


async def pending_design_is_approved_for_reply(
    *,
    scope: HarnessExecutionScope,
    utterance: str,
) -> bool:
    """Return whether this current run already has durable design approval."""
    thread = await ChatThread.get(scope.thread_id)
    if thread is None or (
        thread.user_id != scope.principal_id
        or thread.workspace_id != scope.workspace_id
        or thread.provider_id != "integral_native"
    ):
        raise InsufficientPermissionsError(message="Design approval scope mismatch")
    marker = dict(getattr(thread, "design_proposed", None) or {})
    if not marker or marker.get("build_receipt"):
        return False
    proposed_turn = marker.get("proposed_at_user_turn")
    if (
        not isinstance(proposed_turn, int)
        or await chat_threads.count_user_turns(thread) <= proposed_turn
        or await chat_threads.latest_user_message_text(thread) != utterance
    ):
        return False
    # Approval comes from the primary Pydantic AI run selecting the approved
    # build capability. This preflight must not run a second semantic judge.
    return bool(marker.get("approved"))


async def authorize_pending_design_build(
    *,
    scope: HarnessExecutionScope,
    expected_user_turn: int,
    expected_utterance: str,
    work_context: WorkExecutionContext | None = None,
) -> bool:
    """Persist build authority when the primary model selects the build tool.

    Tool selection is the native model's semantic decision. Core binds that
    decision to the current user turn, saved design revision, principal,
    workspace, and active run before any staged write can occur.
    """
    thread = await ChatThread.get(scope.thread_id)
    if thread is None or (
        thread.user_id != scope.principal_id
        or thread.workspace_id != scope.workspace_id
        or thread.provider_id != "integral_native"
    ):
        raise InsufficientPermissionsError(message="Design approval scope mismatch")
    marker = dict(getattr(thread, "design_proposed", None) or {})
    if not marker or marker.get("build_receipt"):
        return False
    if marker.get("approved"):
        return True
    proposed_turn = marker.get("proposed_at_user_turn")
    if (
        not isinstance(proposed_turn, int)
        or not isinstance(expected_user_turn, int)
        or expected_user_turn <= proposed_turn
        or not expected_utterance.strip()
        or not marker.get("design_id")
        or not marker.get("proposed_at")
        or not marker.get("blueprint_digest")
        or not isinstance(marker.get("blueprint_revision"), int)
    ):
        return False
    current_turn = await chat_threads.count_user_turns(thread)
    utterance = await chat_threads.latest_user_message_text(thread)
    if current_turn != expected_user_turn or utterance != expected_utterance:
        return False
    stamped = dict(marker)
    stamped.update(
        approved=True,
        affirm=True,
        affirm_for=utterance,
        affirm_via="native_build_tool_selection",
        affirm_run_id=scope.run_id,
        reply_kind="approve",
        approved_at=chat_threads.utc_now_iso(),
        approved_via="native_build_tool_selection",
    )
    async with _authority_transaction(work_context) as transaction:
        # Reading the graph participant inside the transaction also registers
        # its cache key for jvspatial's commit invalidation. Raw CAS alone
        # would leave an older ChatThread in the parent graph cache.
        current = await ChatThread.get(scope.thread_id)
        if current is None:
            raise ResourceConflictError(message="Design conversation unavailable")
        session = (
            await HarnessSession.get(thread.active_harness_session_id)
            if thread.active_harness_session_id
            else None
        )
        if (
            session is None
            or session.session_id != scope.session_id
            or session.thread_id != scope.thread_id
            or session.principal_id != scope.principal_id
            or session.workspace_id != scope.workspace_id
            or session.last_run_id != scope.run_id
            or session.status != "active"
        ):
            raise ResourceConflictError(
                message="Design approval run is no longer current"
            )
        # Lock the owning session row with its run fence in the same
        # transaction as the proposal CAS. A read alone permits a newer run
        # to claim the session between the check and the approval write.
        fenced = await transaction.find_one_and_update(
            "node",
            {
                "id": session.id,
                "context.session_id": scope.session_id,
                "context.thread_id": scope.thread_id,
                "context.principal_id": scope.principal_id,
                "context.workspace_id": scope.workspace_id,
                "context.status": "active",
                "context.last_run_id": scope.run_id,
            },
            {"$set": {"context.last_run_id": scope.run_id}},
        )
        if fenced is None:
            raise ResourceConflictError(
                message="Design approval lost the active run fence"
            )
        predicate = {
            "id": scope.thread_id,
            "context.user_id": scope.principal_id,
            "context.workspace_id": scope.workspace_id,
            "context.active_harness_session_id": session.id,
            "context.updated_at": thread.updated_at,
            "context.last_message_at": thread.last_message_at,
            "context.design_proposed.design_id": marker["design_id"],
            "context.design_proposed.proposed_at": marker["proposed_at"],
        }
        if marker.get("blueprint_digest"):
            predicate["context.design_proposed.blueprint_digest"] = marker[
                "blueprint_digest"
            ]
            predicate["context.design_proposed.blueprint_revision"] = marker[
                "blueprint_revision"
            ]
        updated = await transaction.find_one_and_update(
            "node",
            predicate,
            {"$set": {"context.design_proposed": stamped}},
        )
        if updated is None:
            raise ResourceConflictError(
                message="Design or conversation changed during approval"
            )
    logger.info(
        "Native design approval run=%s design=%s revision=%s via=build_tool_selection",
        scope.run_id,
        marker.get("design_id"),
        marker.get("blueprint_revision"),
    )
    return True
