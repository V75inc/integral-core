"""Durable chat events expose only approved public chat metadata."""

from __future__ import annotations

import pytest

from app.services.chat_turn_events import normalize_public_event


def test_text_replacement_preserves_authoritative_public_answer() -> None:
    """Allow the settled provider result to replace mismatched deltas."""
    assert normalize_public_event(
        {
            "type": "text-replace",
            "content": "Complete answer.",
            "raw": "secret",
        }
    ) == {"type": "text-replace", "content": "Complete answer."}


def test_tool_call_strips_arguments_and_result() -> None:
    """Persist tool state without arguments or result payloads."""
    assert normalize_public_event(
        {
            "type": "tool-call",
            "toolCallId": "call-1",
            "name": "integral.search",
            "status": "complete",
            "args": {"credential": "must-not-replay"},
            "result": "private tool result",
        }
    ) == {
        "type": "tool-call",
        "toolCallId": "call-1",
        "name": "integral.search",
        "status": "complete",
    }


def test_step_and_finish_keep_source_backed_usage_and_timing() -> None:
    """Retain provider-attributed usage and safe timing scalars."""
    assert normalize_public_event(
        {
            "type": "step",
            "modelId": "ollama_chat/gemma4:26b",
            "provider": "ollama_chat",
            "requestId": "request-1",
            "attempt": 1,
            "outcome": "succeeded",
            "costSource": "provider",
            "usage": {"inputTokens": 120, "outputTokens": 30},
            "providerCostUsd": 0.0,
            "durationMs": 1234.5,
            "private": "must-not-replay",
        }
    ) == {
        "type": "step",
        "modelId": "ollama_chat/gemma4:26b",
        "provider": "ollama_chat",
        "requestId": "request-1",
        "attempt": 1,
        "outcome": "succeeded",
        "costSource": "provider",
        "usage": {"inputTokens": 120, "outputTokens": 30},
        "providerCostUsd": 0.0,
        "durationMs": 1234.5,
    }
    assert normalize_public_event(
        {
            "type": "message-finish",
            "timing": {"totalMs": 1450, "firstTokenMs": 220},
        }
    ) == {
        "type": "message-finish",
        "timing": {"totalMs": 1450, "firstTokenMs": 220},
    }


@pytest.mark.parametrize(
    "event",
    [
        {"type": "reasoning-delta", "delta": "private reasoning"},
        {"type": "step", "usage": {"inputTokens": 1, "secret": "hidden"}},
        {"type": "message-finish", "timing": {"prompt": "hidden"}},
        {"type": "step", "usage": {"outputTokens": 1.5}},
        {"type": "step", "attempt": 0},
        {"type": "step", "providerCostUsd": -0.01},
        {"type": "step", "durationMs": -1},
        {"type": "text-delta", "delta": ["not", "text"]},
        {"type": "step", "durationMs": float("nan")},
        {"type": "untrusted-provider-event", "raw": "private"},
    ],
)
def test_rejects_private_or_malformed_public_event(event: dict) -> None:
    """Reject reasoning and malformed data in otherwise public event types."""
    with pytest.raises(ValueError):
        normalize_public_event(event)
