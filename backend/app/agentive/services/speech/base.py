"""Provider-neutral contract for speech-to-text adapters.

An adapter knows one vendor's HTTP surface and nothing else: it never reads
the graph, decrypts credentials, or checks permissions. The speech service
hands it a :class:`ProviderContext` carrying the already-resolved key.

Two capabilities, each optional:

- **streaming** — :meth:`SttProvider.mint_client_session` returns a
  short-lived credential the browser uses to open a live transcription
  session with the vendor directly. The raw API key never leaves the server.
- **batch** — :meth:`SttProvider.transcribe` turns a stored audio file into
  text server-side (the ``integral_transcribe_audio`` tool).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, FrozenSet, Literal, Mapping, Optional, Protocol, Tuple

SttErrorCode = Literal["auth", "quota", "bad_request", "unavailable", "timeout"]

#: Where an adapter's key comes from. ``model_credentials`` — the workspace
#: owner's BYOK ``speech`` slot (LLM vendors that also transcribe).
#: ``connector`` — reserved for non-LLM vendors installed as catalog
#: connectors; no adapter uses it yet.
CredentialSource = Literal["model_credentials", "connector"]


@dataclass(frozen=True)
class SttProviderCapabilities:
    """What an adapter can do, and what the browser needs to reach it."""

    streaming: bool
    batch: bool
    #: Frontend engine id that speaks this provider's live protocol.
    client_engine: Optional[str] = None
    #: Origins the browser connects to. Each must appear in every CSP source
    #: (backend middleware and both nginx templates) — enforced by test.
    browser_connect_origins: Tuple[str, ...] = ()
    max_batch_bytes: int = 0
    batch_mime_types: FrozenSet[str] = frozenset()


@dataclass(frozen=True)
class ProviderContext:
    """Resolved credential and model for one adapter call."""

    provider: str
    model: str
    api_key: str = field(repr=False)
    source: str = "byok"
    #: Stable, privacy-preserving end-user id for vendor abuse tracking.
    safety_id: Optional[str] = None


@dataclass(frozen=True)
class ClientSessionOptions:
    language: Optional[str] = None
    ttl_seconds: int = 60


@dataclass(frozen=True)
class ClientSession:
    """What the browser needs to open a live session with the vendor."""

    engine: str
    transport: Literal["webrtc", "websocket"]
    connect_url: str
    client_secret: str = field(repr=False)
    expires_at: datetime
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TranscribeOptions:
    model: Optional[str] = None
    language: Optional[str] = None
    timeout_seconds: float = 120.0


@dataclass(frozen=True)
class Transcript:
    text: str
    provider: str
    model: str
    language: Optional[str] = None
    duration_seconds: Optional[float] = None


class SttProviderError(Exception):
    """A vendor call failed; ``code`` is provider-neutral."""

    def __init__(
        self, code: SttErrorCode, message: str, retryable: bool = False
    ) -> None:
        super().__init__(code, message, retryable)
        self.code: SttErrorCode = code
        self.message = message
        self.retryable = retryable

    def __str__(self) -> str:
        """Return the human-readable message, not the args tuple."""
        return self.message


class SttProvider(Protocol):
    """A speech-to-text vendor adapter."""

    id: str
    display_name: str
    credential_source: CredentialSource
    capabilities: SttProviderCapabilities

    async def validate_credentials(self, api_key: str, model: str) -> Tuple[bool, str]:
        """Prove ``api_key`` can do what voice input needs with ``model``."""
        ...

    async def mint_client_session(
        self, ctx: ProviderContext, opts: ClientSessionOptions
    ) -> ClientSession:
        """Return a short-lived browser credential (streaming adapters)."""
        ...

    async def transcribe(
        self,
        ctx: ProviderContext,
        audio: bytes,
        *,
        mime_type: str,
        filename: str,
        opts: TranscribeOptions,
    ) -> Transcript:
        """Transcribe a complete audio file (batch adapters)."""
        ...


def primary_language_subtag(language: Optional[str]) -> Optional[str]:
    """``"en-US"`` → ``"en"``; ``"auto"`` or empty → ``None``."""
    value = (language or "").strip()
    if not value or value.lower() == "auto":
        return None
    return value.split("-", 1)[0].lower()
