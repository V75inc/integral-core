"""Compatibility facade for the v1 deployment API; policy lives in runtime."""

from app.runtime.composition import capabilities, owner_ids, require_capability, runtime

__all__ = [
    "capabilities",
    "owner_ids",
    "desktop_mode",
    "require_collaboration",
    "action_disabled",
]


def desktop_mode() -> bool:
    """Retain the legacy predicate for external v1 consumers."""
    return runtime().identity == "installation_owner"


def require_collaboration() -> None:
    """Delegate the legacy guard to the generic runtime ceiling."""
    require_capability("collaboration")


def action_disabled(action: str) -> bool:
    """Retain the v1 action check with explicit operation metadata."""
    return not runtime().supports_action(action)
