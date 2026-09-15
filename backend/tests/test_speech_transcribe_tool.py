"""integral_transcribe_audio — wiring, access, workspace scope, provider handling."""

from __future__ import annotations

import pytest

from app.agentive.services.speech import service as speech_service
from app.agentive.services.speech.base import SttProviderError, Transcript
from app.agentive.services.speech.providers.openai import OpenAISttProvider
from app.agentive.services.speech.rate_limit import reset_rate_limits
from app.agentive.tooling import bindings
from app.config import settings
from app.services import attachment_agent
from app.services.model_credential_resolver import SpeechCredential

WS = "ws-speech"
OWNER_KEY = "sk-owner-key-1234"
OWNER_CRED = SpeechCredential(
    provider="openai", model="gpt-live-transcribe", api_key=OWNER_KEY, source="byok"
)


class _Allow:
    allowed = True


class _Deny:
    allowed = False


async def _async(value):
    return value


class _Storage:
    async def read_attachment(self, storage_key):
        return b"RIFF-audio-bytes" if storage_key else None


@pytest.fixture
def speech_tool(monkeypatch):
    """Readable entries, an owner credential, fake storage and vendor."""
    reset_rate_limits()
    monkeypatch.setattr(
        attachment_agent, "policy_evaluate", lambda **kw: _async(_Allow())
    )

    async def resolve(workspace_id, *, touch=True):
        return OWNER_CRED

    monkeypatch.setattr(speech_service, "resolve_speech_credential", resolve)
    monkeypatch.setattr(
        "app.services.attachment_storage.get_attachment_storage_service",
        lambda: _Storage(),
    )
    calls = []

    async def transcribe(self, ctx, audio, *, mime_type, filename, opts):
        calls.append((ctx, audio, mime_type, filename, opts))
        return Transcript(
            text="hello from the memo",
            provider="openai",
            model=opts.model or ctx.model,
            language="en",
            duration_seconds=3.2,
        )

    monkeypatch.setattr(OpenAISttProvider, "transcribe", transcribe)
    yield calls
    reset_rate_limits()


async def _seed_audio(
    *,
    workspace_id: str = WS,
    mime: str = "audio/mp4",
    size: int = 1024,
    scan: str = "clean",
):
    from app.models.edges import CONTAINS, HAS_ATTACHMENT
    from app.models.nodes import Attachment, Entry, Track

    track = await Track.create(
        title="Calls", visibility="private", workspace_id=workspace_id
    )
    entry = await Entry.create(title="Call notes", track_id=track.id)
    await track.connect(entry, edge=CONTAINS)
    att = await Attachment.create(
        filename="memo.m4a",
        mime_type=mime,
        size=size,
        source_type="file",
        storage_key="k-audio",
        scan_status=scan,
    )
    await entry.connect(att, edge=HAS_ATTACHMENT)
    return att


async def _transcribe(att, **kwargs):
    return await speech_service.transcribe_attachment_for_agent(
        user_id="o.User.test", attachment_id=att.id, workspace_id=WS, **kwargs
    )


def test_tool_is_bound_to_the_speech_service():
    """The manifest tool resolves to the speech service with a narrow arg map."""
    binding = bindings.TOOL_BINDINGS["integral_transcribe_audio"]
    assert binding.service_ref() is speech_service.transcribe_attachment_for_agent
    forwarded = binding.service_param_map(
        {"attachment_id": "a", "language": "en", "workspace_id": "other"}
    )
    # workspace_id is never forwarded — the dispatcher injects the bound scope.
    assert forwarded == {"attachment_id": "a", "language": "en"}


def test_tool_is_in_the_catalogue():
    """The read tool is dispatchable."""
    import app.models.nodes  # noqa: F401 — register node classes
    from app.agentive.tooling.catalogue import build_tool_catalogue

    assert "integral_transcribe_audio" in {
        t.get("name") for t in build_tool_catalogue()
    }


@pytest.mark.asyncio
async def test_external_agents_with_read_scope_see_the_tool():
    """An MCP client granted only ``integral:read`` can call it (op_class read)."""
    import app.models.nodes  # noqa: F401 — register node classes
    from app.agentive.mcp.server import _list_tools_impl

    names = {tool.name for tool in await _list_tools_impl(["integral:read"])}
    assert "integral_transcribe_audio" in names


@pytest.mark.asyncio
async def test_transcribes_audio_in_the_bound_workspace(speech_tool):
    """Happy path: bytes go to the vendor under the owner's key; text comes back."""
    att = await _seed_audio()
    out = await _transcribe(att, language="en-GB")

    assert out["text"] == "hello from the memo"
    assert out["content_untrusted"] is True
    assert out["duration_seconds"] == 3.2
    assert out["model"] == settings.SPEECH_BATCH_MODEL_DEFAULT
    assert OWNER_KEY not in str(out)

    ctx, audio, mime, filename, opts = speech_tool[0]
    assert ctx.api_key == OWNER_KEY
    assert audio == b"RIFF-audio-bytes"
    assert mime == "audio/mp4"
    assert filename == "memo.m4a"
    assert opts.language == "en-GB"


@pytest.mark.asyncio
async def test_caps_the_transcript(speech_tool):
    """``max_chars`` truncates and says so."""
    att = await _seed_audio()
    out = await _transcribe(att, max_chars=5)
    assert out["text"] == "hello"
    assert out["truncated"] is True


