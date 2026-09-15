"""Tests for notification frontend route resolution."""

from app.services.notification_paths import (
    entry_path,
    resolve_notification_action_url,
    resolve_resource_action_url,
)


def test_entry_path_uses_track_deep_link() -> None:
    assert entry_path("e-1", "t-1") == "/tracks/t-1?entry=e-1"


def test_resolve_mention_action_url() -> None:
    url = resolve_notification_action_url(
        "mention",
        {
            "entry_id": "e-1",
            "track_id": "t-1",
            "snippet": "hello",
        },
    )
    assert url == "/tracks/t-1?entry=e-1"


def test_resolve_mention_without_entry_falls_back_to_track() -> None:
    url = resolve_notification_action_url(
        "mention",
        {"track_id": "t-1", "snippet": "hello"},
    )
    assert url == "/tracks/t-1"


def test_resolve_share_entry_requires_track_id() -> None:
    assert (
        resolve_resource_action_url("entry", "e-1", track_id="t-1")
        == "/tracks/t-1?entry=e-1"
    )
    assert resolve_resource_action_url("entry", "e-1") is None


def test_resolve_share_track_and_app() -> None:
    assert resolve_resource_action_url("track", "t-1") == "/tracks/t-1"
    assert resolve_resource_action_url("app", "a-1") == "/apps/a-1"


def test_explicit_action_url_passthrough() -> None:
    url = resolve_notification_action_url(
        "system",
        {"action_url": "/settings"},
    )
    assert url == "/settings"


def test_agent_pending_write_route() -> None:
    assert resolve_notification_action_url("agent_pending_write", {}) == "/approvals"
