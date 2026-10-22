"""Stable, WorkItem-fenced assistant transcript persistence for native chat."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.agentive.services.work_items import authorized_work_item_effect
from app.agentive.work_models import WorkItem
from app.models.edges import CONTAINS
from app.models.nodes import ChatMessage, ChatThread
from app.schemas.agentive.work import WorkError, WorkExecutionContext
from app.services.chat_threads import append_message
from app.services.chat_turn_events import normalize_public_event

_PART_FIELDS = {
    "text": frozenset({"type", "text"}),
    "tool-call": frozenset(
        {"type", "toolCallId", "toolName", "status", "isError", "diagnostic", "summary"}
    ),
    "source": frozenset({"type", "sourceType", "id", "url", "title"}),
    "error": frozenset({"type", "code", "message"}),
}
_MAX_TRANSCRIPT_BYTES = 1_000_000


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _message_id(work_item_id: str) -> str:
    digest = hashlib.sha256(f"{work_item_id}\0assistant-result".encode()).hexdigest()
    return f"n.ChatMessage.{digest}"


async def load_work_item_assistant_result(
    *, context: WorkExecutionContext
) -> ChatMessage | None:
    """Read the deterministic result only when it belongs to this exact turn."""
    item = await WorkItem.get(f"o.WorkItem.{context.work_item_id}")
    thread = await ChatThread.get(context.thread_id)
    if (
        item is None
        or item.kind != "chat_turn"
        or item.work_item_id != context.work_item_id
        or item.principal_id != context.principal_id
        or item.workspace_id != context.workspace_id
        or item.thread_id != context.thread_id
        or thread is None
        or thread.user_id != context.principal_id
        or thread.workspace_id != context.workspace_id
    ):
        raise WorkError("work.policy_denied", "chat transcript scope mismatch")

    message = await ChatMessage.get(_message_id(context.work_item_id))
    if message is None:
        return None
    accepted_message_id = str(
        (item.input_payload or {}).get("accepted_message_id") or ""
    )
    graph = await thread.get_context()
    result_edges = await graph.find_edges_between(
        thread.id, message.id, edge_class=CONTAINS
    )
    if (
        message.id != _message_id(context.work_item_id)
        or message.thread_id != thread.id
        or message.role != "assistant"
        or message.parent_id != accepted_message_id
        or not result_edges
    ):
        raise WorkError("work.idempotency_conflict", "chat result scope mismatch")
    return message


def _public_parts(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only transcript parts explicitly supported by the public chat UI."""
    result: list[dict[str, Any]] = []
    for part in parts:
        if not isinstance(part, dict):
            raise ValueError("assistant transcript parts must be objects")
        kind = part.get("type")
        allowed = _PART_FIELDS.get(kind) if isinstance(kind, str) else None
        if allowed is None or set(part).difference(allowed):
            raise ValueError("assistant transcript contains an unsupported part")
        if kind == "text" and not isinstance(part.get("text"), str):
            raise ValueError("assistant text part must contain text")
        if kind == "tool-call" and any(
            key in part and not isinstance(part[key], str)
            for key in ("toolCallId", "toolName", "status")
        ):
            raise ValueError("assistant tool-call fields must be strings")
        if (
            kind == "tool-call"
            and "isError" in part
            and not isinstance(part["isError"], bool)
        ):
            raise ValueError("assistant tool-call isError must be boolean")
        if kind == "source" and any(
            key in part and part[key] is not None and not isinstance(part[key], str)
            for key in ("sourceType", "id", "url", "title")
        ):
            raise ValueError("assistant source fields must be strings")
        if kind == "error" and any(
            key in part and not isinstance(part[key], str)
            for key in ("code", "message")
        ):
            raise ValueError("assistant transcript contains an unsupported value")
        if kind == "tool-call" and "diagnostic" in part:
            from app.services.chat_tool_diagnostics import normalize_tool_diagnostic

            normalize_tool_diagnostic(part["diagnostic"])
        if kind == "tool-call" and "summary" in part:
            from app.services.chat_tool_summary import normalize_tool_summary

            normalize_tool_summary(part["summary"])
        result.append(dict(part))
    return result


