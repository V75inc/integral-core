"""WhatsApp channel binding storage: encryption, ownership, and redaction."""

import json

import pytest

from app.api.errors import ResourceConflictError
from app.services.credential_crypto import decrypt_secret_from_storage


def _user_id(user) -> str:
    """Principal id stored on channel records."""
    return getattr(user, "user_id", None) or user.id


@pytest.fixture
def enc_key(monkeypatch):
    """32-byte hex credential key for this test."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "ab" * 32)
    return "ab" * 32


@pytest.mark.asyncio
async def test_create_stores_personal_binding_and_round_trips_secrets(
    enc_key, test_user
):
    """Encrypt both secrets with the binding id and keep them off the public view."""
    from app.agentive.services.whatsapp_channel_binding import (
        create_whatsapp_channel_binding,
        public_binding_view,
        read_api_key,
        read_webhook_secret,
        store_webhook_secret,
    )
    from app.services.personal_workspace import ensure_personal_workspace

    workspace = await ensure_personal_workspace(test_user)
    binding = await create_whatsapp_channel_binding(
        user_id=_user_id(test_user),
        jvconnect_base_url="https://connect.example",
        api_key="jvk_test_secret_value",
    )

    assert binding.workspace_id == workspace.id
    assert binding.jvconnect_base_url == "https://connect.example"
    assert binding.phone_number_id == ""
    assert binding.display_phone == ""
    assert binding.enabled is False
    assert binding.last_error == ""
    assert binding.api_key_ciphertext.startswith("v1:")
    assert "jvk_test_secret_value" not in binding.api_key_ciphertext
    assert read_api_key(binding) == "jvk_test_secret_value"
    assert (
        decrypt_secret_from_storage(binding.api_key_ciphertext, aad="someone-else")
        == ""
    )

    await store_webhook_secret(binding, "jvs_hook_secret")
    assert read_webhook_secret(binding) == "jvs_hook_secret"
    assert "jvs_hook_secret" not in binding.webhook_secret_ciphertext

    view = public_binding_view(binding)
    rendered = json.dumps(view)
    assert "jvk_" not in rendered
    assert "jvs_" not in rendered
    assert view["api_key_last4"] == "alue"

    from app.agentive.edges import HAS_WHATSAPP_CHANNEL_BINDING
    from app.services.permissions import get_user_node

    owner = await get_user_node(_user_id(test_user))
    linked = await owner.nodes(
        edge=[HAS_WHATSAPP_CHANNEL_BINDING],
        direction="out",
        node=["WhatsAppChannelBinding"],
    )
    assert [node.id for node in linked] == [binding.id]


@pytest.mark.asyncio
async def test_create_rejects_a_second_binding_for_the_same_user(enc_key, test_user):
    """One user gets one WhatsApp channel binding."""
    from app.agentive.services.whatsapp_channel_binding import (
        create_whatsapp_channel_binding,
    )

    await create_whatsapp_channel_binding(
        user_id=_user_id(test_user),
        jvconnect_base_url="https://connect.example",
        api_key="jvk_first_key",
    )
    with pytest.raises(ResourceConflictError):
        await create_whatsapp_channel_binding(
            user_id=_user_id(test_user),
            jvconnect_base_url="https://connect.example",
            api_key="jvk_second_key",
        )


@pytest.mark.asyncio
async def test_create_rolls_back_when_the_ownership_edge_fails(
    enc_key, test_user, monkeypatch
):
    """A failed ownership edge leaves no binding row."""
    from app.agentive.nodes import WhatsAppChannelBinding
    from app.agentive.services.whatsapp_channel_binding import (
        create_whatsapp_channel_binding,
    )

    async def fail_connect(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError("edge failed")

    monkeypatch.setattr("app.models.nodes.User.connect", fail_connect)

    with pytest.raises(RuntimeError, match="edge failed"):
        await create_whatsapp_channel_binding(
            user_id=_user_id(test_user),
            jvconnect_base_url="https://connect.example",
            api_key="jvk_orphan_key",
        )

    assert await WhatsAppChannelBinding.find(user_id=_user_id(test_user)) == []


@pytest.mark.asyncio
async def test_disable_keeps_ciphertext(enc_key, test_user):
    """Disabling the channel does not wipe the stored API key."""
    from app.agentive.nodes import WhatsAppChannelBinding
    from app.agentive.services.whatsapp_channel_binding import (
        create_whatsapp_channel_binding,
        disable_whatsapp_channel_binding,
    )

    created = await create_whatsapp_channel_binding(
        user_id=_user_id(test_user),
        jvconnect_base_url="https://connect.example",
        api_key="jvk_keep_me",
    )
    ciphertext = created.api_key_ciphertext
    disabled = await disable_whatsapp_channel_binding(_user_id(test_user))

    assert disabled.enabled is False
    assert disabled.api_key_ciphertext == ciphertext
    reloaded = await WhatsAppChannelBinding.get(created.id)
    assert reloaded is not None
    assert reloaded.enabled is False
    assert reloaded.api_key_ciphertext == ciphertext


@pytest.mark.asyncio
async def test_binding_api_returns_last4_and_hides_the_key(
    enc_key, authenticated_client
):
    """PUT and GET expose the key's last four characters only."""
    saved = await authenticated_client.put(
        "/api/agentive/channels/whatsapp/binding",
        json={
            "jvconnect_base_url": "https://connect.example",
            "api_key": "jvk_test_secret_value",
        },
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()["binding"]
    assert body["enabled"] is False
    assert body["api_key_last4"] == "alue"
    assert "api_key" not in body
    assert "jvk_test_secret_value" not in saved.text

    fetched = await authenticated_client.get("/api/agentive/channels/whatsapp/binding")
    assert fetched.status_code == 200
    assert fetched.json()["binding"]["api_key_last4"] == "alue"
    assert "jvk_test_secret_value" not in fetched.text


@pytest.mark.asyncio
async def test_binding_api_rejects_non_https_and_non_jvconnect_keys(
    enc_key, authenticated_client
):
    """Reject a non-https host and a key that is not a jvconnect key."""
    http_url = await authenticated_client.put(
        "/api/agentive/channels/whatsapp/binding",
        json={"jvconnect_base_url": "http://connect.example", "api_key": "jvk_ok"},
    )
    assert http_url.status_code == 400
    assert "jvk_ok" not in http_url.text

    bad_key = await authenticated_client.put(
        "/api/agentive/channels/whatsapp/binding",
        json={
            "jvconnect_base_url": "https://connect.example",
            "api_key": "meta-token",
        },
    )
    assert bad_key.status_code == 400
    assert "meta-token" not in bad_key.text


@pytest.mark.asyncio
async def test_delete_binding_is_owner_only(
    enc_key, authenticated_client, second_user_client
):
    """Another user cannot disable this binding; the owner can."""
    saved = await authenticated_client.put(
        "/api/agentive/channels/whatsapp/binding",
        json={
            "jvconnect_base_url": "https://connect.example",
            "api_key": "jvk_owner_key",
        },
    )
    assert saved.status_code == 200, saved.text
    binding_id = saved.json()["binding"]["id"]

    foreign = await second_user_client.delete(
        f"/api/agentive/channels/whatsapp/binding/{binding_id}"
    )
    assert foreign.status_code == 404

    disabled = await authenticated_client.delete(
        f"/api/agentive/channels/whatsapp/binding/{binding_id}"
    )
    assert disabled.status_code == 200
    assert disabled.json()["binding"]["enabled"] is False
    assert "jvk_owner_key" not in disabled.text


@pytest.mark.asyncio
async def test_binding_save_fails_when_encryption_is_unavailable(
    authenticated_client, monkeypatch
):
    """Refuse to store a key when credential encryption is not configured."""
    monkeypatch.setattr(
        "app.agentive.services.whatsapp_channel_binding.encryption_available",
        lambda: False,
    )
    monkeypatch.setattr(
        "app.agentive.services.whatsapp_channel_binding.encryption_unavailable_reason",
        lambda: "INTEGRAL_CREDENTIAL_ENC_KEY is not set",
    )
    saved = await authenticated_client.put(
        "/api/agentive/channels/whatsapp/binding",
        json={
            "jvconnect_base_url": "https://connect.example",
            "api_key": "jvk_should_not_persist",
        },
    )
    assert saved.status_code == 503
    assert "jvk_should_not_persist" not in saved.text
    fetched = await authenticated_client.get("/api/agentive/channels/whatsapp/binding")
    assert fetched.json()["binding"] is None
