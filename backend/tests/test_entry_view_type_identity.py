"""View projections use manifest identity even when display labels differ."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.api.entries import _apply_view_entry_type_filter
from app.models.nodes import EntryType
from app.services.entry_listing import _resolve_view_type_ids
from app.services.entry_type_resolver import canonical_entry_type_key


def test_canonical_identity_precedes_display_label():
    current = EntryType(
        name="Published Worksheet",
        form_schema={"_manifest_entry_type_key": "worksheet_asset"},
    )
    assert canonical_entry_type_key(current) == "worksheet_asset"
    assert (
        canonical_entry_type_key(EntryType(name="Legacy Worksheet"))
        == "legacy_worksheet"
    )


@pytest.mark.asyncio
async def test_listing_and_legacy_filter_preserve_differently_named_type():
    et = EntryType(
        id="n.EntryType.identity",
        name="Published Worksheet",
        form_schema={"_manifest_entry_type_key": "worksheet_asset"},
    )
    cp = SimpleNamespace(nodes=AsyncMock(return_value=[et]))
    track = SimpleNamespace(id="n.Track.identity")
    view = SimpleNamespace(track_id=track.id, entry_type_keys=["worksheet_asset"])
    entry = SimpleNamespace(type_id=et.id)
    with (
        patch("app.models.nodes.Track.get", new=AsyncMock(return_value=track)),
        patch(
            "app.services.app_graph.ensure_track_attached_operational_model",
            new=AsyncMock(return_value=cp),
        ),
        patch(
            "app.api.entries.ensure_track_attached_operational_model",
            new=AsyncMock(return_value=cp),
        ),
    ):
        assert await _resolve_view_type_ids(view) == {et.id}
        assert await _apply_view_entry_type_filter(view, [entry]) == [entry]
        view.entry_type_keys = ["different_asset"]
        assert await _resolve_view_type_ids(view) == set()
        assert await _apply_view_entry_type_filter(view, [entry]) == []
