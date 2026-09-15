"""Speech-to-text adapters keyed by provider slug.

Built-in adapters load lazily on first lookup so importing the registry never
pulls vendor modules into processes that don't use them.
"""

from __future__ import annotations

from typing import Dict, List

from app.agentive.services.speech.base import SttProvider

_PROVIDERS: Dict[str, SttProvider] = {}
_BUILTINS_LOADED = False


def register_stt_provider(provider: SttProvider) -> SttProvider:
    """Register ``provider`` under its ``id``; a duplicate id raises."""
    if provider.id in _PROVIDERS:
        raise ValueError(f"speech provider {provider.id!r} is already registered")
    _PROVIDERS[provider.id] = provider
    return provider


def _ensure_builtins() -> None:
    global _BUILTINS_LOADED
    if _BUILTINS_LOADED:
        return
    _BUILTINS_LOADED = True
    from app.agentive.services.speech.providers import builtin_providers

    for provider in builtin_providers():
        if provider.id not in _PROVIDERS:
            _PROVIDERS[provider.id] = provider


def get_stt_provider(provider_id: str) -> SttProvider:
    """Return the adapter for ``provider_id``; raises ``KeyError`` if unknown."""
    _ensure_builtins()
    try:
        return _PROVIDERS[provider_id]
    except KeyError:
        raise KeyError(provider_id) from None


def list_stt_providers() -> List[SttProvider]:
    """All registered adapters, sorted by id."""
    _ensure_builtins()
    return [_PROVIDERS[key] for key in sorted(_PROVIDERS)]


def reset_stt_registry() -> None:
    """Test helper — forget every adapter; built-ins reload on next lookup."""
    global _BUILTINS_LOADED
    _PROVIDERS.clear()
    _BUILTINS_LOADED = False
