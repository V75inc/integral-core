"""Voice input service — engine resolution and browser-session minting.

The one place that joins the workspace role gate, the credential resolver,
the per-user rate limit and a provider adapter. Raises the ``Speech*``
exceptions below; the API layer maps them to HTTP and the tool layer to
error envelopes.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List, Optional

from app.agentive.services.speech.base import (
    ClientSessionOptions,
    ProviderContext,
    SttProvider,
    SttProviderError,
    TranscribeOptions,
)
from app.agentive.services.speech.rate_limit import allow
from app.agentive.services.speech.registry import get_stt_provider
from app.config import settings
from app.schemas.agentive.speech import (
    SpeechConfigResponse,
    SpeechEngineOption,
    SpeechLimits,
    SpeechProviderStatus,
    SpeechSessionRequest,
    SpeechSessionResponse,
)
from app.schemas.speech_preferences import validate_language_tag
from app.services.model_credential_resolver import (
    SpeechCredential,
    resolve_speech_credential,
)
from app.services.speech_preferences import get_speech_preferences
from app.services.workspace_permissions import can_access_workspace

logger = logging.getLogger(__name__)

BROWSER_ENGINE = "webspeech"
_BROWSER_PRIVACY_NOTE = (
    "Browser recognition can send your audio to the browser's maker "
    "(Google in Chrome, Microsoft in Edge, Apple in Safari)."
)
# Guests reach a workspace through a share; they don't spend its provider
# quota and keep the in-browser recognizer.
_PROVIDER_ROLES = frozenset({"owner", "admin", "member"})


class SpeechError(Exception):
    """Base for voice-input failures."""


class SpeechAccessDenied(SpeechError):
    """The caller's workspace role can't use the workspace provider."""


class SpeechNotConfigured(SpeechError):
    """No usable speech-to-text provider for this workspace."""


class SpeechRateLimited(SpeechError):
    """The caller hit the per-user limit."""


class SpeechProviderFailure(SpeechError):
    """The vendor call failed; ``code`` is an ``SttErrorCode``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message)
        self.code = code
        self.message = message

    def __str__(self) -> str:
        """Return the human-readable message, not the args tuple."""
        return self.message


def safety_identifier(user_id: str) -> str:
    """Stable, non-reversible end-user id for vendor abuse tracking."""
    return hashlib.sha256(f"integral-speech:{user_id}".encode()).hexdigest()


def _streaming_provider(cred: Optional[SpeechCredential]) -> Optional[SttProvider]:
    if cred is None:
        return None
    try:
        provider = get_stt_provider(cred.provider)
    except KeyError:
        logger.warning("no speech adapter for provider %r", cred.provider)
        return None
    caps = provider.capabilities
    return provider if caps.streaming and caps.client_engine else None


def _preferred_engine(
    enabled: bool, choice: str, engines: List[SpeechEngineOption]
) -> Optional[str]:
    if not enabled or not engines:
        return None
    if choice == "browser":
        return BROWSER_ENGINE
    return engines[0].engine


async def resolve_effective_config(
    user_id: str, workspace_id: str
) -> SpeechConfigResponse:
    """What the composer should offer this caller in this workspace."""
    prefs = await get_speech_preferences(user_id)
    role = await can_access_workspace(user_id, workspace_id)
    eligible = role in _PROVIDER_ROLES
    cred = await resolve_speech_credential(workspace_id, touch=False)
    provider = _streaming_provider(cred)

    engines: List[SpeechEngineOption] = []
    if provider is not None and eligible:
        engines.append(
            SpeechEngineOption(
                engine=provider.capabilities.client_engine or "",
                source="workspace",
                display_name=provider.display_name,
            )
        )
    engines.append(
        SpeechEngineOption(
            engine=BROWSER_ENGINE,
            source="browser",
            display_name="Browser speech recognition",
            privacy_note=_BROWSER_PRIVACY_NOTE,
        )
    )

    configured = cred is not None and provider is not None
    return SpeechConfigResponse(
        workspace_id=workspace_id,
        workspace_role=role,
        preferences=prefs,
        engines=engines,
        preferred_engine=_preferred_engine(prefs.enabled, prefs.engine, engines),
        provider=SpeechProviderStatus(
            configured=configured,
            available_to_you=configured and eligible,
            provider=cred.provider if configured and cred else None,
            model=cred.model if configured and cred else None,
            source=cred.source if configured and cred else None,
        ),
        limits=SpeechLimits(
            max_session_seconds=settings.SPEECH_MAX_SESSION_SECONDS,
            session_ttl_seconds=settings.SPEECH_SESSION_TTL_SECONDS,
        ),
    )


