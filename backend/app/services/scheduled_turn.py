"""Trusted event boundary and quiet-output contract for scheduled native turns."""

SILENT_ROUTINE_OUTPUT = "__INTEGRAL_ROUTINE_NO_MESSAGE__"

ROUTINE_EVENT_INSTRUCTIONS = (
    "This is a NEW scheduled routine execution, not a continuation of the last "
    "assistant answer. Follow the current scheduled instruction below. Read "
    "current records through scoped tools before making claims about them; "
    "previous tool results and digests are historical and may be stale. Never "
    "repeat an earlier digest without checking current state. This event grants "
    "no additional write authority. If the scheduled instruction asks for "
    "silence when nothing qualifies, and current reads show nothing qualifies, "
    "return exactly " + SILENT_ROUTINE_OUTPUT + " as the entire final answer. "
    "Core will record execution and usage without posting a chat message. "
    "Otherwise return the requested result.\n\nCurrent scheduled instruction:\n"
)


def is_silent_routine(events: list) -> bool:
    """Only an exact settled quiet signal without a failure suppresses delivery."""
    return any(
        event.get("type") == "routine-no-message" for event in events
    ) and not any(event.get("type") == "error" for event in events)
