"""The turn trace must make a stalled turn diagnosable from one log line.

Three investigations in one session guessed why a turn stopped and were wrong,
because the deciding facts — which protocol it spoke and why, which guard
fired, how the loop ended — were never in the log. jvagent records all of it on
the final envelope; these tests pin that we read it and surface it.
"""

from __future__ import annotations

import logging

import pytest

from app.services.agent_trace import (
    extract_turn_trace,
    format_turn_trace,
    is_turn_end,
    log_turn_trace,
)


def _final(**activation):
    data = {
        "tick_count": 10,
        "budget": 30,
        "ticks_light": 1,
        "ticks_heavy": 9,
        "ended_via": "repeat_guard",
        "guards": ["repeat", "repeat"],
        "tools_invoked": [],
        "skills_used": [],
        "tool_protocol": "native",
        "protocol_reason": "auto:native",
        "loop_duration_ms": 372,
    }
    data.update(activation)
    return {
        "type": "final",
        "interaction": {
            "id": "n.Interaction.1",
            "observability_metrics": [
                {"event_type": "model_call", "data": {}},
                {"event_type": "model_call", "data": {}},
                {"event_type": "orchestrator_activation", "data": data},
            ],
        },
    }


def test_extracts_the_activation_and_counts_model_calls():
    trace = extract_turn_trace(_final())
    assert trace is not None
    assert trace["ended_via"] == "repeat_guard"
    assert trace["guards"] == ["repeat", "repeat"]
    # model_calls vs tick_count is the ratio that showed the loop spinning
    # without dispatching anything.
    assert trace["model_calls"] == 2


def test_summary_leads_with_what_explains_a_stall():
    line = format_turn_trace(extract_turn_trace(_final()), session_id="sess_1")
    assert "ended_via=repeat_guard" in line
    assert "guards=repeat,repeat" in line
    # Protocol AND the reason: auto:native vs auto:json:supports_tools=False
    # is the distinction that made a capable model look incapable.
    assert "protocol=native(auto:native)" in line
    assert "ticks=10/30" in line
    assert "session=sess_1" in line


def test_json_protocol_reason_is_visible():
    line = format_turn_trace(
        extract_turn_trace(
            _final(
                tool_protocol="json",
                protocol_reason="auto:json:supports_tools=False(litellm)",
            )
        )
    )
    assert "protocol=json(auto:json:supports_tools=False(litellm))" in line


def test_non_final_and_redacted_events_are_ignored():
    """Production payloads omit observability_metrics; that is not an error."""
    assert extract_turn_trace({"type": "message", "text": "hi"}) is None
    assert extract_turn_trace({"type": "final", "interaction": {"id": "x"}}) is None
    assert extract_turn_trace("not a dict") is None
    assert extract_turn_trace({"type": "final"}) is None


def test_logging_emits_one_line_and_returns_the_trace(caplog):
    with caplog.at_level(logging.INFO, logger="app.services.agent_trace"):
        trace = log_turn_trace(_final(), session_id="sess_2")
    assert trace is not None
    lines = [r.getMessage() for r in caplog.records if "agent-trace" in r.getMessage()]
    assert len(lines) == 1, "exactly one summary line per turn"
    assert "ended_via=repeat_guard" in lines[0]


def test_diagnostics_never_break_a_turn():
    """A malformed envelope must not raise into the stream."""

    class Hostile(dict):
        def get(self, *a, **k):  # noqa: D401
            raise RuntimeError("boom")

    assert log_turn_trace(Hostile()) is None


@pytest.mark.parametrize(
    "value,expected", [("1", True), ("on", True), ("", False), ("0", False)]
)
def test_detail_dump_is_opt_in(monkeypatch, value, expected):
    from app.services import agent_trace

    monkeypatch.setenv("INTEGRAL_AGENT_TRACE", value)
    assert agent_trace.trace_enabled() is expected


def _final_content(**activation):
    """Integral's real terminal event: final-content, raw response under payload.

    The chat provider never sees jvagent's bare ``final`` — the embed streamer
    translates the turn into a UI vocabulary first. Reading only ``final`` is
    how the first version of this logged nothing at all while looking correct.
    """
    inner = _final(**activation)
    return {
        "type": "final-content",
        "content": "…",
        "payload": {"interaction": inner["interaction"]},
    }


def test_reads_integrals_final_content_envelope():
    trace = extract_turn_trace(_final_content())
    assert trace is not None
    assert trace["ended_via"] == "repeat_guard"
    assert trace["guards"] == ["repeat", "repeat"]


def test_turn_end_recognises_both_vocabularies():
    assert is_turn_end({"type": "final-content"})
    assert is_turn_end({"type": "final"})
    # The chatty mid-turn events must not be mistaken for a turn ending.
    for kind in (
        "text-delta",
        "reasoning-delta",
        "tool-call",
        "status",
        "message-finish",
        "message-boundary",
        "_meta",
    ):
        assert not is_turn_end({"type": kind}), kind
    assert not is_turn_end("nope")


def test_mid_turn_events_carry_no_trace():
    assert extract_turn_trace({"type": "tool-call", "name": "x"}) is None
    assert extract_turn_trace({"type": "message-finish", "timing": {}}) is None
