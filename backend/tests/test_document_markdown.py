"""Document bodies are markdown: docx / pdf / pptx get real structure."""

import io

import pytest

from app.services.document_markdown import parse_blocks, parse_inline, plain_text
from app.services.document_render import render_document

BODY = (
    "Intro line.\n\n"
    "# Overview\n\n"
    "Revenue grew **12%** and *costs* held.\n\n"
    "- supplier delays\n- staffing\n\n"
    "## Next steps\n\n"
    "1. finalize plan\n2. review\n"
)


def test_parse_blocks_kinds():
    kinds = [b.kind for b in parse_blocks(BODY)]
    assert kinds == [
        "paragraph", "heading", "paragraph", "bullet", "bullet",
        "heading", "number", "number",
    ]


def test_parse_inline_styles_and_links():
    runs = parse_inline("a **b** *c* `d` [e](http://x)")
    assert ("b", True, False, False) in runs
    assert ("c", False, True, False) in runs
    assert ("d", False, False, True) in runs
    assert "e" in [r[0] for r in runs]
    assert "http" not in "".join(r[0] for r in runs)


def test_plain_body_is_still_paragraphs():
    blocks = parse_blocks("one\n\ntwo")
    assert [b.text for b in blocks] == ["one", "two"]


def test_docx_has_headings_lists_and_bold():
    from docx import Document

    data = render_document("T", BODY, "docx")
    doc = Document(io.BytesIO(data))
    styles = [p.style.name for p in doc.paragraphs]
    assert "Heading 1" in styles and "Heading 2" in styles
    assert styles.count("List Bullet") == 2 and styles.count("List Number") == 2
    assert not any("**" in p.text or "# " in p.text for p in doc.paragraphs)
    bold = [r.text for p in doc.paragraphs for r in p.runs if r.bold]
    assert "12%" in bold


def test_pptx_headings_become_slides():
    from pptx import Presentation

    prs = Presentation(io.BytesIO(render_document("T", BODY, "pptx")))
    titles = [s.shapes.title.text for s in prs.slides]
    assert titles == ["T", "Overview", "Next steps"]


def test_pdf_builds():
    pytest.importorskip("reportlab")
    data = render_document("T", BODY, "pdf")
    assert data.startswith(b"%PDF")


def test_markdown_passthrough():
    assert "# Overview" in render_document("T", BODY, "markdown").decode()


def test_editor_blank_paragraphs_and_entities_do_not_leak():
    blocks = parse_blocks("Title\n\n&nbsp;\n\nA &amp; B\n\n&#160;\n\nEnd")
    assert [b.text for b in blocks] == ["Title", "A & B", "End"]
    runs = parse_inline("5&nbsp;kg")
    assert "&nbsp;" not in "".join(r[0] for r in runs)
