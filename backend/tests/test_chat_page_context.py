"""Tests for chat page context preamble building."""

from app.schemas.api.ai_chat import (
    PageContext,
    PageContextBreadcrumb,
    PageContextVisibleData,
    PageContextVisibleEntry,
    PageContextVisibleTrack,
    SendMessageRequest,
)
from app.services.chat_page_context import (
    build_page_context_preamble,
    lightweight_page_context_metadata,
)


def test_build_page_context_preamble_track_detail():
    """Preamble includes URL, breadcrumbs, focus, and visible entries."""
    ctx = PageContext(
        url="/tracks/n.Track.abc?view=calendar&entry=n.Entry.xyz",
        route_path="/tracks/n.Track.abc",
        page_kind="track_detail",
        breadcrumbs=[
            PageContextBreadcrumb(label="Home", to="/"),
            PageContextBreadcrumb(label="Sprint board"),
        ],
        focused_track_id="n.Track.abc",
        focused_view_id="n.View.cal",
        focused_entry_id="n.Entry.xyz",
        visible_data=PageContextVisibleData(
            entries=[
                PageContextVisibleEntry(
                    id="n.Entry.xyz",
                    title="Ship auth refactor",
                    status="open",
                    entry_type="task",
                )
            ]
        ),
        metadata={"view_name": "Calendar", "view_type": "calendar"},
    )
    preamble = build_page_context_preamble(ctx)
    assert "[Page context]" in preamble
    assert "URL: /tracks/n.Track.abc?view=calendar&entry=n.Entry.xyz" in preamble
    assert "Breadcrumbs: Home › Sprint board" in preamble
    assert "Page: track_detail" in preamble
    assert "track=n.Track.abc" in preamble
    assert 'Focused entry: n.Entry.xyz — "Ship auth refactor"' in preamble
    assert "Visible entries (1 shown):" in preamble
    assert "view_name=Calendar" in preamble


def test_build_page_context_preamble_app_detail_tracks():
    """Preamble lists visible tracks with total_count when truncated."""
    ctx = PageContext(
        url="/apps/n.App.abc",
        route_path="/apps/n.App.abc",
        page_kind="app_detail",
        focused_app_id="n.App.abc",
        visible_data=PageContextVisibleData(
            tracks=[
                PageContextVisibleTrack(id="n.Track.1", title="Backlog"),
                PageContextVisibleTrack(id="n.Track.2", title="Done"),
            ],
            total_count=40,
        ),
    )
    preamble = build_page_context_preamble(ctx)
    assert "Visible tracks (2 shown of 40):" in preamble
    assert "n.Track.1 | Backlog" in preamble


def test_build_page_context_preamble_empty_returns_blank():
    """None page context yields an empty preamble."""
    assert build_page_context_preamble(None) == ""


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