def _public_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Retain product observability while excluding prompts and raw model payloads."""
    allowed_fields = {"steps", "timing", "error"}
    if set(metadata).difference(allowed_fields):
        raise ValueError("assistant observability contains unsupported fields")
    result: dict[str, Any] = {"streaming": True}
    steps = metadata.get("steps")
    if steps is not None:
        if not isinstance(steps, list):
            raise ValueError("assistant observability steps must be a list")
        safe_steps = []
        for step in steps:
            if not isinstance(step, dict) or "type" in step:
                raise ValueError("assistant observability contains unsupported fields")
            safe_steps.append(
                {
                    key: value
                    for key, value in normalize_public_event(
                        {"type": "step", **step}, reject_unknown_fields=True
                    ).items()
                    if key != "type"
                }
            )
        result["steps"] = safe_steps
    timing = metadata.get("timing")
    if timing is not None:
        result["timing"] = normalize_public_event(
            {"type": "message-finish", "timing": timing}, reject_unknown_fields=True
        )["timing"]
    error = metadata.get("error")
    if error is not None:
        if (
            not isinstance(error, dict)
            or set(error).difference({"code", "message"})
            or any(not isinstance(value, str) for value in error.values())
        ):
            raise ValueError("assistant error metadata has an invalid shape")
        result["error"] = dict(error)
    return result


def fold_chat_transcript(
    events: list[dict[str, Any]], *, error: dict[str, str] | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fold normalized provider events into the single durable chat result.

    Raw provider payloads and reasoning are deliberately not accepted here.
    Reuse the event store's projection so new public usage fields survive
    both live replay and a subsequent transcript reload.
    """
    from app.services.chat_turn_events import normalize_public_event

    text = ""
    parts: list[dict[str, Any]] = []
    tool_positions: dict[str, int] = {}
    steps: list[dict[str, Any]] = []
    timing: dict[str, Any] = {}
    for event in events:
        kind = event.get("type")
        if kind == "text-delta":
            text += str(event.get("delta") or "")
        elif kind == "text-replace":
            text = str(event.get("content") or "")
        elif kind == "tool-call":
            call_id = str(event.get("toolCallId") or "")
            if not call_id:
                continue
            summary = {
                "type": "tool-call",
                "toolCallId": call_id,
                "toolName": str(event.get("name") or ""),
                "status": str(event.get("status") or "complete"),
                "isError": event.get("status") == "error",
            }
            if event.get("diagnostic") is not None:
                summary["diagnostic"] = normalize_public_event(event)["diagnostic"]
            if event.get("summary") is not None:
                summary["summary"] = normalize_public_event(event)["summary"]
            # Start/result events describe one call, not two transcript parts.
            # Keep the first position and update it with the final receipt state.
            if call_id in tool_positions:
                parts[tool_positions[call_id]] = summary
            else:
                tool_positions[call_id] = len(parts)
                parts.append(summary)
        elif kind == "source":
            parts.append(
                {
                    "type": "source",
                    "sourceType": "url",
                    "url": event.get("url"),
                    "title": event.get("title"),
                }
            )
        elif kind == "step":
            steps.append(
                {
                    key: value
                    for key, value in normalize_public_event(event).items()
                    if key not in {"type", "name", "status"}
                }
            )
        elif kind == "message-finish" and isinstance(event.get("timing"), dict):
            timing = dict(normalize_public_event(event)["timing"])
        elif kind == "error":
            error = {
                "code": str(event.get("code") or "internal_error"),
                "message": str(event.get("message") or "The assistant turn failed."),
            }
    if text:
        parts.insert(0, {"type": "text", "text": text})
    if error:
        parts.append({"type": "error", **error})
    if not parts:
        parts.append({"type": "text", "text": ""})
    metadata: dict[str, Any] = {"steps": steps}
    if timing:
        metadata["timing"] = timing
    if error:
        metadata["error"] = error
    return parts, metadata


async def persist_work_item_assistant_result(
    *,
    context: WorkExecutionContext,
    parts: list[dict[str, Any]],
    provider_metadata: dict[str, Any],
) -> ChatMessage:
    """Persist one deterministic assistant message under the active WorkItem fence.

    The accepted user message is resolved from the WorkItem itself. Retrying a
    safe transcript write returns the same message; divergent output under the
    same WorkItem is rejected instead of creating a second assistant answer.
    """
    safe_parts = _public_parts(parts)
    safe_metadata = _public_metadata(provider_metadata)
    if not safe_parts:
        raise ValueError("assistant result must contain a public transcript part")
    if (
        len(_canonical_json([safe_parts, safe_metadata]).encode("utf-8"))
        > _MAX_TRANSCRIPT_BYTES
    ):
        raise ValueError("assistant transcript exceeds its size limit")

    async with authorized_work_item_effect(context):
        return await _persist_assistant_result(
            context=context, safe_parts=safe_parts, safe_metadata=safe_metadata
        )


