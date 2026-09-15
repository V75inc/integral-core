"""Process-global registry for :class:`ChatBackendProvider` adapters.

Single source of truth for which chat harnesses are wired into this
process. The chat router (``app/api/ai_chat.py``) reads from the registry
exclusively — it never imports a harness module directly. Startup
(``app/main.py``) is responsible for registering whatever harnesses the
deployment supports; tests can register a mock provider via the same API.

Why a process-global rather than a FastAPI dependency: the router is
pure-async + per-request stateless; provider availability is a process
fact, not a request fact; and the FE registry endpoint is read-only. A
dependency-injected variant can replace this transparently if/when the
ai_chat router gains real injection needs.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from app.services.chat_providers.base import ChatBackendProvider

logger = logging.getLogger(__name__)


class ChatBackendRegistry:
    """In-memory map of provider id → adapter.

    Intentionally minimal — register, look up, list, designate a default.
    Anything fancier (capability filtering, request-time scoring, A/B
    routing) belongs in the router or in a higher-level service that wraps
    this.
    """

    def __init__(self) -> None:
        self._providers: Dict[str, ChatBackendProvider] = {}
        self._default_id: Optional[str] = None

    def register(self, provider: ChatBackendProvider, *, default: bool = False) -> None:
        """Register a provider under its declared ``id``.

        Re-registering the same id is a deliberate replace (logs at debug)
        — useful for tests that swap a real adapter for a mock without
        rebuilding the registry.
        """
        existing = self._providers.get(provider.id)
        if existing is not None and existing is not provider:
            logger.debug(
                "ChatBackendRegistry: replacing provider %r (%s -> %s)",
                provider.id,
                type(existing).__name__,
                type(provider).__name__,
            )
        self._providers[provider.id] = provider
        if default or self._default_id is None:
            self._default_id = provider.id

    def get(self, provider_id: str) -> Optional[ChatBackendProvider]:
        """Return the registered provider, or ``None`` if not registered."""
        return self._providers.get(provider_id)

    def list(self) -> List[ChatBackendProvider]:
        """Stable-ordered list of registered providers (insertion order)."""
        return list(self._providers.values())

    def default(self) -> Optional[ChatBackendProvider]:
        """The default provider, or ``None`` if none registered."""
        if self._default_id is None:
            return None
        return self._providers.get(self._default_id)

    def clear(self) -> None:
        """Reset registry — for tests."""
        self._providers.clear()
        self._default_id = None


_registry: Optional[ChatBackendRegistry] = None


def get_registry() -> ChatBackendRegistry:
    """Return the process-global registry, creating it on first access."""
    global _registry
    if _registry is None:
        _registry = ChatBackendRegistry()
    return _registry
