from app.services.onboarding_prompt import (
    absolutize_member_onboarding_url,
    member_assigned_form_relative_url,
)


def test_member_assigned_form_relative_url():
    assert member_assigned_form_relative_url("n.Entry.1") == (
        "/me/assigned-form?entry=n.Entry.1"
    )


def test_absolutize_member_onboarding_url_relative(monkeypatch):
    monkeypatch.setattr(
        "app.config.settings.APP_BASE_URL",
        "http://localhost:9006",
        raising=False,
    )
    assert (
        absolutize_member_onboarding_url("/me/assigned-form?entry=n.Entry.1")
        == "http://localhost:9006/me/assigned-form?entry=n.Entry.1"
    )
    assert (
        absolutize_member_onboarding_url("/hr/employee-onboarding?entry=n.Entry.1")
        == "http://localhost:9006/hr/employee-onboarding?entry=n.Entry.1"
    )


def test_absolutize_member_onboarding_url_already_absolute():
    url = "https://app.example.com/hr/employee-onboarding"
    assert absolutize_member_onboarding_url(url) == url
