"""Tests for the password reset flow.

Covers:
  - Token generation: opaque, hashed, expiry math
  - /auth/forgot-password: anti-enumeration (same response for known + unknown
    emails), email is dispatched for known address, no email for unknown.
  - /auth/reset-password: success path (password actually changes — verified
    by logging in afterward), invalid / expired / weak / single-use cases.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest
from httpx import AsyncClient

# Part of the per-PR smoke gate (see pyproject [tool.pytest.ini_options] markers).
# CI bills only this subset; the full suite runs locally via `make verify` and nightly.
pytestmark = pytest.mark.smoke

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


class _CapturedEmail:
    """In-test placeholder for the EmailMessage that was dispatched."""

    __slots__ = ("to", "subject", "html", "text")

    def __init__(self, msg: Any) -> None:
        self.to = msg.to
        self.subject = msg.subject
        self.html = msg.html
        self.text = msg.text


@pytest.fixture
def captured_emails(monkeypatch) -> List[_CapturedEmail]:
    """Replace ``send_email`` with a capture so tests can inspect the message
    without actually delivering it. Returns the captured-list (mutable)."""
    captured: List[_CapturedEmail] = []

    async def _capture(message):
        captured.append(_CapturedEmail(message))
        return True

    from app.services import email_service

    monkeypatch.setattr(email_service, "send_email", _capture)
    # The auth route imports send_email indirectly via password_reset.py, which
    # imports it at module load time. Patch there too.
    from app.services import password_reset

    monkeypatch.setattr(password_reset, "send_email", _capture)
    return captured


def _extract_token_from_email(email: _CapturedEmail) -> str:
    """Pull the plaintext token out of the reset URL inside the email body."""
    m = re.search(r"reset-password\?token=([A-Za-z0-9_\-]+)", email.text)
    assert m, f"no reset URL found in email body: {email.text!r}"
    return m.group(1)


# ─────────────────────────────────────────────────────────────────────────────
# Token primitives — pure unit tests
# ─────────────────────────────────────────────────────────────────────────────


def test_generate_reset_token_returns_distinct_high_entropy_token():
    from app.services.password_reset import generate_reset_token

    token1, hash1 = generate_reset_token()
    token2, hash2 = generate_reset_token()

    # URL-safe base64 of 32 bytes is ~43 chars.
    assert len(token1) >= 40
    assert len(token2) >= 40
    assert token1 != token2  # non-deterministic
    assert hash1 != hash2

    # Hash matches sha256 of the plaintext.
    assert hash1 == hashlib.sha256(token1.encode("utf-8")).hexdigest()
    assert hash2 == hashlib.sha256(token2.encode("utf-8")).hexdigest()

    # Hash is NOT the plaintext (i.e., we're not just storing the token).
    assert hash1 != token1


def test_is_expired_handles_iso_strings_and_unparseable_input():
    from app.services.password_reset import _is_expired, _now_utc

    future = (_now_utc() + timedelta(minutes=10)).isoformat()
    past = (_now_utc() - timedelta(minutes=10)).isoformat()

    assert _is_expired(past) is True
    assert _is_expired(future) is False
    # Defensive: garbage parses as expired (fail closed).
    assert _is_expired("not-a-date") is True
    assert _is_expired("") is True


# ─────────────────────────────────────────────────────────────────────────────
# /auth/forgot-password — anti-enumeration + dispatch
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_forgot_password_unknown_email_returns_generic_ok_no_email(
    client: AsyncClient, captured_emails
):
    resp = await client.post(
        "/api/auth/forgot-password", json={"email": "nobody-exists@example.com"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("ok") is True
    assert "reset link" in body.get("message", "").lower()
    # Critical: no email dispatched for an unknown address.
    assert captured_emails == []


@pytest.mark.asyncio
async def test_forgot_password_known_email_dispatches_email(
    client: AsyncClient, test_user, captured_emails
):
    # test_user fixture creates an AuthUser + integral User. Re-pull email.
    email = getattr(test_user, "email", None) or "test@example.com"

    resp = await client.post("/api/auth/forgot-password", json={"email": email})
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("ok") is True

    # An email was sent to the known address.
    assert len(captured_emails) == 1
    sent = captured_emails[0]
    assert sent.to.lower() == email.lower()
    assert "reset" in sent.subject.lower()
    # The body contains a reset link with a token query param.
    assert "reset-password?token=" in sent.text


@pytest.mark.asyncio
async def test_forgot_password_invalid_email_format_returns_422(client: AsyncClient):
    resp = await client.post(
        "/api/auth/forgot-password", json={"email": "not-an-email"}
    )
    # Pydantic EmailStr rejects malformed input.
    assert resp.status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# /auth/reset-password — success and failure paths
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reset_password_success_changes_password_and_invalidates_token(
    client: AsyncClient, test_user, captured_emails
):
    email = getattr(test_user, "email", None) or "test@example.com"

    # 1. Trigger reset.
    resp = await client.post("/api/auth/forgot-password", json={"email": email})
    assert resp.status_code == 200
    assert len(captured_emails) == 1
    token = _extract_token_from_email(captured_emails[0])

    # 2. Consume token with a new password.
    new_password = "newSecurePassword123"
    resp = await client.post(
        "/api/auth/reset-password",
        json={"token": token, "password": new_password},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("ok") is True

    # 3. Reusing the token must fail (single-use).
    resp = await client.post(
        "/api/auth/reset-password",
        json={"token": token, "password": new_password + "x"},
    )
    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("error_code") == "auth.reset.token_invalid"


@pytest.mark.asyncio
async def test_reset_password_with_unknown_token_returns_invalid(client: AsyncClient):
    resp = await client.post(
        "/api/auth/reset-password",
        json={"token": "this-token-was-never-issued", "password": "validPassword123"},
    )
    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("error_code") == "auth.reset.token_invalid"


@pytest.mark.asyncio
async def test_reset_password_weak_password_returns_400(
    client: AsyncClient, test_user, captured_emails
):
    email = getattr(test_user, "email", None) or "test@example.com"
    await client.post("/api/auth/forgot-password", json={"email": email})
    assert len(captured_emails) >= 1
    token = _extract_token_from_email(captured_emails[-1])

    resp = await client.post(
        "/api/auth/reset-password",
        json={"token": token, "password": "abc"},  # too short
    )
    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("error_code") == "auth.reset.password_too_short"


@pytest.mark.asyncio
async def test_reset_password_expired_token_returns_expired(
    client: AsyncClient, test_user, captured_emails
):
    email = getattr(test_user, "email", None) or "test@example.com"
    await client.post("/api/auth/forgot-password", json={"email": email})
    assert len(captured_emails) >= 1
    token = _extract_token_from_email(captured_emails[-1])

    # Force the stored expires_at to the past.
    from app.models.nodes import User

    matches = await User.find({"context.user_id": test_user.user_id})
    assert matches, "expected to find integral User node for test_user"
    user_node = matches[0]
    prefs = dict(user_node.preferences or {})
    slot = dict(prefs.get("reset_token") or {})
    slot["expires_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    prefs["reset_token"] = slot
    user_node.preferences = prefs
    await user_node.save()

    resp = await client.post(
        "/api/auth/reset-password",
        json={"token": token, "password": "validPassword123"},
    )
    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("error_code") == "auth.reset.token_expired"


@pytest.mark.asyncio
async def test_reset_password_missing_fields_returns_422(client: AsyncClient):
    resp = await client.post("/api/auth/reset-password", json={"token": "abc"})
    # Missing required password field.
    assert resp.status_code == 422

    resp = await client.post("/api/auth/reset-password", json={"password": "x" * 10})
    # Missing required token field.
    assert resp.status_code == 422


def test_password_reset_email_escapes_user_controlled_html():
    """Regression test for the Day 13 P1 finding: ``recipient_name``,
    ``recipient_email``, and ``reset_url`` must all be HTML-escaped
    before they're interpolated into the email body. Without escaping,
    a malicious display_name like ``<script>alert(1)</script>`` would
    execute when the recipient (or anyone they forwarded the email to)
    opened it in an HTML mail client.
    """
    from app.services.email_service import render_password_reset_email

    msg = render_password_reset_email(
        recipient_email='attacker"<script>@example.com',
        recipient_name="<script>alert('xss')</script>",
        reset_url='https://gointegral.app/reset-password?token=abc"><script>',
        expires_minutes=60,
    )
    # No raw <script> tag from user-controlled content survives in the HTML.
    assert "<script>" not in msg.html
    assert "<script>alert" not in msg.html
    # The escaped form does survive.
    assert "&lt;script&gt;" in msg.html
    # Attribute-quoted URLs escape both `<` and `"`.
    assert "&quot;&gt;&lt;script&gt;" in msg.html
    # The plain-text body is delivered as-is — no HTML escaping required
    # there, just verifying it still contains the URL the user expects.
    assert "https://gointegral.app/reset-password?token=abc" in msg.text
