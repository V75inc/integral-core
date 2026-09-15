"""Phase 9 Plan 09-03b (NOTIF-02 / A3) — WhatsApp dispatch tests.

Covers:

- ``send_whatsapp_message`` HTTPX client (Task 1):
  - welcome-template body shape (Meta Cloud API contract).
  - text body shape (freeform).
  - HTTP 200 returns ``(True, message_id)``.
  - HTTP 470 returns ``(False, None)``.
  - missing creds returns ``(False, None)`` WITHOUT a network call
    (T-09-03b-V02 — console-provider fallback for dev/CI).

- ``WhatsappChannel`` adapter (Task 2):
  - welcome-template on first send (no prior whatsapp success in
    ``Notification.metadata.dispatched_to[]``).
  - freeform on subsequent send (prior whatsapp success entry exists).
  - ``status="skipped"`` ``reason="not_opted_in"`` when
    ``user.notification_preferences.whatsapp.opted_in_at is None``.
  - ``status="failed"`` ``reason="invalid_phone"`` when the captured
    phone fails the E.164 regex (T-09-03b-V01).
  - ``status="skipped"`` ``reason="outbound_disabled"`` when
    ``NOTIFICATION_OUTBOUND_ENABLED=False``.

- End-to-end opt-in capture path (Task 2):
  - POST to ``/agentive/channels/whatsapp/verify-otp`` with mocked
    OTP verify success + mocked HTTPX 200 fires exactly ONE
    ``send_whatsapp_message(kind="template", ...)`` call and persists
    ``user.notification_preferences.whatsapp.opted_in_at`` +
    ``phone_e164``.
"""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.nodes import User

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_response(
    status_code: int, json_body: Dict[str, Any] | None = None, text_body: str = ""
) -> MagicMock:
    """Build a minimal mock for ``httpx.Response`` covering the fields the
    service reads (``status_code``, ``json()``, ``text``)."""
    resp = MagicMock()
    resp.status_code = status_code
    if json_body is not None:
        resp.json.return_value = json_body
    else:
        resp.json.side_effect = ValueError("no json")
    resp.text = text_body
    return resp


def _patch_httpx_post(response: MagicMock):
    """Context-manager helper that patches httpx.AsyncClient.post to return
    the given mock response, and exposes the post mock for assertions."""
    post_mock = AsyncMock(return_value=response)
    return patch("httpx.AsyncClient.post", new=post_mock), post_mock


# ---------------------------------------------------------------------------
# Task 1 — send_whatsapp_message (transport layer)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_whatsapp_message_welcome_template_body_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``kind="template"`` builds a Meta-spec template body and POSTs to the
    correct phone-id URL with the bearer token header."""
    from app.config import settings
    from app.services import whatsapp_service

    monkeypatch.setattr(settings, "WHATSAPP_CLOUD_API_TOKEN", "test-token")
    monkeypatch.setattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "111222333")
    monkeypatch.setattr(
        settings, "WHATSAPP_API_BASE", "https://graph.facebook.com/v18.0"
    )
    monkeypatch.setattr(
        settings, "WHATSAPP_WELCOME_TEMPLATE_NAME", "integral_welcome_v1"
    )
    monkeypatch.setattr(settings, "WHATSAPP_WELCOME_TEMPLATE_LANG", "en_US")

    resp = _fake_response(200, json_body={"messages": [{"id": "wamid.AAA"}]})
    patcher, post_mock = _patch_httpx_post(resp)
    with patcher:
        ok, ref = await whatsapp_service.send_whatsapp_message(
            "+15551234567",
            kind="template",
            template_params=[{"type": "text", "text": "Alice"}],
        )

    assert ok is True
    assert ref == "wamid.AAA"
    assert post_mock.await_count == 1
    call = post_mock.await_args
    # URL is base/phone_id/messages
    assert call.args[0] == "https://graph.facebook.com/v18.0/111222333/messages"
    headers = call.kwargs["headers"]
    assert headers["Authorization"] == "Bearer test-token"
    body = call.kwargs["json"]
    assert body["messaging_product"] == "whatsapp"
    assert body["to"] == "+15551234567"
    assert body["type"] == "template"
    assert body["template"]["name"] == "integral_welcome_v1"
    assert body["template"]["language"] == {"code": "en_US"}
    # template_params expand into a body component
    assert body["template"]["components"] == [
        {"type": "body", "parameters": [{"type": "text", "text": "Alice"}]}
    ]


@pytest.mark.asyncio
async def test_send_whatsapp_message_text_body_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``kind="text"`` builds a freeform Meta-spec text body."""
    from app.config import settings
    from app.services import whatsapp_service

    monkeypatch.setattr(settings, "WHATSAPP_CLOUD_API_TOKEN", "test-token")
    monkeypatch.setattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "111222333")

    resp = _fake_response(200, json_body={"messages": [{"id": "wamid.BBB"}]})
    patcher, post_mock = _patch_httpx_post(resp)
    with patcher:
        ok, ref = await whatsapp_service.send_whatsapp_message(
            "+15551234567", kind="text", text="hello"
        )

    assert ok is True
    assert ref == "wamid.BBB"
    body = post_mock.await_args.kwargs["json"]
    assert body["type"] == "text"
    assert body["text"] == {"body": "hello"}
    assert body["to"] == "+15551234567"


