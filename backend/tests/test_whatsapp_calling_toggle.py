"""WhatsApp calling toggle on the workspace line."""

import pytest

from app.agentive.clients.jvconnect_whatsapp import JvconnectResult
from app.config import settings

_BINDING = "/api/agentive/channels/whatsapp/binding"
_CALLING = "/api/agentive/channels/whatsapp/binding/calling"


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

    async def fetch_calling_settings(*args: object, **kwargs):
        return JvconnectResult(
            ok=True, status_code=200, data={"status": "DISABLED"}, error=""
        )

    async def set_calling_settings(base_url, api_key, enabled, **kwargs):
        return JvconnectResult(
            ok=True,
            status_code=200,
            data={"status": "ENABLED" if enabled else "DISABLED"},
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
    monkeypatch.setattr(
        "app.agentive.clients.jvconnect_whatsapp.fetch_calling_settings",
        fetch_calling_settings,
    )
    monkeypatch.setattr(
        "app.agentive.clients.jvconnect_whatsapp.set_calling_settings",
        set_calling_settings,
    )
    monkeypatch.setattr(settings, "PUBLIC_API_BASE_URL", "")
    monkeypatch.setattr(settings, "DEBUG", False)


@pytest.fixture
def enc_key(monkeypatch):
    """32-byte hex credential key for this test."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "ab" * 32)
    return "ab" * 32


async def _save_line(client):
    saved = await client.put(
        _BINDING,
        json={
            "jvconnect_base_url": "https://connect.example",
            "api_key": "jvk_test_secret_value",
        },
    )
    assert saved.status_code == 200, saved.text
    return saved.json()["binding"]


async def _edge(binding):
    from app.agentive.services.whatsapp_channel_binding import workspace_line_grant

    return await workspace_line_grant(binding["workspace_id"], binding["id"])


def _calling_body(edge, *, enabled: bool, revision: int, grant_id: str | None = None):
    return {
        "calling_enabled": enabled,
        "expected_grant_id": edge.id if grant_id is None else grant_id,
        "expected_revision": revision,
    }


@pytest.mark.asyncio
async def test_get_reports_calling_off_on_the_workspace_edge(
    enc_key, authenticated_client, monkeypatch
):
    """A saved line starts with calling off, keyed by the edge id."""
    _stub_jvconnect_account_failure(monkeypatch)
    saved = await _save_line(authenticated_client)
    edge = await _edge(saved)
    assert edge is not None
    assert edge.calling_enabled is False
    assert edge.revision == 0

    fetched = await authenticated_client.get(_BINDING)
    assert fetched.status_code == 200, fetched.text
    body = fetched.json()["binding"]
    grant = fetched.json()["grant"]
    assert body["calling_enabled"] is False
    assert body["calling_grant_id"] == edge.id
    assert body["calling_grant_id"] != body["id"]
    assert body["calling_grant_id"] != grant["id"]
    assert body["calling_revision"] == 0
    assert "calling_enabled" not in grant
    assert edge.enabled is True


@pytest.mark.asyncio
async def test_put_toggles_calling_and_ignores_a_repeated_value(
    enc_key, authenticated_client, monkeypatch
):
    """Calling flips the edge boolean and revision, and a repeat does not."""
    _stub_jvconnect_account_failure(monkeypatch)
    saved = await _save_line(authenticated_client)
    edge = await _edge(saved)
    text_enabled = saved["enabled"]
    events = []

    async def capture(**kwargs):
        events.append(kwargs)

    monkeypatch.setattr("app.agentive.api.channels.emit_change_event", capture)

    same = await authenticated_client.put(
        _CALLING, json=_calling_body(edge, enabled=False, revision=0)
    )
    assert same.status_code == 200, same.text
    assert same.json()["binding"]["calling_enabled"] is False
    assert same.json()["binding"]["calling_revision"] == 0
    assert events == []
    assert (await _edge(saved)).revision == 0

    turned_on = await authenticated_client.put(
        _CALLING, json=_calling_body(edge, enabled=True, revision=0)
    )
    assert turned_on.status_code == 200, turned_on.text
    assert turned_on.json()["binding"]["calling_enabled"] is True
    assert turned_on.json()["binding"]["calling_revision"] == 1
    fetched = await authenticated_client.get(_BINDING)
    assert fetched.json()["binding"]["calling_enabled"] is True
    assert fetched.json()["binding"]["calling_revision"] == 1
    assert fetched.json()["binding"]["enabled"] is text_enabled
    assert len(events) == 1
    assert events[0]["actor_kind"] == "human"
    assert events[0]["action"] == "whatsapp_channel_calling.update"
    assert events[0]["resource_type"] == "UsesWhatsAppLine"
    assert events[0]["resource_id"] == edge.id
    assert events[0]["scope"] == f"user:{saved['user_id']}"
    assert events[0]["actor_id"] == saved["user_id"]
    assert events[0]["before"] == {"calling_enabled": False, "revision": 0}
    assert events[0]["after"] == {"calling_enabled": True, "revision": 1}

    again = await authenticated_client.put(
        _CALLING, json=_calling_body(edge, enabled=True, revision=1)
    )
    assert again.status_code == 200, again.text
    assert again.json()["binding"]["calling_enabled"] is True
    assert again.json()["binding"]["calling_revision"] == 1
    assert len(events) == 1

    turned_off = await authenticated_client.put(
        _CALLING, json=_calling_body(edge, enabled=False, revision=1)
    )
    assert turned_off.status_code == 200, turned_off.text
    assert turned_off.json()["binding"]["calling_enabled"] is False
    assert turned_off.json()["binding"]["calling_revision"] == 2
    fetched_off = await authenticated_client.get(_BINDING)
    assert fetched_off.json()["binding"]["calling_enabled"] is False
    assert fetched_off.json()["binding"]["calling_revision"] == 2
    assert fetched_off.json()["binding"]["enabled"] is text_enabled
    stored = await _edge(saved)
    assert stored.calling_enabled is False
    assert stored.revision == 2
    assert stored.enabled is True
    assert events[1]["before"] == {"calling_enabled": True, "revision": 1}
    assert events[1]["after"] == {"calling_enabled": False, "revision": 2}
    rendered = str(events)
    assert "JVVOICE" not in rendered
    assert "LIVEKIT" not in rendered
    assert "jvvoice" not in rendered
    assert "livekit" not in rendered


@pytest.mark.asyncio
async def test_non_admin_cannot_change_calling(
    enc_key, authenticated_client, monkeypatch
):
    """A caller who is not an owner or admin receives 403 and writes nothing."""
    _stub_jvconnect_account_failure(monkeypatch)
    saved = await _save_line(authenticated_client)
    edge = await _edge(saved)

    async def not_admin(*args: object, **kwargs: object) -> bool:
        return False

    monkeypatch.setattr(
        "app.services.workspace_permissions.is_workspace_admin_or_owner",
        not_admin,
    )
    denied = await authenticated_client.put(
        _CALLING, json=_calling_body(edge, enabled=True, revision=0)
    )
    assert denied.status_code == 403
    assert "Only workspace owners and admins can manage WhatsApp" in denied.text
    stored = await _edge(saved)
    assert stored.calling_enabled is False
    assert stored.revision == 0


@pytest.mark.asyncio
async def test_stale_grant_is_rejected(enc_key, authenticated_client, monkeypatch):
    """A stale revision or a different grant id is a conflict and does not write."""
    _stub_jvconnect_account_failure(monkeypatch)
    saved = await _save_line(authenticated_client)
    edge = await _edge(saved)

    stale = await authenticated_client.put(
        _CALLING, json=_calling_body(edge, enabled=True, revision=4)
    )
    assert stale.status_code == 409
    assert "WhatsApp grant changed. Refresh and try again." in stale.text

    wrong = await authenticated_client.put(
        _CALLING,
        json=_calling_body(edge, enabled=True, revision=0, grant_id="edge-other"),
    )
    assert wrong.status_code == 409
    stored = await _edge(saved)
    assert stored.calling_enabled is False
    assert stored.revision == 0
    assert stored.id == edge.id


@pytest.mark.asyncio
async def test_secret_fields_are_rejected(enc_key, authenticated_client, monkeypatch):
    """Extra jvvoice and LiveKit fields are rejected and never echoed."""
    _stub_jvconnect_account_failure(monkeypatch)
    saved = await _save_line(authenticated_client)
    edge = await _edge(saved)
    secret_base = "https://jvvoice.example/hidden-base"
    secret_key = "jvvoice-hidden-key"
    secret_livekit = "livekit-hidden-key"
    rejected = await authenticated_client.put(
        _CALLING,
        json={
            **_calling_body(edge, enabled=True, revision=0),
            "jvvoice_base_url": secret_base,
            "JVVOICE_API_KEY": secret_key,
            "livekit_api_key": secret_livekit,
        },
    )
    assert rejected.status_code == 400
    for secret in (
        secret_base,
        secret_key,
        secret_livekit,
        "JVVOICE_BASE_URL",
        "JVVOICE_API_KEY",
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
    ):
        assert secret not in rejected.text
    stored = await _edge(saved)
    assert stored.calling_enabled is False
    assert stored.revision == 0


@pytest.mark.asyncio
async def test_put_enables_jvconnect_calling_or_leaves_integral_off(
    enc_key, authenticated_client, monkeypatch
):
    """Meta calling is updated first. A JV Connect failure does not flip the edge."""
    _stub_jvconnect_account_failure(monkeypatch)
    saved = await _save_line(authenticated_client)
    edge = await _edge(saved)
    calls = []

    async def set_calling_settings(base_url, api_key, enabled, **kwargs):
        calls.append(enabled)
        return JvconnectResult(ok=False, status_code=502, data={}, error="graph failed")

    monkeypatch.setattr(
        "app.agentive.clients.jvconnect_whatsapp.set_calling_settings",
        set_calling_settings,
    )
    failed = await authenticated_client.put(
        _CALLING, json=_calling_body(edge, enabled=True, revision=0)
    )
    assert failed.status_code == 503
    assert "graph failed" in failed.text
    assert "jvk_test_secret_value" not in failed.text
    assert calls == [True]
    stored = await _edge(saved)
    assert stored.calling_enabled is False
    assert stored.revision == 0

    async def enable_calling(base_url, api_key, enabled, **kwargs):
        calls.append(enabled)
        return JvconnectResult(
            ok=True,
            status_code=200,
            data={"status": "ENABLED" if enabled else "DISABLED"},
            error="",
        )

    monkeypatch.setattr(
        "app.agentive.clients.jvconnect_whatsapp.set_calling_settings",
        enable_calling,
    )
    turned_on = await authenticated_client.put(
        _CALLING, json=_calling_body(edge, enabled=True, revision=0)
    )
    assert turned_on.status_code == 200, turned_on.text
    assert turned_on.json()["binding"]["calling_enabled"] is True
    assert turned_on.json()["binding"]["jvconnect_calling_status"] == "ENABLED"
    assert calls == [True, True]


@pytest.mark.asyncio
async def test_put_leaves_integral_off_when_jvconnect_settings_are_missing(
    enc_key, authenticated_client, monkeypatch
):
    """A 404 from JV Connect is 503. The Integral edge stays off."""
    _stub_jvconnect_account_failure(monkeypatch)
    saved = await _save_line(authenticated_client)
    edge = await _edge(saved)

    async def missing_settings(*args: object, **kwargs: object):
        return JvconnectResult(
            ok=False, status_code=404, data={}, error="invalid JSON response"
        )

    monkeypatch.setattr(
        "app.agentive.clients.jvconnect_whatsapp.set_calling_settings",
        missing_settings,
    )
    monkeypatch.setattr(
        "app.agentive.clients.jvconnect_whatsapp.fetch_calling_settings",
        missing_settings,
    )
    turned_on = await authenticated_client.put(
        _CALLING, json=_calling_body(edge, enabled=True, revision=0)
    )
    assert turned_on.status_code == 503
    assert "Upgrade JV Connect" in turned_on.text
    assert "invalid JSON" not in turned_on.text
    stored = await _edge(saved)
    assert stored.calling_enabled is False
    assert stored.revision == 0
    fetched = await authenticated_client.get(_BINDING)
    body = fetched.json()["binding"]
    assert body["calling_enabled"] is False
    assert "Upgrade JV Connect" in body["jvconnect_calling_error"]
    assert "invalid JSON" not in fetched.text


@pytest.mark.asyncio
async def test_get_surfaces_jvconnect_calling_graph_errors(
    enc_key, authenticated_client, monkeypatch
):
    """A Graph 400 from calling settings is shown, not treated as missing."""
    _stub_jvconnect_account_failure(monkeypatch)
    await _save_line(authenticated_client)

    async def graph_denied(*args: object, **kwargs: object):
        return JvconnectResult(
            ok=False,
            status_code=400,
            data={"error": "Calling is not available for this number"},
            error="Calling is not available for this number",
        )

    monkeypatch.setattr(
        "app.agentive.clients.jvconnect_whatsapp.fetch_calling_settings",
        graph_denied,
    )
    fetched = await authenticated_client.get(_BINDING)
    assert fetched.status_code == 200, fetched.text
    body = fetched.json()["binding"]
    assert body["jvconnect_calling_status"] == ""
    assert body["jvconnect_calling_error"] == "Calling is not available for this number"


@pytest.mark.asyncio
async def test_voice_service_stores_jvvoice_last4(
    enc_key, authenticated_client, monkeypatch
):
    """The calling dialog can save a jvvoice URL without LiveKit fields."""
    _stub_jvconnect_account_failure(monkeypatch)

    async def allow_url(url: str) -> None:
        return None

    monkeypatch.setattr(
        "app.services.url_safety.validate_outbound_http_url",
        allow_url,
    )
    await _save_line(authenticated_client)
    rejected = await authenticated_client.put(
        _CALLING + "/service",
        json={
            "jvvoice_base_url": "https://voice.example",
            "api_key": "voice-secret-key",
            "LIVEKIT_API_KEY": "lk_secret",
        },
    )
    assert rejected.status_code == 400
    assert "lk_secret" not in rejected.text
    saved_voice = await authenticated_client.put(
        _CALLING + "/service",
        json={
            "jvvoice_base_url": "https://voice.example",
            "api_key": "voice-secret-key",
        },
    )
    assert saved_voice.status_code == 200, saved_voice.text
    body = saved_voice.json()["binding"]
    assert body["jvvoice_base_url"] == "https://voice.example"
    assert body["jvvoice_api_key_last4"] == "-key"
    assert "voice-secret-key" not in saved_voice.text
    fetched = await authenticated_client.get(_BINDING)
    assert fetched.json()["binding"]["jvvoice_api_key_last4"] == "-key"
    assert "LIVEKIT" not in fetched.text
