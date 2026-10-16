"""Shared approval dispatch and result reconciliation.

Human approvals, routine reconciliation and host follow-through use this path.
Integrated resource commands recover through their durable effect receipts; the older
executor families still run before consumption and require migration to the
same contract. A terminal proposal decision and an applied effect are separate
facts, and post-commit failures must retain reconciliation state.
"""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from app.agentive.staging import (
    StagingError,
    bless_token,
    claim_execution,
    consume_token,
    persist_consumed_nav_in_transcript,
    record_execute_outcome,
    release_execution_claim,
    resolve_transcript_anchor_for_token,
)
from app.agentive.staging_executors import (
    dispatch_under_request_scope as _dispatch_kind,
)
from app.agentive.staging_executors import supports as _kind_supported


def _durable_command_dispatch_enabled(sc: Any) -> bool:
    """Canonical command receipt integration on transactional shared stores."""
    from app.agentive.services.staging_command_apply import CANONICAL_COMMAND_KINDS
    from app.agentive.services.staging_command_batch import supports_canonical_batch
    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
    )

    return (
        sc.kind in CANONICAL_COMMAND_KINDS or supports_canonical_batch(sc)
    ) and graph_transaction_available()


async def _maybe_decide_linked_work_approval(
    *,
    token: str,
    user_id: str,
    decision: Literal["approved", "rejected"],
    reason: str = "",
) -> None:
    """Decide a linked WorkApproval when present; no-op for legacy cards."""
    from app.agentive.services import work_approvals
    from app.schemas.agentive.work import WorkError

    pending = await work_approvals.get_pending_by_staging_token(token)
    if pending is None:
        return
    try:
        if decision == "approved":
            await work_approvals.approve_work_approval(
                work_approval_id=pending.work_approval_id,
                decider_id=user_id,
            )
        else:
            await work_approvals.reject_work_approval(
                work_approval_id=pending.work_approval_id,
                decider_id=user_id,
                reason=reason,
            )
    except WorkError as exc:
        # Already decided by a concurrent caller — continue staging path.
        if exc.code != "work.approval_decided":
            raise


async def bless_and_execute(
    *,
    user_id: str,
    token: str,
    autonomy: Literal["single", "session"] = "single",
    decision_source: Optional[str] = None,
    request: Optional[Any] = None,
) -> Dict[str, Any]:
    """Bless ``token``, dispatch its kind's executor, consume on success.

    Mirrors ``bless_token_endpoint`` exactly: bless first (state transitions
    to blessed, push event fires), run the dispatcher, then consume the
    token if the executor succeeded. If the executor errors, the token is
    left blessed so a future retry path can pick it up.

    ``request`` is optional — the scheduler loop has no live HTTP request,
    and ``dispatch_under_request_scope`` only falls back to
    ``resolve_executor_workspace_id(request, user_id)`` when the token's own
    ``workspace_id`` (captured at mint time) is unset, which every
    ``routine_task_*`` stager always sets.

    Returns the same ``{"ok", "staged_change", "execute_result", ...}``
    envelope shape ``bless_token_endpoint`` returns, plus ``consumed`` (True
    once the token was consumed on a clean execute). Raises ``StagingError``
    on a bless failure (unknown/foreign/already-terminal token) or when the
    token is already being applied by a concurrent caller
    (``already_executing``) — callers that want a soft failure should catch it
    themselves.

    V1 accepts only one-time approval for this token. Session-wide approval
    is rejected before deciding linked work or dispatching any mutation.

    When a durable ``WorkApproval`` is linked to ``token``, the decide unit
    requeues the original WorkItem before the staging apply path continues.
    Cards without a linked approval keep the legacy inline path.
    """
    if autonomy != "single":
        raise StagingError(
            "autonomy_disabled",
            "Integral V1 can approve only the specific proposed change. "
            "Session-wide auto-approval is not enabled.",
        )
    await _maybe_decide_linked_work_approval(
        token=token, user_id=user_id, decision="approved"
    )
    sc = await bless_token(
        user_id=user_id,
        token=token,
        autonomy="single",
        decision_source=decision_source,
    )
    return await execute_blessed_change(
        user_id=user_id,
        token=token,
        request=request,
        staged=sc,
    )


