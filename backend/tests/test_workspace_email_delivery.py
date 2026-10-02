"""Workspace email delivery resolver and system-kind routing."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.email_source_kinds import SYSTEM_EMAIL_SOURCE_KINDS
from app.services.workspace_email_delivery import (
    _get_record,
    resolve_send_context,
    upsert_delivery_config,
    validate_delivery_provider,
)


@pytest.mark.asyncio
async def test_system_source_kinds_always_platform():
    with patch("app.services.workspace_email_delivery.settings") as mock_settings:
        mock_settings.EMAIL_PROVIDER = "resend"
        mock_settings.EMAIL_FROM = "platform@example.com"
        mock_settings.EMAIL_FROM_NAME = "Integral"
        mock_settings.RESEND_API_KEY = "re_platform"
        for kind in SYSTEM_EMAIL_SOURCE_KINDS:
            ctx = await resolve_send_context(
                workspace_id="n.Workspace.org1",
                source_kind=kind,
            )
            assert ctx.delivery_source == "platform"
            assert ctx.provider == "resend"


@pytest.mark.asyncio
async def test_no_workspace_config_falls_back_to_platform():
    with patch("app.services.workspace_email_delivery.settings") as mock_settings:
        mock_settings.EMAIL_PROVIDER = "console"
        mock_settings.EMAIL_FROM = "platform@example.com"
        mock_settings.EMAIL_FROM_NAME = "Integral"
        with patch(
            "app.services.workspace_email_delivery._is_org_workspace",
            new=AsyncMock(return_value=True),
        ):
            with patch(
                "app.services.workspace_email_delivery._get_record",
                new=AsyncMock(return_value=None),
            ):
                ctx = await resolve_send_context(
                    workspace_id="n.Workspace.org1",
                    source_kind="invitation",
                )
                assert ctx.delivery_source == "platform"


@pytest.mark.asyncio
async def test_get_record_finds_by_context_workspace_id():
    ws_id = "n.Workspace.email_delivery_find_test"
    record = __import__(
        "app.models.workspace_email_delivery", fromlist=["WorkspaceEmailDelivery"]
    ).WorkspaceEmailDelivery(
        workspace_id=ws_id,
        provider="resend",
        from_email="a@example.com",
        from_name="Test",
        api_key_enc="v1:ciphertext-placeholder",
        key_fingerprint="fp",
        is_enabled=True,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    await record.save()
    try:
        found = await _get_record(ws_id)
        assert found is not None
        assert found.workspace_id == ws_id
    finally:
        await record.delete()


@pytest.mark.asyncio
async def test_upsert_updates_existing_row_when_api_key_omitted():
    ws_id = "n.Workspace.email_delivery_upsert_test"
    with patch(
        "app.services.workspace_email_delivery._is_org_workspace",
        new=AsyncMock(return_value=True),
    ):
        with patch(
            "app.services.workspace_email_delivery.validate_delivery_provider",
            new=AsyncMock(return_value=(True, "validated")),
        ):
            with patch(
                "app.services.workspace_email_delivery.encryption_available",
                return_value=True,
            ):
                with patch(
                    "app.services.workspace_email_delivery.encrypt_secret_for_storage",
                    return_value="v1:enc",
                ):
                    first = await upsert_delivery_config(
                        workspace_id=ws_id,
                        actor_user_id="o.User.u1",
                        payload={
                            "delivery_provider": "resend",
                            "api_key": "re_" + "a" * 32,
                            "from_email": "a@example.com",
                            "from_name": "One",
                        },
                    )
                    assert first.get("ok") is True
                    second = await upsert_delivery_config(
                        workspace_id=ws_id,
                        actor_user_id="o.User.u1",
                        payload={
                            "delivery_provider": "resend",
                            "from_name": "Two",
                        },
                    )
                    assert second.get("ok") is True
                    found = await _get_record(ws_id)
                    assert found is not None
                    assert found.from_name == "Two"
    if found:
        await found.delete()


@pytest.mark.asyncio
async def test_validate_delivery_provider_detects_resend_key_under_sendgrid():
    ok, msg = await validate_delivery_provider(
        "sendgrid", "re_12345678901234567890123456789012"
    )
    assert ok is False
    assert "Resend" in msg


@pytest.mark.asyncio
async def test_validate_delivery_provider_detects_sendgrid_key_under_resend():
    ok, msg = await validate_delivery_provider("resend", "SG.test.sendgrid.key.value")
    assert ok is False
    assert "SendGrid" in msg


@pytest.mark.asyncio
async def test_validate_delivery_provider_accepts_resend_sending_only_401():
    class _Resp:
        status_code = 401

        @staticmethod
        def json():
            return {
                "name": "restricted_api_key",
                "message": "This API key is restricted to only send emails",
            }

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, headers=None):
            assert "resend.com" in url
            return _Resp()

    with patch(
        "app.services.workspace_email_delivery.httpx.AsyncClient",
        return_value=_Client(),
    ):
        ok, msg = await validate_delivery_provider(
            "resend", "re_12345678901234567890123456789012"
        )
    assert ok is True
    assert "sending" in msg.lower()


@pytest.mark.asyncio
async def test_tool_context_send_facade_delegates_to_send_email():
    from app.services.hooks.registry import ToolContext

    ctx = ToolContext(user_id="u1", workspace_id="w1", scope="tool:test")
    with patch(
        "app.services.email_service.send_email", new=AsyncMock(return_value=True)
    ) as send_mock:
        result = await ctx.send_workspace_transactional_email(
            to="a@example.com",
            subject="Hi",
            text="plain",
            html="<p>plain</p>",
            source_kind="recruitment_hire",
            source_id="e1",
        )
        assert result == {"ok": True}
        send_mock.assert_awaited_once()
        msg = send_mock.await_args.args[0]
        assert msg.to == "a@example.com"
        assert msg.workspace_id == "w1"
        assert msg.source_kind == "recruitment_hire"
        assert msg.source_id == "e1"
        assert msg.actor_user_id == "u1"
