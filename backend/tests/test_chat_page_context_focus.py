"""Page context: snapshot tool + no utterance preamble."""

from __future__ import annotations

from app.schemas.api.ai_chat import PageContext, PageContextBreadcrumb
from app.services.chat_page_context import (
    lightweight_page_context_metadata,
    page_context_snapshot_dict,
    wrap_injected_context,
)


def _sales_ctx() -> PageContext:
    return PageContext(
        url="/apps/n.App.sales/tracks/n.Track.pipe",
        route_path="/apps/n.App.sales/tracks/n.Track.pipe",
        page_kind="track_detail",
        breadcrumbs=[
            PageContextBreadcrumb(label="Sales"),
            PageContextBreadcrumb(label="Pipeline"),
        ],
        focused_app_id="n.App.sales",
        focused_track_id="n.Track.pipe",
        metadata={"app_title": "Sales", "track_title": "Pipeline"},
    )


def test_snapshot_dict_preserves_focus_for_visitor_data():
    snap = page_context_snapshot_dict(_sales_ctx())
    assert snap is not None
    assert snap["focused_app_id"] == "n.App.sales"
    assert snap["page_kind"] == "track_detail"
    assert snap["metadata"]["app_title"] == "Sales"


def test_lightweight_metadata_is_compact():
    meta = lightweight_page_context_metadata(_sales_ctx())
    assert meta is not None
    assert meta["focused_app_id"] == "n.App.sales"
    assert "breadcrumbs" not in meta


def test_wrap_injected_context_still_frames_entity_blocks():
    body = wrap_injected_context("entity_refs", "Entry n.Entry.1")
    assert "BEGIN_CONTEXT_DATA kind=entity_refs" in body
    assert "Entry n.Entry.1" in body