async def execute_blessed_change(
    *,
    user_id: str,
    token: str,
    request: Optional[Any] = None,
    staged: Optional[Any] = None,
) -> Dict[str, Any]:
    """Dispatch + consume an already-blessed token (host follow-through / retry).

    Same post-bless path as :func:`bless_and_execute` without re-blessing.
    Used by ``staging_follow_through`` so an empty host turn does not leave
    apply to an unreliable model utterance.
    """
    from app.agentive.staging import get_token

    sc = staged if staged is not None else await get_token(token)
    if sc is None:
        raise StagingError("unknown_token", f"No staged change for token {token!r}")
    if sc.user_id != user_id:
        raise StagingError("wrong_user", "Token does not belong to this user")
    if sc.state != "blessed":
        raise StagingError(
            f"already_{sc.state}" if sc.state != "pending" else "not_blessed",
            f"Token is not blessed (state={sc.state!r})",
        )

    if (sc.last_error or {}).get("application_state") == "reconciliation_required":
        from app.agentive.services.staged_operation_apply import (
            enabled as operation_enabled,
        )
        from app.agentive.services.staging_command_apply import (
            has_applied_command_receipt,
        )

        if not operation_enabled(sc) and not (
            _durable_command_dispatch_enabled(sc)
            and await has_applied_command_receipt(sc, request)
        ):
            raise StagingError(
                "application_reconciliation_required",
                "Execution already returned successfully, but completion could not be "
                "recorded. Reconcile the saved result before retrying; do not execute "
                "this approved change again.",
            )

    response: Dict[str, Any] = {
        "ok": True,
        "staged_change": sc.to_dict(),
        "consumed": False,
    }
    if not _kind_supported(sc.kind):
        response["execute_result"] = {
            "skipped": True,
            "reason": f"No executor registered for kind {sc.kind!r}",
        }
        # Nothing was refused — the agent's own execute turn applies it.
        return response

    from app.agentive.services.staged_operation_apply import (
        enabled as operation_enabled,
    )

    # Canonical operation receipts serialize their own cross-worker effect.
    # A lost process cache must not prevent observation of that committed fact.
    if not operation_enabled(sc):
        await claim_execution(user_id=user_id, token=token)

    thread_id, message_id = await resolve_transcript_anchor_for_token(
        sc.session_id or "", sc.token
    )
    from app.services.mutation_provenance import (
        bind_mutation_provenance,
        reset_mutation_provenance,
    )

    _, prov_reset = bind_mutation_provenance(
        staging_token=sc.token,
        thread_id=thread_id,
        message_id=message_id,
    )

    async def execute() -> Dict[str, Any]:
        return await _dispatch_kind(
            request=request,
            user_id=user_id,
            kind=sc.kind,
            payload=sc.payload,
            # Execute in the workspace the change was STAGED in, not whatever
            # the caller's context resolves to — see bless_token_endpoint.
            preferred_workspace_id=sc.workspace_id,
        )

    operation_application = None
    try:
        from app.agentive.services.staged_operation_apply import (
            enabled as operation_enabled,
        )

        if operation_enabled(sc):
            from app.agentive.services.staged_operation_apply import (
                apply_staged_operation_once,
            )

            operation_application = await apply_staged_operation_once(
                sc=sc, execute=execute
            )
            result = operation_application.result
        elif _durable_command_dispatch_enabled(sc):
            if sc.kind == "batch":
                from app.agentive.services.staging_command_batch import (
                    apply_command_batch_once,
                )

                result = await apply_command_batch_once(sc=sc, request=request)
            else:
                from app.agentive.services.staging_command_apply import (
                    apply_staged_command_once,
                )

                result = await apply_staged_command_once(
                    sc=sc, request=request, execute=execute
                )
        else:
            result = await execute()
    except StagingError as exc:
        if exc.code in {
            "external_outcome_reconciliation_required",
            "application_reconciliation_required",
        }:
            await record_execute_outcome(
                token=token,
                error={
                    "error_code": exc.code,
                    "message": str(exc),
                    "application_state": "reconciliation_required",
                },
            )
        await release_execution_claim(token)
        raise
    except BaseException:
        await release_execution_claim(token)
        raise
    finally:
        reset_mutation_provenance(prov_reset)
    if isinstance(result, dict) and isinstance(result.get("message"), str):
        try:
            from app.services.id_resolver import humanize_ids

            result["message"] = await humanize_ids(
                result["message"], user_id=user_id, workspace_id=sc.workspace_id
            )
        except Exception:  # noqa: BLE001
            pass
    response["execute_result"] = result

    if operation_application is not None and operation_application.staged is not None:
        import logging

        from app.agentive import staging

        sc = operation_application.staged
        response["consumed"] = True
        response["staged_change"] = sc.to_dict()
        try:
            await staging._push_state_and_record_closure(sc)
            await persist_consumed_nav_in_transcript(sc, result)
        except (
            Exception
        ):  # noqa: BLE001 — committed decision/effect cannot be undone by notification failure
            logging.getLogger(__name__).warning(
                "operation completion notification pending", exc_info=True
            )
            response["completion_pending"] = True
        await release_execution_claim(token)
        return response

    if (
        not result.get("error")
        and result.get("ok") is not False
        and result.get("filed") is not False
        and result.get("skipped") is not True
    ):
        try:
            # A partial design resumed through the inbox must retain its
            # design receipt before the terminal staging row is removed.
            if sc.kind == "batch" and sc.session_id:
                from app.services import chat_threads

                thread = await chat_threads.get_thread_by_session(sc.session_id)
                marker = getattr(thread, "design_proposed", None) or {}
                if (marker.get("partial_build") or {}).get("batch_token") == sc.token:
                    receipt = await chat_threads.record_design_build_receipt(
                        session_id=sc.session_id,
                        user_id=user_id,
                        batch_token=sc.token,
                        execute_result=result,
                    )
                    if not isinstance(receipt, dict):
                        raise StagingError(
                            "build_receipt_unavailable",
                            "The batch applied but its design receipt could not be persisted.",
                        )
            # Consume clears the execution claim.
            await consume_token(user_id=user_id, token=token, expected_kind=sc.kind)
            response["consumed"] = True
            # A rehydrated cache may hold another instance of this proposal.
            # Serialize the instance actually transitioned by consume_token.
            sc = await get_token(token) or sc
            # The initial envelope was made while the decision was merely
            # blessed. Refresh it after consumption so callers receive one
            # authoritative state instead of "approved" alongside a separate
            # success flag and accidentally narrate an applied change as pending.
            response["staged_change"] = sc.to_dict()
            await persist_consumed_nav_in_transcript(sc, result)
            # Hand the output back to the agent. The transcript patch above
            # feeds the FE card; agent runtime's history build reads only
            # utterance + response, so without this a blessed external READ
            # returned its data to the human and never to the model that
            # asked for it.
            from app.services.no_fit_route import maybe_file_preserve_after_structure

            follow = await maybe_file_preserve_after_structure(
                user_id=user_id,
                session_id=sc.session_id,
                kind=sc.kind,
                payload=sc.payload if isinstance(sc.payload, dict) else {},
                result=result if isinstance(result, dict) else {},
            )
            if follow is not None:
                response["preserve_filed"] = follow
                if isinstance(result, dict):
                    result["preserve_filed"] = follow
                    response["execute_result"] = result
        except StagingError as exc:
            if exc.code == "already_consumed" and _durable_command_dispatch_enabled(sc):
                from app.agentive import staging_store
                from app.agentive.services.staging_command_apply import (
                    has_applied_command_receipt,
                )

                marker = await staging_store.load_decision_state(token, user_id)
                if (
                    marker is not None
                    and marker["state"] == "consumed"
                    and marker["kind"] == sc.kind
                    and await has_applied_command_receipt(sc, request)
                ):
                    # Another admitted worker already reconciled this exact
                    # effect. Terminal readback is success, not an unknown write.
                    response["consumed"] = True
                    response["staged_change"] = marker
                    await release_execution_claim(token)
                    return response
            # The executor succeeded. A completion/receipt failure is not a
            # refused write and must never invite another executor invocation.
            message = (
                "Execution returned successfully, but completion could not be "
                "recorded. Reconcile its saved result before retrying; do not "
                "approve or execute this change again."
            )
            response["ok"] = False
            response["error_code"] = "application_reconciliation_required"
            response["message"] = message
            response["application_state"] = "reconciliation_required"
            response["consume_warning"] = {"error_code": exc.code, "message": str(exc)}
            await record_execute_outcome(
                token=token,
                error={
                    "error_code": "application_reconciliation_required",
                    "message": message,
                    "application_state": "reconciliation_required",
                    "completion_error_code": exc.code,
                },
            )
            response["staged_change"] = sc.to_dict()
            await release_execution_claim(token)
            return response
        # A retry that lands clears any earlier explanation.
        await record_execute_outcome(token=token, error=None)
    else:
        # The write is still owed: release the claim so a retry can run.
        await release_execution_claim(token)
        # The token stays blessed and the write is still owed, so the reason
        # has to outlive this response — it is the only thing that lets a
        # surface which did not click say more than "approved, not applied".
        await record_execute_outcome(
            token=token,
            error={
                "message": _failure_message(result),
                "error_code": result.get("error_code"),
                "status_code": result.get("status_code"),
                **(
                    {"application_state": result["application_state"]}
                    if result.get("application_state")
                    else {}
                ),
            },
        )

    if operation_application is not None:
        # Refusal may race a current revocation; report the durable decision,
        # not the caller's earlier blessed cache snapshot.
        from app.agentive import staging_store

        current = await get_token(token)
        marker = (
            None
            if current is not None
            else await staging_store.load_decision_state(token, user_id)
        )
        if current is not None:
            response["staged_change"] = current.to_dict()
        elif marker is not None:
            response["staged_change"] = marker
    return response


def _failure_message(result: Dict[str, Any]) -> str:
    """The most specific thing we can tell the user about a refused write."""
    for key in ("message", "detail", "reason"):
        val = result.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    if result.get("filed") is False:
        return "The write did not complete."
    return "The change was approved but the write was refused."
