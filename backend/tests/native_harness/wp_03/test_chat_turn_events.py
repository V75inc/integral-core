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


def test_usage_evidence_survives_replay_and_saved_transcript() -> None:
    from app.agentive.services.work_worker import _native_chat_transcript
    from app.services.chat_turn_transcript import _public_metadata

    event = {
        "type": "step",
        "usage": {"inputTokens": 0},
        "usageComplete": False,
        "inputTokensReported": True,
        "outputTokensReported": False,
        "costComplete": False,
        "requestContext": {
            "instruction_chars": 12,
            "conversation_chars": 30,
            "tool_result_chars": 0,
            "tool_schema_chars": 0,
            "message_count": 1,
            "visible_tool_count": 0,
            "tool_schema_fingerprint": None,
        },
    }
    assert normalize_public_event(event) == event
    _, metadata = _native_chat_transcript([event])
    assert metadata["steps"] == [{k: v for k, v in event.items() if k != "type"}]
    assert _public_metadata(metadata)["steps"] == metadata["steps"]


@pytest.mark.parametrize(
    "metadata",
    [
        {"steps": [{"raw": {"prompt": "must not persist"}}]},
        {"steps": [{"usage": {"inputTokens": 1, "prompt": "private"}}]},
        {"steps": [{"type": "text-delta", "delta": "private"}]},
        {"steps": [{"requestContext": {"prompt": "private"}}]},
        {"steps": [{"usageComplete": 1}]},
        {"timing": {"prompt": "private"}},
        {"timing": {"modelPassMs": float("nan")}},
    ],
)
def test_saved_transcript_keeps_the_strict_public_boundary(metadata: dict) -> None:
    from app.services.chat_turn_transcript import _public_metadata

    with pytest.raises(ValueError):
        _public_metadata(metadata)


@pytest.mark.parametrize(
    "event",
    [
        {"type": "reasoning-delta", "delta": "private reasoning"},
        {"type": "step", "usage": {"inputTokens": 1, "secret": "hidden"}},
        {"type": "message-finish", "timing": {"prompt": "hidden"}},
        {"type": "message-finish", "timing": {"modelPassMs": -1}},
        {"type": "message-finish", "contextMode": "arbitrary"},
        {"type": "message-finish", "contextMode": []},
        {"type": "step", "usageComplete": 1},
        {"type": "step", "inputTokensReported": "true"},
        {"type": "step", "outputTokensReported": 0},
        {"type": "step", "costComplete": "false"},
        {"type": "step", "requestContext": {"prompt": "must not replay"}},
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
