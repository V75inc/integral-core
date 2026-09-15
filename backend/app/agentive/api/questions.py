"""HTTP endpoints for resident clarifying questions.

``integral_ask_user`` records a pending question on the ChatThread and returns
a ``_kind: "user_question"`` envelope, which the chat surface renders as a card
with clickable options. These endpoints are the other half:

* ``POST /api/agentive/questions/answer`` — the card calls this when the user
  picks an option. It clears the marker and reports the pick. The answer then
  reaches the model as an ordinary next user turn (the card appends it), which
  is the same resume path bless already uses — nothing in this stack suspends
  a turn waiting on a human.
* ``GET /api/agentive/questions/pending`` — the pending question for a thread,
  so a card can reconcile its state on remount. Without it, navigating away
  and back would re-render an already-answered question as still open, the
  same bug the staging token-state endpoint exists to prevent.

Both routes are JWT-authenticated and ownership-checked against the thread, so
one user cannot answer or read another's question even with the thread id.
Forbidden ownership fails with ``InsufficientPermissionsError`` (403), not
HTTP 200 ``{"ok": false}``.
"""

from typing import Any, Dict

from fastapi import Request
from jvspatial.api import endpoint
from pydantic import ValidationError

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    MissingAuthenticationError,
    ResourceNotFoundError,
    UnprocessableEntityError,
)
from app.api.utils import resolve_principal_id
from app.models.nodes import ChatThread
from app.schemas.agentive.questions import (
    AnswerQuestionRequest,
    AnswerQuestionResponse,
    PendingQuestionResponse,
)
from app.services.chat_threads import resolve_pending_question


def _resolve_user(request: Request) -> str:
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    return user_id


async def _load_thread(thread_id: str) -> ChatThread:
    thread = await ChatThread.get(thread_id)
    if thread is None:
        raise ResourceNotFoundError(
            message="Chat thread not found", details={"thread_id": thread_id}
        )
    return thread


def _require_thread_owner(thread: ChatThread, user_id: str) -> None:
    if (getattr(thread, "user_id", "") or "") != user_id:
        raise InsufficientPermissionsError(
            message="Thread does not belong to the caller",
            details={"thread_id": getattr(thread, "id", None)},
        )


@endpoint(
    "/agentive/questions/answer",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def answer_question_endpoint(request: Request) -> Dict[str, Any]:
    """Clear a pending question once the user has answered it.

    ``question_id`` is validated against the marker, so a card left open in a
    background tab cannot clear a question that has since been superseded.

    This does NOT deliver the answer to the model — the card appends the
    user's pick as a normal chat message for that. Keeping the two separate
    means a user who answers in prose instead of clicking still works: their
    message clears the marker on its own (see
    ``clear_pending_question_for_session``).
    """
    user_id = _resolve_user(request)
    try:
        raw = await request.json()
    except Exception as exc:
        raise BadRequestError(message="Request body must be a JSON object") from exc
    try:
        body = AnswerQuestionRequest.model_validate(raw or {})
    except ValidationError as exc:
        raise BadRequestError(
            message="Invalid answer payload",
            details={"errors": exc.errors()},
        ) from exc

    thread = await _load_thread(body.thread_id)
    _require_thread_owner(thread, user_id)

    picks = list(body.choices or [])
    if body.free_text.strip():
        picks.append(body.free_text.strip())

    result = await resolve_pending_question(
        user_id=user_id,
        thread=thread,
        question_id=body.question_id,
        choices=picks,
    )
    if result.get("error") == "forbidden":
        raise InsufficientPermissionsError(
            message=result.get("detail") or "Thread does not belong to the caller"
        )
    if result.get("error") == "stale_question":
        raise UnprocessableEntityError(
            message=result.get("detail")
            or "That question is no longer the pending one",
            details={"error_code": "stale_question"},
        )
    if result.get("error"):
        raise UnprocessableEntityError(
            message=result.get("detail") or result["error"],
            details={"error_code": result["error"]},
        )
    return AnswerQuestionResponse(
        ok=True,
        cleared=bool(result.get("cleared")),
        question_id=str(result.get("question_id") or ""),
        choices=list(result.get("choices") or []),
        detail=result.get("detail"),
    ).model_dump()


@endpoint(
    "/agentive/questions/pending",
    methods=["GET"],
    auth=True,
    tags=["Agentive"],
)
async def pending_question_endpoint(
    request: Request, thread_id: str = ""
) -> Dict[str, Any]:
    """Return the thread's pending question, if any.

    Lets an inline question card reconcile on remount rather than trusting the
    persisted tool-call result, which captured the state at ask time (always
    ``pending``) and goes stale the moment the user answers.
    """
    user_id = _resolve_user(request)
    if not thread_id:
        raise ResourceNotFoundError(
            message="Chat thread not found", details={"thread_id": thread_id}
        )
    thread = await _load_thread(thread_id)
    _require_thread_owner(thread, user_id)

    from app.services.prompt_queue import ITEM_QUESTION, STATUS_PENDING, get_queue

    queue = get_queue(thread)
    pending_item = next(
        (
            i
            for i in queue["items"]
            if i.get("kind") == ITEM_QUESTION and i.get("status") == STATUS_PENDING
        ),
        None,
    )
    pending = None
    if pending_item:
        pending = {
            "question_id": pending_item.get("id"),
            "question": pending_item.get("question"),
            "options": pending_item.get("options") or [],
            "header": pending_item.get("header") or "",
            "multi_select": bool(pending_item.get("multi_select")),
            "asked_at_user_turn": pending_item.get("asked_at_user_turn"),
            "asked_at": pending_item.get("created_at"),
        }
    return PendingQuestionResponse(ok=True, pending=pending).model_dump()