@pytest.mark.asyncio
async def test_send_whatsapp_message_returns_message_id_on_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP 200 with ``messages[].id`` returns ``(True, "<wamid>")``."""
    from app.config import settings
    from app.services import whatsapp_service

    monkeypatch.setattr(settings, "WHATSAPP_CLOUD_API_TOKEN", "test-token")
    monkeypatch.setattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "111222333")

    resp = _fake_response(200, json_body={"messages": [{"id": "wamid.CCC"}]})
    patcher, _ = _patch_httpx_post(resp)
    with patcher:
        ok, ref = await whatsapp_service.send_whatsapp_message(
            "+15551234567", kind="text", text="ping"
        )
    assert ok is True
    assert ref == "wamid.CCC"


@pytest.mark.asyncio
async def test_send_whatsapp_message_returns_false_on_non_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP 470 (Meta-specific rate / template issue) returns ``(False, None)``."""
    from app.config import settings
    from app.services import whatsapp_service

    monkeypatch.setattr(settings, "WHATSAPP_CLOUD_API_TOKEN", "test-token")
    monkeypatch.setattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "111222333")

    resp = _fake_response(
        470,
        json_body={"error": {"message": "template not approved"}},
        text_body='{"error": {"message": "template not approved"}}',
    )
    patcher, _ = _patch_httpx_post(resp)
    with patcher:
        ok, ref = await whatsapp_service.send_whatsapp_message(
            "+15551234567", kind="template"
        )
    assert ok is False
    assert ref is None


@pytest.mark.asyncio
async def test_send_whatsapp_message_missing_creds_no_network_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing creds returns ``(False, None)`` WITHOUT an HTTP call
    (T-09-03b-V02 — console-provider fallback for dev/CI)."""
    from app.config import settings
    from app.services import whatsapp_service

    monkeypatch.setattr(settings, "WHATSAPP_CLOUD_API_TOKEN", "")
    monkeypatch.setattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "")

    resp = _fake_response(200, json_body={"messages": [{"id": "should-never-fire"}]})
    patcher, post_mock = _patch_httpx_post(resp)
    with patcher:
        ok, ref = await whatsapp_service.send_whatsapp_message(
            "+15551234567", kind="text", text="should not send"
        )
    assert ok is False
    assert ref is None
    assert post_mock.await_count == 0, "HTTPX must NOT be called when creds missing"


# ---------------------------------------------------------------------------
# Task 2 — WhatsappChannel adapter
# ---------------------------------------------------------------------------


class _StubUser:
    """Minimal duck-type for User used by WhatsappChannel.dispatch.

    The adapter only reads ``notification_preferences``; we don't need a
    real ``User.create`` round-trip for these unit cases.
    """

    def __init__(self, *, opted_in: bool = True, phone: str | None = "+15551234567"):
        wa: Dict[str, Any] = {}
        if opted_in:
            wa["opted_in_at"] = "2026-05-18T00:00:00+00:00"
        if phone is not None:
            wa["phone_e164"] = phone
        self.notification_preferences = {"whatsapp": wa}


class _StubNotification:
    """Minimal duck-type for Notification — only ``.metadata`` is read."""

    def __init__(self, dispatched_to: list | None = None):
        self.metadata = {"dispatched_to": list(dispatched_to or [])}


@pytest.mark.asyncio
async def test_whatsapp_channel_welcome_template_on_first_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First send (no prior whatsapp success in dispatched_to[]) uses the
    pre-approved welcome template (A3)."""
    from app.config import settings
    from app.services.notification_channels.whatsapp_channel import WhatsappChannel

    monkeypatch.setattr(settings, "NOTIFICATION_OUTBOUND_ENABLED", True)

    channel = WhatsappChannel()
    user = _StubUser(opted_in=True, phone="+15551234567")
    notif = _StubNotification(dispatched_to=[])

    send_mock = AsyncMock(return_value=(True, "wamid.welcome"))
    with patch(
        "app.services.notification_channels.whatsapp_channel.send_whatsapp_message",
        new=send_mock,
    ):
        result = await channel.dispatch(
            user=user,
            kind="whatsapp_welcome",
            payload={"actor_name": "Alice"},
            notification=notif,
            actor_id="system",
            actor_kind="system",
        )

    assert result.status == "sent"
    assert result.channel == "whatsapp"
    assert result.external_ref == "wamid.welcome"
    assert send_mock.await_count == 1
    call_kwargs = send_mock.await_args.kwargs
    assert call_kwargs["kind"] == "template"
    # template_params binds the actor_name into the welcome template
    assert call_kwargs.get("template_params") == [{"type": "text", "text": "Alice"}]


