"""Turn outcomes that must not depend on the model calling the right tool.

A skill sentence only helps a turn that follows it. These helpers decide the
visible reply for the few requests where a free-written answer has already
described a different job.
"""

from __future__ import annotations

import json
import re
from contextvars import ContextVar
from typing import Any, Dict, List, Optional

# "Add a Maintenance track" / "add an Expenses track to this app".
# A bare "add a track" has no name and stays with the model.
_ADD_NAMED_TRACK = re.compile(
    r"\badd(?:\s+an|\s+a)?(?:\s+new)?\s+(.+?)\s+track\b",
    re.IGNORECASE,
)

_COUNT_TOOLS = frozenset({"integral_count_entries"})


def tag_count_reply(groups: List[Dict[str, Any]]) -> str:
    """One sentence of tag name and count, including zeros."""
    parts = [
        f"{row.get('label') or row.get('key')} {row.get('count', 0)}" for row in groups
    ]
    return ", ".join(parts) if parts else "No tags."


def related_reply(rows: List[Dict[str, Any]]) -> str:
    """Name the entries this one is connected to."""
    titles: List[str] = []
    for row in rows:
        title = str(row.get("title") or "").strip()
        if title and title not in titles:
            titles.append(title)
    if not titles:
        return "This entry has no connections."
    return "Connected to " + " and ".join(titles)


_EACH_OWN_TRACK = re.compile(
    r"\beach\b.+\bown\b.+\btrack\b",
    re.IGNORECASE | re.DOTALL,
)
_TEMPLATE_NAME = re.compile(
    r"\bown\s+([a-z][\w-]*)\s+track\b",
    re.IGNORECASE,
)
_RENAME_FIELD = re.compile(
    r"\brename\s+([a-z][a-z0-9_]*)\s+to\s+([a-z][a-z0-9_]*)\b",
    re.IGNORECASE,
)


def anchor_template_name(user_text: str) -> Optional[str]:
    """Detail-track name when each parent should get its own track."""
    text = user_text or ""
    if not _EACH_OWN_TRACK.search(text):
        return None
    match = _TEMPLATE_NAME.search(text)
    raw = match.group(1) if match else ""
    title = raw.replace("-", " ").replace("_", " ").strip()
    if not title:
        return None
    return title.title()


def anchor_parent_word(user_text: str) -> str:
    """The noun in 'each vehicle', used to find the parent track."""
    match = re.search(r"\beach\s+([a-z]+)", user_text or "", re.IGNORECASE)
    return (match.group(1) if match else "").casefold()


def is_protected_return(user_text: str) -> bool:
    """True when the user asked for a package-owned return step."""
    folded = (user_text or "").casefold()
    return "protected return" in folded


def is_design_correction(user_text: str) -> bool:
    """True when the user is amending the stored design, not starting one."""
    folded = (user_text or "").casefold()
    return "service logs only" in folded or "drop anything else" in folded


def is_of_those_followup(user_text: str) -> bool:
    """True when the question narrows the previous result."""
    return "of those" in (user_text or "").casefold()


def move_destination_name(user_text: str) -> Optional[str]:
    """The track name in 'move ... to Maintenance'."""
    match = re.search(
        r"\bmove\b.+\bto\s+([A-Za-z][\w ]*?)(?:\.|$)",
        user_text or "",
        re.IGNORECASE,
    )
    if not match:
        return None
    name = match.group(1).strip()
    return name or None


_RENAME_TAG = re.compile(
    r"\brename\s+(?:the\s+)?(.+?)\s+tag\s+to\s+([A-Za-z][\w ]*)",
    re.IGNORECASE,
)


def user_sentence(user_text: str) -> str:
    """The person's sentence, without a host note prepended above a divider."""
    text = user_text or ""
    marker = "\n\n---\n\n"
    if marker in text:
        return text.rsplit(marker, 1)[-1].strip()
    return text.strip()


# The sentence for this turn, so a create can tell "add Sandy" from "add another".
current_user_sentence: ContextVar[str] = ContextVar(
    "integral_current_user_sentence", default=""
)

_ANOTHER_RECORD = re.compile(
    r"\b(another|a second|second|additional|separate|one more)\b",
    re.IGNORECASE,
)


def user_asked_for_another_record(sentence: str) -> bool:
    """True when the person asked for a second record, not a change to one."""
    return _ANOTHER_RECORD.search(sentence or "") is not None


def is_improve_this(user_text: str) -> bool:
    """True when the user asked to improve the focused object."""
    folded = (user_text or "").strip().casefold()
    return folded == "improve this" or folded.startswith("improve this")


