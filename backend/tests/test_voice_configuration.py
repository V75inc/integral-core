"""Saved voice providers are independent of chat and follow live key ownership."""

import base64

import pytest

from app.api.errors import InsufficientPermissionsError, ResourceConflictError
from app.schemas.voice_configuration import VoiceModelWrite
from app.services import voice_configuration as voice
from app.services.credential_crypto import encrypt_secret_for_storage
from app.services.retrieval import embedding_configuration as embedding
from tests.test_embedding_configuration import fixture

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]


async def test_voice_uses_saved_provider_without_default_chat_and_key_rotation(
    monkeypatch,
):
    from app.config import settings

    monkeypatch.setattr(settings, "INTEGRAL_AGENT_KEY_MODE", "hybrid")
    monkeypatch.setenv(
        "INTEGRAL_CREDENTIAL_ENC_KEY", base64.urlsafe_b64encode(b"v" * 32).decode()
    )
    user, workspace, connection, _ = await fixture()
    connection.provider, connection.base_url = "openai", ""
    connection.api_key_enc = encrypt_secret_for_storage(
        "synthetic-voice-key", aad=user.user_id
    )
    await connection.save()
    body = VoiceModelWrite(
        mode="provider",
        connection_id=connection.id,
        model="gpt-4o-mini-transcribe",
        revision=0,
    )
    with pytest.raises(InsufficientPermissionsError):
        await voice.save("other", workspace.id, body)
    result = await voice.save(user.user_id, workspace.id, body)
    assert result["effective_model"] == "gpt-4o-mini-transcribe"
    from app.services.model_credential_resolver import resolve_speech_credential

    assert (
        await resolve_speech_credential(workspace.id)
    ).api_key == "synthetic-voice-key"
    connection.api_key_enc = encrypt_secret_for_storage(
        "rotated-voice-key", aad=user.user_id
    )
    await connection.save()
    assert (
        await resolve_speech_credential(workspace.id)
    ).api_key == "rotated-voice-key"
    with pytest.raises(ResourceConflictError):
        await voice.save(user.user_id, workspace.id, body)
    from app.services.ai_configuration import remove_item

    with pytest.raises(ValueError, match="Reassign"):
        await remove_item(user.user_id, connection.id, connection=True)
    await voice.save(
        user.user_id, workspace.id, VoiceModelWrite(mode="disabled", revision=1)
    )
    assert await resolve_speech_credential(workspace.id) is None


async def test_voice_refuses_wrong_provider_or_foreign_connection(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "INTEGRAL_AGENT_KEY_MODE", "hybrid")
    user, workspace, connection, _ = await fixture()
    body = VoiceModelWrite(
        mode="provider", connection_id=connection.id, model="transcribe", revision=0
    )
    with pytest.raises(ValueError, match="OpenAI"):
        await voice.save(user.user_id, workspace.id, body)
    connection.user_id = "foreign"
    await connection.save()
    with pytest.raises(ValueError, match="owned provider"):
        await voice.save(user.user_id, workspace.id, body)