@pytest.mark.asyncio
async def test_whatsapp_channel_freeform_on_subsequent_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Subsequent send (prior whatsapp success entry exists in dispatched_to[])
    uses freeform text within Meta's 24h window."""
    from app.config import settings
    from app.services.notification_channels.whatsapp_channel import WhatsappChannel

    monkeypatch.setattr(settings, "NOTIFICATION_OUTBOUND_ENABLED", True)

    channel = WhatsappChannel()
    user = _StubUser(opted_in=True, phone="+15551234567")
    notif = _StubNotification(
        dispatched_to=[
            {"channel": "whatsapp", "status": "sent", "external_ref": "wamid.prior"}
        ]
    )

    send_mock = AsyncMock(return_value=(True, "wamid.followup"))
    with patch(
        "app.services.notification_channels.whatsapp_channel.send_whatsapp_message",
        new=send_mock,
    ):
        result = await channel.dispatch(
            user=user,
            kind="mention",
            payload={"actor_name": "Bob", "snippet": "ping"},
            notification=notif,
            actor_id="actor-1",
            actor_kind="human",
        )

    assert result.status == "sent"
    assert result.external_ref == "wamid.followup"
    call_kwargs = send_mock.await_args.kwargs
    assert call_kwargs["kind"] == "text"
    # freeform body falls back to snippet (mention kind)
    assert call_kwargs["text"] == "ping"


@pytest.mark.asyncio
async def test_whatsapp_channel_not_opted_in_skipped() -> None:
    """No ``opted_in_at`` → ``status="skipped"`` ``reason="not_opted_in"``."""
    from app.services.notification_channels.whatsapp_channel import WhatsappChannel

    channel = WhatsappChannel()
    user = _StubUser(opted_in=False, phone="+15551234567")
    notif = _StubNotification(dispatched_to=[])

    result = await channel.dispatch(
        user=user,
        kind="mention",
        payload={"snippet": "x"},
        notification=notif,
        actor_id="actor-2",
        actor_kind="human",
    )
    assert result.status == "skipped"
    assert result.reason == "not_opted_in"


@pytest.mark.asyncio
async def test_whatsapp_channel_invalid_phone_failed() -> None:
    """Phone missing or not E.164 → ``status="failed"`` ``reason="invalid_phone"``."""
    from app.services.notification_channels.whatsapp_channel import WhatsappChannel

    channel = WhatsappChannel()
    notif = _StubNotification(dispatched_to=[])

    # Missing phone
    user = _StubUser(opted_in=True, phone=None)
    r = await channel.dispatch(
        user=user,
        kind="mention",
        payload={},
        notification=notif,
        actor_id="a",
        actor_kind="human",
    )
    assert r.status == "failed"
    assert r.reason == "invalid_phone"

    # Non-E.164 (no leading +)
    user2 = _StubUser(opted_in=True, phone="15551234567")
    r2 = await channel.dispatch(
        user=user2,
        kind="mention",
        payload={},
        notification=notif,
        actor_id="a",
        actor_kind="human",
    )
    assert r2.status == "failed"
    assert r2.reason == "invalid_phone"

    # Non-E.164 (starts with +0)
    user3 = _StubUser(opted_in=True, phone="+0155512345")
    r3 = await channel.dispatch(
        user=user3,
        kind="mention",
        payload={},
        notification=notif,
        actor_id="a",
        actor_kind="human",
    )
    assert r3.status == "failed"
    assert r3.reason == "invalid_phone"


