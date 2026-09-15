"""Built-in speech-to-text adapters."""

from __future__ import annotations

from typing import List

from app.agentive.services.speech.base import SttProvider
from app.agentive.services.speech.providers.openai import OpenAISttProvider


def builtin_providers() -> List[SttProvider]:
    """Adapters the registry loads on first lookup."""
    return [OpenAISttProvider()]
