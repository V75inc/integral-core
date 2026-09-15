"""Assistant-turn drafting splits on ``message-boundary``.

The orchestrator can publish more than one user-facing message in a single
turn (e.g. a first-time intro greeting that is a distinct adhoc message from
the actual answer). The translator emits a ``message-boundary`` event between
them; the persistence layer must mirror the browser by producing ONE persisted
ChatMessage per bubble — not one merged blob — so a reload looks like the live
stream.

Covers the ``_drain_turn`` helper that converts a normalized event stream into
an ordered list of per-bubble parts (each a ``_AssistantDraft.to_parts()``).
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.api.ai_chat import _AssistantDraft, drafts_from_events


def _texts(parts: List[Dict[str, Any]]) -> str:
    return "".join(p["text"] for p in parts if p.get("type") == "text")


def test_single_message_one_draft() -> None:
    events = [
        {"type": "text-delta", "delta": "Hello "},
        {"type": "text-delta", "delta": "world."},
        {"type": "message-finish", "timing": {"totalMs": 1.0}},
    ]
    drafts = drafts_from_events(events)
    assert len(drafts) == 1
    assert _texts(drafts[0].to_parts()) == "Hello world."


def test_boundary_splits_into_two_drafts() -> None:
    """Intro greeting then the answer → two separate persisted bubbles."""
    events = [
        {"type": "text-delta", "delta": "Hello! I'm Integral's assistant."},
        {"type": "message-boundary"},
        {"type": "text-delta", "delta": "Your highest grossing project is X."},
        {"type": "message-finish", "timing": {"totalMs": 2.0}},
    ]
    drafts = drafts_from_events(events)
    assert len(drafts) == 2
    assert _texts(drafts[0].to_parts()) == "Hello! I'm Integral's assistant."
    assert _texts(drafts[1].to_parts()) == "Your highest grossing project is X."


def test_reasoning_and_tools_attach_to_their_bubble() -> None:
    """Reasoning + tool-call land on the SAME bubble as the answer they
    precede; a prior boundary's bubble keeps only its own text."""
    events = [
        {"type": "text-delta", "delta": "Greeting."},
        {"type": "message-boundary"},
        {"type": "reasoning-delta", "delta": "thinking", "segmentId": "r1"},
        {
            "type": "tool-call",
            "toolCallId": "tc1",
            "name": "search",
            "status": "complete",
            "result": "{}",
        },
        {"type": "text-delta", "delta": "The answer."},
        {"type": "message-finish", "timing": {"totalMs": 3.0}},
    ]
    drafts = drafts_from_events(events)
    assert len(drafts) == 2
    # Bubble 0: greeting only — no reasoning/tool parts.
    g_parts = drafts[0].to_parts()
    assert _texts(g_parts) == "Greeting."
    assert not any(p["type"] in ("reasoning", "tool-call") for p in g_parts)
    # Bubble 1: reasoning + tool-call + answer text.
    a_parts = drafts[1].to_parts()
    kinds = [p["type"] for p in a_parts]
    assert "reasoning" in kinds and "tool-call" in kinds
    assert _texts(a_parts) == "The answer."


def test_empty_leading_bubble_is_dropped() -> None:
    """A boundary that arrives before any content does not create an empty
    bubble (defensive — translator shouldn't emit it, but persistence must
    not write a blank message if it does)."""
    events = [
        {"type": "message-boundary"},
        {"type": "text-delta", "delta": "Only one real message."},
        {"type": "message-finish", "timing": {}},
    ]
    drafts = [d for d in drafts_from_events(events) if d.to_parts()]
    assert len(drafts) == 1
    assert _texts(drafts[0].to_parts()) == "Only one real message."


def test_timing_and_steps_land_on_last_bubble() -> None:
    """``step`` + ``message-finish`` are turn-level; they attach to the final
    bubble (where the UI shows the run stats)."""
    events = [
        {"type": "text-delta", "delta": "Intro."},
        {"type": "message-boundary"},
        {"type": "text-delta", "delta": "Answer."},
        {"type": "step", "usage": {"inputTokens": 5, "outputTokens": 2}},
        {"type": "message-finish", "timing": {"totalMs": 9.0}},
    ]
    drafts = drafts_from_events(events)
    assert len(drafts) == 2
    assert drafts[0].steps == []
    assert drafts[0].timing is None
    assert drafts[1].steps and drafts[1].steps[0]["usage"]["outputTokens"] == 2
    assert drafts[1].timing == {"totalMs": 9.0}


def test_trailing_boundary_folds_observability_onto_answer() -> None:
    """Turn-level events after a trailing boundary must not die on an empty
    draft — that blanked the response meta bar on reload."""
    events = [
        {"type": "text-delta", "delta": "Answer."},
        {"type": "message-boundary"},
        {"type": "step", "usage": {"inputTokens": 1, "outputTokens": 2}},
        {
            "type": "final-content",
            "content": "Answer.",
            "payload": {"interaction": {"usage": {"total_tokens": 3}}},
        },
        {"type": "message-finish", "timing": {"totalMs": 12.0}},
    ]
    drafts = drafts_from_events(events)
    contentful = [d for d in drafts if d.to_parts()]
    assert len(contentful) == 1
    assert contentful[0].timing == {"totalMs": 12.0}
    assert contentful[0].steps and contentful[0].steps[0]["usage"]["outputTokens"] == 2
    assert contentful[0].final_payload is not None
    assert contentful[0].final_content == "Answer."
