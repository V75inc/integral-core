"""OpenAI speech-to-text adapter — request shapes and error mapping."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from app.agentive.services.speech.base import (
    ClientSessionOptions,
    ProviderContext,
    SttProviderError,
    TranscribeOptions,
)
from app.agentive.services.speech.providers.openai import (
    CLIENT_SECRETS_URL,
    REALTIME_CALLS_URL,
    TRANSCRIPTIONS_URL,
    OpenAISttProvider,
)

CTX = ProviderContext(
    provider="openai",
    model="gpt-live-transcribe",
    api_key="sk-test-secret-key",
    safety_id="safety-abc",
)


def _provider(handler) -> OpenAISttProvider:
    return OpenAISttProvider(transport=httpx.MockTransport(handler))


def _secret_response(**overrides):
    body = {"value": "ek_live_123", "expires_at": 1893456000, "session": {}}
    body.update(overrides)
    return httpx.Response(200, json=body)


@pytest.mark.asyncio
async def test_mint_posts_transcription_session_and_maps_secret():
    """Mint sends a transcription-only session and returns a WebRTC secret."""
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        await request.aread()
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["safety"] = request.headers.get("openai-safety-identifier")
        seen["body"] = json.loads(request.content)
        return _secret_response()

    session = await _provider(handler).mint_client_session(
        CTX, ClientSessionOptions(language="en-US", ttl_seconds=60)
    )

    assert seen["url"] == CLIENT_SECRETS_URL
    assert seen["auth"] == "Bearer sk-test-secret-key"
    assert seen["safety"] == "safety-abc"
    body = seen["body"]
    assert body["expires_after"] == {"anchor": "created_at", "seconds": 60}
    assert body["session"]["type"] == "transcription"
    transcription = body["session"]["audio"]["input"]["transcription"]
    assert transcription == {"model": "gpt-live-transcribe", "language": "en"}

    assert session.client_secret == "ek_live_123"
    assert session.transport == "webrtc"
    assert session.connect_url == REALTIME_CALLS_URL
    assert session.expires_at == datetime.fromtimestamp(1893456000, tz=timezone.utc)
    assert "ek_live_123" not in repr(session)


@pytest.mark.asyncio
async def test_mint_accepts_beta_nested_secret_shape():
    """The beta response nested the secret under ``client_secret``."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"client_secret": {"value": "ek_beta", "expires_at": 1893456000}},
        )

    session = await _provider(handler).mint_client_session(CTX, ClientSessionOptions())
    assert session.client_secret == "ek_beta"


@pytest.mark.asyncio
async def test_mint_clamps_ttl_and_omits_auto_language():
    """TTL is clamped to the provider minimum; ``auto`` sends no language."""
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        await request.aread()
        seen["body"] = json.loads(request.content)
        return _secret_response()

    await _provider(handler).mint_client_session(
        CTX, ClientSessionOptions(language="auto", ttl_seconds=1)
    )
    assert seen["body"]["expires_after"]["seconds"] == 10
    transcription = seen["body"]["session"]["audio"]["input"]["transcription"]
    assert "language" not in transcription


@pytest.mark.asyncio
async def test_mint_without_secret_is_unavailable():
    """A 200 without a secret is a provider failure, not a session."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"session": {}})

    with pytest.raises(SttProviderError) as err:
        await _provider(handler).mint_client_session(CTX, ClientSessionOptions())
    assert err.value.code == "unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "auth"),
        (403, "auth"),
        (429, "quota"),
        (500, "unavailable"),
        (400, "bad_request"),
    ],
)
async def test_mint_maps_http_errors(status, code):
    """Vendor HTTP errors map to provider-neutral codes."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            json={"error": {"message": "Incorrect API key provided: sk-te****key"}},
        )

    with pytest.raises(SttProviderError) as err:
        await _provider(handler).mint_client_session(CTX, ClientSessionOptions())
    assert err.value.code == code
    if code == "auth":
        # The vendor's auth message can quote part of the key — never echo it.
        assert "sk-te" not in err.value.message


@pytest.mark.asyncio
async def test_mint_timeout_maps_to_timeout():
    """A transport timeout is a retryable ``timeout``."""

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(SttProviderError) as err:
        await _provider(handler).mint_client_session(CTX, ClientSessionOptions())
    assert err.value.code == "timeout"
    assert err.value.retryable is True


