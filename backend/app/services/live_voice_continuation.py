"""Host-owned explicit continuation intent from a completed caller turn."""

import re
from typing import Literal


def explicit_continuation(text: str) -> tuple[Literal["queue", "steer"], str] | None:
    """Recognize explicit spoken continuation commands, never infer from a delegation.

    The Live wire protocol does not carry a typed operation. Restrict this bridge
    to explicit caller-authored phrases, preserving the complete remaining request.
    """
    match = re.fullmatch(
        r"(?:queue (?:a |the )?(?:separate )?follow[- ]?up|after that)[,:.;]?[ \t]+(.+)",
        text.strip(),
        re.IGNORECASE | re.DOTALL,
    )
    if match and 0 < len(match[1].strip()) <= 2000:
        return "queue", match[1].strip()
    match = re.fullmatch(
        r"(?:correction to (?:the |my )?(?:current |running )?(?:task|request)|"
        r"correct (?:the |my )?(?:current |running )?(?:task|request))[,:.;]?[ \t]+(.+)",
        text.strip(),
        re.IGNORECASE | re.DOTALL,
    )
    if match and 0 < len(match[1].strip()) <= 2000:
        return "steer", match[1].strip()
    return None


def multiple_continuation_cues(text: str) -> bool:
    """Do not collapse two independently requested operations into one."""
    cues = re.findall(
        r"\b(?:after that[,:.;]|queue (?:a |the )?(?:separate )?follow[- ]?up[,:.;]?|"
        r"correction to (?:the |my )?(?:current |running )?(?:task|request)[,:.;]?)",
        text,
        re.IGNORECASE,
    )
    return len(cues) > 1
