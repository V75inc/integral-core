"""Bless → dispatch → consume: the shared "approve and apply now" sequence.

Extracted from ``bless_token_endpoint`` (``app/agentive/api/staging.py``) so
the FE "Approve" button and the ``routine_task_scheduler`` background loop's
write-scope reconciliation (auto-applying a pre-approved staged change on a
routine run) share ONE apply path instead of two copies drifting apart. Pure
extract-function — no behavior change versus the original inline endpoint
logic.
"""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from app.agentive.staging import (
    StagedChange,
    StagingError,
    bless_token,
    claim_execution,
    consume_token,
    grant_autonomy,
    persist_consumed_nav_in_transcript,
    record_execute_outcome,
    record_external_result_for_agent,
    release_execution_claim,
    resolve_transcript_anchor_for_token,
)
from app.agentive.staging_executors import (
    dispatch_under_request_scope as _dispatch_kind,
)
from app.agentive.staging_executors import supports as _kind_supported


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

    Session autonomy (``autonomy="session"``) is granted only AFTER a clean
    execute (C2): a refused write must not leave a standing grant that
    auto-blesses the next same-kind card. The grant itself goes through
    ``grant_autonomy``, which keeps ``SESSION_AUTONOMY_BLOCKED_KINDS`` refused.

    When a durable ``WorkApproval`` is linked to ``token``, the decide unit
    requeues the original WorkItem before the staging apply path continues.
    Cards without a linked approval keep the legacy inline path.
    """
    await _maybe_decide_linked_work_approval(token=token, user_id=user_id, decision="approved")
    sc = await bless_token(user_id=user_id, token=token, autonomy="single")

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
        await _maybe_grant_session_autonomy(sc, user_id=user_id, autonomy=autonomy)
        return response

    # One executor per token (B3): a second concurrent approve gets
    # ``already_executing`` instead of running the write again.
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
    try:
        result = await _dispatch_kind(
            request=request,
            user_id=user_id,
            kind=sc.kind,
            payload=sc.payload,
            # Execute in the workspace the change was STAGED in, not whatever
            # the caller's context resolves to — see bless_token_endpoint.
            preferred_workspace_id=sc.workspace_id,
        )
    except BaseException:
        await release_execution_claim(token)
        raise
    finally:
        reset_mutation_provenance(prov_reset)
    if isinstance(result, dict) and isinstance(result.get("message"), str):
        try:
            from app.services.id_resolver import humanize_ids

            result["message"] = await humanize_ids(result["message"])
        except Exception:  # noqa: BLE001
            pass
    response["execute_result"] = result

    if not result.get("error") and result.get("filed") is not False:
        try:
            # Consume clears the execution claim.
            await consume_token(user_id=user_id, token=token, expected_kind=sc.kind)
            response["consumed"] = True
            await persist_consumed_nav_in_transcript(sc, result)
            # Hand the output back to the agent. The transcript patch above
            # feeds the FE card; jvagent's history build reads only
            # utterance + response, so without this a blessed external READ
            # returned its data to the human and never to the model that
            # asked for it.
            await record_external_result_for_agent(sc, result)
        except StagingError as exc:
            response["consume_warning"] = {"error_code": exc.code, "message": str(exc)}
            await release_execution_claim(token)
        # A retry that lands clears any earlier explanation.
        await record_execute_outcome(token=token, error=None)
        await _maybe_grant_session_autonomy(sc, user_id=user_id, autonomy=autonomy)
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
            },
        )

    return response


async def _maybe_grant_session_autonomy(
    sc: StagedChange, *, user_id: str, autonomy: str
) -> None:
    """Grant session autonomy for ``sc.kind`` once the write is known clean.

    ``payload`` is threaded through because some kinds are too coarse to grant
    on the kind alone — ``mcp_tool_call`` is one string shared by every remote
    tool on every mounted connector, so a kind-level grant here would let one
    "approve & auto-allow" on a Drive search pre-bless a QuickBooks invoice.
    ``staging.autonomy_key_for`` narrows those to the specific target, and this
    is the call site that has to supply the target for it to do so.
    """
    if autonomy != "session" or not sc.session_id:
        return
    await grant_autonomy(
        user_id=user_id,
        session_id=sc.session_id,
        kind=sc.kind,
        payload=sc.payload,
    )


def _failure_message(result: Dict[str, Any]) -> str:
    """The most specific thing we can tell the user about a refused write."""
    for key in ("message", "detail", "reason"):
        val = result.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    if result.get("filed") is False:
        return "The write did not complete."
    return "The change was approved but the write was refused."
