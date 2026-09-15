"""The loop-guard bail must not paste raw tool JSON into the conversation.

Seen live: a correction turn tripped the orchestrator's repeat guard and the
reply began "I made progress on this but hit a loop..." followed by
``{"total": 8, "next_cursor": null, "tracks": [{"id": "n.Track...``. jvagent
salvages the turn by pasting raw tool observations, which is fine for prose
observations and wrong for Integral's, which are serialized JSON.
"""

from __future__ import annotations

from app.services.chat_providers.loop_salvage import sanitize_loop_salvage

_DUMP = (
    "I made progress on this but hit a loop before finishing every step. "
    'Here is what I gathered so far:\n\n{"total": 8, "next_cursor": null, '
    '"tracks": [{"id": "n.Track.abc", "title": "Attention Log"}]}\n\n'
    "Ask me to continue and I will finish from here."
)


def test_salvage_dump_is_replaced_in_a_streamed_message():
    out = sanitize_loop_salvage({"type": "message", "text": _DUMP})
    assert "n.Track.abc" not in out["text"]
    assert "next_cursor" not in out["text"]
    assert "ask me to continue" in out["text"].lower()


def test_salvage_dump_is_replaced_wherever_it_is_nested():
    """The preamble appears in streamed chunks and again in the final envelope."""
    ev = {
        "type": "final",
        "response": {"messages": [{"role": "assistant", "content": _DUMP}]},
        "history": [_DUMP],
    }
    out = sanitize_loop_salvage(ev)
    assert "n.Track.abc" not in str(out)
    assert out["response"]["messages"][0]["content"].startswith("I started on this")
    assert out["history"][0].startswith("I started on this")


def test_ordinary_events_are_untouched():
    """A turn that never trips the guard must pass through byte-identical."""
    ev = {
        "type": "final",
        "text": "You're the VP of Engineering at Northwind Labs.",
        "tools": ["integral_query_entries"],
        "count": 3,
        "ok": True,
        "nothing": None,
    }
    assert sanitize_loop_salvage(ev) == ev


def test_json_that_is_not_salvage_survives():
    """Only the salvage preamble triggers a rewrite -- not JSON generally."""
    ev = {"type": "tool_result", "text": '{"total": 8, "tracks": []}'}
    assert sanitize_loop_salvage(ev) == ev
