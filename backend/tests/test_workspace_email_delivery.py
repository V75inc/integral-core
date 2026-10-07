"""Workspace Email Log delivery routing for send_email."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.email_service import EmailMessage, send_email


@pytest.mark.asyncio
async def test_send_email_uses_workspace_resend_when_enabled(monkeypatch):
    monkeypatch.setattr("app.config.settings.EMAIL_PROVIDER", "console")
    monkeypatch.setattr("app.config.settings.EMAIL_FROM", "platform@example.com")
    monkeypatch.setattr("app.config.settings.EMAIL_FROM_NAME", "Platform")

    delivery = MagicMock(
        provider="resend",
        from_email="hr@company.com",
        from_name="Company HR",
        api_key="re_workspace_key",
        reply_to="noreply@company.com",
    )

    with patch(
        "app.services.workspace_email_delivery.resolve_active_delivery",
        new=AsyncMock(return_value=delivery),
    ):
        with patch(
            "app.services.email_service._send_via_resend",
            new=AsyncMock(
                return_value=MagicMock(success=True, provider_message_id="msg_1")
            ),
        ) as send_resend:
            ok = await send_email(
                EmailMessage(
                    to="candidate@personal.com",
                    subject="Welcome",
                    html="<p>Hi</p>",
                    text="Hi",
                    workspace_id="ws-org-1",
                    source_kind="member_provision",
                    actor_user_id="admin-1",
                )
            )

    assert ok is True
    send_resend.assert_awaited_once()
    _msg, creds = send_resend.await_args.args
    assert creds.api_key == "re_workspace_key"
    assert creds.from_email == "hr@company.com"


@pytest.mark.asyncio
async def test_send_email_platform_only_skips_workspace(monkeypatch):
    monkeypatch.setattr("app.config.settings.EMAIL_PROVIDER", "console")

    resolve = AsyncMock(return_value=MagicMock(provider="resend"))
    with patch(
        "app.services.workspace_email_delivery.resolve_active_delivery",
        new=resolve,
    ):
        with patch(
            "app.services.email_service._send_via_console",
            return_value=MagicMock(success=True),
        ) as send_console:
            await send_email(
                EmailMessage(
                    to="user@example.com",
                    subject="Verify",
                    html="<p>code</p>",
                    text="code",
                    workspace_id="ws-1",
                    source_kind="email_verification",
                )
            )

    resolve.assert_not_awaited()
    send_console.assert_called_once()
