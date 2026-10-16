"""Typed app operation facade without importing HTTP routes at package load."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.app_operations.dispatch import (
        invoke_app_operation,
        list_app_operations,
        register_app_operations,
        unregister_app_operations,
    )

__all__ = [
    "invoke_app_operation",
    "list_app_operations",
    "register_app_operations",
    "unregister_app_operations",
]


def __getattr__(name: str) -> Any:
    """Load dispatcher exports on demand; transaction primitives stay acyclic."""
    if name not in __all__:
        raise AttributeError(name)
    from app.services.app_operations import dispatch

    return getattr(dispatch, name)
