"""HTTP endpoints for the staging primitive.

Web flow endpoints:

* ``POST /api/agentive/staging/bless-token`` — frontend approval card calls
  this when the user clicks Approve. Optionally grants session-scoped
  autonomy for the change's kind.
* ``POST /api/agentive/staging/revoke-token`` — frontend approval card
  calls this when the user clicks Reject (or undoes an auto-approval).
* ``GET /api/agentive/staging/pending`` — returns the user's currently
  pending staged changes.
* ``GET /api/agentive/staging/token/{token}`` — current state of one
  token; used by the FE to reconcile inline-card state on remount.

Cross-channel flow endpoints (for SMS / Slack / voice / email
adapters that don't speak the assistant-ui card protocol):

* ``GET /api/agentive/staging/render/{token}?channel=<hint>`` —
  one StagedChange rendered for the named channel. Returns the
  ``text_prompt`` an adapter shows the user + structured
  ``action_links`` an adapter binds to native affordances.
* ``GET /api/agentive/staging/render-pending?channel=<hint>`` —
  all pending tokens for the user, rendered as a single combined
  prompt with numeric ordinals so the user can disambiguate via
  text reply.
* ``POST /api/agentive/staging/text-approve`` — adapter posts a
  user's free-text reply ("yes" / "approve 2" / "reject all" /
  etc.); endpoint parses against the user's pending list, dispatches
  the resolved bless / revoke calls, returns per-token results.

All routes are JWT-authenticated; the staging store is keyed by user_id
so a token minted for one user cannot be blessed/revoked/consumed by
another even if the token leaks.
"""

from typing import Annotated, Any, Dict, List, Literal, cast

from fastapi import Query, Request
from jvspatial.api import endpoint

from app.agentive.staging import (
    StagingError,
    get_pending_for_user,
    get_token,
    persist_rollback_in_transcript,
    revoke_token,
)
from app.api.errors import MissingAuthenticationError
from app.api.utils import resolve_principal_id


def _ok(staged_change_to_dict: Dict[str, Any]) -> Dict[str, Any]:
    return {"ok": True, "staged_change": staged_change_to_dict}


def _err(exc: StagingError) -> Dict[str, Any]:
    return {
        "ok": False,
        "error_code": exc.code,
        "message": str(exc),
    }