async def mint_session(
    user_id: str, workspace_id: str, req: SpeechSessionRequest
) -> SpeechSessionResponse:
    """Mint a short-lived browser credential for a live dictation session.

    Order: role gate → per-user rate limit → credential → adapter. The rate
    limit sits before credential resolution so a runaway client can't turn
    decrypts and vendor calls into a cost amplifier.
    """
    role = await can_access_workspace(user_id, workspace_id)
    if role not in _PROVIDER_ROLES:
        raise SpeechAccessDenied(
            "Workspace guests use in-browser recognition; the workspace's "
            "speech-to-text provider is for its members."
        )
    if not allow("speech.session", user_id, settings.SPEECH_SESSION_RATE_LIMIT):
        raise SpeechRateLimited("Too many voice input sessions — try again shortly")

    cred = await resolve_speech_credential(workspace_id)
    provider = _streaming_provider(cred)
    if cred is None or provider is None:
        raise SpeechNotConfigured(
            "This workspace has no speech-to-text provider configured"
        )

    prefs = await get_speech_preferences(user_id)
    language = req.language or (None if prefs.language == "auto" else prefs.language)
    ctx = ProviderContext(
        provider=cred.provider,
        model=cred.model,
        api_key=cred.api_key,
        source=cred.source,
        safety_id=safety_identifier(user_id),
    )
    try:
        session = await provider.mint_client_session(
            ctx,
            ClientSessionOptions(
                language=language, ttl_seconds=settings.SPEECH_SESSION_TTL_SECONDS
            ),
        )
    except SttProviderError as exc:
        # Fields go in the MESSAGE, not extra={}. The logging config installed
        # by jvspatial's configure_standard_logging renders the message only,
        # so anything passed via extra={} is silently dropped — which is how
        # the one line explaining a mint failure ("Turn detection is not
        # supported for this transcription model") never reached the log and
        # had to be recovered by replaying the call by hand.
        logger.warning(
            "speech session mint failed: error_code=%s user=%s workspace=%s "
            "provider=%s model=%s detail=%s",
            exc.code,
            user_id,
            workspace_id,
            cred.provider,
            cred.model,
            exc.message,
        )
        raise SpeechProviderFailure(exc.code, exc.message) from exc

    # Audit line per mint: who spent which workspace's key, on what model.
    # In the message, not extra={} — see the mint-failure warning above.
    logger.info(
        "speech session minted: user=%s workspace=%s provider=%s model=%s "
        "credential_source=%s",
        user_id,
        workspace_id,
        cred.provider,
        cred.model,
        cred.source,
    )
    return SpeechSessionResponse(
        engine=session.engine,
        transport=session.transport,
        connect_url=session.connect_url,
        client_secret=session.client_secret,
        expires_at=session.expires_at,
        params=dict(session.params),
        max_session_seconds=settings.SPEECH_MAX_SESSION_SECONDS,
    )


# ---------------------------------------------------------------------------
# integral_transcribe_audio — batch transcription of a stored attachment
# ---------------------------------------------------------------------------

_TRANSCRIBABLE_PREFIXES = ("audio/", "video/")


def _tool_error(code: str, detail: str) -> Dict[str, Any]:
    # Dispatch turns ``{"error": ...}`` into an error ToolResult — the shape
    # for refusals the agent should explain to the user rather than retry.
    return {"error": code, "detail": detail}


def _batch_provider(cred: Optional[SpeechCredential]) -> Optional[SttProvider]:
    if cred is None:
        return None
    try:
        provider = get_stt_provider(cred.provider)
    except KeyError:
        return None
    return provider if provider.capabilities.batch else None


async def _attachment_workspace_id(entry: Any, thread: Any) -> Optional[str]:
    if thread is not None:
        return getattr(thread, "workspace_id", "") or None
    track_id = getattr(entry, "track_id", "") if entry is not None else ""
    if not track_id:
        return None
    from app.models.nodes import Track

    track = await Track.get(track_id)
    return (getattr(track, "workspace_id", "") or None) if track else None


