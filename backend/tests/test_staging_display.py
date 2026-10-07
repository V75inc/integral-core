"""Unit tests for human-readable staging labels."""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agentive.tooling import staging_display as sd


class TestUnwrapHandlerRecord:
    def test_unwraps_entry_wrapper(self):
        inner = {"id": "n.Entry.abc", "title": "Q2 Planning"}
        assert sd.unwrap_handler_record({"entry": inner}, "entry") == inner

    def test_unwraps_track_wrapper(self):
        inner = {"id": "n.Track.xyz", "title": "Marketing"}
        assert sd.unwrap_handler_record({"track": inner}, "track") == inner

    def test_returns_none_on_error_envelope(self):
        assert sd.unwrap_handler_record({"error": True, "message": "nope"}) is None

    def test_flat_record_passthrough(self):
        flat = {"id": "n.Entry.1", "title": "Flat"}
        assert sd.unwrap_handler_record(flat, "entry") == flat


class TestEntryDisplayLabel:
    def test_prefers_title(self):
        assert (
            sd.entry_display_label({"title": "  Hello  "}, "n.Entry.fullid") == "Hello"
        )

    def test_falls_back_to_body(self):
        assert (
            sd.entry_display_label({"body": "First line\nSecond"}, "n.Entry.x")
            == "First line"
        )

    def test_falls_back_to_custom_fields_name(self):
        assert (
            sd.entry_display_label(
                {"custom_fields": {"name": "Contact A"}}, "n.Entry.longidhere"
            )
            == "Contact A"
        )

    def test_short_id_when_no_label(self):
        label = sd.entry_display_label({}, "n.Entry.547354fa7bf343dc88611dd5")
        assert label.startswith("Entry …")
        assert "88611dd5" in label
        assert "n.Entry.547354" not in label


class TestCollectNodeIds:
    def test_collects_tag_and_entry_ids(self):
        value = {
            "tags": ["n.Tag.aaa", "n.Tag.bbb"],
            "contact": "n.Entry.ccc",
        }
        tags, entries, tracks = sd._collect_node_ids_in_value(value)
        assert tags == ["n.Tag.aaa", "n.Tag.bbb"]
        assert entries == ["n.Entry.ccc"]
        assert tracks == []

    def test_collects_track_ids(self):
        value = {"track": "n.Track.441f32cbe65b44aa8c4abb88"}
        tags, entries, tracks = sd._collect_node_ids_in_value(value)
        assert tracks == ["n.Track.441f32cbe65b44aa8c4abb88"]


class TestFormatScalarForDiff:
    def test_resolved_tag_shows_name_and_footnote(self):
        rendered = sd.format_scalar_for_diff(
            ["n.Tag.60ee07c0e52f484fa9e4efb3"],
            tag_names={"n.Tag.60ee07c0e52f484fa9e4efb3": "Priority"},
            entry_names={},
        )
        assert "Priority" in rendered
        assert "n.Tag.60ee07" in rendered

    def test_resolved_track_shows_name_only(self):
        rendered = sd.format_scalar_for_diff(
            "n.Track.441f32cbe65b44aa8c4abb88",
            tag_names={},
            entry_names={},
            track_names={"n.Track.441f32cbe65b44aa8c4abb88": "Projects"},
        )
        assert rendered == "Projects"
        assert "n.Track." not in rendered

    def test_long_url_and_qualifying_text_are_not_truncated(self):
        url = "https://example.com/research/" + "full-source-path/" * 8
        claim = "Vendor claims only; " * 8 + "customer demand remains unvalidated."
        for value in (url, claim):
            assert (
                sd.format_scalar_for_diff(value, tag_names={}, entry_names={}) == value
            )

    def test_unresolved_id_truncated(self):
        rendered = sd.format_scalar_for_diff(
            "n.Tag.unknown",
            tag_names={},
            entry_names={},
        )
        assert "n.Tag.unknown" in rendered


@pytest.mark.asyncio
async def test_resolve_node_labels_for_diff(monkeypatch):
    tag_id = "n.Tag.60ee07c0e52f484fa9e4efb3"
    entry_id = "n.Entry.5442eb6eb4e14943a63a396d"
    track_id = "n.Track.441f32cbe65b44aa8c4abb88"

    mock_tag = MagicMock()
    mock_tag.id = tag_id
    mock_tag.name = "Priority"
    mock_tag_cls = MagicMock()
    mock_tag_cls.nodes = AsyncMock(return_value=[mock_tag])

    mock_entry = MagicMock()
    mock_entry.id = entry_id
    mock_entry.title = "Sprint backlog"

    mock_track = MagicMock()
    mock_track.id = track_id
    mock_track.title = "Projects"
    mock_track.nodes = AsyncMock(return_value=[mock_tag])
    monkeypatch.setattr(
        "app.services.permissions.get_user_accessible_entries",
        AsyncMock(return_value=[mock_entry]),
    )
    monkeypatch.setattr(
        "app.services.permissions.get_user_accessible_tracks",
        AsyncMock(return_value=[mock_track]),
    )

    fields: Dict[str, Any] = {
        "tags": [tag_id],
        "owner": entry_id,
        "track": track_id,
    }
    tag_names, entry_names, track_names = await sd.resolve_node_labels_for_diff(
        fields, user_id="u1"
    )
    assert tag_names[tag_id] == "Priority"
    assert entry_names[entry_id] == "Sprint backlog"
    assert track_names[track_id] == "Projects"

    line = await sd.format_fields_patch_for_diff(fields, user_id="u1")
    assert "Priority" in line
    assert "Sprint backlog" in line
    assert "Projects" in line
    assert tag_id in line or "n.Tag.60ee07" in line


@pytest.mark.asyncio
async def test_resolve_node_labels_omits_records_the_actor_cannot_access(monkeypatch):
    entry_id = "n.Entry.private-record"
    track_id = "n.Track.private-track"
    monkeypatch.setattr(
        "app.services.permissions.get_user_accessible_entries",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "app.services.permissions.get_user_accessible_tracks",
        AsyncMock(return_value=[]),
    )

    tags, entries, tracks = await sd.resolve_node_labels_for_diff(
        {"venture": entry_id, "related_track": track_id}, user_id="u1"
    )

    assert tags == {}
    assert entries == {}
    assert tracks == {}
