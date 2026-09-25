"""Page context: tool snapshot + no utterance preamble."""

from __future__ import annotations

from app.schemas.api.ai_chat import (
    PageContext,
    PageContextVisibleData,
    PageContextVisibleEntry,
    SendMessageRequest,
)
from app.services.agent_scope import current_page_context
from app.services.chat_page_context import (
    get_page_context_for_dispatch,
    lightweight_page_context_metadata,
    page_context_snapshot_dict,
    wrap_injected_context,
)
import pytest


def test_lightweight_page_context_metadata_omits_visible_data():
    ctx = PageContext(
        url="/feed",
        route_path="/feed",
        page_kind="feed",
        visible_data=PageContextVisibleData(
            entries=[PageContextVisibleEntry(id="n.Entry.1", title="Hello")]
        ),
    )
    meta = lightweight_page_context_metadata(ctx)
    assert meta == {"url": "/feed", "route_path": "/feed", "page_kind": "feed"}


def test_page_context_snapshot_dict_for_tool():
    ctx = PageContext(
        url="/apps/n.App.abc",
        route_path="/apps/n.App.abc",
        page_kind="app_dashboards",
        focused_app_id="n.App.abc",
        metadata={"app_name": "Sales"},
    )
    snap = page_context_snapshot_dict(ctx)
    assert snap is not None
    assert snap["focused_app_id"] == "n.App.abc"


def test_send_message_request_accepts_page_context():
    req = SendMessageRequest.model_validate(
        {
            "text": "What am I looking at?",
            "page_context": {
                "url": "/tracks/n.Track.abc",
                "route_path": "/tracks/n.Track.abc",
                "page_kind": "track_detail",
            },
        }
    )
    assert req.page_context is not None


def test_wrap_injected_context_still_frames_entity_blocks():
    body = wrap_injected_context("entity_refs", "Entry n.Entry.1")
    assert "BEGIN_CONTEXT_DATA kind=entity_refs" in body


@pytest.mark.asyncio
async def test_get_page_context_for_dispatch_reads_contextvar():
    snap = {
        "url": "/",
        "route_path": "/",
        "page_kind": "mission_control",
        "visible_data": {
            "entries": [{"id": "n.Entry.1", "title": "Priya Patel"}],
            "tracks": [{"id": "n.Track.1", "title": "Customers"}],
            "total_count": 19,
        },
    }
    token = current_page_context.set(snap)
    try:
        out = await get_page_context_for_dispatch(user_id="u1")
        assert out["not_a_query_spec"] is True
        assert (
            out["page_context"]["visible_data"]["entries"][0]["title"] == "Priya Patel"
        )
    finally:
        current_page_context.reset(token)


@pytest.mark.asyncio
async def test_get_page_context_for_dispatch_missing():
    token = current_page_context.set(None)
    try:
        out = await get_page_context_for_dispatch(user_id="u1")
        assert out["error"] == "no_page_context"
    finally:
        current_page_context.reset(token)
