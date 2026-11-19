"""jvconnect WhatsApp client: account, webhook register, and text send."""

import json
import logging

import httpx
import pytest

from app.agentive.clients.jvconnect_whatsapp import (
    fetch_account,
    register_webhook,
    send_text,
)

pytestmark = pytest.mark.unit

_BASE = "https://connect.example.test/"
_API_KEY = "jvk_test"
_SECRET = "whsec_do_not_log"


def _client(handler):
    """Build an AsyncClient whose transport is the given handler."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _logged(caplog) -> str:
    """Flatten captured log records, including args."""
    parts = []
    for record in caplog.records:
        parts.append(record.getMessage())
        if record.args:
            parts.append(repr(record.args))
        if record.exc_text:
            parts.append(record.exc_text)
    return "\n".join(parts)


@pytest.mark.asyncio
async def test_fetch_account_returns_phone_fields():
    """A 200 account payload exposes phone_number_id and display_phone_number."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == (
            "https://connect.example.test/api/v1/meta/whatsapp/account"
        )
        assert request.method == "GET"
        return httpx.Response(
            200,
            json={
                "phone_number_id": "109876543210",
                "display_phone_number": "+1 555 0100",
                "waba_id": "waba-1",
                "verified_name": "Integral",
            },
        )

    async with _client(handler) as client:
        result = await fetch_account(_BASE, _API_KEY, client=client)

    assert result.ok is True
    assert result.status_code == 200
    assert result.error == ""
    assert result.data["phone_number_id"] == "109876543210"
    assert result.data["display_phone_number"] == "+1 555 0100"
    assert result.data["waba_id"] == "waba-1"
    assert result.data["verified_name"] == "Integral"


@pytest.mark.asyncio
async def test_register_webhook_posts_callback_and_omits_secret_from_logs(caplog):
    """Register posts callback_url with a bearer token and does not log the secret."""
    callback = "https://integral.example.test/api/agentive/channels/whatsapp/webhook"
    caplog.set_level(logging.DEBUG)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == (
            "https://connect.example.test/api/v1/meta/whatsapp/webhook/register"
        )
        assert request.headers["Authorization"] == "Bearer jvk_test"
        assert json.loads(request.content) == {"callback_url": callback}
        return httpx.Response(
            200,
            json={
                "ok": True,
                "webhook_secret": _SECRET,
                "phone_number_id": "109876543210",
            },
        )

    async with _client(handler) as client:
        result = await register_webhook(_BASE, _API_KEY, callback, client=client)

    assert result.ok is True
    assert result.data["webhook_secret"] == _SECRET
    assert result.data["phone_number_id"] == "109876543210"
    logged = _logged(caplog)
    assert logged
    assert _SECRET not in logged
    assert _API_KEY not in logged
    assert "Bearer" not in logged


@pytest.mark.asyncio
async def test_send_text_posts_message_wrapper_and_bearer():
    """send_text posts the Meta message wrapper and a bearer Authorization header."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["auth"] = request.headers["Authorization"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"messaging_product": "whatsapp", "messages": [{"id": "wamid.1"}]},
        )

    async with _client(handler) as client:
        result = await send_text(
            _BASE, _API_KEY, "15551212000", "Hello from Integral", client=client
        )

    assert result.ok is True
    assert result.data["messages"] == [{"id": "wamid.1"}]
    assert captured["method"] == "POST"
    assert captured["url"] == (
        "https://connect.example.test/api/v1/meta/whatsapp/messages"
    )
    assert captured["auth"] == "Bearer jvk_test"
    assert captured["body"] == {
        "message": {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": "15551212000",
            "type": "text",
            "text": {"body": "Hello from Integral"},
        }
    }


@pytest.mark.asyncio
async def test_rate_limit_and_connection_error_do_not_raise():
    """429 and connection failures return ok False, with retry_after on 429."""

    def limited(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"Retry-After": "15"},
            json={"error": "slow down"},
        )

    async with _client(limited) as client:
        limited_result = await send_text(
            _BASE, _API_KEY, "15551212000", "again", client=client
        )

    assert limited_result.ok is False
    assert limited_result.status_code == 429
    assert limited_result.error == "slow down"
    assert limited_result.retry_after_seconds == 15

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    async with _client(refused) as client:
        down = await fetch_account(_BASE, _API_KEY, client=client)

    assert down.ok is False
    assert down.status_code == 0
    assert down.error
    assert down.retry_after_seconds is None
