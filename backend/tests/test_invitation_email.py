"""Phase 3b — invitation email render and delivery hook."""

from app.services.email_service import render_invitation_email


def test_render_invitation_email_subject_and_body():
    msg = render_invitation_email(
        recipient_email="alice@example.com",
        inviter_name="Bob",
        organization_name="Acme Inc",
        role="member",
        acceptance_url="http://localhost:9006/invitations/abc-def",
        message="Welcome!",
        expires_days=14,
    )
    assert msg.to == "alice@example.com"
    assert "Acme Inc" in msg.subject
    assert "Acme Inc" in msg.html
    assert "Bob" in msg.html
    assert "member" in msg.html
    assert "abc-def" in msg.html
    assert "Welcome!" in msg.html
    # Plain-text variant carries everything too.
    assert "Acme Inc" in msg.text
    assert "abc-def" in msg.text
    assert "Welcome!" in msg.text


def test_render_invitation_email_without_inviter_name():
    msg = render_invitation_email(
        recipient_email="alice@example.com",
        inviter_name=None,
        organization_name="Acme",
        role="guest",
        acceptance_url="http://localhost:9006/invitations/xyz",
    )
    assert "guest" in msg.html
    assert "Acme" in msg.html
    # No XSS via empty inviter.
    assert "<script>" not in msg.html