@pytest.mark.asyncio
async def test_whatsapp_channel_outbound_disabled_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``NOTIFICATION_OUTBOUND_ENABLED=False`` → ``status="skipped"``
    ``reason="outbound_disabled"``."""
    from app.config import settings
    from app.services.notification_channels.whatsapp_channel import WhatsappChannel

    monkeypatch.setattr(settings, "NOTIFICATION_OUTBOUND_ENABLED", False)
    channel = WhatsappChannel()
    user = _StubUser(opted_in=True, phone="+15551234567")
    notif = _StubNotification(dispatched_to=[])

    result = await channel.dispatch(
        user=user,
        kind="mention",
        payload={"snippet": "x"},
        notification=notif,
        actor_id="a",
        actor_kind="human",
    )
    assert result.status == "skipped"
    assert result.reason == "outbound_disabled"


@pytest.mark.asyncio
async def test_whatsapp_channel_cloud_api_error_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``send_whatsapp_message`` returns (False, _) → ``status="failed"``
    ``reason="cloud_api_error"``."""
    from app.config import settings
    from app.services.notification_channels.whatsapp_channel import WhatsappChannel

    monkeypatch.setattr(settings, "NOTIFICATION_OUTBOUND_ENABLED", True)
    channel = WhatsappChannel()
    user = _StubUser(opted_in=True, phone="+15551234567")
    notif = _StubNotification(dispatched_to=[])

    with patch(
        "app.services.notification_channels.whatsapp_channel.send_whatsapp_message",
        new=AsyncMock(return_value=(False, None)),
    ):
        result = await channel.dispatch(
            user=user,
            kind="whatsapp_welcome",
            payload={"actor_name": "X"},
            notification=notif,
            actor_id="system",
            actor_kind="system",
        )
    assert result.status == "failed"
    assert result.reason == "cloud_api_error"


# ---------------------------------------------------------------------------
# Task 2 — end-to-end opt-in capture path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_whatsapp_verify_otp_end_to_end_fires_welcome_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: ``whatsapp_verify_otp`` with mocked OTP success + mocked
    HTTPX 200 fires exactly ONE template send AND captures opt-in state.

    Verifies (per Task 2's <action> #3):
    - ``target.notification_preferences.whatsapp.opted_in_at`` is non-None.
    - ``target.notification_preferences.whatsapp.phone_e164 == "+15555550100"``.
    - Exactly ONE call to ``send_whatsapp_message`` with ``kind="template"``.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "NOTIFICATION_OUTBOUND_ENABLED", True)
    monkeypatch.setattr(settings, "WHATSAPP_CLOUD_API_TOKEN", "test-token")
    monkeypatch.setattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "111222333")

    user = await User.create(
        user_id="auth-wa-e2e",
        display_name="WA E2E User",
        preferences={"email": "wa-e2e@example.com"},
    )
    phone = "+15555550100"

    fake_ci = type(
        "CI",
        (),
        {"id": "ci-e2e", "user_id": user.user_id, "verified": True, "verified_at": "x"},
    )()

    send_mock = AsyncMock(return_value=(True, "wamid.welcome-e2e"))

    with (
        patch(
            "app.agentive.api.channels.verify_channel_otp",
            new=AsyncMock(return_value={"identity_id": "ci-e2e"}),
        ),
        patch(
            "app.agentive.api.channels.ChannelIdentity.get",
            new=AsyncMock(return_value=fake_ci),
        ),
        patch(
            "app.agentive.api.channels.export_node",
            new=AsyncMock(return_value={"id": "ci-e2e"}),
        ),
        patch(
            "app.agentive.api.channels.emit_change_event",
            new=AsyncMock(),
        ),
        patch(
            "app.agentive.api.channels.User.get",
            new=AsyncMock(return_value=user),
        ),
        patch(
            "app.services.notification_channels.whatsapp_channel.send_whatsapp_message",
            new=send_mock,
        ),
    ):
        from app.agentive.api import channels as channels_api

        result = await channels_api.whatsapp_verify_otp(
            request=None,  # type: ignore[arg-type]
            phone=phone,
            otp_code="123456",
        )

    assert result == {"identity_id": "ci-e2e"}

    # Opt-in state persisted on the User node.
    prefs = user.notification_preferences or {}
    wa = (prefs.get("whatsapp") or {}) if isinstance(prefs, dict) else {}
    assert wa.get("opted_in_at") is not None
    assert wa.get("phone_e164") == phone

    # Exactly ONE template send fired.
    assert send_mock.await_count == 1
    call_kwargs = send_mock.await_args.kwargs
    assert call_kwargs["kind"] == "template"
