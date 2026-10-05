"""Translate Pydantic AI stream events into Integral's chat envelope."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterator

from app.agentive.harness.pydantic_ai_compat import (
    AgentRunResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    TextPartDelta,
    ThinkingPart,
)

_PRIVATE_FIELDS = frozenset(
    {
        "analysis",
        "thought",
        "reasoning",
        "reasoning_content",
        "internal_reasoning",
        "scratchpad",
    }
)
_PRIVATE_PREFIX = re.compile(
    r'^\s*(?:\{\s*)?(?:")?(?:analysis|thought|reasoning|reasoning_content|'
    r'internal_reasoning|scratchpad)(?:")?\s*:',
    re.IGNORECASE,
)
_TOOL_PREFIX = re.compile(
    r'^\s*(?:\{\s*)?(?:")?(?:tool_calls|tool_call|function_call|functionCall|'
    r'function_arguments|type)(?:")?\s*:',
    re.IGNORECASE,
)
_FLATTENED_PREFIXES = (
    '"analysis',
    '"thought',
    '"reasoning',
    '"reasoning_content',
    '"internal_reasoning',
    '"scratchpad',
    '"role',
    '"function_call',
    '"tool_calls',
    '"functioncall',
    '"type',
    'analysis"',
    'thought"',
    'reasoning"',
    'reasoning_content"',
    'type"',
)
logger = logging.getLogger(__name__)


def _candidate_json(text: str) -> dict[str, Any] | None:
    """Decode an object, repairing only known flattened object text."""
    candidate = text.strip()
    if not candidate.startswith("{"):
        candidate = '{"' + candidate
    try:
        decoded = json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _contains_serialized_tool_call(value: Any) -> bool:
    """Recognize common serialized tool protocols; never dispatch text as a tool."""
    if isinstance(value, list):
        return any(_contains_serialized_tool_call(item) for item in value)
    if not isinstance(value, dict):
        return False
    if value.get("type") in {"function", "tool_call", "tool_use"} and (
        isinstance(value.get("function"), (str, dict))
        or isinstance(value.get("name"), str)
    ):
        return True
    for key in ("tool_calls", "function_call", "functionCall", "tool_call"):
        nested = value.get(key)
        if isinstance(nested, dict) and (
            isinstance(nested.get("name"), str)
            or isinstance(nested.get("function"), (str, dict))
        ):
            return True
        if isinstance(nested, list) and _contains_serialized_tool_call(nested):
            return True
    function = value.get("function")
    if isinstance(function, dict) and isinstance(function.get("name"), str):
        return True
    return isinstance(function, str) and isinstance(
        value.get("function_arguments"), (dict, str)
    )


def _classify_text_object(text: str) -> tuple[str, str | None, str | None]:
    """Return (kind, safe answer, reason) for a complete JSON-looking part."""
    decoded = _candidate_json(text)
    if decoded is None:
        if _PRIVATE_PREFIX.match(text):
            return "unsafe", None, "invalid_private_envelope"
        if _TOOL_PREFIX.match(text):
            return "tool", None, None
        return "ordinary", None, None
    if _contains_serialized_tool_call(decoded):
        return "tool", None, None
    if not _PRIVATE_FIELDS.intersection(decoded):
        return "ordinary", None, None
    if decoded.get("role") not in (None, "assistant"):
        return "unsafe", None, "unexpected_role"
    content = next(
        (
            decoded.get(key)
            for key in ("content", "final", "response", "answer", "output", "text")
            if isinstance(decoded.get(key), str)
        ),
        None,
    )
    if not isinstance(content, str) or not content.strip():
        return "unsafe", None, "missing_final_text"
    return "private", content, None


class SettledTextBuffer:
    """Hold model text until the enclosing Pydantic AI run succeeds."""

    def __init__(self) -> None:
        self._deltas: list[str] = []
        self._replacement: str | None = None

    def capture(self, event: dict[str, Any]) -> bool:
        """Capture a text event and report whether the caller should defer it."""
        if event.get("type") == "text-delta":
            self._deltas.append(str(event.get("delta", "")))
            return True
        if event.get("type") == "text-replace":
            self._replacement = str(event.get("content", ""))
            self._deltas.clear()
            return True
        return False

    def settled_event(self) -> dict[str, str] | None:
        """Return exactly one authoritative answer event after successful run."""
        if self._replacement is not None:
            return {"type": "text-delta", "delta": self._replacement}
        if self._deltas:
            return {"type": "text-delta", "delta": "".join(self._deltas)}
        return None


class PydanticAIEventTranslator:
    """Translate stream events while guarding text-shaped private envelopes.

    Normal prose streams immediately. JSON-looking parts are buffered until
    part-end so user JSON can be preserved while private envelopes and textual
    tool protocols are safely classified above provider-specific transports.
    """

    def __init__(self) -> None:
        self._text: dict[int, str] = {}
        self._suspected: set[int] = set()
        self._streamed: set[int] = set()
        self._pending_envelope: str | None = None
        self._emitted_text = False
        self._streamed_public_text = ""

    def _text_event(self, content: str) -> dict[str, str]:
        self._emitted_text = self._emitted_text or bool(content)
        self._streamed_public_text += content
        return {"type": "text-delta", "delta": content}

    def _settled_public_text(self, raw_output: str) -> tuple[str | None, str | None]:
        """Return (safe public answer, failure code) for the authoritative result."""
        if (
            self._looks_like_json_start(raw_output)
            or _PRIVATE_PREFIX.match(raw_output)
            or self._could_be_envelope_prefix(raw_output)
        ):
            kind, answer, failure = _classify_text_object(raw_output)
            if kind == "private" and answer:
                return answer, None
            if kind == "tool":
                return None, "undispatched_tool_envelope"
            if kind == "unsafe":
                logger.warning(
                    "Rejected unsafe final output: reason=%s chars=%d",
                    len(raw_output),
                )
                return None, "unsafe_model_output"
            return raw_output, None
        return raw_output, None

    @staticmethod
    def _could_be_envelope_prefix(text: str) -> bool:
        normalized = text.lstrip().lower()
        if not normalized:
            return False
        return any(
            prefix.startswith(normalized) or normalized.startswith(prefix)
            for prefix in _FLATTENED_PREFIXES
        )

    @staticmethod
    def _looks_like_json_start(text: str) -> bool:
        return text.lstrip().startswith(("{", "["))

    def translate(self, event: Any) -> Iterator[dict[str, Any]]:
        """Yield safe Integral events for one Pydantic AI event."""
        if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
            delta = event.part.content
            index = event.index
        elif isinstance(event, PartDeltaEvent) and isinstance(
            event.delta, TextPartDelta
        ):
            delta = event.delta.content_delta
            index = event.index
        else:
            delta = None
            index = None

        if delta and index is not None:
            if not delta:
                return
            accumulated = self._text.get(index, "") + delta
            self._text[index] = accumulated
            if self._pending_envelope is not None:
                self._suspected.add(index)
                return
            if index in self._suspected:
                return
            if (
                self._looks_like_json_start(accumulated)
                or _PRIVATE_PREFIX.match(accumulated)
                or self._could_be_envelope_prefix(accumulated)
            ):
                self._suspected.add(index)
                return
            self._streamed.add(index)
            yield self._text_event(accumulated)
            self._text[index] = ""
            return

        if isinstance(event, PartEndEvent):
            index = event.index
            if isinstance(event.part, ThinkingPart):
                # ThinkingPartDelta is ignored by translate_event, but its
                # PartEndEvent still carries the complete private content.
                # Never use the generic content fallback for model reasoning.
                self._text.pop(index, None)
                self._suspected.discard(index)
                self._streamed.discard(index)
                return
            if index in self._suspected:
                candidate = str(getattr(event.part, "content", "") or "")
                if event.next_part_kind == "text":
                    self._pending_envelope = candidate
                    self._text.pop(index, None)
                    self._suspected.discard(index)
                    self._streamed.discard(index)
                    return
                if self._pending_envelope is not None:
                    candidate = self._pending_envelope + candidate
                    self._pending_envelope = None
                kind, answer, failure = _classify_text_object(candidate)
                if kind == "unsafe":
                    logger.warning(
                        "Rejected unsafe serialized assistant output: reason=%s chars=%d",
                        failure,
                        len(candidate),
                    )
                    yield {
                        "type": "error",
                        "code": "unsafe_model_output",
                        "message": "The assistant response could not be safely normalized. Please retry.",
                    }
                elif kind == "tool":
                    yield {
                        "type": "error",
                        "code": "undispatched_tool_envelope",
                        "message": "The assistant produced an invalid tool-call response. Please retry.",
                    }
                elif kind == "private" and answer:
                    yield self._text_event(answer)
                elif kind == "ordinary" and candidate:
                    yield self._text_event(candidate)
            elif index not in self._streamed and isinstance(event.part, TextPart):
                # Short text and incomplete prefix candidates are released only
                # once the complete text part is known.
                content = str(getattr(event.part, "content", "") or "")
                if content:
                    yield self._text_event(content)
            self._text.pop(index, None)
            self._suspected.discard(index)
            self._streamed.discard(index)
            return

        if isinstance(event, AgentRunResultEvent):
            # Adapters can stream a text value that differs from the settled
            # public result. Reconcile at completion so chat cannot settle on
            # a truncated answer. The authoritative value passes through the
            # same private/tool-envelope guards as streamed text.
            raw_output = getattr(event.result, "output", None)
            if isinstance(raw_output, str) and raw_output:
                safe_output, failure = self._settled_public_text(raw_output)
                if failure == "undispatched_tool_envelope":
                    yield {
                        "type": "error",
                        "code": failure,
                        "message": "The assistant produced an invalid tool-call response. Please retry.",
                    }
                elif failure == "unsafe_model_output":
                    yield {
                        "type": "error",
                        "code": failure,
                        "message": "The assistant response could not be safely normalized. Please retry.",
                    }
                elif safe_output and safe_output != self._streamed_public_text:
                    if self._emitted_text:
                        yield {"type": "text-replace", "content": safe_output}
                        self._streamed_public_text = safe_output
                    else:
                        yield self._text_event(safe_output)
        yield from translate_event(event)


def translate_event(event: Any) -> Iterator[dict[str, Any]]:
    """Yield zero or more provider-neutral events for one Pydantic AI event.

    Tool arguments and results stay in the normal chat transcript envelope so
    the existing persistence and UI code remains authoritative. The translator
    intentionally omits raw model messages, provider metadata, and exceptions.
    """
    if isinstance(event, PartDeltaEvent):
        if isinstance(event.delta, TextPartDelta) and event.delta.content_delta:
            yield {"type": "text-delta", "delta": event.delta.content_delta}
        # Never put private model reasoning in Integral's user transcript or
        # persisted chat history. Operational visibility comes from run,
        # request, usage, and tool events instead.
        return

    if isinstance(event, FunctionToolCallEvent):
        part = event.part
        tool_call_id = getattr(part, "tool_call_id", None)
        tool_name = getattr(part, "tool_name", None)
        if not isinstance(tool_call_id, str) or not tool_call_id:
            return
        if not isinstance(tool_name, str) or not tool_name:
            return
        try:
            arguments = part.args_as_dict() if event.args_valid else {}
        except Exception:  # noqa: BLE001 - invalid provider args must not break SSE
            arguments = {}
        yield {
            "type": "tool-call",
            "toolCallId": tool_call_id,
            "name": tool_name,
            "args": arguments,
            "status": "running",
        }
        return

    if isinstance(event, FunctionToolResultEvent):
        part = event.part
        name = getattr(part, "tool_name", None)
        call_id = getattr(part, "tool_call_id", None)
        if isinstance(name, str) and name and isinstance(call_id, str) and call_id:
            yield {
                "type": "tool-call",
                "toolCallId": call_id,
                "name": name,
                # Pydantic AI v2 carries the serialized tool output on the
                # return part. FunctionToolResultEvent itself has no content
                # attribute (getattr returned None and hid real results in
                # Integral's trace UI).
                "result": getattr(part, "content", None),
                "status": "error" if isinstance(part, RetryPromptPart) else "complete",
            }
        return

    if isinstance(event, AgentRunResultEvent):
        usage = event.result.usage
        usage_data: dict[str, int] = {}
        if isinstance(getattr(usage, "input_tokens", None), int):
            usage_data["inputTokens"] = usage.input_tokens
        if isinstance(getattr(usage, "output_tokens", None), int):
            usage_data["outputTokens"] = usage.output_tokens
        yield {
            "type": "step",
            "usage": usage_data,
        }
