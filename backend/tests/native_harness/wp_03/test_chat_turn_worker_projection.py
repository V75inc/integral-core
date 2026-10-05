"""Tests for durable native chat event-to-transcript projection."""

from app.agentive.services.work_worker import _native_chat_transcript


def test_native_chat_projection_folds_and_redacts_events() -> None:
    """Keep private provider events out of the durable assistant result."""
    parts, metadata = _native_chat_transcript(
        [
            {"type": "text-delta", "delta": "A "},
            {"type": "reasoning-delta", "delta": "private"},
            {"type": "text-delta", "delta": "draft"},
            {"type": "text-replace", "content": "Final answer"},
            {
                "type": "step",
                "modelId": "gpt-control",
                "provider": "openai",
                "usage": {"inputTokens": 12, "outputTokens": 5},
                "requestId": "req-1",
                "attempt": 1,
            },
            {
                "type": "message-finish",
                "timing": {"totalMs": 125.0, "firstTokenMs": 30.0},
            },
            {"type": "unknown-private-event", "prompt": "must not persist"},
        ]
    )

    assert parts == [{"type": "text", "text": "Final answer"}]
    assert metadata == {
        "steps": [
            {
                "modelId": "gpt-control",
                "provider": "openai",
                "usage": {"inputTokens": 12, "outputTokens": 5},
                "requestId": "req-1",
                "attempt": 1,
            }
        ],
        "timing": {"totalMs": 125.0, "firstTokenMs": 30.0},
    }
    assert "private" not in repr(parts)
    assert "must not persist" not in repr(metadata)


def test_native_chat_projection_preserves_safe_parts() -> None:
    """Retain safe tool/source summaries and the final failure information."""
    parts, metadata = _native_chat_transcript(
        [
            {
                "type": "tool-call",
                "toolCallId": "call-1",
                "name": "list_tracks",
                "status": "error",
                "arguments": {"secret": "not persisted"},
            },
            {
                "type": "source",
                "title": "Manual",
                "url": "https://example.test",
            },
        ],
        error={"code": "provider_error", "message": "The provider failed."},
    )

    assert parts == [
        {
            "type": "tool-call",
            "toolCallId": "call-1",
            "toolName": "list_tracks",
            "status": "error",
            "isError": True,
        },
        {
            "type": "source",
            "sourceType": "url",
            "url": "https://example.test",
            "title": "Manual",
        },
        {
            "type": "error",
            "code": "provider_error",
            "message": "The provider failed.",
        },
    ]
    assert metadata == {
        "steps": [],
        "error": {"code": "provider_error", "message": "The provider failed."},
    }
    assert "not persisted" not in repr(parts)
