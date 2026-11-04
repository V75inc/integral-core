"""Tests for notification frontend route resolution."""

import copy

import pytest

from app.services.notification_paths import (
    absolute_resource_links,
    entry_path,
    resolve_notification_action_url,
    resolve_resource_action_url,
)


@pytest.mark.parametrize(
    "base",
    [
        "https://workspace.example.test",
        "http://localhost:9141",
        "https://proxy.example.test/integral/",
    ],
)
def test_external_resource_links_use_configured_base_without_changing_evidence(base):
    payload = {
        "action_url": "/apps/n.WorkspaceApp.saved?section=dashboards&dashboard_id=d#chart",
        "tracks": [{"id": "n.Track.saved", "action_url": "/tracks/n.Track.saved"}],
        "entry": {"url": "/tracks/n.Track.saved?entry=n.Entry.saved#notes"},
        "output": {
            "rows": [
                {
                    "url": "/entries/n.Entry.saved",
                    "custom_fields": {"url": "/tracks/private"},
                }
            ]
        },
        "_receipt": [{"url": "/tracks/private"}],
        "receipt": {"url": "/tracks/private"},
        "evidence": {"object_refs": [{"url_path": "/tracks/n.Track.saved"}]},
    }
    original = copy.deepcopy(payload)
    result = absolute_resource_links(payload, base)
    assert result["action_url"] == base.rstrip("/") + payload["action_url"]
    assert (
        result["tracks"][0]["action_url"] == base.rstrip("/") + "/tracks/n.Track.saved"
    )
    assert result["entry"]["url"] == base.rstrip("/") + payload["entry"]["url"]
    assert (
        result["output"]["rows"][0]["url"]
        == base.rstrip("/") + "/entries/n.Entry.saved"
    )
    assert (
        result["output"]["rows"][0]["custom_fields"]
        == payload["output"]["rows"][0]["custom_fields"]
    )
    for key in ("_receipt", "receipt", "evidence"):
        assert result[key] == payload[key]
    assert payload == original


@pytest.mark.parametrize(
    "base",
    [
        "",
        "//foreign.test",
        "javascript:alert(1)",
        "https://user:secret@host.test",
        "https://host.test?query",
        "https://host.test#fragment",
        "https://host.test:bad",
        "https://host.test\\evil",
        "https://ho\nst.test",
    ],
)
def test_invalid_public_base_never_invents_a_deployment(base):
    payload = {"action_url": "/tracks/n.Track.saved"}
    assert absolute_resource_links(payload, base) == payload


@pytest.mark.parametrize(
    "item,path",
    [
        ({"id": "n.Track.saved"}, "/tracks/n.Track.saved"),
        ({"id": "n.WorkspaceApp.saved"}, "/apps/n.WorkspaceApp.saved"),
        ({"id": "n.Workspace.saved"}, "/workspaces/n.Workspace.saved"),
        (
            {"id": "n.Entry.saved", "track_id": "n.Track.saved"},
            "/tracks/n.Track.saved?entry=n.Entry.saved",
        ),
        ({"id": "n.Entry.saved"}, "/entries/n.Entry.saved"),
    ],
)
def test_external_records_get_canonical_links_even_without_navigation_fields(
    item, path
):
    result = absolute_resource_links({"rows": [item]}, "https://workspace.example.test")
    assert result["rows"][0]["action_url"] == "https://workspace.example.test" + path
    assert "action_url" not in item


def test_external_projection_preserves_an_existing_dashboard_destination():
    payload = {
        "app": {
            "id": "n.WorkspaceApp.saved",
            "action_url": "/apps/n.WorkspaceApp.saved?section=dashboards&dashboard_id=d#chart",
        }
    }
    result = absolute_resource_links(payload, "https://workspace.example.test")
    assert result["app"]["action_url"] == (
        "https://workspace.example.test" + payload["app"]["action_url"]
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://another.example/tracks/n.Track.saved",
        "//foreign.example/tracks/n.Track.saved",
        "/help",
        "javascript:alert(1)",
        "/tracks/a/b",
        None,
        123,
    ],
)
def test_external_projection_only_expands_existing_core_routes(url):
    payload = {"url": url, "rows": ["scalar", {"url": url}]}
    assert absolute_resource_links(payload, "https://workspace.example.test") == payload


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
