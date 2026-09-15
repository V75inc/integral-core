"""Per-turn diagnostic trace for the agent loop.

Debugging a stuck turn meant reading raw LiteLLM DEBUG output and counting
dispatch lines, which answers "what ran" but not "why did it stop" — the
question that actually matters. Three separate investigations in one session
guessed a cause from that data and were wrong, because the deciding facts were
never in the log: which protocol the turn spoke and why, which guard fired, how
the loop ended.

jvagent already records all of it. Each turn appends an ``orchestrator_activation``
event to ``interaction.observability_metrics``, and the final stream envelope
carries that payload when the host is not in production mode. Nothing here asks
jvagent for anything new; it reads what was always being sent and writes one
line a person can act on:

    agent-trace session=… protocol=native(auto:native) ticks=10 light=1 heavy=9
      guards=repeat,repeat ended_via=repeat_guard tools=- skills=- 372ms

``guards`` is the field that ends most arguments: it names which guard produced
each ``(guard)`` step, in order, so a repeat-guard death is visible as such
instead of being inferred from a missing reply. ``protocol_reason`` distinguishes
``auto:native`` from ``auto:json:supports_tools=False(litellm)`` — the
difference that made a capable model look incapable for most of a day.

Set ``INTEGRAL_AGENT_TRACE=1`` to also emit the full activation payload as JSON
(tool timings, token costs, context trims) for the turns where the summary is
not enough.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ACTIVATION = "orchestrator_activation"
_MODEL_CALL = "model_call"


# Integral translates jvagent's stream into a UI vocabulary before the chat
# provider sees it, so the turn-ending event here is ``final-content`` carrying
# the raw response under ``payload`` — not jvagent's own ``final``. Both are
# accepted: the bare ``final`` shape is what jvagent emits natively, and reading
# only one of them is how the first version of this silently logged nothing.
_TERMINAL_TYPES = ("final-content", "final")


def _metrics_from_final(event: Any) -> List[Dict[str, Any]]:
    """Pull ``observability_metrics`` out of a turn-ending stream event."""
    if not isinstance(event, dict):
        return []
    if event.get("type") not in _TERMINAL_TYPES:
        return []
    container = event.get("payload")
    if not isinstance(container, dict):
        container = event
    interaction = container.get("interaction")
    if not isinstance(interaction, dict):
        return []
    metrics = interaction.get("observability_metrics")
    return (
        [m for m in metrics if isinstance(m, dict)] if isinstance(metrics, list) else []
    )


def is_turn_end(event: Any) -> bool:
    """True when this event ends a turn and may carry a trace."""
    return isinstance(event, dict) and event.get("type") in _TERMINAL_TYPES


def extract_turn_trace(event: Any) -> Optional[Dict[str, Any]]:
    """Return the turn's orchestrator activation record, or None.

    ``None`` covers every uninteresting case — a non-final event, a redacted
    production payload, a turn that died before the orchestrator recorded
    anything — so callers never need to distinguish them.
    """
    metrics = _metrics_from_final(event)
    if not metrics:
        return None
    activations = [
        m.get("data")
        for m in metrics
        if m.get("event_type") == _ACTIVATION and isinstance(m.get("data"), dict)
    ]
    if not activations:
        return None
    trace = dict(activations[-1])
    trace["model_calls"] = sum(1 for m in metrics if m.get("event_type") == _MODEL_CALL)
    return trace


def format_turn_trace(trace: Dict[str, Any], *, session_id: str = "") -> str:
    """One line, ordered so the fields that explain a stall come first."""

    def _join(key: str) -> str:
        value = trace.get(key)
        if isinstance(value, list) and value:
            return ",".join(str(v) for v in value)
        return "-"

    protocol = str(trace.get("tool_protocol") or "?")
    reason = trace.get("protocol_reason")
    if reason:
        protocol = f"{protocol}({reason})"

    parts = [
        "agent-trace",
        f"session={session_id or '-'}",
        f"protocol={protocol}",
        f"ticks={trace.get('tick_count', '?')}/{trace.get('budget', '?')}",
        f"light={trace.get('ticks_light', 0)}",
        f"heavy={trace.get('ticks_heavy', 0)}",
        f"model_calls={trace.get('model_calls', '?')}",
        f"guards={_join('guards')}",
        f"ended_via={trace.get('ended_via') or '-'}",
        f"tools={_join('tools_invoked')}",
        f"skills={_join('skills_used')}",
    ]
    if trace.get("context_trims"):
        parts.append(f"context_trims={trace['context_trims']}")
    if trace.get("fallbacks_used"):
        parts.append(f"fallbacks={_join('fallbacks_used')}")
    if trace.get("loop_duration_ms") is not None:
        parts.append(f"{trace['loop_duration_ms']}ms")
    return " ".join(parts)


def trace_enabled() -> bool:
    """Full-payload dump, off unless asked for."""
    return (os.getenv("INTEGRAL_AGENT_TRACE") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def log_turn_trace(event: Any, *, session_id: str = "") -> Optional[Dict[str, Any]]:
    """Log the turn's trace if this event carries one. Never raises.

    Returns the trace so callers can assert on it; the logging is the point.
    """
    try:
        trace = extract_turn_trace(event)
        if trace is None:
            return None
        logger.info("%s", format_turn_trace(trace, session_id=session_id))
        if trace_enabled():
            logger.info("agent-trace-detail %s", json.dumps(trace, default=str))
        return trace
    except Exception:  # noqa: BLE001 - diagnostics must never break a turn
        logger.debug("agent trace extraction failed", exc_info=True)
        return None
