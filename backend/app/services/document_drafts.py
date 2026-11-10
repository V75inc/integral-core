"""Render and structurally inspect a private session document draft.

Integral's deterministic renderers own the output. This adapter does not
provide a filesystem, execute arbitrary code, or publish a workspace record.
"""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from collections import Counter
from dataclasses import dataclass

from app.services.document_render import extension_for, mime_for, render_document

_FORMATS = frozenset({"markdown", "docx", "pdf", "pptx"})


def _stable_office_archive(content: bytes) -> bytes:
    """Remove ZIP entry timestamps without altering Office package contents."""
    target = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(content), "r") as source,
        zipfile.ZipFile(target, "w") as output,
    ):
        for item in source.infolist():
            data = source.read(item.filename)
            item.date_time = (1980, 1, 1, 0, 0, 0)
            output.writestr(item, data)
    return target.getvalue()


@dataclass(frozen=True)
class RenderedDraft:
    """Rendered bytes and the format-specific structural inspection result."""

    content: bytes
    mime_type: str
    extension: str
    units: int


def render_document_draft(*, title: str, body: str, format: str) -> RenderedDraft:
    """Render a draft and reopen the actual output, failing if unreadable.

    This is a structural check, not a claim that pagination, accessibility,
    source accuracy or visual layout has been reviewed.
    """
    if format not in _FORMATS:
        raise ValueError("Choose markdown, docx, pdf, or pptx for this draft")
    # The renderer writes the title itself. Models often repeat it as the
    # first Markdown heading; omit only an exact duplicate in the export.
    lines = body.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        heading = re.fullmatch(r"\s*#\s+(.+?)\s*#*\s*", line.rstrip("\r\n"))
        if heading and heading.group(1).strip() == title.strip():
            del lines[index]
        break
    rendered_body = "".join(lines)
    content = render_document(title=title, body=rendered_body, renderer=format)
    if format in {"docx", "pptx"}:
        content = _stable_office_archive(content)
    if format == "markdown":
        if not content.decode("utf-8"):
            raise ValueError("Rendered draft is empty")
        units = 1
    elif format == "docx":
        from docx import Document

        doc = Document(io.BytesIO(content))
        units = len(doc.paragraphs) + len(doc.tables)
    elif format == "pptx":
        from pptx import Presentation

        presentation = Presentation(io.BytesIO(content))
        units = len(presentation.slides)
    else:
        from pypdf import PdfReader

        pdf = PdfReader(io.BytesIO(content), strict=True)
        units = len(pdf.pages)
        # ReportLab's built-in PDF fonts can silently replace unsupported
        # characters with squares. Reopen the output and compare visible
        # non-ASCII text rather than claiming that a readable PDF is faithful.
        from app.services.document_markdown import parse_blocks

        visible = [title]
        for block in parse_blocks(rendered_body):
            if block.kind == "table":
                visible.extend(
                    "".join(run.text for run in cell)
                    for row in block.rows
                    for cell in row
                )
            else:
                visible.append(block.text)

        def significant(value: str) -> Counter[str]:
            return Counter(
                char
                for char in unicodedata.normalize("NFC", value)
                if ord(char) > 127
                and not char.isspace()
                and unicodedata.category(char)[0] != "C"
            )

        expected = significant("\n".join(visible))
        actual = significant("\n".join(page.extract_text() or "" for page in pdf.pages))
        if expected - actual:
            raise ValueError(
                "This PDF renderer cannot preserve some characters in the draft. "
                "Choose Word or Markdown to keep the original text."
            )
    if units < 1:
        raise ValueError("Rendered draft has no readable content")
    return RenderedDraft(
        content=content,
        mime_type=mime_for(format),
        extension=extension_for(format),
        units=units,
    )
