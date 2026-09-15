"""Per-skill tool allowlist (ADR-002 Phase 1 / Full Sweep T1).

When a skill declares ``tools_required``, dispatch may optionally enforce
that the tool being called is in that list. Empty / missing lists are a
no-op for backward compatibility.
"""

from __future__ import annotations

from typing import Iterable, Optional


class SkillToolAllowlistError(Exception):
    """Raised when a skill attempts a tool outside its declared allowlist."""


def assert_tool_allowed_for_skill(
    tool_name: str,
    tools_required: Optional[Iterable[str]],
) -> None:
    """No-op when ``tools_required`` is empty/None; else require membership."""
    if not tools_required:
        return
    allowed = {t for t in tools_required if t}
    if not allowed:
        return
    if tool_name not in allowed:
        raise SkillToolAllowlistError(
            f"tool {tool_name!r} is not in skill tools_required {sorted(allowed)}"
        )
