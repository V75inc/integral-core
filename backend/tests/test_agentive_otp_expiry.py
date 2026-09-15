"""D-05 OTP expiry regression tests (Plan 01-02 — concrete bodies, no skip placeholders).

Verifies the Task 1 fix to backend/app/agentive/services/channel_identity.py:
  - otp_expires stores FUTURE expiry (utc_now() + timedelta(minutes=10))
  - comparison is `utc_now() > expires` (direction-correct)
  - parse failure fail-closes to "OTP expired" (no silent accept)
"""

from datetime import timedelta

import pytest

from app.agentive.nodes import ChannelIdentity
from app.agentive.services.channel_identity import (
    create_channel_identity_with_otp,
    verify_channel_otp,
)
from app.models.nodes import User
from app.utils.time import utc_now


@pytest.mark.asyncio
async def test_otp_fresh_not_expired():
    """D-05 regression: a freshly-created OTP is NOT expired and verifies successfully."""
    # Set up a User the channel-identity service can attach to.
    user = await User.create(
        user_id="otp-test-user-fresh",
        display_name="OTP Test User Fresh",
        created_at=utc_now().isoformat(),
        updated_at=utc_now().isoformat(),
    )
    result = await create_channel_identity_with_otp(
        user_id=user.id,
        channel="whatsapp",
        channel_user_id="+15555550100",
    )
    assert result["verified"] is False
    assert result["otp_code"], "OTP plaintext must be returned for delivery"

    # Verify with the correct code IMMEDIATELY — must NOT be expired (D-05 regression).
    verify = await verify_channel_otp(
        channel="whatsapp",
        channel_user_id="+15555550100",
        otp_code=result["otp_code"],
    )
    assert verify["verified"] is True, (
        f"Fresh OTP must verify; got: {verify!r} "
        "(D-05 regression: pre-fix, otp_expires stored creation time so OTPs were always 'expired')"
    )


@pytest.mark.asyncio
async def test_otp_past_expiry_returns_expired():
    """D-05 regression: an OTP with otp_expires in the past returns 'OTP expired'."""
    user = await User.create(
        user_id="otp-test-user-past",
        display_name="OTP Test User Past",
        created_at=utc_now().isoformat(),
        updated_at=utc_now().isoformat(),
    )
    created = await create_channel_identity_with_otp(
        user_id=user.id,
        channel="whatsapp",
        channel_user_id="+15555550101",
    )

    # Mutate the stored expiry to one hour in the PAST.
    ci = await ChannelIdentity.get(created["identity_id"])
    prefs = dict(getattr(ci, "preferences", {}) or {})
    prefs["otp_expires"] = (utc_now() - timedelta(hours=1)).isoformat()
    ci.preferences = prefs
    await ci.save()

    verify = await verify_channel_otp(
        channel="whatsapp",
        channel_user_id="+15555550101",
        otp_code=created["otp_code"],
    )
    assert verify["verified"] is False
    assert "expired" in verify["message"].lower()


@pytest.mark.asyncio
async def test_otp_malformed_expires_fails_closed():
    """D-05 regression: malformed otp_expires fails CLOSED — does not silently accept."""
    user = await User.create(
        user_id="otp-test-user-malformed",
        display_name="OTP Test User Malformed",
        created_at=utc_now().isoformat(),
        updated_at=utc_now().isoformat(),
    )
    created = await create_channel_identity_with_otp(
        user_id=user.id,
        channel="whatsapp",
        channel_user_id="+15555550102",
    )

    # Corrupt the stored expiry — non-ISO garbage.
    ci = await ChannelIdentity.get(created["identity_id"])
    prefs = dict(getattr(ci, "preferences", {}) or {})
    prefs["otp_expires"] = "not-a-valid-timestamp"
    ci.preferences = prefs
    await ci.save()

    verify = await verify_channel_otp(
        channel="whatsapp",
        channel_user_id="+15555550102",
        otp_code=created["otp_code"],
    )
    # D-05 fail-closed: parse failure must NOT silently accept.
    assert verify["verified"] is False
    assert "expired" in verify["message"].lower()
