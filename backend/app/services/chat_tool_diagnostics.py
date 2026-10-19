"""Content-free tool failure summaries for durable public chat."""

from __future__ import annotations

from typing import Any

_MESSAGES = {
    "invalid_arguments": "The proposed arguments did not match the tool's contract.",
    "permission_denied": "A required resource is unavailable in the current scope.",
    "policy_denied": "The current permissions do not allow this operation.",
    "internal_error": "The tool could not complete because of an internal error.",
    "tool_failed": "The tool did not complete. Detailed output is not retained.",
}


def tool_diagnostic(code: Any) -> dict[str, str]:
    """Project an error code to fixed product copy, never raw tool content."""
    canonical = code if isinstance(code, str) and code in _MESSAGES else "tool_failed"
    return {"code": canonical, "message": _MESSAGES[canonical]}


def normalize_tool_diagnostic(value: Any) -> dict[str, str]:
    """Reject unsupported fields and rederive text at the persistence boundary."""
    if not isinstance(value, dict) or set(value) != {"code", "message"}:
        raise ValueError("unsupported tool diagnostic")
    canonical = tool_diagnostic(value["code"])
    if value != canonical:
        raise ValueError("unsupported tool diagnostic content")
    return canonical
