"""App settings redaction, secret merge, and workspace email sync."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.app_settings_helpers import (
    build_workspace_delivery_sync_payload,
    merge_secret_fields_on_patch,
    redact_settings_for_response,
    sync_workspace_email_delivery_from_app_settings,
)


def test_redact_settings_strips_secrets():
    schema = {
        "properties": {
            "api_key": {"ui:widget": "secret"},
            "from_email": {"type": "string"},
        }
    }
    out = redact_settings_for_response(
        {"api_key": "SG.secret", "from_email": "a@b.com"},
        schema,
    )
    assert out["api_key"] == ""
    assert out["from_email"] == "a@b.com"


def test_merge_secret_fields_keeps_stored_key_when_blank():
    schema = {"properties": {"api_key": {"ui:widget": "secret"}}}
    merged = merge_secret_fields_on_patch(
        {"api_key": "stored"},
        {"api_key": ""},
        schema,
    )
    assert merged["api_key"] == "stored"


def test_delivery_sync_payload_omits_blank_secret_patch():
    schema = {"properties": {"api_key": {"ui:widget": "secret"}}}
    payload = build_workspace_delivery_sync_payload(
        {"api_key": "re_test", "from_email": "a@b.com"},
        {"api_key": "", "from_email": "a@b.com"},
        schema,
    )
    assert "api_key" not in payload
    assert payload["from_email"] == "a@b.com"


@pytest.mark.asyncio
async def test_sync_calls_upsert_for_delivery_settings():
    app = MagicMock()
    app.workspace_id = "n.Workspace.org1"
    app.settings = {
        "delivery_provider": "sendgrid",
        "delivery_enabled": True,
        "api_key": "SG.test",
        "from_email": "noreply@example.com",
    }
    app.settings_schema = {"properties": {"api_key": {"ui:widget": "secret"}}}
    with patch(
        "app.services.workspace_email_delivery.upsert_delivery_config",
        new=AsyncMock(return_value={"ok": True, "configured": True}),
    ) as upsert:
        await sync_workspace_email_delivery_from_app_settings(
            app_node=app,
            actor_id="o.User.u1",
            settings_patch=app.settings,
        )
    upsert.assert_awaited_once()
    assert upsert.await_args.kwargs["workspace_id"] == "n.Workspace.org1"
    assert upsert.await_args.kwargs["payload"]["api_key"] == "SG.test"