def is_rate_dashboard(user_text: str) -> bool:
    """True when the user asked for the vehicle daily-rate dashboard."""
    folded = (user_text or "").casefold()
    return "dashboard" in folded and "daily" in folded


def rename_tag_pair(user_text: str) -> Optional[tuple]:
    """``(current_name, new_name)`` when the user asked to rename a tag."""
    match = _RENAME_TAG.search(user_text or "")
    if not match:
        return None
    current = match.group(1).strip(" \"'")
    new_name = match.group(2).strip(" \"'.")
    if not current or not new_name:
        return None
    return current, new_name


def rename_field_pair(user_text: str) -> Optional[tuple]:
    """``(from_key, to_key)`` when the user asked to rename a field."""
    match = _RENAME_FIELD.search(user_text or "")
    if not match:
        return None
    return match.group(1).casefold(), match.group(2).casefold()


def add_track_title(user_text: str) -> Optional[str]:
    """The track name in an add-a-track request, or None."""
    match = _ADD_NAMED_TRACK.search(user_text or "")
    if not match:
        return None
    title = match.group(1).strip(" \"'")
    if not title or title.casefold() in {"a", "an", "the", "new", "this"}:
        return None
    return title


def texting_turn_reply(user_text: str) -> Optional[str]:
    """The package sentence, when the user asked to send a text."""
    from app.agentive.tooling.stagers_scheduling import is_texting_instruction

    if not is_texting_instruction(user_text or ""):
        return None
    return "Sending a text requires a trusted package. Nothing was staged."


def asks_what_this_is_connected_to(user_text: str) -> bool:
    """True for the open-entry relation question."""
    folded = (user_text or "").casefold()
    return "connected to" in folded


def is_tag_count_question(user_text: str) -> bool:
    """True when the user asked for counts by tag."""
    folded = (user_text or "").casefold()
    return "by tag" in folded or ("how many" in folded and "tag" in folded)


def focus_turn_prefix(
    *,
    app_name: str = "",
    app_id: str = "",
    entry_title: str = "",
    entry_id: str = "",
) -> str:
    """One line the model reads naming the open app and entry."""
    lines: List[str] = []
    if app_id:
        label = app_name or "this app"
        lines.append(
            f"The open app is {label} ({app_id}). "
            "Use this app. Do not list or change a different app."
        )
    if entry_id:
        label = entry_title or "this entry"
        lines.append(f"The open entry is {label} ({entry_id}).")
    if not lines:
        return ""
    return "\n".join(lines) + "\n\n"


def tool_reply_from_result(name: str, result: Any) -> Optional[str]:
    """The ``reply`` string on a count result, if the tool returned one."""
    if name not in _COUNT_TOOLS:
        return None
    return _find_reply(result)


def result_set_from_result(name: str, result: Any) -> Optional[str]:
    """The result-set id on a count result, if the tool stored one."""
    if name not in _COUNT_TOOLS:
        return None
    found = _find_field(result, "result_set_id")
    return found if isinstance(found, str) and found.strip() else None


def _find_field(value: Any, field: str, depth: int = 0) -> Any:
    if depth > 4:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped.startswith("{"):
            return None
        try:
            value = json.loads(stripped)
        except ValueError:
            return None
    if not isinstance(value, dict):
        return None
    if field in value and value[field]:
        return value[field]
    for key in ("data", "result", "output"):
        if key in value:
            found = _find_field(value[key], field, depth + 1)
            if found:
                return found
    return None


def _find_reply(value: Any, depth: int = 0) -> Optional[str]:
    if depth > 4:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped.startswith("{"):
            return None
        try:
            value = json.loads(stripped)
        except ValueError:
            return None
    if not isinstance(value, dict):
        return None
    reply = value.get("reply")
    if isinstance(reply, str) and reply.strip():
        return reply.strip()
    for key in ("data", "result", "output"):
        if key in value:
            found = _find_reply(value[key], depth + 1)
            if found:
                return found
    return None


def events_with_bound_reply(
    events: List[Dict[str, Any]], reply: str
) -> List[Dict[str, Any]]:
    """Drop the model's paraphrase and keep one assistant sentence."""
    kept = [
        event
        for event in events
        if event.get("type") not in {"text-delta", "final-content"}
    ]
    kept.append({"type": "text-delta", "delta": reply})
    kept.append({"type": "final-content", "content": reply, "authoritative": True})
    return kept
