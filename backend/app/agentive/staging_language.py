"""Stage-safe wording helpers for propose / pre-consume surfaces.

Filing and other propose tools stage changes for user approval. Status text
must not use completion verbs until the bless path records ``consumed``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

# Completion verbs that imply the write already happened.
_COMPLETION_RE = re.compile(
    r"\b(filed|created|updated|deleted|saved|applied|committed)\b",
    re.IGNORECASE,
)

_FILED_TO_RE = re.compile(r"^Filed to\b", re.IGNORECASE)


def ensure_staged_wording(text: str) -> str:
    """Rewrite pre-consume completion phrasing to staged/planned language."""
    if not text or not text.strip():
        return text
    out = text.strip()
    if _FILED_TO_RE.match(out):
        rest = out[8:].lstrip()
        return f"Proposed filing to {rest} (pending your approval)"
    if _COMPLETION_RE.search(out):
        out = _COMPLETION_RE.sub("staged", out)
    return out


def sanitize_filing_result(data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Normalize ``message`` fields on smart-filing / filing-candidate payloads."""
    if not isinstance(data, dict):
        return data
    out = dict(data)
    if isinstance(out.get("message"), str):
        out["message"] = ensure_staged_wording(out["message"])
    return out


def staged_change_reply_hint(summary: str) -> str:
    """Short instruction for the model when a propose tool minted a card."""
    label = (summary or "this change").strip()
    return (
        f"Staged {label} for the user's review — not filed yet. "
        "Use 'staged' / 'pending approval' wording only."
    )


_FILING_TOOL_NAMES = frozenset({"integral_file_content"})


def enrich_filing_tool_result(tool_name: str, data: Any) -> Any:
    """Apply staged wording and reply hints to filing tool payloads."""
    if tool_name not in _FILING_TOOL_NAMES or not isinstance(data, dict):
        return data
    if data.get("_kind") == "staged_change":
        out = dict(data)
        out["assistant_reply_hint"] = staged_change_reply_hint(
            str(data.get("summary") or "")
        )
        return out
    return sanitize_filing_result(data)