async def _persist_assistant_result(
    *,
    context: WorkExecutionContext,
    safe_parts: list[dict[str, Any]],
    safe_metadata: dict[str, Any],
) -> ChatMessage:
    """Internal write: caller must hold an effect or terminal control transaction."""
    item = await WorkItem.get(f"o.WorkItem.{context.work_item_id}")
    accepted_message_id = (
        str((item.input_payload or {}).get("accepted_message_id") or "")
        if item is not None
        else ""
    )
    thread = await ChatThread.get(context.thread_id)
    if (
        item is None
        or item.kind != "chat_turn"
        or item.principal_id != context.principal_id
        or item.workspace_id != context.workspace_id
        or item.thread_id != context.thread_id
        or thread is None
        or thread.user_id != context.principal_id
        or thread.workspace_id != context.workspace_id
        or not accepted_message_id
    ):
        raise WorkError("work.policy_denied", "chat transcript scope mismatch")

    accepted = await ChatMessage.get(accepted_message_id)
    graph = await thread.get_context()
    accepted_edges = await graph.find_edges_between(
        thread.id, accepted_message_id, edge_class=CONTAINS
    )
    accepted_role = (item.input_payload or {}).get("message_role", "user")
    if (
        accepted_role not in {"user", "system"}
        or accepted is None
        or accepted.thread_id != thread.id
        or accepted.role != accepted_role
        or not accepted_edges
    ):
        raise WorkError("work.policy_denied", "accepted chat message is unavailable")

    stable_id = _message_id(context.work_item_id)
    existing = await ChatMessage.get(stable_id)
    expected = {
        "thread_id": thread.id,
        "role": "assistant",
        "parts": safe_parts,
        "parent_id": accepted_message_id,
        "provider_metadata": safe_metadata,
    }
    if existing is not None:
        existing_edges = await graph.find_edges_between(
            thread.id, stable_id, edge_class=CONTAINS
        )
        actual = {key: getattr(existing, key) for key in expected}
        if not existing_edges or _canonical_json(actual) != _canonical_json(expected):
            raise WorkError(
                "work.idempotency_conflict",
                "assistant transcript identity is bound to different content",
            )
        return existing

    return await append_message(
        thread=thread,
        role="assistant",
        parts=safe_parts,
        parent_id=accepted_message_id,
        provider_metadata=safe_metadata,
        message_id=stable_id,
    )


async def persist_cancelled_chat_result(
    *, context: WorkExecutionContext
) -> ChatMessage:
    """Finalize only committed public output inside the cancellation transaction.

    No caller-supplied content or provider execution is accepted here. The caller
    must first win the leased terminal transition in this same transaction.
    """
    from app.services.app_operations.transaction_scope import (
        current_graph_transaction_database,
    )
    from app.services.chat_turn_events import replay_work_item_chat_events

    current_graph_transaction_database(required=True)
    item = await WorkItem.get(f"o.WorkItem.{context.work_item_id}")
    if item is None or item.status != "cancelled":
        raise WorkError(
            "work.policy_denied", "cancelled chat result requires terminal authority"
        )
    existing = await load_work_item_assistant_result(context=context)
    if existing is not None:
        # Completion may have saved its immutable result immediately before Stop.
        return existing
    events: list[dict[str, Any]] = []
    cursor = 0
    while True:
        page = await replay_work_item_chat_events(
            principal_id=context.principal_id,
            workspace_id=context.workspace_id,
            thread_id=context.thread_id,
            work_item_id=context.work_item_id,
            after_sequence=cursor,
            limit=500,
        )
        if page.gap or (page.has_more and page.next_after_sequence <= cursor):
            raise WorkError(
                "work.event_sequence_gap", "committed chat event cursor did not advance"
            )
        events.extend(page.events)
        if not page.has_more:
            break
        cursor = page.next_after_sequence
    parts, metadata = fold_chat_transcript(
        events, error={"code": "work.cancelled", "message": "Turn stopped."}
    )
    return await _persist_assistant_result(
        context=context,
        safe_parts=_public_parts(parts),
        safe_metadata=_public_metadata(metadata),
    )


__all__ = [
    "load_work_item_assistant_result",
    "persist_work_item_assistant_result",
]