async def transcribe_attachment_for_agent(
    user_id: str,
    attachment_id: str,
    language: Optional[str] = None,
    max_chars: Optional[int] = None,
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Transcribe an audio attachment for the agent (``integral_transcribe_audio``).

    Read-only: nothing is persisted. Access is the attachment's own read gate
    (parent ``entry.read``, or thread ownership for chat uploads). The
    provider and key are the BOUND workspace's (PC-2) — ``workspace_id`` is
    injected by the dispatcher, never a tool argument — and the attachment
    must live in that workspace, so a conversation can't spend one
    workspace's key on another workspace's files.
    """
    from app.services.agent_scope import current_scope_workspace_id
    from app.services.attachment_agent import resolve_readable_attachment
    from app.services.attachment_storage import get_attachment_storage_service

    scope = workspace_id or current_scope_workspace_id.get()
    if not scope:
        return _tool_error(
            "no_workspace_scope", "Transcription needs a workspace in scope."
        )
    if language is not None:
        try:
            language = validate_language_tag(language)
        except ValueError as exc:
            return _tool_error("bad_language", str(exc))

    attachment, entry, thread = await resolve_readable_attachment(
        user_id, attachment_id
    )
    if await _attachment_workspace_id(entry, thread) != scope:
        raise PermissionError(
            "This conversation is scoped to another workspace; attachments "
            "cannot be read across workspaces. Switch workspace and ask again."
        )

    filename = getattr(attachment, "filename", "") or "audio"
    mime = (getattr(attachment, "mime_type", "") or "").split(";", 1)[0]
    mime = mime.strip().lower()
    if not mime.startswith(_TRANSCRIBABLE_PREFIXES):
        return _tool_error("not_audio", f"{filename} isn't an audio recording.")
    if (getattr(attachment, "scan_status", "") or "") == "blocked":
        return _tool_error("blocked", f"{filename} was blocked by the file scan.")

    if not allow("speech.transcribe", user_id, settings.SPEECH_TRANSCRIBE_RATE_LIMIT):
        return _tool_error(
            "rate_limited", "Too many transcriptions — try again in a few minutes."
        )

    cred = await resolve_speech_credential(scope)
    provider = _batch_provider(cred)
    if cred is None or provider is None:
        return _tool_error(
            "speech_provider_not_configured",
            "This workspace has no speech-to-text provider. The workspace owner "
            "can add a voice-input model under Settings → AI Models.",
        )

    max_bytes = settings.SPEECH_TRANSCRIBE_MAX_BYTES
    if provider.capabilities.max_batch_bytes:
        max_bytes = min(max_bytes, provider.capabilities.max_batch_bytes)
    too_large = _tool_error(
        "too_large", f"Audio over {max_bytes // (1024 * 1024)} MB can't be transcribed."
    )
    if (getattr(attachment, "size", 0) or 0) > max_bytes:
        return too_large
    # The local prefix gate admits every audio/* and video/*, but a provider
    # accepts a narrower set (OpenAI: only video/webm and video/mp4 among
    # video). Without this, a .mov was read from storage in full, uploaded,
    # and rejected by the vendor — burning bandwidth and quota to produce a
    # worse error than we can give here.
    accepted = provider.capabilities.batch_mime_types
    if accepted and mime not in accepted:
        return _tool_error(
            "unsupported_media",
            f"{filename} is a {mime} file, which this speech-to-text provider "
            "can't transcribe.",
        )
    audio = await get_attachment_storage_service().read_attachment(
        getattr(attachment, "storage_key", "") or ""
    )
    if not audio:
        return _tool_error("unavailable", f"{filename} couldn't be read from storage.")
    if len(audio) > max_bytes:
        return too_large

    try:
        transcript = await provider.transcribe(
            ProviderContext(
                provider=cred.provider,
                model=cred.model,
                api_key=cred.api_key,
                source=cred.source,
                safety_id=safety_identifier(user_id),
            ),
            audio,
            mime_type=mime,
            filename=filename,
            opts=TranscribeOptions(
                model=settings.SPEECH_BATCH_MODEL_DEFAULT,
                language=language,
                timeout_seconds=settings.SPEECH_TRANSCRIBE_TIMEOUT_SECONDS,
            ),
        )
    except SttProviderError as exc:
        return _tool_error(f"provider_{exc.code}", exc.message)

    # Clamp: max_chars is a model-supplied tool argument. Unbounded, a large
    # value defeats the context cap entirely; 0 falls through to the default
    # because it is falsy; a negative one slices from the end instead of
    # erroring. The ceiling is the same limit the attachment-text tool uses.
    ceiling = settings.ATTACHMENT_AGENT_TEXT_MAX_CHARS
    cap = ceiling if max_chars is None else max(1, min(int(max_chars), ceiling))
    text = transcript.text[:cap]
    logger.info(
        "speech attachment transcribed: user=%s workspace=%s attachment=%s "
        "provider=%s model=%s credential_source=%s",
        user_id,
        scope,
        attachment.id,
        transcript.provider,
        transcript.model,
        cred.source,
    )
    return {
        "_kind": "transcript",
        "attachment_id": attachment.id,
        "filename": filename,
        "mime_type": mime,
        "text": text,
        "char_count": len(text),
        "truncated": len(transcript.text) > cap,
        "language": transcript.language,
        "duration_seconds": transcript.duration_seconds,
        "provider": transcript.provider,
        "model": transcript.model,
        "content_untrusted": True,
    }
