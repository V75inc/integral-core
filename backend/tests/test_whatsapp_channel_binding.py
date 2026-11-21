"""WhatsApp channel binding storage: encryption, ownership, and redaction."""

import asyncio
import json

import pytest

from app.agentive.clients.jvconnect_whatsapp import JvconnectResult
from app.config import settings
from app.services.credential_crypto import decrypt_secret_from_storage


def _user_id(user) -> str:
    """Principal id stored on channel records."""
    return getattr(user, "user_id", None) or user.id


def _stub_jvconnect_account_failure(monkeypatch) -> None:
    """Account lookup fails closed so PUT does not open a socket."""

    async def fetch_account(*args: object, **kwargs):
        return JvconnectResult(
            ok=False,
            status_code=503,
            data={},
            error="account unavailable",
        )

    async def register_webhook(*args: object, **kwargs):
        raise AssertionError("register_webhook must not be called")

    monkeypatch.setattr(
        "app.agentive.clients.jvconnect_whatsapp.fetch_account",
        fetch_account,
    )
    monkeypatch.setattr(
        "app.agentive.clients.jvconnect_whatsapp.register_webhook",
        register_webhook,
    )
    monkeypatch.setattr(settings, "PUBLIC_API_BASE_URL", "")
    monkeypatch.setattr(settings, "DEBUG", False)


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
async def test_a_second_line_replaces_the_workspace_grant(enc_key, test_user):
    """A workspace keeps one line. A second create moves the grant."""
    from app.agentive.services.whatsapp_channel_binding import (
        binding_for_workspace,
        create_whatsapp_channel_binding,
    )

    first = await create_whatsapp_channel_binding(
        user_id=_user_id(test_user),
        jvconnect_base_url="https://connect.example",
        api_key="jvk_first_key",
    )
    second = await create_whatsapp_channel_binding(
        user_id=_user_id(test_user),
        jvconnect_base_url="https://connect.example",
        api_key="jvk_second_key",
    )

    assert first.id != second.id
    current = await binding_for_workspace(second.workspace_id)
    assert current is not None
    assert current.id == second.id
    assert first.enabled is False


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
    enc_key, authenticated_client, monkeypatch
):
    """A missing public URL or a failed account leaves the channel disabled."""
    _stub_jvconnect_account_failure(monkeypatch)
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
    enc_key, authenticated_client, second_user_client, monkeypatch
):
    """Another user cannot disable this binding; the owner can."""
    _stub_jvconnect_account_failure(monkeypatch)
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


def _stub_jvconnect_registration(
    monkeypatch, *, secret="whsec_registered", phone="109876543210"
):
    """Account and webhook registration succeed without a socket."""
    calls = {"register": []}

    async def fetch_account(*args: object, **kwargs):
        assert kwargs.get("client") is None
        return JvconnectResult(
            ok=True,
            status_code=200,
            data={
                "phone_number_id": phone,
                "display_phone_number": "+15550001111",
            },
            error="",
        )

    async def register_webhook(base_url, api_key, callback_url, *, client=None):
        assert client is None
        calls["register"].append(callback_url)
        return JvconnectResult(
            ok=True,
            status_code=200,
            data={"webhook_secret": secret},
            error="",
        )

    monkeypatch.setattr(
        "app.agentive.clients.jvconnect_whatsapp.fetch_account",
        fetch_account,
    )
    monkeypatch.setattr(
        "app.agentive.clients.jvconnect_whatsapp.register_webhook",
        register_webhook,
    )
    return calls


