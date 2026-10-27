"""A pending code must not prevent a real resend; sending failure is not success."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from app.api.errors import RateLimitedError, ServiceUnavailableError
from app.models.nodes import User
from app.services import email_verification as verification

pytestmark = pytest.mark.smoke


async def pending(user, monkeypatch):
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(verification, "send_email", send)
    await verification.create_verification_request(user, "test@example.com")
    initial = verification._now_utc()
    monkeypatch.setattr(
        verification, "_now_utc", lambda: initial + timedelta(seconds=61)
    )
    send.reset_mock()
    return send


@pytest.mark.asyncio
async def test_pending_code_resends_after_one_minute_and_preserves_preferences(
    test_user, monkeypatch
):
    send = await pending(test_user, monkeypatch)
    old = dict(test_user.preferences["email_verification"])
    test_user.preferences = {**test_user.preferences, "another_preference": "retained"}
    await test_user.save()
    await verification.resend_verification_request(test_user, "test@example.com")
    send.assert_awaited_once()
    saved = await User.get(test_user.id)
    slot = saved.preferences["email_verification"]
    assert slot["hash"] != old["hash"]
    assert saved.preferences["another_preference"] == "retained"
    assert slot["email"] == "test@example.com"


@pytest.mark.asyncio
async def test_resend_cooldown_is_not_a_fake_success(test_user, monkeypatch):
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(verification, "send_email", send)
    await verification.create_verification_request(test_user, "test@example.com")
    with pytest.raises(RateLimitedError):
        await verification.resend_verification_request(test_user, "test@example.com")
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_concurrent_resends_dispatch_only_one_message(test_user, monkeypatch):
    send = await pending(test_user, monkeypatch)
    results = await asyncio.gather(
        *[
            verification.resend_verification_request(test_user, "test@example.com")
            for _ in range(3)
        ],
        return_exceptions=True,
    )
    assert sum(result is None for result in results) == 1
    assert sum(isinstance(result, RateLimitedError) for result in results) == 2
    send.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("raises", [False, True])
async def test_failed_delivery_retains_previous_code_and_reports_failure(
    test_user, monkeypatch, raises
):
    send = await pending(test_user, monkeypatch)
    old = dict(test_user.preferences["email_verification"])
    send.return_value = False
    if raises:
        send.side_effect = RuntimeError("provider unavailable")
    with pytest.raises(ServiceUnavailableError, match="send your verification code"):
        await verification.resend_verification_request(test_user, "test@example.com")
    saved = await User.get(test_user.id)
    assert saved.preferences["email_verification"]["hash"] == old["hash"]
    with pytest.raises(RateLimitedError):
        await verification.resend_verification_request(saved, "test@example.com")
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_resending_does_not_reset_wrong_code_attempts(test_user, monkeypatch):
    await pending(test_user, monkeypatch)
    slot = dict(test_user.preferences["email_verification"])
    slot["attempts"] = 3
    test_user.preferences = {**test_user.preferences, "email_verification": slot}
    await test_user.save()
    await verification.resend_verification_request(test_user, "test@example.com")
    assert (await User.get(test_user.id)).preferences["email_verification"][
        "attempts"
    ] == 3


@pytest.mark.asyncio
async def test_stale_verification_request_cannot_overwrite_resent_code(
    test_user, monkeypatch
):
    import hashlib

    codes = iter(["111111", "222222"])

    def next_code():
        code = next(codes)
        return code, hashlib.sha256(code.encode()).hexdigest()

    monkeypatch.setattr(verification, "_generate_otp", next_code)
    await pending(test_user, monkeypatch)
    stale = User.model_validate(test_user.model_dump())
    test_user.preferences = {**test_user.preferences, "other_setting": "new value"}
    await test_user.save()
    await verification.resend_verification_request(test_user, "test@example.com")
    ok, error = await verification.consume_verification_code(stale, "111111")
    assert not ok and error == verification.ERR_INVALID_CODE
    saved = await User.get(test_user.id)
    assert (
        saved.preferences["email_verification"]["hash"]
        == hashlib.sha256(b"222222").hexdigest()
    )
    assert saved.preferences["email_verification"]["attempts"] == 1
    assert saved.preferences["other_setting"] == "new value"
    assert await verification.consume_verification_code(stale, "222222") == (True, None)
    assert (await User.get(test_user.id)).preferences["other_setting"] == "new value"


@pytest.mark.asyncio
async def test_concurrent_code_consumption_is_single_use(test_user, monkeypatch):
    import hashlib

    monkeypatch.setattr(
        verification,
        "_generate_otp",
        lambda: ("123456", hashlib.sha256(b"123456").hexdigest()),
    )
    await pending(test_user, monkeypatch)
    requests = [User.model_validate(test_user.model_dump()) for _ in range(3)]
    results = await asyncio.gather(
        *[verification.consume_verification_code(user, "123456") for user in requests]
    )
    assert results.count((True, None)) == 1
    assert results.count((False, verification.ERR_ALREADY_VERIFIED)) == 2
    assert "email_verification" not in (await User.get(test_user.id)).preferences