def _resolve_user(request: Request) -> str:
    """Resolve principal id from request, raising 401 if missing.

    Same auth pattern as the rest of the agentive layer — see
    ``api/agent_tools.py``. Raises ``MissingAuthenticationError`` (which
    integral's error middleware translates to 401).
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    return user_id


@endpoint(
    "/agentive/staging/bless-token",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def bless_token_endpoint(
    request: Request,
    token: str = "",
    autonomy: Literal["single", "session"] = "single",
) -> Dict[str, Any]:
    """Mark pending staged change approved and auto-execute the write.

    Auto-execute is the key UX move: the user already approved, so we
    don't wait for the agent's next turn to call ``execute_X``. The
    staging dispatcher (``staging_executors.dispatch``) routes the
    blessed token's payload to the matching write handler, then we
    consume the token so the StagedChangeCard flips from
    blessed/awaiting → consumed/done in real time. Eliminates a
    common LLM failure mode (model loses the token between turns or
    misinterprets the user's "Approved" prose).

    If ``autonomy="session"`` and the staged change has a session_id,
    the change's kind is also added to the session's autonomy grants —
    subsequent same-kind staged changes in the session will be created
    already-blessed (with an undo affordance still surfaced in the UI).
    """
    user_id = _resolve_user(request)
    from app.agentive.services.staging_apply import bless_and_execute

    try:
        return await bless_and_execute(
            user_id=user_id, token=token, autonomy=autonomy, request=request
        )
    except StagingError as exc:
        return _err(exc)


@endpoint(
    "/agentive/staging/revoke-token",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def revoke_token_endpoint(
    request: Request,
    token: str = "",
) -> Dict[str, Any]:
    """Reject (or undo an auto-approval of) a staged change.

    Tokens in any non-terminal state can be revoked, including blessed
    ones that have not yet been consumed.
    """
    user_id = _resolve_user(request)
    try:
        sc = await revoke_token(user_id=user_id, token=token)
    except StagingError as exc:
        return _err(exc)
    return _ok(sc.to_dict())


@endpoint(
    "/agentive/staging/rollback-status/{token}",
    methods=["GET"],
    auth=True,
    tags=["Agentive"],
)
async def rollback_status_endpoint(request: Request, token: str) -> Dict[str, Any]:
    """Return whether a consumed staged change can be rolled back."""
    user_id = _resolve_user(request)
    sc = await get_token(token)
    if sc is not None and sc.user_id != user_id:
        return {"ok": False, "error_code": "wrong_user"}
    from app.services.mutation_rollback import assess_rollback

    status = await assess_rollback(staging_token=token, user_id=user_id)
    return {"ok": True, "staging_token": token, **status}


@endpoint(
    "/agentive/staging/rollback-token",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def rollback_token_endpoint(
    request: Request,
    token: str = "",
    force: bool = False,
) -> Dict[str, Any]:
    """Undo all graph writes caused by a consumed staged-change token."""
    user_id = _resolve_user(request)
    from app.services.mutation_rollback import RollbackError, rollback_staged_change

    sc = await get_token(token)
    session_id = sc.session_id if sc is not None else None
    try:
        result = await rollback_staged_change(
            staging_token=token,
            user_id=user_id,
            force=force,
        )
    except RollbackError as exc:
        return {
            "ok": False,
            "error_code": exc.code,
            "message": exc.message,
        }

    if session_id is None:
        from app.services.change_event_logger import (
            envelope_from_dblog,
            get_change_event_logger,
        )

        rows = await get_change_event_logger().find_by_staging_token(token)
        if rows:
            details = envelope_from_dblog(rows[0]).details or {}
            thread_id = details.get("thread_id")
            if thread_id:
                try:
                    from app.models.nodes import ChatThread

                    thread = await ChatThread.get(thread_id)
                    if thread is not None:
                        session_id = getattr(thread, "provider_session_id", None)
                except Exception:  # noqa: BLE001
                    pass

    if session_id:
        await persist_rollback_in_transcript(
            session_id=session_id,
            token=token,
            rolled_back_at=result["rolled_back_at"],
            rollback_event_ids=result.get("rollback_event_ids") or [],
        )

    return result


@endpoint(
    "/agentive/staging/pending",
    methods=["GET"],
    auth=True,
    tags=["Agentive"],
)
async def pending_endpoint(request: Request) -> Dict[str, Any]:
    """Return all non-terminal staged changes for the authenticated user.

    Used as a fallback when the chat surface opens with stale state. The
    primary signal is still the inline StagedChange that arrives as a
    tool-call result during a turn.
    """
    user_id = _resolve_user(request)
    items = await get_pending_for_user(user_id)
    return {
        "pending": [sc.to_dict() for sc in items],
        "total": len(items),
    }


@endpoint(
    "/agentive/staging/token/{token}",
    methods=["GET"],
    auth=True,
    tags=["Agentive"],
)
async def token_state_endpoint(request: Request, token: str) -> Dict[str, Any]:
    """Return the current state of one staged change by token.

    Used by the chat surface to reconcile inline-card state on remount
    after navigation: the persisted message's tool-call result captures
    the state AT THE TIME OF MINTING (always ``pending``), but the
    user may have already approved / rejected since. On every mount
    the card asks here for the authoritative state and reflects it,
    preventing the "card reverts to AWAITING APPROVAL after navigating
    away" bug.

    Returns ``{ok: false, error_code: 'unknown_token'}`` when the
    token is no longer in the in-memory store (typically because it
    was consumed long enough ago to be swept, or the backend was
    restarted). Callers should fall back to the staged.state from the
    message context in that case — the most common terminal state we
    can't represent here is ``consumed``, which is also the user's
    expected interpretation of a missing token.
    """
    user_id = _resolve_user(request)
    sc = await get_token(token)
    if sc is None:
        return {"ok": False, "error_code": "unknown_token"}
    if sc.user_id != user_id:
        return {"ok": False, "error_code": "wrong_user"}
    return _ok(sc.to_dict())


# ---------------------------------------------------------------------------
# Cross-channel rendering + approval-by-text
# ---------------------------------------------------------------------------
#
# These endpoints let SMS / Slack / voice / email / MCP adapters
# (anything not the assistant-ui card surface) render staged changes
# in their native medium and accept text-based approval replies.
# See ``app/agentive/services/staging_renderers.py`` and
# ``app/agentive/services/approval_intent.py`` for the rendering and
# parsing primitives respectively.


# Channels recognised by the renderer. Validated server-side so a
# malformed adapter request gets a clean 400 instead of opaque
# behaviour.
_VALID_CHANNELS = {"text", "sms", "slack", "voice", "email", "mcp"}


@endpoint(
    "/agentive/staging/render/{token}",
    methods=["GET"],
    auth=True,
    tags=["Agentive"],
)
async def render_token_endpoint(
    request: Request,
    token: str,
    channel: Annotated[str, Query(max_length=32)] = "text",
) -> Dict[str, Any]:
    """Render one staged-change token for the named channel.

    Adapters fetch this after observing a ``staging_created`` event,
    show the rendered ``text_prompt`` to the user, and (when the
    user replies) post back via ``/text-approve``.

    Returns ``{ok: false, error_code: 'unknown_token' | 'wrong_user'
    | 'unknown_channel'}`` on the standard failure modes.
    """
    if channel not in _VALID_CHANNELS:
        return {
            "ok": False,
            "error_code": "unknown_channel",
            "message": (
                f"channel={channel!r} not recognised; "
                f"expected one of {sorted(_VALID_CHANNELS)}"
            ),
        }
    user_id = _resolve_user(request)
    sc = await get_token(token)
    if sc is None:
        return {"ok": False, "error_code": "unknown_token"}
    if sc.user_id != user_id:
        return {"ok": False, "error_code": "wrong_user"}
    from app.agentive.services.staging_renderers import (
        render_for_channel,
    )

    rendered = render_for_channel(sc, channel=channel)  # type: ignore[arg-type]
    return {"ok": True, "rendered": rendered}


@endpoint(
    "/agentive/staging/render-pending",
    methods=["GET"],
    auth=True,
    tags=["Agentive"],
)
async def render_pending_endpoint(
    request: Request,
    channel: Annotated[str, Query(max_length=32)] = "text",
    max_render: Annotated[int, Query(ge=1, le=50)] = 10,
) -> Dict[str, Any]:
    """Render every pending staged change as a single combined prompt.

    Returns numeric ordinals so adapters can disambiguate via text reply.

    Used by adapters that wake a session ("hi, are you there?") and
    want to surface the user's unresolved card backlog in one
    message. Ordinals returned in the prompt are stable: the same
    list order is what ``/text-approve`` resolves against.
    """
    if channel not in _VALID_CHANNELS:
        return {
            "ok": False,
            "error_code": "unknown_channel",
            "message": (
                f"channel={channel!r} not recognised; "
                f"expected one of {sorted(_VALID_CHANNELS)}"
            ),
        }
    user_id = _resolve_user(request)
    pending = await get_pending_for_user(user_id)
    from app.agentive.services.staging_renderers import render_pending_list

    payload = render_pending_list(
        pending,
        channel=cast(Literal["text", "sms", "slack", "voice", "email", "mcp"], channel),
        max_render=max_render,
    )
    return {"ok": True, **payload}


@endpoint(
    "/agentive/staging/text-approve",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def text_approve_endpoint(
    request: Request,
    text: str = "",
    channel: str = "text",
) -> Dict[str, Any]:
    """Parse user's text reply and dispatch resolved approval intent.

    The adapter POSTs the user's raw reply ("yes" / "approve 2" /
    "reject all" / etc.). This endpoint:

      1. Loads the user's pending staged-change list.
      2. Parses the text via ``approval_intent.parse_approval_intent``.
      3. For each resolved ``ApprovalIntent``, calls
         ``staging_apply.bless_and_execute`` (the SAME apply path as
         ``/bless-token``) or ``revoke_token``.
      4. Returns per-token results so the adapter can compose a
         confirmation reply for the user.

    ``{ok: True, results: [{token, verb, success, ...}], parsed: N}``
        when the text was an approval intent. ``parsed`` is the
        number of intents resolved (0 means "looked like an
        approval but no actionable pending token matched"; the
        adapter should explain to the user).

    ``{ok: True, parsed: 0, reason: "not_approval_intent"}``
        when the text doesn't parse as an approval. The adapter
        should forward the message to the agent as a normal turn.

    No state mutation happens on the ``not_approval_intent`` path —
    safe to call this from any inbound-text adapter pre-emptively.
    """
    user_id = _resolve_user(request)
    pending = await get_pending_for_user(user_id)
    from app.agentive.services.approval_intent import parse_approval_intent
    from app.agentive.services.staging_apply import bless_and_execute

    intents = parse_approval_intent(text, pending=pending)
    if not intents:
        return {
            "ok": True,
            "parsed": 0,
            "reason": "not_approval_intent",
            "pending_count": len(pending),
        }

    results: List[Dict[str, Any]] = []
    for intent in intents:
        result: Dict[str, Any] = {
            "token": intent.token,
            "verb": intent.verb,
            "autonomy": intent.autonomy,
            "ordinal": intent.ordinal,
            "confidence": intent.confidence,
        }
        try:
            if intent.verb == "bless":
                # Same apply path as ``/bless-token`` (B5): bless, run the
                # executor in the workspace the change was STAGED in, consume
                # on success, record the outcome. The previous inline copy
                # dropped the card's workspace (executes fell back to the
                # personal workspace) and never recorded a refusal.
                envelope = await bless_and_execute(
                    user_id=user_id,
                    token=intent.token,
                    autonomy=intent.autonomy,
                    request=request,
                )
                # Adapters expect a ``state: consumed`` echo on the happy path.
                result["state"] = (
                    "consumed"
                    if envelope.get("consumed")
                    else envelope["staged_change"]["state"]
                )
                result["execute_result"] = envelope.get("execute_result")
                if envelope.get("consume_warning") is not None:
                    result["consume_warning"] = envelope["consume_warning"]
                result["success"] = True
            else:  # revoke
                sc = await revoke_token(user_id=user_id, token=intent.token)
                result["state"] = sc.state
                result["success"] = True
        except StagingError as exc:
            result["success"] = False
            result["error_code"] = exc.code
            result["message"] = str(exc)
        results.append(result)

    return {
        "ok": True,
        "parsed": len(intents),
        "channel": channel,
        "results": results,
    }
