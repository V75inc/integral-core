"""HTTP endpoints for the Prompt Sheet queue.

Durable sequester UI over the chat composer. See
``docs/superpowers/specs/2026-09-08-prompt-sheet-design.md``.
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
from app.schemas.agentive.prompt_queue import (
    CancelAllRequest,
    MarkWriteRequest,
    ResolveQuestionRequest,
)
from app.services import prompt_queue as pq


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


def _require_owner(thread: ChatThread, user_id: str) -> None:
    if (getattr(thread, "user_id", "") or "") != user_id:
        raise InsufficientPermissionsError(
            message="Thread does not belong to the caller",
            details={"thread_id": getattr(thread, "id", None)},
        )


def _raise_service_error(result: Dict[str, Any]) -> None:
    err = result.get("error")
    if not err:
        return
    if err == "forbidden":
        raise InsufficientPermissionsError(message=result.get("detail") or "Forbidden")
    if err in ("stale_question", "wrong_kind", "invalid_answer", "invalid_status"):
        raise UnprocessableEntityError(
            message=result.get("detail") or str(err),
            details={"error_code": err},
        )
    if err == "not_found":
        raise ResourceNotFoundError(
            message=result.get("detail") or "Not found",
            details={"error_code": err},
        )
    raise UnprocessableEntityError(
        message=result.get("detail") or str(err),
        details={"error_code": err},
    )


@endpoint(
    "/agentive/prompt-queue",
    methods=["GET"],
    auth=True,
    tags=["Agentive"],
)
async def get_prompt_queue_endpoint(
    request: Request, thread_id: str = ""
) -> Dict[str, Any]:
    """Return the open Prompt Sheet queue for a chat thread, if any."""
    user_id = _resolve_user(request)
    if not thread_id:
        raise ResourceNotFoundError(
            message="Chat thread not found", details={"thread_id": thread_id}
        )
    thread = await _load_thread(thread_id)
    _require_owner(thread, user_id)
    result = await pq.get_open_queue_for_thread(user_id=user_id, thread=thread)
    _raise_service_error(result)
    return result


@endpoint(
    "/agentive/prompt-queue/resolve-question",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def resolve_question_endpoint(request: Request) -> Dict[str, Any]:
    """Answer or skip a pending clarifying-question queue item."""
    user_id = _resolve_user(request)
    try:
        raw = await request.json()
        body = ResolveQuestionRequest.model_validate(raw or {})
    except ValidationError as exc:
        raise BadRequestError(
            message="Invalid resolve payload",
            details={"errors": exc.errors()},
        ) from exc
    except Exception as exc:
        raise BadRequestError(message="Request body must be a JSON object") from exc

    thread = await _load_thread(body.thread_id)
    _require_owner(thread, user_id)

    picks = list(body.choices or [])
    if body.free_text.strip():
        picks.append(body.free_text.strip())

    result = await pq.resolve_question_item(
        user_id=user_id,
        thread=thread,
        item_id=body.item_id,
        choices=picks if not body.skip else None,
        skip=body.skip,
    )
    _raise_service_error(result)
    return {
        "ok": True,
        "item": result.get("item"),
        "queue": result.get("queue"),
        "resume_text": result.get("resume_text"),
        "closed": bool(result.get("closed")),
    }


@endpoint(
    "/agentive/prompt-queue/mark-write",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def mark_write_endpoint(request: Request) -> Dict[str, Any]:
    """Mark a staged-write queue item approved or rejected after bless/revoke."""
    user_id = _resolve_user(request)
    try:
        raw = await request.json()
        body = MarkWriteRequest.model_validate(raw or {})
    except ValidationError as exc:
        raise BadRequestError(
            message="Invalid mark-write payload",
            details={"errors": exc.errors()},
        ) from exc
    except Exception as exc:
        raise BadRequestError(message="Request body must be a JSON object") from exc

    thread = await _load_thread(body.thread_id)
    _require_owner(thread, user_id)
    result = await pq.mark_write_item(
        user_id=user_id,
        thread=thread,
        token=body.token,
        status=body.status,
    )
    _raise_service_error(result)
    return {
        "ok": True,
        "matched": bool(result.get("matched")),
        "queue": result.get("queue"),
        "resume_text": result.get("resume_text"),
        "closed": bool(result.get("closed")),
    }


@endpoint(
    "/agentive/prompt-queue/cancel-all",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def cancel_all_endpoint(request: Request) -> Dict[str, Any]:
    """Cancel remaining prompts, revoke pending writes, and close the queue."""
    user_id = _resolve_user(request)
    try:
        raw = await request.json()
        body = CancelAllRequest.model_validate(raw or {})
    except ValidationError as exc:
        raise BadRequestError(
            message="Invalid cancel payload",
            details={"errors": exc.errors()},
        ) from exc
    except Exception as exc:
        raise BadRequestError(message="Request body must be a JSON object") from exc

    thread = await _load_thread(body.thread_id)
    _require_owner(thread, user_id)
    result = await pq.cancel_all(user_id=user_id, thread=thread)
    _raise_service_error(result)
    return {
        "ok": True,
        "cancelled": bool(result.get("cancelled")),
        "revoked_tokens": result.get("revoked_tokens") or [],
        "queue": result.get("queue"),
        "resume_text": result.get("resume_text"),
        "closed": bool(result.get("closed")),
    }