@pytest.mark.asyncio
async def test_validate_credentials_reports_valid_and_invalid_keys():
    """Validation mints a minimum-TTL secret and reports the outcome."""

    async def ok(request: httpx.Request) -> httpx.Response:
        return _secret_response()

    async def rejected(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    assert await _provider(ok).validate_credentials(
        "sk-good", "gpt-live-transcribe"
    ) == (
        True,
        "validated",
    )
    assert await _provider(rejected).validate_credentials(
        "sk-bad", "gpt-live-transcribe"
    ) == (False, "invalid API key")


@pytest.mark.asyncio
async def test_transcribe_posts_multipart_and_parses_transcript():
    """Batch transcription sends a multipart upload and parses the result."""
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        await request.aread()
        seen["url"] = str(request.url)
        seen["content_type"] = request.headers["content-type"]
        seen["content"] = request.content
        return httpx.Response(
            200, json={"text": "hello world", "language": "en", "duration": 2.5}
        )

    transcript = await _provider(handler).transcribe(
        CTX,
        b"RIFF-fake-wav",
        mime_type="audio/wav",
        filename="memo.wav",
        opts=TranscribeOptions(model="gpt-transcribe", language="en-GB"),
    )

    assert seen["url"] == TRANSCRIPTIONS_URL
    assert seen["content_type"].startswith("multipart/form-data")
    assert b"gpt-transcribe" in seen["content"]
    assert b'name="languages"' in seen["content"]
    assert b"RIFF-fake-wav" in seen["content"]
    assert transcript.text == "hello world"
    assert transcript.language == "en"
    assert transcript.duration_seconds == 2.5
    assert transcript.model == "gpt-transcribe"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("audio", "mime"),
    [(b"x", "application/pdf"), (b"x" * (25 * 1024 * 1024 + 1), "audio/wav")],
)
async def test_transcribe_rejects_unsupported_type_and_oversize(audio, mime):
    """Unsupported types and oversize files fail before any HTTP call."""

    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    with pytest.raises(SttProviderError) as err:
        await _provider(handler).transcribe(
            CTX, audio, mime_type=mime, filename="f", opts=TranscribeOptions()
        )
    assert err.value.code == "bad_request"


def test_session_body_omits_turn_detection_for_models_that_reject_it():
    """The regression this whole fix turns on.

    Verified against the live API on 2026-09-11: ``gpt-live-transcribe``
    answers ``400 invalid_value "Turn detection is not supported for this
    transcription model."`` to BOTH ``server_vad`` and ``semantic_vad``, and
    mints normally when the field is absent. Because the default streaming
    model is exactly that model, dictation could never open a session — and
    these tests never caught it, since they mock the transport and the body
    was asserted nowhere.
    """
    from app.agentive.services.speech.providers.openai import OpenAISttProvider

    body = OpenAISttProvider.session_body("gpt-live-transcribe", None, 60)
    audio_input = body["session"]["audio"]["input"]
    assert "turn_detection" not in audio_input
    # The rest of the body is unchanged.
    assert audio_input["transcription"]["model"] == "gpt-live-transcribe"
    assert audio_input["noise_reduction"] == {"type": "near_field"}


def test_session_body_keeps_turn_detection_for_models_that_support_it():
    """The gate is per model, not a blanket removal."""
    from app.agentive.services.speech.providers.openai import OpenAISttProvider

    for model in ("gpt-transcribe", "gpt-4o-transcribe"):
        audio_input = OpenAISttProvider.session_body(model, None, 60)["session"][
            "audio"
        ]["input"]
        assert audio_input["turn_detection"] == {"type": "server_vad"}, model


def test_supports_turn_detection_predicate():
    from app.agentive.services.speech.providers.openai import supports_turn_detection

    assert supports_turn_detection("gpt-transcribe") is True
    assert supports_turn_detection("gpt-live-transcribe") is False
    assert supports_turn_detection("  gpt-live-transcribe  ") is False
    # Unknown models are assumed capable — the deny-set is what we verified.
    assert supports_turn_detection("some-future-model") is True


@pytest.mark.asyncio
async def test_mint_surfaces_turn_detection_flag_to_the_browser():
    """The browser engine keys its buffer-flush off this field.

    Without server VAD the provider sends no ``speech_started`` /
    ``speech_stopped``, so the client cannot tell when an utterance is in
    flight and must commit the audio buffer itself on stop. If this flag
    stopped being sent, dictation would mint fine and then silently drop the
    final utterance.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return _secret_response()

    session = await _provider(handler).mint_client_session(CTX, ClientSessionOptions())
    # CTX is gpt-live-transcribe — no server VAD.
    assert session.params["turn_detection"] is False

    capable = ProviderContext(
        provider="openai",
        model="gpt-transcribe",
        api_key="sk-test-secret-key",
        safety_id="safety-abc",
    )
    session2 = await _provider(handler).mint_client_session(
        capable, ClientSessionOptions()
    )
    assert session2.params["turn_detection"] is True


@pytest.mark.asyncio
async def test_mint_request_body_matches_the_model_capability():
    """End to end on the wire: the posted JSON is what OpenAI actually gets."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content.decode()))
        return _secret_response()

    await _provider(handler).mint_client_session(CTX, ClientSessionOptions())
    assert "turn_detection" not in seen["session"]["audio"]["input"]
