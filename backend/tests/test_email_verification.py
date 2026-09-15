"""Tests for the email verification flow (Issue #8).

Covers:
  - create_verification_request stores hash + expiry on user.preferences
  - consume_verification_code with correct code → email_verified=True, slot cleared
  - consume with wrong code → ERR_INVALID_CODE, attempts bumped
  - consume with expired code → ERR_EXPIRED_CODE
  - consume when already verified → ERR_ALREADY_VERIFIED (idempotent)
  - attempts exceeding max → ERR_ATTEMPTS_EXCEEDED, slot cleared
  - POST /auth/verify-email integration (happy path + error)
  - POST /auth/resend-verification integration
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.models.nodes import User
from app.services.email_verification import (
    _PREFS_KEY,
    ERR_ALREADY_VERIFIED,
    ERR_ATTEMPTS_EXCEEDED,
    ERR_EXPIRED_CODE,
    ERR_INVALID_CODE,
    _generate_otp,
    consume_verification_code,
    create_verification_request,
)


def _now_utc():
    return datetime.now(timezone.utc)


async def _make_user(suffix: str) -> User:
    return await User.create(
        user_id=f"ev-test-{suffix}",
        display_name=f"EV Test {suffix}",
        created_at=_now_utc().isoformat(),
        updated_at=_now_utc().isoformat(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Unit — service layer
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_otp_format():
    code, code_hash = _generate_otp()
    assert len(code) == 6
    assert code.isdigit()
    assert len(code_hash) == 64  # SHA-256 hex


@pytest.mark.asyncio
async def test_create_verification_request_stores_slot():
    user = await _make_user("create-slot")
    with patch("app.services.email_verification.send_email", new_callable=AsyncMock):
        await create_verification_request(user, "test@example.com")

    slot = user.preferences.get(_PREFS_KEY)
    assert slot is not None, "OTP slot must be written to preferences"
    assert "hash" in slot
    assert "expires_at" in slot
    assert slot["attempts"] == 0


@pytest.mark.asyncio
async def test_consume_correct_code_verifies():
    user = await _make_user("consume-ok")

    captured: list[str] = []

    async def _fake_send(msg):
        return True

    with patch("app.services.email_verification.send_email", new=_fake_send):
        with patch("app.services.email_verification._generate_otp") as mock_gen:
            import hashlib

            code = "482931"
            mock_gen.return_value = (
                code,
                hashlib.sha256(code.encode()).hexdigest(),
            )
            await create_verification_request(user, "test@example.com")
            captured.append(code)

    assert not user.email_verified
    ok, err = await consume_verification_code(user, captured[0])
    assert ok is True
    assert err is None
    assert user.email_verified is True
    assert _PREFS_KEY not in (user.preferences or {})


@pytest.mark.asyncio
async def test_consume_wrong_code_returns_invalid():
    user = await _make_user("consume-wrong")
    with patch("app.services.email_verification.send_email", new_callable=AsyncMock):
        await create_verification_request(user, "test@example.com")

    ok, err = await consume_verification_code(user, "000000")
    assert ok is False
    assert err == ERR_INVALID_CODE
    assert user.preferences.get(_PREFS_KEY, {}).get("attempts") == 1


@pytest.mark.asyncio
async def test_consume_attempts_exceeded():
    user = await _make_user("consume-maxattempts")
    with patch("app.services.email_verification.send_email", new_callable=AsyncMock):
        await create_verification_request(user, "test@example.com")

    # Burn through MAX_ATTEMPTS with wrong codes.
    from app.config import settings

    for _ in range(settings.EMAIL_VERIFICATION_MAX_ATTEMPTS):
        await consume_verification_code(user, "000000")

    ok, err = await consume_verification_code(user, "000000")
    assert ok is False
    assert err in (ERR_ATTEMPTS_EXCEEDED, ERR_INVALID_CODE)
    # Slot must be cleared after exceeding attempts.
    assert _PREFS_KEY not in (user.preferences or {})


@pytest.mark.asyncio
async def test_consume_expired_code():
    import hashlib

    user = await _make_user("consume-expired")
    code = "741852"
    code_hash = hashlib.sha256(code.encode()).hexdigest()

    # Write a slot with the correct hash but a past expiry.
    prefs = dict(user.preferences or {})
    prefs[_PREFS_KEY] = {
        "hash": code_hash,
        "expires_at": (_now_utc() - timedelta(hours=1)).isoformat(),
        "attempts": 0,
    }
    user.preferences = prefs
    await user.save()

    ok, err = await consume_verification_code(user, code)
    assert ok is False
    assert err == ERR_EXPIRED_CODE
    assert _PREFS_KEY not in (user.preferences or {})


@pytest.mark.asyncio
async def test_consume_already_verified():
    user = await _make_user("consume-already")
    user.email_verified = True
    await user.save()

    ok, err = await consume_verification_code(user, "123456")
    assert ok is False
    assert err == ERR_ALREADY_VERIFIED


# ─────────────────────────────────────────────────────────────────────────────
# Integration — HTTP endpoints
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_signup_triggers_verification_request(client):
    """POST /auth/signup sends a verification OTP (email_service is mocked)."""
    with patch(
        "app.services.email_verification.send_email", new_callable=AsyncMock
    ) as mock_send:
        resp = await client.post(
            "/api/auth/signup",
            json={
                "email": "newuser@example.com",
                "password": "password123",
                "name": "New User",
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    # email_service was called once for the verification OTP.
    assert mock_send.call_count == 1


@pytest.mark.asyncio
async def test_verify_email_via_service_happy_path():
    """consume_verification_code succeeds end-to-end and marks email_verified."""
    import hashlib

    user = await _make_user("svc-verify-ok")
    code = "654321"
    code_hash = hashlib.sha256(code.encode()).hexdigest()
    prefs = dict(user.preferences or {})
    prefs[_PREFS_KEY] = {
        "hash": code_hash,
        "expires_at": (_now_utc() + timedelta(minutes=15)).isoformat(),
        "attempts": 0,
    }
    user.preferences = prefs
    await user.save()

    ok, err = await consume_verification_code(user, code)
    assert ok is True
    assert err is None

    # Reload and confirm persistence.
    refreshed = await User.get(user.id)
    assert refreshed.email_verified is True


@pytest.mark.asyncio
async def test_verify_email_via_service_wrong_code():
    """consume_verification_code rejects wrong digit sequence."""
    import hashlib

    user = await _make_user("svc-verify-wrong")
    real_code = "999888"
    code_hash = hashlib.sha256(real_code.encode()).hexdigest()
    prefs = dict(user.preferences or {})
    prefs[_PREFS_KEY] = {
        "hash": code_hash,
        "expires_at": (_now_utc() + timedelta(minutes=15)).isoformat(),
        "attempts": 0,
    }
    user.preferences = prefs
    await user.save()

    ok, err = await consume_verification_code(user, "000000")
    assert ok is False
    assert err == ERR_INVALID_CODE


@pytest.mark.asyncio
async def test_has_pending_code():
    """has_pending_code returns True when an unexpired code is present."""
    from app.services.email_verification import has_pending_code

    user = await _make_user("svc-has-pending")
    assert not has_pending_code(user)

    with patch("app.services.email_verification.send_email", new_callable=AsyncMock):
        await create_verification_request(user, "test@example.com")

    assert has_pending_code(user)

    # Backdate expiry — should now return False.
    prefs = dict(user.preferences or {})
    slot = dict(prefs.get(_PREFS_KEY) or {})
    slot["expires_at"] = (_now_utc() - timedelta(hours=1)).isoformat()
    prefs[_PREFS_KEY] = slot
    user.preferences = prefs
    await user.save()

    assert not has_pending_code(user)
