"""Transactional email provider dispatch tests."""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
from app.services import email_service
from app.services.email_service import EmailMessage, send_email


def _fake_response(
    status_code: int, json_body: Dict[str, Any] | None = None, text_body: str = ""
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    if json_body is not None:
        resp.json.return_value = json_body
    else:
        resp.json.side_effect = ValueError("no json")
    resp.text = text_body
    return resp


def _patch_httpx_post(response: MagicMock):
    post_mock = AsyncMock(return_value=response)
    return patch("httpx.AsyncClient.post", new=post_mock), post_mock


def _sample_message(*, reply_to: str | None = None) -> EmailMessage:
    return EmailMessage(
        to="recipient@example.test",
        subject="Test subject",
        html="<p>Hello</p>",
        text="Hello",
        reply_to=reply_to,
    )


@pytest.mark.asyncio
async def test_sendgrid_success_posts_expected_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "EMAIL_PROVIDER", "sendgrid")
    monkeypatch.setattr(settings, "SENDGRID_API_KEY", "SG.test-key")
    monkeypatch.setattr(settings, "EMAIL_FROM", "noreply@example.test")
    monkeypatch.setattr(settings, "EMAIL_FROM_NAME", "Integral")

    resp = _fake_response(202)
    patcher, post_mock = _patch_httpx_post(resp)
    with patcher:
        ok = await send_email(_sample_message())

    assert ok is True
    assert post_mock.await_count == 1
    call = post_mock.await_args
    assert call.args[0] == "https://api.sendgrid.com/v3/mail/send"
    assert call.kwargs["headers"]["Authorization"] == "Bearer SG.test-key"
    payload = call.kwargs["json"]
    assert payload["from"] == {
        "email": "noreply@example.test",
        "name": "Integral",
    }
    assert payload["personalizations"] == [
        {"to": [{"email": "recipient@example.test"}]}
    ]
    assert payload["subject"] == "Test subject"
    assert payload["content"] == [
        {"type": "text/plain", "value": "Hello"},
        {"type": "text/html", "value": "<p>Hello</p>"},
    ]
    assert "reply_to" not in payload


@pytest.mark.asyncio
async def test_sendgrid_includes_reply_to_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "EMAIL_PROVIDER", "sendgrid")
    monkeypatch.setattr(settings, "SENDGRID_API_KEY", "SG.test-key")

    resp = _fake_response(202)
    patcher, post_mock = _patch_httpx_post(resp)
    with patcher:
        ok = await send_email(_sample_message(reply_to="support@example.test"))

    assert ok is True
    payload = post_mock.await_args.kwargs["json"]
    assert payload["reply_to"] == {"email": "support@example.test"}


@pytest.mark.asyncio
async def test_sendgrid_returns_false_when_api_key_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "EMAIL_PROVIDER", "sendgrid")
    monkeypatch.setattr(settings, "SENDGRID_API_KEY", None)

    patcher, post_mock = _patch_httpx_post(_fake_response(202))
    with patcher:
        ok = await send_email(_sample_message())

    assert ok is False
    assert post_mock.await_count == 0


@pytest.mark.asyncio
async def test_sendgrid_returns_false_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "EMAIL_PROVIDER", "sendgrid")
    monkeypatch.setattr(settings, "SENDGRID_API_KEY", "SG.test-key")

    resp = _fake_response(403, text_body='{"errors":[{"message":"Forbidden"}]}')
    patcher, post_mock = _patch_httpx_post(resp)
    with patcher:
        ok = await send_email(_sample_message())

    assert ok is False
    assert post_mock.await_count == 1


@pytest.mark.asyncio
async def test_unknown_provider_does_not_claim_delivery_or_log_the_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "EMAIL_PROVIDER", "unknown-provider")

    patcher, post_mock = _patch_httpx_post(_fake_response(202))
    with patcher, patch("app.services.email_service._send_via_console") as console:
        ok = await send_email(_sample_message())

    assert ok is False
    console.assert_not_called()
    assert post_mock.await_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("debug", [False, True])
async def test_console_is_only_an_explicit_debug_preview(monkeypatch, debug):
    monkeypatch.setattr(settings, "EMAIL_PROVIDER", "console")
    monkeypatch.setattr(settings, "DEBUG", debug)
    with patch(
        "app.services.email_service._send_via_console",
        wraps=email_service._send_via_console,
    ) as console:
        ok = await send_email(_sample_message())
    assert ok is debug
    assert console.call_count == int(debug)
