"""Keep raw tool JSON out of the chat when the orchestrator's loop guard trips.

When jvagent's orchestrator detects a repeating step it bails and "salvages"
the turn: it takes the raw ``observation`` strings from the tools it touched,
truncates each to 600 characters, and pastes up to four of them into the reply
under "Here is what I gathered so far".

That is reasonable for an agent whose observations are prose. Integral's are
JSON -- a tool observation is the tool's serialized result -- so the salvage
pastes things like ``{"total": 8, "next_cursor": null, "tracks": [{"id": ...``
straight into the conversation. It is unreadable, it leaks internal ids and
field names, and it arrives precisely when the person is already not getting
what they asked for.

jvagent has its own wording for this same bail when it has nothing worth
salvaging, so we reuse it verbatim rather than inventing a second voice: the
person is told the turn got stuck and can be resumed, which is the whole of
the useful content in the salvage anyway.

Fix the upstream salvage (or stop the loop) and this becomes a no-op -- it
matches on the preamble, so a turn that never trips the guard is untouched.
"""

from __future__ import annotations

from typing import Any

# The preamble jvagent emits before pasting raw observations
# (jvagent/action/orchestrator/loop_helpers.py).
_SALVAGE_PREAMBLE = (
    "I made progress on this but hit a loop before finishing every step."
)

# jvagent's own message for the same bail with nothing to salvage.
_CLEAN_REPLACEMENT = (
    "I started on this request and gathered material, but I got stuck "
    "repeating a step and could not finish cleanly. Please ask me to "
    "continue — I will pick up from the work already done."
)


def _clean(text: str) -> str:
    return _CLEAN_REPLACEMENT if _SALVAGE_PREAMBLE in text else text


def sanitize_loop_salvage(event: Any) -> Any:
    """Return ``event`` with any loop-salvage dump replaced by clean prose.

    Walks the structure rather than reaching for a known key: the preamble
    shows up in streamed ``message`` chunks and again in the consolidated
    ``final`` envelope, and their shapes are jvagent's to change.
    """
    if isinstance(event, str):
        return _clean(event)
    if isinstance(event, dict):
        return {k: sanitize_loop_salvage(v) for k, v in event.items()}
    if isinstance(event, list):
        return [sanitize_loop_salvage(v) for v in event]
    return event