@pytest.mark.asyncio
async def test_refuses_an_attachment_from_another_workspace(speech_tool):
    """PC-2: a conversation can't spend its key on another workspace's files."""
    att = await _seed_audio(workspace_id="ws-elsewhere")
    with pytest.raises(PermissionError):
        await _transcribe(att)
    assert speech_tool == []


@pytest.mark.asyncio
async def test_refuses_when_the_parent_entry_is_unreadable(speech_tool, monkeypatch):
    """The attachment's own read gate applies."""
    monkeypatch.setattr(
        attachment_agent, "policy_evaluate", lambda **kw: _async(_Deny())
    )
    att = await _seed_audio()
    with pytest.raises((PermissionError, ValueError)):
        await _transcribe(att)
    assert speech_tool == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("seed", "code"),
    [
        ({"mime": "application/pdf"}, "not_audio"),
        ({"size": 10**12}, "too_large"),
    ],
)
async def test_refuses_unsuitable_files(speech_tool, seed, code):
    """Non-audio and oversize files never reach the vendor."""
    att = await _seed_audio(**seed)
    out = await _transcribe(att)
    assert out["error"] == code
    assert speech_tool == []


@pytest.mark.asyncio
async def test_scan_blocked_files_read_as_not_found(speech_tool):
    """A blocked file is invisible to agents, so it can't be transcribed."""
    att = await _seed_audio(scan="blocked")
    with pytest.raises(ValueError, match="not found"):
        await _transcribe(att)
    assert speech_tool == []


@pytest.mark.asyncio
async def test_reports_a_missing_provider(speech_tool, monkeypatch):
    """No provider: a clean, explainable error."""

    async def none(workspace_id, *, touch=True):
        return None

    monkeypatch.setattr(speech_service, "resolve_speech_credential", none)
    att = await _seed_audio()
    out = await _transcribe(att)
    assert out["error"] == "speech_provider_not_configured"
    assert "AI Models" in out["detail"]


@pytest.mark.asyncio
async def test_reports_vendor_failures(speech_tool, monkeypatch):
    """A vendor rejection becomes ``provider_<code>``."""

    async def rejected(self, ctx, audio, *, mime_type, filename, opts):
        raise SttProviderError("auth", "OpenAI rejected the API key")

    monkeypatch.setattr(OpenAISttProvider, "transcribe", rejected)
    att = await _seed_audio()
    out = await _transcribe(att)
    assert out["error"] == "provider_auth"


@pytest.mark.asyncio
async def test_is_rate_limited_per_user(speech_tool, monkeypatch):
    """Transcriptions spend quota, so they are rate limited per user."""
    monkeypatch.setattr(settings, "SPEECH_TRANSCRIBE_RATE_LIMIT", "1/300")
    att = await _seed_audio()
    assert "text" in await _transcribe(att)
    assert (await _transcribe(att))["error"] == "rate_limited"


@pytest.mark.asyncio
async def test_rejects_a_bad_language_tag(speech_tool):
    """Tool args are validated like the REST boundary."""
    att = await _seed_audio()
    out = await _transcribe(att, language="not a tag!")
    assert out["error"] == "bad_language"


# ---------------------------------------------------------------------------
# max_chars is a model-supplied tool argument (finding 1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_chars_is_clamped_to_the_context_ceiling(speech_tool, monkeypatch):
    """A model asking for more than the cap must not get more than the cap."""
    monkeypatch.setattr(settings, "ATTACHMENT_AGENT_TEXT_MAX_CHARS", 5)
    att = await _seed_audio()
    out = await _transcribe(att, max_chars=10_000_000)
    assert out["char_count"] == 5
    assert out["truncated"] is True


@pytest.mark.asyncio
async def test_max_chars_zero_does_not_mean_default(speech_tool):
    """``0`` used to be falsy and silently fell through to the full default."""
    att = await _seed_audio()
    out = await _transcribe(att, max_chars=0)
    assert out["char_count"] == 1
    assert out["text"] == "h"


@pytest.mark.asyncio
async def test_negative_max_chars_does_not_slice_from_the_end(speech_tool):
    """``text[:-5]`` silently returned the transcript minus its last words."""
    att = await _seed_audio()
    out = await _transcribe(att, max_chars=-5)
    assert out["text"] == "h"
    assert out["char_count"] == 1


@pytest.mark.asyncio
async def test_max_chars_none_uses_the_default(speech_tool):
    att = await _seed_audio()
    out = await _transcribe(att)
    assert out["text"] == "hello from the memo"
    assert out["truncated"] is False


# ---------------------------------------------------------------------------
# Provider MIME allowlist is checked before we read storage (finding 2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unsupported_media_refused_without_calling_the_vendor(speech_tool):
    """The local gate admits every video/*; OpenAI's batch set does not.

    A .mov used to be read from storage in full and uploaded just to come back
    rejected — burning bandwidth and the workspace's quota to produce a worse
    error than we can give locally.
    """
    att = await _seed_audio(mime="video/quicktime")
    out = await _transcribe(att)
    assert out.get("error") is True or "error" in out, out
    assert "quicktime" in str(out).lower() or "transcribe" in str(out).lower()
    assert speech_tool == [], "the vendor must not be called for a refused type"


@pytest.mark.asyncio
async def test_supported_video_type_still_transcribes(speech_tool):
    """The check is an allowlist intersection, not a blanket video ban."""
    att = await _seed_audio(mime="video/webm")
    out = await _transcribe(att)
    assert out["text"] == "hello from the memo"
    assert len(speech_tool) == 1
