"""OpenAI speech-to-text adapter.

Streaming: a transcription-only Realtime session. The backend mints a
short-lived client secret (``POST /v1/realtime/client_secrets``); the browser
opens the session over WebRTC by POSTing its SDP offer to
``/v1/realtime/calls`` with that secret. Batch: ``/v1/audio/transcriptions``.

Every URL is a constant — nothing here is caller-supplied, so there is no
outbound-URL (SSRF) surface.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

import httpx

from app.agentive.services.speech.base import (
    ClientSession,
    ClientSessionOptions,
    CredentialSource,
    ProviderContext,
    SttProviderCapabilities,
    SttProviderError,
    TranscribeOptions,
    Transcript,
    primary_language_subtag,
)

OPENAI_ORIGIN = "https://api.openai.com"
_API = f"{OPENAI_ORIGIN}/v1"
CLIENT_SECRETS_URL = f"{_API}/realtime/client_secrets"
REALTIME_CALLS_URL = f"{_API}/realtime/calls"
TRANSCRIPTIONS_URL = f"{_API}/audio/transcriptions"

# Batch models that take ``languages`` (a list) instead of ``language``.
_LANGUAGES_LIST_MODELS = frozenset({"gpt-transcribe"})

# Streaming models that reject ANY ``turn_detection`` block on the realtime
# client-secrets endpoint. Verified against the live API on 2026-09-11:
# ``server_vad`` and ``semantic_vad`` both return
#   400 invalid_value "Turn detection is not supported for this transcription
#   model."
# while omitting the field mints normally. ``gpt-transcribe`` and
# ``gpt-4o-transcribe`` accept it, so this is per-model, not global.
#
# This shipped broken: the default streaming model and the unconditional
# ``turn_detection`` below landed in the same commit, and the provider tests
# use ``httpx.MockTransport``, so nothing ever sent this body to OpenAI.
_NO_TURN_DETECTION_MODELS = frozenset({"gpt-live-transcribe"})


def supports_turn_detection(model: str) -> bool:
    """True when ``model`` accepts a server-side turn-detection block.

    Callers also surface this to the browser (see ``mint_client_session``):
    without server VAD there are no ``input_audio_buffer.speech_started`` /
    ``speech_stopped`` events, and the client engine has to flush its audio
    buffer itself rather than waiting for one.
    """
    return (model or "").strip() not in _NO_TURN_DETECTION_MODELS


_BATCH_MIME_TYPES = frozenset(
    {
        "audio/mpeg",
        "audio/mp3",
        "audio/mpga",
        "audio/mp4",
        "audio/m4a",
        "audio/x-m4a",
        "audio/wav",
        "audio/x-wav",
        "audio/wave",
        "audio/webm",
        "video/webm",
        "video/mp4",
    }
)
_MAX_BATCH_BYTES = 25 * 1024 * 1024
_MIN_TTL_SECONDS = 10  # provider minimum for client secrets
_MINT_TIMEOUT_SECONDS = 15.0
_PROVIDER_MESSAGE_MAX = 200


def _json_object(resp: httpx.Response) -> Dict[str, Any]:
    try:
        data = resp.json()
    except ValueError as exc:
        raise SttProviderError(
            "unavailable", "OpenAI returned a non-JSON response"
        ) from exc
    if not isinstance(data, dict):
        raise SttProviderError("unavailable", "OpenAI returned an unexpected response")
    return data


def _provider_message(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return ""
    err = body.get("error") if isinstance(body, dict) else None
    msg = err.get("message") if isinstance(err, dict) else None
    return str(msg or "")[:_PROVIDER_MESSAGE_MAX]


def _raise_for_status(resp: httpx.Response) -> None:
    status = resp.status_code
    if status < 400:
        return
    if status in (401, 403):
        # Never echo the vendor's auth message: it can quote part of the key.
        raise SttProviderError("auth", "OpenAI rejected the API key")
    if status == 429:
        raise SttProviderError(
            "quota", "OpenAI rate limit or quota exceeded", retryable=True
        )
    if status >= 500:
        raise SttProviderError(
            "unavailable", f"OpenAI returned HTTP {status}", retryable=True
        )
    detail = _provider_message(resp)
    raise SttProviderError("bad_request", detail or f"OpenAI returned HTTP {status}")


class OpenAISttProvider:
    """Speech-to-text through OpenAI — live (WebRTC) and batch."""

    id = "openai"
    display_name = "OpenAI"
    credential_source: CredentialSource = "model_credentials"
    capabilities = SttProviderCapabilities(
        streaming=True,
        batch=True,
        client_engine="openai-realtime",
        browser_connect_origins=(OPENAI_ORIGIN,),
        max_batch_bytes=_MAX_BATCH_BYTES,
        batch_mime_types=_BATCH_MIME_TYPES,
    )

    def __init__(self, *, transport: Optional[httpx.AsyncBaseTransport] = None):
        # ``transport`` is a test seam (``httpx.MockTransport``).
        self._transport = transport

    @staticmethod
    def _headers(api_key: str, safety_id: Optional[str]) -> Dict[str, str]:
        headers = {"Authorization": f"Bearer {api_key}"}
        if safety_id:
            headers["OpenAI-Safety-Identifier"] = safety_id
        return headers

    @staticmethod
    def session_body(
        model: str, language: Optional[str], ttl_seconds: int
    ) -> Dict[str, Any]:
        transcription: Dict[str, Any] = {"model": model}
        if language:
            transcription["language"] = language
        audio_input: Dict[str, Any] = {
            "transcription": transcription,
            "noise_reduction": {"type": "near_field"},
        }
        # Only for models that support it — sending it to one that does not
        # fails the whole mint with a 400 (see _NO_TURN_DETECTION_MODELS).
        if supports_turn_detection(model):
            audio_input["turn_detection"] = {"type": "server_vad"}
        return {
            "expires_after": {"anchor": "created_at", "seconds": ttl_seconds},
            "session": {
                "type": "transcription",
                "audio": {"input": audio_input},
            },
        }

    async def _post(self, url: str, *, timeout: float, **kwargs: Any) -> httpx.Response:
        try:
            async with httpx.AsyncClient(
                timeout=timeout, transport=self._transport
            ) as client:
                resp = await client.post(url, **kwargs)
        except httpx.TimeoutException as exc:
            raise SttProviderError(
                "timeout", "OpenAI did not respond in time", retryable=True
            ) from exc
        except httpx.HTTPError as exc:
            raise SttProviderError(
                "unavailable", "Could not reach OpenAI", retryable=True
            ) from exc
        _raise_for_status(resp)
        return resp

    async def mint_client_session(
        self, ctx: ProviderContext, opts: ClientSessionOptions
    ) -> ClientSession:
        ttl = max(_MIN_TTL_SECONDS, int(opts.ttl_seconds))
        language = primary_language_subtag(opts.language)
        resp = await self._post(
            CLIENT_SECRETS_URL,
            timeout=_MINT_TIMEOUT_SECONDS,
            headers=self._headers(ctx.api_key, ctx.safety_id),
            json=self.session_body(ctx.model, language, ttl),
        )
        data = _json_object(resp)
        # GA returns ``value`` / ``expires_at`` at the top level; the beta
        # nested them under ``client_secret``. Accept either.
        nested = data.get("client_secret")
        nested = nested if isinstance(nested, dict) else {}
        secret = data.get("value") or nested.get("value")
        if not isinstance(secret, str) or not secret:
            raise SttProviderError("unavailable", "OpenAI returned no client secret")
        expires = data.get("expires_at") or nested.get("expires_at")
        if isinstance(expires, (int, float)):
            expires_at = datetime.fromtimestamp(expires, tz=timezone.utc)
        else:
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)
        return ClientSession(
            engine="openai-realtime",
            transport="webrtc",
            connect_url=REALTIME_CALLS_URL,
            client_secret=secret,
            expires_at=expires_at,
            params={
                "model": ctx.model,
                "language": language,
                # The browser engine keys its buffer-flush off this: with no
                # server VAD it never receives speech_started/stopped.
                "turn_detection": supports_turn_detection(ctx.model),
            },
        )

    async def validate_credentials(self, api_key: str, model: str) -> Tuple[bool, str]:
        # Minting a real (minimum-TTL) transcription secret is the exact
        # capability voice input needs — a models list would pass keys that
        # lack Realtime access.
        ctx = ProviderContext(provider=self.id, model=model, api_key=api_key)
        try:
            await self.mint_client_session(
                ctx, ClientSessionOptions(ttl_seconds=_MIN_TTL_SECONDS)
            )
        except SttProviderError as exc:
            return False, "invalid API key" if exc.code == "auth" else exc.message
        return True, "validated"

    async def transcribe(
        self,
        ctx: ProviderContext,
        audio: bytes,
        *,
        mime_type: str,
        filename: str,
        opts: TranscribeOptions,
    ) -> Transcript:
        mime = (mime_type or "").split(";", 1)[0].strip().lower()
        if mime not in _BATCH_MIME_TYPES:
            raise SttProviderError(
                "bad_request", f"OpenAI cannot transcribe {mime or 'this file type'}"
            )
        if len(audio) > _MAX_BATCH_BYTES:
            raise SttProviderError(
                "bad_request", "Audio is larger than OpenAI's 25 MB limit"
            )
        model = opts.model or ctx.model
        language = primary_language_subtag(opts.language)
        form: Dict[str, Any] = {"model": model, "response_format": "json"}
        if language:
            if model in _LANGUAGES_LIST_MODELS:
                form["languages"] = [language]
            else:
                form["language"] = language
        resp = await self._post(
            TRANSCRIPTIONS_URL,
            timeout=opts.timeout_seconds,
            headers=self._headers(ctx.api_key, ctx.safety_id),
            data=form,
            files={"file": (filename or "audio", audio, mime)},
        )
        data = _json_object(resp)
        text = data.get("text")
        if not isinstance(text, str):
            raise SttProviderError("unavailable", "OpenAI returned no transcript")
        duration = data.get("duration")
        usage = data.get("usage")
        if duration is None and isinstance(usage, dict):
            if usage.get("type") == "duration":
                duration = usage.get("seconds")
        detected = data.get("language")
        return Transcript(
            text=text,
            provider=self.id,
            model=model,
            language=detected if isinstance(detected, str) else language,
            duration_seconds=(
                float(duration) if isinstance(duration, (int, float)) else None
            ),
        )
