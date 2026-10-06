"""Unit tests for onboarding form member resolution helpers."""

from types import SimpleNamespace

from app.services.onboarding_form_member import member_field_matches_user


def test_member_field_matches_graph_user_id():
    user = SimpleNamespace(id="n.User.abc", user_id="o.User.auth")
    assert member_field_matches_user("n.User.abc", user) is True


def test_member_field_matches_auth_user_id():
    user = SimpleNamespace(id="n.User.abc", user_id="o.User.auth")
    assert member_field_matches_user("o.User.auth", user) is True


def test_member_field_rejects_other_user():
    user = SimpleNamespace(id="n.User.abc", user_id="o.User.auth")
    assert member_field_matches_user("n.User.other", user) is False
