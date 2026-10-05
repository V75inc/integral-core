"""Overlay a PNG signature onto a PDF at stored placeholder coordinates."""

from __future__ import annotations

import base64
import io
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def normalize_signature_png_alpha(png_bytes: bytes) -> bytes:
    """Treat near-white canvas pixels as transparent before PDF compositing."""
    try:
        from PIL import Image
    except ImportError:
        return png_bytes
    try:
        img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    except Exception:  # noqa: BLE001
        return png_bytes
    pixels = img.load()
    width, height = img.size
    changed = False
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if a == 0:
                continue
            if r >= 245 and g >= 245 and b >= 245:
                pixels[x, y] = (r, g, b, 0)
                changed = True
    if not changed:
        return png_bytes
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def decode_signature_png(signature_png: str) -> bytes:
    raw = (signature_png or "").strip()
    if not raw:
        raise ValueError("Signature image is required")
    if raw.startswith("data:"):
        _header, _comma, payload = raw.partition(",")
        raw = payload
    try:
        data = base64.b64decode(raw, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("Invalid signature image encoding") from exc
    if not data:
        raise ValueError("Signature image is empty")
    return data


def _place_for_role(
    places: List[Dict[str, Any]],
    role: str,
    *,
    allow_fallback: bool = False,
) -> Optional[Dict[str, Any]]:
    target = next((p for p in places if str(p.get("role") or "") == role), None)
    if target is None and allow_fallback and places:
        target = places[0]
    return target


def _reportlab_rect_to_fitz(
    page_height_pt: float,
    x: float,
    y: float,
    width: float,
    height: float,
) -> "fitz.Rect":
    """Map ReportLab-style (origin bottom-left, ``y`` = box bottom) to PyMuPDF."""
    import fitz

    y_top = page_height_pt - y - height
    return fitz.Rect(x, y_top, x + width, y_top + height)


def _collect_stamps_by_page(
    pdf_page_count: int,
    signatures: List[Dict[str, Any]],
    places: List[Dict[str, Any]],
    *,
    allow_place_fallback: bool,
) -> Dict[int, List[Tuple[bytes, Dict[str, Any]]]]:
    by_page: Dict[int, List[Tuple[bytes, Dict[str, Any]]]] = {}
    for sig in signatures:
        role = str(sig.get("role") or "")
        png_bytes = sig.get("png_bytes")
        if not png_bytes:
            continue
        target = _place_for_role(places, role, allow_fallback=allow_place_fallback)
        if target is None:
            logger.warning("No signature place for role=%s; skipping overlay", role)
            continue
        page_index = min(max(int(target.get("page") or 0), 0), pdf_page_count - 1)
        by_page.setdefault(page_index, []).append((png_bytes, target))
    return by_page


def _overlay_signatures_pymupdf(
    pdf_bytes: bytes,
    by_page: Dict[int, List[Tuple[bytes, Dict[str, Any]]]],
) -> bytes:
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for page_index, overlays in by_page.items():
            page = doc[page_index]
            page_height = float(page.rect.height)
            for png_bytes, target in overlays:
                x = float(target.get("x") or 72.0)
                y = float(target.get("y") or 72.0)
                width = float(target.get("width") or 200.0)
                height = float(target.get("height") or 48.0)
                rect = _reportlab_rect_to_fitz(page_height, x, y, width, height)
                page.insert_image(
                    rect,
                    stream=normalize_signature_png_alpha(png_bytes),
                    overlay=True,
                )
        return doc.tobytes()
    finally:
        doc.close()


def _overlay_signatures_reportlab_fallback(
    pdf_bytes: bytes,
    by_page: Dict[int, List[Tuple[bytes, Dict[str, Any]]]],
) -> bytes:
    """Legacy merge path — opaque overlay page; prefer PyMuPDF when installed."""
    from pypdf import PdfReader, PdfWriter
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        overlays = by_page.get(i) or []
        if not overlays:
            writer.add_page(page)
            continue
        page_width = float(page.mediabox.width)
        page_height = float(page.mediabox.height)
        packet = io.BytesIO()
        can = canvas.Canvas(packet, pagesize=(page_width, page_height))
        for png_bytes, target in overlays:
            x = float(target.get("x") or 72.0)
            y = float(target.get("y") or 72.0)
            width = float(target.get("width") or 200.0)
            height = float(target.get("height") or 48.0)
            img = ImageReader(
                io.BytesIO(normalize_signature_png_alpha(png_bytes))
            )
            can.drawImage(img, x, y, width=width, height=height, mask="auto")
        can.save()
        packet.seek(0)
        overlay_reader = PdfReader(packet)
        page.merge_page(overlay_reader.pages[0])
        writer.add_page(page)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def overlay_signatures(
    pdf_bytes: bytes,
    signatures: List[Dict[str, Any]],
    *,
    places: List[Dict[str, Any]],
    allow_place_fallback: bool = False,
) -> bytes:
    """Stamp multiple role PNGs onto ``pdf_bytes``."""
    from pypdf import PdfReader

    if not pdf_bytes:
        raise ValueError("PDF bytes missing")
    if not signatures:
        return pdf_bytes

    reader = PdfReader(io.BytesIO(pdf_bytes))
    if not reader.pages:
        raise ValueError("PDF has no pages")

    by_page = _collect_stamps_by_page(
        len(reader.pages),
        signatures,
        places,
        allow_place_fallback=allow_place_fallback,
    )
    if not by_page:
        return pdf_bytes

    try:
        import fitz  # noqa: F401
    except ImportError:
        fitz = None

    if fitz is not None:
        return _overlay_signatures_pymupdf(pdf_bytes, by_page)

    logger.warning(
        "PyMuPDF unavailable; signature overlay uses ReportLab merge "
        "(may obscure text under the stamp box)"
    )
    return _overlay_signatures_reportlab_fallback(pdf_bytes, by_page)


def overlay_signature_png(
    pdf_bytes: bytes,
    png_bytes: bytes,
    places: List[Dict[str, Any]],
    *,
    role: str = "employee_signature",
) -> bytes:
    """Stamp ``png_bytes`` onto ``pdf_bytes`` at the matching runtime place only."""
    if not png_bytes:
        raise ValueError("Signature PNG missing")
    runtime_places = [
        p
        for p in places
        if str(p.get("mode") or "runtime") != "pre_embedded"
    ]
    target = _place_for_role(runtime_places, role, allow_fallback=False)
    if target is None and runtime_places:
        raise ValueError(f"No runtime signature place configured for role={role}")
    stamp_places = [target] if target else []
    return overlay_signatures(
        pdf_bytes,
        [{"role": role, "png_bytes": png_bytes}],
        places=stamp_places,
        allow_place_fallback=False,
    )
