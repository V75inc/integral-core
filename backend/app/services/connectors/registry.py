"""Sync-connector registry — decorator-style, single-registration invariant.

Mirrors Phase 1 ``register_connector`` at app/agentive/connectors/registry.py
verbatim, but renamed ``agent_type`` → ``slug``. Lives in core per locked
decision #12 (CON-02 — reachable by non-jvagent MCP clients regardless of
AGENTIVE_ENABLED).

Test helpers (``reset_sync_registry``) mirror Phase 2's
``reset_consumer_hooks`` pattern at ``app/services/event_subscription_registry.py``.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Type

from .base import SyncConnector

_SYNC_REGISTRY: Dict[str, Type[SyncConnector]] = {}


def register_sync_connector(
    slug: str,
) -> Callable[[Type[SyncConnector]], Type[SyncConnector]]:
    """Decorator. Registers a ``SyncConnector`` subclass under a canonical slug.

    Slug normalization mirrors Phase 1 ``register_connector``: lowercased,
    stripped. Empty / whitespace-only slugs raise ``ValueError``. Duplicate
    slug registration raises ``ValueError`` (single-registration invariant —
    Phase 1 D-07 mirror; see I-CON-03).
    """

    def _decorator(cls: Type[SyncConnector]) -> Type[SyncConnector]:
        key = (slug or "").strip().lower()
        if not key:
            raise ValueError("register_sync_connector requires a non-empty slug")
        if key in _SYNC_REGISTRY:
            raise ValueError(
                f"sync connector slug {key!r} already registered to "
                f"{_SYNC_REGISTRY[key].__name__}"
            )
        _SYNC_REGISTRY[key] = cls
        return cls

    return _decorator


def get_sync_connector(slug: str) -> SyncConnector:
    """Lookup + instantiate. Returns a fresh subclass instance per Phase 1 idiom.

    Slug normalization matches registration (lowercased, stripped).
    """
    key = (slug or "").strip().lower()
    cls = _SYNC_REGISTRY.get(key)
    if cls is None:
        raise ValueError(f"Unknown sync connector slug: {slug!r}")
    return cls()


def reset_sync_registry() -> None:
    """Test helper — clears the module-level registry between tests.

    Mirrors ``reset_consumer_hooks`` (Phase 2 02-04). Call from an autouse
    pytest fixture in conftest.py (or per-test-file fixture) so cross-test
    registrations don't leak.
    """
    _SYNC_REGISTRY.clear()


def list_registered_slugs() -> List[str]:
    """Return all registered connector slugs in sorted order."""
    return sorted(_SYNC_REGISTRY.keys())
