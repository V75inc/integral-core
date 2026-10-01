"""Deterministic document render engines (docx / pptx / pdf / markdown).

Format conversion lives in the substrate so App bundles compose and attach
artifacts without owning renderer implementations. Privilege redaction,
source fingerprinting, and attachment wiring stay in the calling bundle
tool — this module only turns title / body / sections / detail rows into
bytes.

Reached from trusted bundle tools via ``ToolContext.document_render``
(I-HOOK-01: bundles do not import this module directly).
"""

from __future__ import annotations

import io
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.services.document_markdown import (
    parse_blocks,
    plain_text,
    split_into_slides,
)

_MIME = {
    "docx": (
        "application/vnd.openxmlformats-officedocument" ".wordprocessingml.document"
    ),
    "pptx": (
        "application/vnd.openxmlformats-officedocument" ".presentationml.presentation"
    ),
    "pdf": "application/pdf",
    "markdown": "text/markdown",
}
_EXT = {"docx": "docx", "pptx": "pptx", "pdf": "pdf", "markdown": "md"}

SUPPORTED_RENDERERS = frozenset(_EXT)


def mime_for(renderer: str) -> str:
    """MIME type for a supported renderer key."""
    return _MIME[renderer]


def extension_for(renderer: str) -> str:
    """File extension (no dot) for a supported renderer key."""
    return _EXT[renderer]


def _render_markdown(
    title: str,
    body: str,
    rows: Sequence[Tuple[str, str]],
    sections: Sequence[Dict[str, str]],
) -> bytes:
    lines: List[str] = [f"# {title or 'Untitled'}", ""]
    if body:
        lines += [body, ""]
    for sec in sections:
        if sec.get("title"):
            lines += [f"## {sec['title']}", ""]
        if sec.get("body"):
            lines += [sec["body"], ""]
    if rows:
        lines += ["## Details", ""]
        lines += [f"- **{k}:** {v}" for k, v in rows]
        lines += [""]
    return "\n".join(lines).encode("utf-8")


def _render_docx(
    title: str,
    body: str,
    rows: Sequence[Tuple[str, str]],
    sections: Sequence[Dict[str, str]],
) -> bytes:
    from docx import Document  # lazy — packaged dep

    doc = Document()
    doc.add_heading(title or "Untitled", level=0)
    for block in parse_blocks(body):
        if block.kind == "heading":
            doc.add_heading(block.text, level=min(block.level, 3))
        elif block.kind == "rule":
            continue
        else:
            style = {"bullet": "List Bullet", "number": "List Number"}.get(block.kind)
            para = doc.add_paragraph(style=style) if style else doc.add_paragraph()
            for text, bold, italic, code in block.runs:
                run = para.add_run(text)
                run.bold = bold or None
                run.italic = italic or None
                if code:
                    run.font.name = "Courier New"
    for sec in sections:
        if sec.get("title"):
            doc.add_heading(sec["title"], level=1)
        if sec.get("body"):
            doc.add_paragraph(sec["body"])
    if rows:
        doc.add_heading("Details", level=1)
        for key, value in rows:
            doc.add_paragraph(f"{key}: {value}")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _render_pptx(
    title: str,
    body: str,
    rows: Sequence[Tuple[str, str]],
    sections: Sequence[Dict[str, str]],
) -> bytes:
    from pptx import Presentation  # lazy — packaged dep

    prs = Presentation()
    intro, body_slides = split_into_slides(parse_blocks(body))
    title_slide = prs.slides.add_slide(prs.slide_layouts[0])
    title_slide.shapes.title.text = title or "Untitled"
    intro_text = plain_text(intro)
    if intro_text and len(title_slide.placeholders) > 1:
        title_slide.placeholders[1].text = intro_text[:200]
    # A heading in the document starts a slide; its text is the slide body.
    for heading, blocks in body_slides:
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = heading or "Section"
        if len(slide.placeholders) > 1:
            slide.placeholders[1].text = plain_text(blocks)
    for sec in sections:
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = sec.get("title") or "Section"
        if len(slide.placeholders) > 1:
            slide.placeholders[1].text = sec.get("body") or ""
    if rows:
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = "Details"
        if len(slide.placeholders) > 1:
            slide.placeholders[1].text = "\n".join(f"{k}: {v}" for k, v in rows)
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def _render_pdf(
    title: str,
    body: str,
    rows: Sequence[Tuple[str, str]],
    sections: Sequence[Dict[str, str]],
) -> bytes:
    try:
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    except ImportError as exc:  # pragma: no cover - optional dep
        raise RuntimeError("pdf rendering requires the 'reportlab' package") from exc

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=LETTER)
    styles = getSampleStyleSheet()
    flow: List[Any] = [
        Paragraph(title or "Untitled", styles["Title"]),
        Spacer(1, 12),
    ]
    number = 0
    for block in parse_blocks(body):
        if block.kind == "heading":
            number = 0
            style = styles[f"Heading{min(block.level, 3)}"]
            flow += [Paragraph(_pdf_markup(block.runs), style)]
        elif block.kind == "bullet":
            flow += [Paragraph(_pdf_markup(block.runs), styles["BodyText"], bulletText="•")]
        elif block.kind == "number":
            number += 1
            flow += [Paragraph(_pdf_markup(block.runs), styles["BodyText"], bulletText=f"{number}.")]
        elif block.kind == "paragraph":
            number = 0
            flow += [Paragraph(_pdf_markup(block.runs), styles["BodyText"]), Spacer(1, 6)]
    for sec in sections:
        if sec.get("title"):
            flow += [Paragraph(sec["title"], styles["Heading2"])]
        if sec.get("body"):
            flow += [Paragraph(sec["body"], styles["BodyText"]), Spacer(1, 6)]
    if rows:
        flow += [Paragraph("Details", styles["Heading2"])]
        for key, value in rows:
            flow += [Paragraph(f"{key}: {value}", styles["BodyText"])]
    doc.build(flow)
    return buf.getvalue()


def _pdf_markup(runs: Sequence[Tuple[str, bool, bool, bool]]) -> str:
    """Runs as reportlab paragraph markup (text escaped, emphasis as tags)."""
    from xml.sax.saxutils import escape

    out: List[str] = []
    for text, bold, italic, code in runs:
        piece = escape(text).replace("\n", "<br/>")
        if code:
            piece = f'<font name="Courier">{piece}</font>'
        if bold:
            piece = f"<b>{piece}</b>"
        if italic:
            piece = f"<i>{piece}</i>"
        out.append(piece)
    return "".join(out)


_RENDERERS = {
    "docx": _render_docx,
    "pptx": _render_pptx,
    "pdf": _render_pdf,
    "markdown": _render_markdown,
}


def render_document(
    title: str,
    body: str,
    renderer: str,
    sections: Optional[Sequence[Dict[str, str]]] = None,
    rows: Optional[Sequence[Tuple[str, str]]] = None,
) -> bytes:
    """Render title/body/sections/rows to ``renderer`` bytes.

    ``renderer`` must be one of ``docx``, ``pptx``, ``pdf``, ``markdown``.
    """
    key = (renderer or "").strip().lower()
    fn = _RENDERERS.get(key)
    if fn is None:
        raise ValueError(
            "renderer must be one of docx|pptx|pdf|markdown " f"(got {renderer!r})"
        )
    return fn(title, body, list(rows or ()), list(sections or ()))


__all__ = [
    "SUPPORTED_RENDERERS",
    "extension_for",
    "mime_for",
    "render_document",
]
