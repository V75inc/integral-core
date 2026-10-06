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

_PART_FIELDS = {
    "text": frozenset({"type", "text"}),
    "tool-call": frozenset({"type", "toolCallId", "toolName", "status", "isError"}),
    "source": frozenset({"type", "sourceType", "id", "url", "title"}),
    "error": frozenset({"type", "code", "message"}),
}
_STEP_FIELDS = frozenset(
    {
        "usage",
        "modelId",
        "provider",
        "providerCostUsd",
        "costSource",
        "durationMs",
        "outcome",
        "attempt",
        "requestId",
    }
)
_USAGE_FIELDS = frozenset({"inputTokens", "outputTokens"})
_TIMING_FIELDS = frozenset({"totalMs", "firstTokenMs"})
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
            if not isinstance(step, dict) or set(step).difference(_STEP_FIELDS):
                raise ValueError("assistant observability contains unsupported fields")
            safe = dict(step)
            string_fields = {
                "modelId",
                "provider",
                "costSource",
                "outcome",
                "requestId",
            }
            if any(
                key in safe and not isinstance(safe[key], str) for key in string_fields
            ):
                raise ValueError("assistant observability text fields must be strings")
            usage = safe.get("usage")
            if usage is not None:
                if not isinstance(usage, dict) or set(usage).difference(_USAGE_FIELDS):
                    raise ValueError("assistant usage has unsupported fields")
                if any(
                    not isinstance(value, int) or isinstance(value, bool) or value < 0
                    for value in usage.values()
                ):
                    raise ValueError("assistant token usage must be non-negative")
            for key in ("providerCostUsd", "durationMs"):
                if key in safe and (
                    not isinstance(safe[key], (int, float))
                    or isinstance(safe[key], bool)
                    or safe[key] < 0
                ):
                    raise ValueError(f"assistant {key} must be non-negative")
            if "attempt" in safe and (
                not isinstance(safe["attempt"], int)
                or isinstance(safe["attempt"], bool)
                or safe["attempt"] < 1
            ):
                raise ValueError("assistant attempt must be positive")
            safe_steps.append(safe)
        result["steps"] = safe_steps
    timing = metadata.get("timing")
    if timing is not None:
        if not isinstance(timing, dict) or set(timing).difference(_TIMING_FIELDS):
            raise ValueError("assistant timing has unsupported fields")
        if any(
            not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0
            for value in timing.values()
        ):
            raise ValueError("assistant timing must be non-negative")
        result["timing"] = dict(timing)
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
        if (
            accepted is None
            or accepted.thread_id != thread.id
            or accepted.role != "user"
            or not accepted_edges
        ):
            raise WorkError(
                "work.policy_denied", "accepted chat message is unavailable"
            )

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
            if not existing_edges or _canonical_json(actual) != _canonical_json(
                expected
            ):
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


__all__ = [
    "load_work_item_assistant_result",
    "persist_work_item_assistant_result",
]
