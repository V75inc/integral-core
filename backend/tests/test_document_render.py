"""Substrate document-render engines (docx / pptx / pdf / markdown)."""

from __future__ import annotations

import pytest

from app.services.document_render import (
    SUPPORTED_RENDERERS,
    extension_for,
    mime_for,
    render_document,
)
from app.services.walkers.plan_rollup import classify_item, plan_overdue


def test_markdown_render_includes_title_body_and_sections() -> None:
    blob = render_document(
        title="Contoso brief",
        body="Overview paragraph.",
        renderer="markdown",
        sections=[{"title": "Next", "body": "Ship the pilot."}],
        rows=[("Owner", "Priya")],
    )
    text = blob.decode("utf-8")
    assert "# Contoso brief" in text
    assert "Overview paragraph." in text
    assert "## Next" in text
    assert "Ship the pilot." in text
    assert "**Owner:** Priya" in text


def test_unknown_renderer_raises() -> None:
    with pytest.raises(ValueError, match="docx|pptx|pdf|markdown"):
        render_document(title="x", body="", renderer="xlsx")


def test_mime_and_extension_cover_supported_set() -> None:
    assert SUPPORTED_RENDERERS == frozenset({"docx", "pptx", "pdf", "markdown"})
    assert extension_for("markdown") == "md"
    assert mime_for("pdf") == "application/pdf"


def test_docx_render_is_nonempty() -> None:
    blob = render_document(title="Letter", body="Hello.", renderer="docx")
    assert blob.startswith(b"PK")  # zip/olex container


def test_classify_item_and_plan_overdue() -> None:
    assert classify_item("done") == (False, False)
    assert classify_item("blocked") == (True, True)
    assert classify_item("in_progress") == (True, False)
    assert plan_overdue({"status": "done", "target_date": "2000-01-01"}) is False
    assert plan_overdue({"status": "on_track", "target_date": "2000-01-01"}) is True
