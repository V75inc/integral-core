"""Tests for chat page context tool snapshot (utterance stub retired)."""

import pytest

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
)


def test_lightweight_page_context_metadata_omits_visible_data():
    """Persisted metadata is compact and excludes visible_data."""
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
    assert "visible_data" not in meta


def test_build_ui_route_session_extra_is_host_prose():
    from app.schemas.api.ai_chat import PageContext, PageContextBreadcrumb
    from app.services.chat_page_context import build_ui_route_session_extra

    ctx = PageContext(
        url="/apps/n.App.sales",
        route_path="/apps/n.App.sales",
        page_kind="app_dashboards",
        breadcrumbs=[
            PageContextBreadcrumb(label="Business Admin"),
            PageContextBreadcrumb(label="Sales"),
        ],
        focused_app_id="n.App.sales",
        metadata={"app_name": "Sales"},
    )
    block = build_ui_route_session_extra(ctx)
    assert block is not None
    assert block.startswith("UI ROUTE (optional focus")
    assert 'app="Sales"' in block
    assert "app_id=n.App.sales" in block
    assert "integral_get_page_context" in block
    assert build_ui_route_session_extra(None) is None


def test_page_context_snapshot_dict_includes_focus_for_tool():
    """Full dump stays for integral_get_page_context — not for SESSION CONTEXT."""
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
    assert snap["metadata"]["app_name"] == "Sales"


def test_send_message_request_accepts_page_context():
    """The send-message request validates nested page_context."""
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
    assert req.page_context.page_kind == "track_detail"


@pytest.mark.asyncio
async def test_get_page_context_for_dispatch_reads_contextvar():
    """Tool returns the turn-stashed full snapshot including visible_data."""
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
        assert out["source"] == "page_context_snapshot"
        assert (
            out["page_context"]["visible_data"]["entries"][0]["title"] == "Priya Patel"
        )
        entries_only = await get_page_context_for_dispatch(
            user_id="u1", include="visible_entries"
        )
        assert "tracks" not in (
            entries_only["page_context"].get("visible_data") or {}
        ) or not entries_only["page_context"]["visible_data"].get("tracks")
        assert entries_only["page_context"]["visible_data"]["entries"]
    finally:
        current_page_context.reset(token)


@pytest.mark.asyncio
async def test_get_page_context_for_dispatch_missing():
    """No stash → structured error, not an empty success."""
    token = current_page_context.set(None)
    try:
        out = await get_page_context_for_dispatch(user_id="u1")
        assert out["error"] == "no_page_context"
    finally:
        current_page_context.reset(token)
