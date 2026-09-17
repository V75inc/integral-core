"""Typed app operation dispatcher (ADR-011)."""

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
