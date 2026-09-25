"""Relevance-gated page-context stubs (soft vs minimal)."""

from __future__ import annotations

from app.schemas.api.ai_chat import (
    PageContext,
    PageContextBreadcrumb,
    PageContextVisibleData,
    PageContextVisibleTrack,
)
from app.services.chat_page_context import (
    build_minimal_page_context_preamble,
    build_page_context_preamble,
    build_page_context_preamble_for_turn,
    resolve_page_context_focus_posture,
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
        visible_data=PageContextVisibleData(
            tracks=[PageContextVisibleTrack(id="n.Track.pipe", title="Pipeline")],
            total_count=1,
        ),
    )


def test_unrelated_domain_uses_minimal_posture():
    ctx = _sales_ctx()
    utter = "How much did I spend on personal expenses last month?"
    assert resolve_page_context_focus_posture(utter, ctx) == "minimal"
    body = build_page_context_preamble_for_turn(utter, ctx)
    assert "posture=minimal" in body
    assert "n.App.sales" not in body
    assert "Focused:" not in body
    assert "Breadcrumbs:" not in body
    assert "integral_get_page_context" in body


def test_deixis_uses_soft_posture():
    ctx = _sales_ctx()
    utter = "What's on this page?"
    assert resolve_page_context_focus_posture(utter, ctx) == "soft"
    body = build_page_context_preamble_for_turn(utter, ctx)
    assert "posture=soft" in body
    assert "n.App.sales" in body
    assert "not the default answer scope" in body


def test_focus_name_overlap_uses_soft_posture():
    ctx = _sales_ctx()
    utter = "Summarize the Sales pipeline for Q3"
    assert resolve_page_context_focus_posture(utter, ctx) == "soft"
    body = build_page_context_preamble_for_turn(utter, ctx)
    assert "Focused: track=n.Track.pipe" in body


def test_empty_utterance_stays_soft():
    ctx = _sales_ctx()
    assert resolve_page_context_focus_posture("", ctx) == "soft"


def test_soft_stub_includes_relevance_contract():
    body = build_page_context_preamble(_sales_ctx())
    assert "merely because it is on screen" in body


def test_minimal_stub_omits_focus_ids():
    body = build_minimal_page_context_preamble(_sales_ctx())
    assert "n.App.sales" not in body
    assert "Pipeline" not in body
    assert "Page: track_detail" in body
