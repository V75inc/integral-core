"""One skill owner for the turns a free reply used to mis-route."""

from __future__ import annotations

# Follow-up questions stay with insights. A correction, a rejection, and
# resuming an open build stay with scaffold. None of these start a second design.
ROUTING = {
    "follow_up": "integral_insights",
    "correction": "integral_scaffold",
    "rejection": "integral_scaffold",
    "resume": "integral_scaffold",
}


def owner_for(case: str) -> str:
    """The single skill that owns this routing case."""
    owner = ROUTING.get(case or "")
    if not owner:
        raise KeyError(case)
    return owner
