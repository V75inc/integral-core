"""Backend chat provider abstraction.

Re-exports the public surface so callers can ``from
app.services.chat_providers import ChatBackendProvider, get_registry`` without
caring about internal module layout.
"""

from app.services.chat_providers.base import (
    ChatBackendProvider,
    ChatProviderCapabilities,
    ChatTurnContext,
    ProviderInfo,
)
from app.services.chat_providers.registry import (
    ChatBackendRegistry,
    get_registry,
)

__all__ = [
    "ChatBackendProvider",
    "ChatBackendRegistry",
    "ChatProviderCapabilities",
    "ChatTurnContext",
    "ProviderInfo",
    "get_registry",
]
