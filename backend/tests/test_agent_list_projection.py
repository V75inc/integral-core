"""Lean projection for agent list tools.

Auto-pagination hands the agent every track/app, and jvagent then elides the
result at ``observation_max_chars`` and tells the model to re-run the tool. The
breadth was being spent on fields no caller reads: a Track serialized by
``export_node`` is 639 chars across 16 fields, an App 944 across 22. Projecting
to what a listing is actually for cuts both ~4x, which decides whether a
workspace's tracks arrive whole or truncated mid-list.

The subtle failure this guards is the field-name divergence: App uses
``name``/``description`` where Track uses ``title``/``purpose``. Projecting
both through one field list reduces every app to a bare ``{"id": ...}`` — an
18.5x "saving" that throws away everything the agent needs, while still looking
like a win on a size metric.
"""

from __future__ import annotations

import pytest

from app.agentive.tooling.list_pagination import project_agent_list_items

pytestmark = pytest.mark.smoke


def _track(**over):
    base = {
        "id": "n.Track.abc",
        "title": "Invoices",
        "purpose": "Privileged. QuickBooks-synced invoices.",
        "kind": "standard",
        # Everything below is dropped — presentation + provenance the web UI
        # reads off the ROUTE, not off this projection.
        "icon": "receipt",
        "accent_color": "#ff8800",
        "title_fold": "invoices",
        "template_id": "n.Track.tmpl",
        "library_merge_source_id": "n.ContentProfile.xyz",
        "attached_content_profile_id": "n.ContentProfile.abc",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-02-01T00:00:00Z",
        "visibility": "private",
        "owner_id": "n.User.1",
        "workspace_id": "n.Workspace.1",
        "entity": "Track",
    }
    base.update(over)
    return base


def _app(**over):
    base = {
        "id": "n.WorkspaceApp.abc",
        "name": "Finance",
        "description": "QuickBooks-synced invoices + expenses.",
        "lifecycle_state": "installed",
        "version": "1.2.0",
        "settings_schema": {"big": "blob"},
        "settings": {"also": "big"},
        "name_fold": "finance",
        "position": 3,
        "accent_color": "#00aaff",
        "updated_at": "2026-02-01T00:00:00Z",
    }
    base.update(over)
    return base


class TestTrackProjection:
    def test_keeps_what_a_listing_is_for(self):
        out = project_agent_list_items(
            "integral_list_tracks", {"tracks": [_track()], "total": 1}
        )
        assert out["tracks"] == [
            {
                "id": "n.Track.abc",
                "title": "Invoices",
                "purpose": "Privileged. QuickBooks-synced invoices.",
                "kind": "standard",
            }
        ]

    def test_drops_empty_values_rather_than_serializing_them(self):
        # An absent purpose is noise in a listing the model has to read.
        out = project_agent_list_items(
            "integral_list_tracks", {"tracks": [_track(purpose="")]}
        )
        assert "purpose" not in out["tracks"][0]
        assert out["tracks"][0]["title"] == "Invoices"

    def test_preserves_envelope_keys(self):
        # total / has_more / next_cursor are the caller's paging contract.
        payload = {
            "tracks": [_track()],
            "total": 93,
            "has_more": False,
            "next_cursor": None,
        }
        out = project_agent_list_items("integral_list_tracks", payload)
        assert out["total"] == 93
        assert out["has_more"] is False
        assert "next_cursor" in out

    def test_does_not_mutate_the_input(self):
        payload = {"tracks": [_track()]}
        project_agent_list_items("integral_list_tracks", payload)
        assert "icon" in payload["tracks"][0]


class TestAppProjection:
    def test_uses_app_field_names_not_track_ones(self):
        """The regression this file exists for.

        App has no `title`/`purpose`. A shared field list yields `{"id": ...}`
        — a large apparent saving that blinds the agent completely.
        """
        out = project_agent_list_items(
            "integral_list_apps", {"apps": [_app()], "total": 1}
        )
        item = out["apps"][0]
        assert item["name"] == "Finance"
        assert item["description"] == "QuickBooks-synced invoices + expenses."
        assert item["lifecycle_state"] == "installed"
        assert item["version"] == "1.2.0"
        assert set(item) != {"id"}, "app reduced to a bare id"

    def test_drops_the_heavy_settings_blobs(self):
        out = project_agent_list_items("integral_list_apps", {"apps": [_app()]})
        assert "settings_schema" not in out["apps"][0]
        assert "settings" not in out["apps"][0]


class TestPassthrough:
    @pytest.mark.parametrize(
        "tool", ["integral_list_entries", "integral_whoami", "unknown_tool"]
    )
    def test_untouched_for_tools_without_a_field_set(self, tool):
        payload = {"tracks": [_track()], "entries": [{"a": 1}]}
        assert project_agent_list_items(tool, payload) == payload

    def test_survives_a_payload_that_is_not_a_dict(self):
        assert project_agent_list_items("integral_list_tracks", None) is None
        assert project_agent_list_items("integral_list_tracks", []) == []

    def test_survives_a_list_key_that_is_not_a_list(self):
        payload = {"tracks": {"unexpected": "shape"}}
        assert project_agent_list_items("integral_list_tracks", payload) == payload

    def test_keeps_non_dict_rows_verbatim(self):
        # Dropping a row the caller depends on is worse than a fat row.
        payload = {"tracks": [_track(), "sentinel", 42]}
        out = project_agent_list_items("integral_list_tracks", payload)
        assert out["tracks"][1] == "sentinel"
        assert out["tracks"][2] == 42

    def test_empty_list_stays_empty(self):
        out = project_agent_list_items("integral_list_tracks", {"tracks": []})
        assert out["tracks"] == []
