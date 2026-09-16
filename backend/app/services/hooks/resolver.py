"""Hook resolver — match candidate bindings for (workspace, point, payload).

Semantics:
- Match keys AND-combined: every key in binding.match MUST appear in payload
  with an equal value.
- Missing key in binding.match = wildcard (binding doesn't constrain).
- Missing key in payload WHEN binding constrains it = no match.
- Empty match block ({}) = matches every payload.
- Multiple matches returned in registration order — endpoint decides
  ambiguity handling (substrate NEVER auto-picks).
- explicit_hook_key (caller's ?hook=k) filters to that single binding.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.hooks.registry import get_workspace_hooks

# Keys whose values are type/track slugs — compare after normalizing
# hyphens/spaces to underscores so ``customer-projects`` == ``customer_projects``.
_TYPE_MATCH_KEYS = frozenset(
    {
        "source_entry_type",
        "target_entry_type",
        "source_track_type",
        "target_track_type",
    }
)

# When the caller already chose ``to_track``, ``target_track_type`` in the
# match block is advisory (title slug vs template_id drift). With an explicit
# hook key we only enforce source identity.
_SOFT_MATCH_KEYS = frozenset({"source_entry_type", "source_track_type"})


def _slug_type(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def _track_types_equivalent(a: Any, b: Any, workspace_id: str = "") -> bool:
    from app.services.hooks.track_aliases import track_types_equivalent

    return track_types_equivalent(a, b, workspace_id)


def find_matching_bindings(
    workspace_id: str,
    point: str,
    payload: Dict[str, Any],
    explicit_hook_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return workspace bindings at ``point`` whose ``match`` block agrees with ``payload``."""
    candidates = get_workspace_hooks(workspace_id, point)
    if explicit_hook_key:
        candidates = [b for b in candidates if str(b.get("key")) == explicit_hook_key]
    matched: List[Dict[str, Any]] = []
    for binding in candidates:
        match_block = binding.get("match") or {}
        if explicit_hook_key:
            soft = {k: v for k, v in match_block.items() if k in _SOFT_MATCH_KEYS}
            if _matches(soft, payload, workspace_id=workspace_id):
                matched.append(binding)
        elif _matches(match_block, payload, workspace_id=workspace_id):
            matched.append(binding)
    return matched


def _matches(
    match_block: Dict[str, Any],
    payload: Dict[str, Any],
    *,
    workspace_id: str = "",
) -> bool:
    for key, expected in match_block.items():
        if key not in payload:
            return False
        actual = payload[key]
        if key in _TYPE_MATCH_KEYS:
            if key == "target_track_type" or key == "source_track_type":
                if not _track_types_equivalent(
                    actual, expected, workspace_id=workspace_id
                ):
                    return False
            elif _slug_type(actual) != _slug_type(expected):
                return False
        elif actual != expected:
            return False
    return True
