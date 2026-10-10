from app.services.email_service import (
    render_member_provision_email,
    render_public_form_email,
)


def test_member_provision_email_new_account_html():
    msg = render_member_provision_email(
        recipient_email="casper@example.com",
        recipient_name="Casper Ghost",
        workspace_name="V75",
        login_url="http://localhost:9006/login",
        account_email="casper.work@example.com",
        temp_password="TempPass123!",
    )
    assert msg.subject == "Your Integral account"
    assert "TempPass123!" in msg.text
    assert "<pre>" not in msg.html
    assert "Sign in to Integral" in msg.html
    assert "Temporary password" in msg.html


def test_onboarding_form_email_html():
    msg = render_public_form_email(
        recipient_email="casper@example.com",
        recipient_name="Casper",
        form_url="http://localhost:9006/me/assigned-form?entry=1",
        form_title="employee onboarding form",
    )
    assert msg.subject == "Welcome — complete your onboarding"
    assert "Start onboarding" in msg.html
    assert "employment contract" in msg.html
