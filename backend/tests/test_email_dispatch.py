"""Phase 9 Plan 09-03a (NOTIF-02) — EmailChannel render + send tests.

Covers the email channel adapter contract:

- Renders ``<kind>.txt.j2`` + ``<kind>.html.j2`` via Jinja2 with
  ``autoescape=select_autoescape(['html','j2'])`` (T-09-03a-T01 — XSS gate).
- Resolves the recipient from ``user.preferences['email']`` server-side
  (T-09-03a-I01 — no caller override of recipient).
- Invokes ``email_service.send_email`` exactly once per dispatch with
  the rendered text + html payload.
- Returns ``ChannelDispatchResult(status="sent")`` when send_email
  returns True, ``status="failed"`` when False, ``status="skipped"``
  when the recipient is unresolvable.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.models.nodes import Notification, User


@pytest.mark.asyncio
async def test_email_channel_renders_mention_template_and_calls_send_email() -> None:
    """Mention dispatch renders both .txt.j2 + .html.j2 templates and sends.

    The mock captures the EmailMessage and verifies the rendered text
    contains the actor_name and snippet, and the HTML contains the
    escaped variants.
    """
    from app.services.notification_channels.email_channel import EmailChannel

    user = await User.create(
        user_id="auth-em-1",
        display_name="Recipient",
        preferences={"email": "to@example.test"},
    )
    notif = await Notification.create(
        user_id=user.id,
        type="mention",
        content="Alice: hi",
        metadata={"payload": {}, "idempotency_key": None, "dispatched_to": []},
    )
    payload = {
        "actor_name": "Alice",
        "snippet": "Look at this!",
        "resource_label": "Project X",
        "resource_id": "entry-1",
        "app_url": "https://app.integral.test",
    }
    with patch(
        "app.services.notification_channels.email_channel.send_email",
        new=AsyncMock(return_value=True),
    ) as send_mock:
        result = await EmailChannel().dispatch(
            user, "mention", payload, notif, "actor-1", "human"
        )

    assert result.channel == "email"
    assert result.status == "sent"
    assert send_mock.await_count == 1

    sent_msg = send_mock.await_args.args[0]
    assert sent_msg.to == "to@example.test"
    assert "Alice" in sent_msg.text
    assert "Look at this!" in sent_msg.text
    # HTML render: variables interpolated
    assert "Alice" in sent_msg.html
    assert "Project X" in sent_msg.html


@pytest.mark.asyncio
async def test_email_channel_autoescape_blocks_html_injection() -> None:
    """T-09-03a-T01 mitigation: HTML template autoescape gates XSS.

    Payload with ``<script>`` in the snippet must NOT appear unescaped
    in the rendered HTML body. The text template carries the raw value
    (it's plain text, no XSS surface), but the HTML one must escape.
    """
    from app.services.notification_channels.email_channel import EmailChannel

    user = await User.create(
        user_id="auth-em-2",
        display_name="Recipient",
        preferences={"email": "to@example.test"},
    )
    notif = await Notification.create(
        user_id=user.id,
        type="mention",
        content="x",
        metadata={"payload": {}, "idempotency_key": None, "dispatched_to": []},
    )
    payload = {
        "actor_name": "Mallory",
        "snippet": "<script>alert('xss')</script>",
        "resource_label": "X",
        "resource_id": "y",
        "app_url": "https://app.integral.test",
    }
    with patch(
        "app.services.notification_channels.email_channel.send_email",
        new=AsyncMock(return_value=True),
    ) as send_mock:
        await EmailChannel().dispatch(
            user, "mention", payload, notif, "actor-2", "human"
        )
    sent_msg = send_mock.await_args.args[0]
    # HTML must escape the < / > in the script tag.
    assert (
        "<script>alert" not in sent_msg.html
    ), "autoescape must HTML-escape script tags in the rendered body"
    # The escaped form should be present.
    assert "&lt;script&gt;" in sent_msg.html or "&lt;script" in sent_msg.html


@pytest.mark.asyncio
async def test_email_channel_skipped_when_recipient_unresolvable() -> None:
    """User without an email in preferences → ``status="skipped"``."""
    from app.services.notification_channels.email_channel import EmailChannel

    user = await User.create(
        user_id="auth-em-3",
        display_name="No Email",
        preferences={},  # no 'email' key
    )
    notif = await Notification.create(
        user_id=user.id,
        type="mention",
        content="x",
        metadata={"payload": {}, "idempotency_key": None, "dispatched_to": []},
    )
    payload = {
        "actor_name": "X",
        "snippet": "y",
        "resource_label": "z",
        "resource_id": "w",
        "app_url": "u",
    }
    with patch(
        "app.services.notification_channels.email_channel.send_email",
        new=AsyncMock(return_value=True),
    ) as send_mock:
        result = await EmailChannel().dispatch(
            user, "mention", payload, notif, "actor-3", "human"
        )
    assert result.status == "skipped"
    assert result.reason == "no_recipient"
    assert send_mock.await_count == 0


@pytest.mark.asyncio
async def test_email_channel_failed_when_send_email_returns_false() -> None:
    """send_email returning False → ``status="failed"``."""
    from app.services.notification_channels.email_channel import EmailChannel

    user = await User.create(
        user_id="auth-em-4",
        display_name="Recipient",
        preferences={"email": "to@example.test"},
    )
    notif = await Notification.create(
        user_id=user.id,
        type="mention",
        content="x",
        metadata={"payload": {}, "idempotency_key": None, "dispatched_to": []},
    )
    payload = {
        "actor_name": "X",
        "snippet": "y",
        "resource_label": "z",
        "resource_id": "w",
        "app_url": "u",
    }
    with patch(
        "app.services.notification_channels.email_channel.send_email",
        new=AsyncMock(return_value=False),
    ):
        result = await EmailChannel().dispatch(
            user, "mention", payload, notif, "actor-4", "human"
        )
    assert result.status == "failed"
