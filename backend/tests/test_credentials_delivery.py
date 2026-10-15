"""Alternate-mailbox credential delivery verification."""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, patch

import pytest

from app.models.nodes import User
from app.services.credential_delivery import (
    begin_alternate_credentials_delivery,
    consume_credentials_delivery_code,
    delivery_status,
)


@pytest.mark.asyncio
async def test_alternate_delivery_defers_credentials_until_verify(test_user):
    user = test_user
    user.preferences = {}
    await user.save()

    sent: list = []

    async def _capture(message):
        sent.append(message)
        return True

    with patch(
        "app.services.credential_delivery.send_email",
        new=AsyncMock(side_effect=_capture),
    ):
        meta = await begin_alternate_credentials_delivery(
            user_node=user,
            recipient_email="personal@example.com",
            account_email="work@company.com",
            workspace_id="ws-1",
            workspace_name="Acme",
            display_name="Pat",
            welcome_message=None,
            actor_user_id="actor-1",
        )

    assert meta["intent_id"]
    status = delivery_status(user)
    assert status["pending_verification"] is True
    assert status["credentials_emailed"] is False
    assert len(sent) == 1
    assert sent[0].to == "personal@example.com"
    assert "work@company.com" in sent[0].text
    assert "Temporary password" not in sent[0].text

    slot = (user.preferences or {}).get("credentials_delivery") or {}
    code = "123456"
    slot["hash"] = hashlib.sha256(code.encode()).hexdigest()
    prefs = dict(user.preferences or {})
    prefs["credentials_delivery"] = slot
    user.preferences = prefs
    await user.save()

    with (
        patch(
            "app.services.credential_delivery.send_email",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.services.credential_delivery._hash_password",
            return_value="hashed",
        ),
        patch("app.services.authentication.get_authentication_service") as mock_auth,
    ):
        auth_user = AsyncMock()
        auth_user.is_active = True
        mock_auth.return_value._get_user_by_id = AsyncMock(return_value=auth_user)

        ok, err, emailed = await consume_credentials_delivery_code(
            member_user_id=user.id,
            intent_id=meta["intent_id"],
            code=code,
        )

    assert ok is True
    assert err is None
    assert emailed is True
    after = delivery_status(await User.get(user.id))
    assert after["credentials_emailed"] is True
    assert after["pending_verification"] is False


@pytest.mark.asyncio
async def test_wrong_code_does_not_release(test_user):
    user = test_user
    with patch(
        "app.services.credential_delivery.send_email",
        new=AsyncMock(return_value=True),
    ):
        meta = await begin_alternate_credentials_delivery(
            user_node=user,
            recipient_email="personal@example.com",
            account_email="work@company.com",
            workspace_id="ws-1",
            workspace_name="Acme",
            display_name="Pat",
            welcome_message=None,
            actor_user_id="actor-1",
        )

    ok, err, emailed = await consume_credentials_delivery_code(
        member_user_id=user.id,
        intent_id=meta["intent_id"],
        code="000000",
    )
    assert ok is False
    assert emailed is False
    assert delivery_status(user)["pending_verification"] is True