@pytest.mark.asyncio
async def test_put_registers_webhook_and_hides_secrets(
    enc_key, authenticated_client, monkeypatch
):
    """A mocked register stores the webhook secret and enables the channel."""
    from app.agentive.services.whatsapp_channel_binding import (
        get_whatsapp_channel_binding,
        read_webhook_secret,
    )

    calls = _stub_jvconnect_registration(monkeypatch)
    monkeypatch.setattr(settings, "PUBLIC_API_BASE_URL", "https://integral.example")
    monkeypatch.setattr(settings, "DEBUG", False)
    saved = await authenticated_client.put(
        "/api/agentive/channels/whatsapp/binding",
        json={
            "jvconnect_base_url": "https://connect.example",
            "api_key": "jvk_test_secret_value",
        },
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()["binding"]
    assert body["enabled"] is True
    assert body["phone_number_id"] == "109876543210"
    assert body["display_phone"] == "+15550001111"
    assert body["last_error"] == ""
    assert "api_key" not in body
    assert "webhook_secret" not in body
    assert "whsec_registered" not in saved.text
    assert "jvk_test_secret_value" not in saved.text
    assert calls["register"] == [
        "https://integral.example/api/agentive/channels/whatsapp/webhook"
    ]

    binding = await get_whatsapp_channel_binding(body["user_id"])
    assert binding is not None
    assert read_webhook_secret(binding) == "whsec_registered"

    fetched = await authenticated_client.get("/api/agentive/channels/whatsapp/binding")
    fetched_body = fetched.json()["binding"]
    assert "api_key" not in fetched_body
    assert "webhook_secret" not in fetched_body
    assert "whsec_registered" not in fetched.text
    assert "jvk_test_secret_value" not in fetched.text


@pytest.mark.asyncio
async def test_two_adds_cannot_replace_the_workspace_line(
    enc_key, authenticated_client, monkeypatch
):
    """Only one explicit Add may claim an empty workspace."""
    calls = _stub_jvconnect_registration(monkeypatch)
    monkeypatch.setattr(settings, "PUBLIC_API_BASE_URL", "https://integral.example")
    path = "/api/agentive/channels/whatsapp/binding"
    body = {
        "jvconnect_base_url": "https://connect.example",
        "api_key": "jvk_fixture_key",
        "expected_binding_id": None,
    }
    replies = await asyncio.gather(
        authenticated_client.put(path, json=body),
        authenticated_client.put(path, json=body),
    )
    assert sorted(reply.status_code for reply in replies) == [200, 409]
    winner = next(
        reply.json()["binding"] for reply in replies if reply.status_code == 200
    )
    stored = (await authenticated_client.get(path)).json()["binding"]
    assert stored["id"] == winner["id"]
    assert len(calls["register"]) == 1

    stale = await authenticated_client.put(
        path, json={**body, "expected_binding_id": "stale-binding"}
    )
    assert stale.status_code == 409
    assert len(calls["register"]) == 1

    updated = await authenticated_client.put(
        path, json={**body, "expected_binding_id": winner["id"]}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["binding"]["id"] == winner["id"]
    assert len(calls["register"]) == 1


@pytest.mark.asyncio
async def test_configure_cannot_replace_verified_line_with_a_different_number(
    enc_key, authenticated_client, monkeypatch
):
    calls = _stub_jvconnect_registration(monkeypatch, phone="fixture-first")
    monkeypatch.setattr(settings, "PUBLIC_API_BASE_URL", "https://integral.example")
    path = "/api/agentive/channels/whatsapp/binding"
    body = {
        "jvconnect_base_url": "https://connect.example",
        "api_key": "jvk_fixture_key",
        "expected_binding_id": None,
    }
    first = await authenticated_client.put(path, json=body)
    assert first.status_code == 200, first.text
    binding = first.json()["binding"]
    other_calls = _stub_jvconnect_registration(monkeypatch, phone="fixture-other")
    other = await authenticated_client.put(
        path, json={**body, "expected_binding_id": binding["id"]}
    )
    assert other.status_code == 409
    assert len(calls["register"]) == 1
    assert other_calls["register"] == []
    assert (await authenticated_client.get(path)).json()["binding"] == binding


@pytest.mark.asyncio
async def test_late_registration_cannot_restore_a_disconnected_workspace_line(
    enc_key, authenticated_client, monkeypatch
):
    _stub_jvconnect_registration(monkeypatch)
    monkeypatch.setattr(settings, "PUBLIC_API_BASE_URL", "https://integral.example")
    started, complete = asyncio.Event(), asyncio.Event()

    async def register_webhook(*args, **kwargs):
        started.set()
        await complete.wait()
        return JvconnectResult(
            ok=True, status_code=200, data={"webhook_secret": "whsec_fixture"}, error=""
        )

    monkeypatch.setattr(
        "app.agentive.clients.jvconnect_whatsapp.register_webhook", register_webhook
    )
    path = "/api/agentive/channels/whatsapp/binding"
    adding = asyncio.create_task(
        authenticated_client.put(
            path,
            json={
                "jvconnect_base_url": "https://connect.example",
                "api_key": "jvk_fixture_key",
                "expected_binding_id": None,
            },
        )
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        pending = (await authenticated_client.get(path)).json()["binding"]
        assert pending["enabled"] is False
        removed = await authenticated_client.delete(f"{path}/{pending['id']}")
        assert removed.status_code == 200
    finally:
        complete.set()
        result = await adding
    assert result.status_code == 409
    assert (await authenticated_client.get(path)).json()["binding"] is None


@pytest.mark.asyncio
async def test_put_without_public_url_stays_disabled_outside_debug(
    enc_key, authenticated_client, monkeypatch
):
    """An empty PUBLIC_API_BASE_URL outside DEBUG does not register a webhook."""
    calls = _stub_jvconnect_registration(monkeypatch)
    monkeypatch.setattr(settings, "PUBLIC_API_BASE_URL", "")
    monkeypatch.setattr(settings, "DEBUG", False)
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
    assert "PUBLIC_API_BASE_URL" in body["last_error"]
    assert calls["register"] == []
    assert "jvk_test_secret_value" not in saved.text


@pytest.mark.asyncio
async def test_second_put_updates_the_binding_and_registers(
    enc_key, authenticated_client, monkeypatch
):
    """Saving again replaces credentials and retries webhook registration."""
    from app.agentive.services.whatsapp_channel_binding import read_api_key

    calls = _stub_jvconnect_registration(monkeypatch)
    monkeypatch.setattr(settings, "PUBLIC_API_BASE_URL", "")
    monkeypatch.setattr(settings, "DEBUG", False)
    first = await authenticated_client.put(
        "/api/agentive/channels/whatsapp/binding",
        json={
            "jvconnect_base_url": "https://connect.example",
            "api_key": "jvk_test_secret_value",
        },
    )
    assert first.status_code == 200, first.text
    first_body = first.json()["binding"]
    assert first_body["enabled"] is False
    assert "PUBLIC_API_BASE_URL" in first_body["last_error"]

    monkeypatch.setattr(settings, "PUBLIC_API_BASE_URL", "https://integral.example")
    second = await authenticated_client.put(
        "/api/agentive/channels/whatsapp/binding",
        json={
            "jvconnect_base_url": "https://connect.example",
            "api_key": "jvk_replaced_secret",
        },
    )
    assert second.status_code == 200, second.text
    body = second.json()["binding"]
    assert body["id"] == first_body["id"]
    assert body["enabled"] is True
    assert body["last_error"] == ""
    assert body["api_key_last4"] == "cret"
    assert calls["register"] == [
        "https://integral.example/api/agentive/channels/whatsapp/webhook"
    ]
    assert "jvk_replaced_secret" not in second.text

    from app.agentive.services.whatsapp_channel_binding import (
        get_whatsapp_channel_binding,
    )

    stored = await get_whatsapp_channel_binding(body["user_id"])
    assert stored is not None
    assert read_api_key(stored) == "jvk_replaced_secret"


@pytest.mark.asyncio
async def test_second_admin_attaches_the_same_phone(
    enc_key, authenticated_client, second_user_client, monkeypatch
):
    """A second workspace admin with the same phone joins the existing line."""
    calls = _stub_jvconnect_registration(monkeypatch, phone="phone-shared")
    monkeypatch.setattr(settings, "PUBLIC_API_BASE_URL", "https://integral.example")
    monkeypatch.setattr(settings, "DEBUG", False)
    first = await authenticated_client.put(
        "/api/agentive/channels/whatsapp/binding",
        json={
            "jvconnect_base_url": "https://connect.example",
            "api_key": "jvk_first_owner",
        },
    )
    assert first.status_code == 200, first.text
    first_id = first.json()["binding"]["id"]
    assert first.json()["binding"]["enabled"] is True

    second = await second_user_client.put(
        "/api/agentive/channels/whatsapp/binding",
        json={
            "jvconnect_base_url": "https://connect.example",
            "api_key": "jvk_second_owner",
        },
    )
    assert second.status_code == 200, second.text
    assert second.json()["binding"]["id"] == first_id
    assert second.json()["binding"]["enabled"] is True
    assert "jvk_second_owner" not in second.text
    assert len(calls["register"]) == 1
    fetched = await second_user_client.get("/api/agentive/channels/whatsapp/binding")
    assert fetched.json()["binding"]["id"] == first_id
    assert fetched.json()["binding"]["enabled"] is True


@pytest.mark.asyncio
async def test_member_cannot_attach_a_line(enc_key, authenticated_client, monkeypatch):
    """A caller who is not an owner or admin cannot save the line."""

    async def not_admin(*args: object, **kwargs: object) -> bool:
        return False

    monkeypatch.setattr(
        "app.services.workspace_permissions.is_workspace_admin_or_owner",
        not_admin,
    )
    saved = await authenticated_client.put(
        "/api/agentive/channels/whatsapp/binding",
        json={
            "jvconnect_base_url": "https://connect.example",
            "api_key": "jvk_member_key",
        },
    )
    assert saved.status_code == 403
    assert "jvk_member_key" not in saved.text


@pytest.mark.asyncio
async def test_a_different_phone_stays_a_separate_line(
    enc_key, authenticated_client, second_user_client, monkeypatch
):
    """A key for another phone does not join the first line."""
    phones = {"jvk_first_owner": "phone-a", "jvk_second_owner": "phone-b"}

    async def fetch_account(base_url, api_key, *, client=None):
        return JvconnectResult(
            ok=True,
            status_code=200,
            data={
                "phone_number_id": phones[api_key],
                "display_phone_number": "+15550001111",
            },
            error="",
        )

    async def register_webhook(base_url, api_key, callback_url, *, client=None):
        return JvconnectResult(
            ok=True,
            status_code=200,
            data={"webhook_secret": "whsec_registered"},
            error="",
        )

    monkeypatch.setattr(
        "app.agentive.clients.jvconnect_whatsapp.fetch_account", fetch_account
    )
    monkeypatch.setattr(
        "app.agentive.clients.jvconnect_whatsapp.register_webhook", register_webhook
    )
    monkeypatch.setattr(settings, "PUBLIC_API_BASE_URL", "https://integral.example")
    first = await authenticated_client.put(
        "/api/agentive/channels/whatsapp/binding",
        json={
            "jvconnect_base_url": "https://connect.example",
            "api_key": "jvk_first_owner",
        },
    )
    second = await second_user_client.put(
        "/api/agentive/channels/whatsapp/binding",
        json={
            "jvconnect_base_url": "https://connect.example",
            "api_key": "jvk_second_owner",
        },
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["binding"]["id"] != second.json()["binding"]["id"]
    assert second.json()["binding"]["phone_number_id"] == "phone-b"
    still = await authenticated_client.get("/api/agentive/channels/whatsapp/binding")
    assert still.json()["binding"]["phone_number_id"] == "phone-a"


@pytest.mark.asyncio
async def test_disconnect_disables_the_line_only_after_the_last_grant(
    enc_key, test_user
):
    """One workspace can leave while another grant keeps the line enabled."""
    from app.agentive.nodes import WhatsAppChannelBinding
    from app.agentive.services.whatsapp_channel_binding import (
        create_whatsapp_channel_binding,
        disconnect_workspace_whatsapp_line,
        grant_whatsapp_line,
        granted_workspace_ids,
    )
    from app.models.edges import IS_MEMBER_OF
    from app.models.nodes import Workspace
    from app.services.app_graph import catalog_workspace
    from app.services.permissions import get_user_node
    from app.utils.time import utc_now_iso

    user_id = _user_id(test_user)
    binding = await create_whatsapp_channel_binding(
        user_id=user_id,
        jvconnect_base_url="https://connect.example",
        api_key="jvk_keep_me",
    )
    binding.enabled = True
    await binding.save()
    user = await get_user_node(user_id)
    now = utc_now_iso()
    org = await Workspace.create(
        kind="organization",
        name="Alpha Org",
        name_fold="alpha org",
        created_at=now,
        updated_at=now,
    )
    await catalog_workspace(org)
    await user.connect(org, edge=IS_MEMBER_OF, role="admin", joined_at=now)
    await grant_whatsapp_line(org.id, binding, user_id)

    await disconnect_workspace_whatsapp_line(user_id, org.id, binding.id)
    kept = await WhatsAppChannelBinding.get(binding.id)
    assert kept is not None
    assert kept.enabled is True
    assert binding.workspace_id in await granted_workspace_ids(kept)

    await disconnect_workspace_whatsapp_line(user_id, binding.workspace_id, binding.id)
    closed = await WhatsAppChannelBinding.get(binding.id)
    assert closed is not None
    assert closed.enabled is False
    assert await granted_workspace_ids(closed) == []
