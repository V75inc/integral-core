"""ReportLab PDF fallback must preserve signature placeholder blocks."""

from __future__ import annotations

from app.services.documents.output import render_pdf_bytes
from app.services.documents.render import merge_document_to_html


def test_reportlab_pdf_includes_signature_placeholder():
    doc = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": "Offer letter"}],
            },
            {
                "type": "signaturePlaceholder",
                "attrs": {
                    "role": "employee_signature",
                    "label": "Employee Signature",
                    "mode": "runtime",
                    "width": 220,
                    "height": 48,
                },
            },
        ],
    }
    html = merge_document_to_html(
        doc,
        {},
        title="Contract",
        embed_pre_signatures=False,
        highlight_field_tokens=False,
    )
    pdf = render_pdf_bytes(html, title="Contract")
    assert pdf.startswith(b"%PDF")

    try:
        import fitz  # PyMuPDF
    except ImportError:
        return

    text = fitz.open(stream=pdf, filetype="pdf")[0].get_text()
    assert "Offer letter" in text
    assert "Employee Signature" in text
    assert "___" in text or "______" in text


def test_signature_overlay_preserves_text_under_transparent_pixels():
    """Stamping ink must not paint a white box over the signature region."""
    try:
        import fitz  # PyMuPDF
        from PIL import Image
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas as rl_canvas
    except ImportError:
        return

    import io

    from app.services.documents.signature_overlay import overlay_signature_png

    buf = io.BytesIO()
    page_w, page_h = letter
    can = rl_canvas.Canvas(buf, pagesize=letter)
    can.setFont("Helvetica", 12)
    sig_x, sig_y, sig_w, sig_h = 72.0, 400.0, 220.0, 48.0
    can.drawString(sig_x, sig_y + 20, "monthly salary of $100")
    can.save()
    pdf_bytes = buf.getvalue()

    img = Image.new("RGBA", (220, 48), (255, 255, 255, 0))
    for x in range(40, 80):
        img.putpixel((x, 24), (0, 0, 0, 255))
    png_buf = io.BytesIO()
    img.save(png_buf, format="PNG")
    png_bytes = png_buf.getvalue()

    places = [
        {
            "role": "employee_signature",
            "mode": "runtime",
            "page": 0,
            "x": sig_x,
            "y": sig_y,
            "width": sig_w,
            "height": sig_h,
        }
    ]
    signed = overlay_signature_png(
        pdf_bytes, png_bytes, places, role="employee_signature"
    )

    page = fitz.open(stream=signed, filetype="pdf")[0]
    assert "monthly salary" in page.get_text()
    hits = page.search_for("monthly")
    assert hits, "expected searchable text under signature region"
    clip = page.get_pixmap(clip=hits[0], dpi=150)
    inkish = sum(
        1
        for y in range(clip.height)
        for x in range(clip.width)
        if max(clip.pixel(x, y)[:3]) < 240
    )
    assert inkish > 0, "overlay painted opaque white over template text"
