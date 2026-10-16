"""Locate signature placeholder boxes for PDF overlay."""

from __future__ import annotations

import io
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SIG_ID_RE = re.compile(r'id="sig-([^"]+)"')
_SIGMARK_PREFIX = "SIGMARK:"


def signature_marker_text(role: str) -> str:
    """Stable PDF text marker paired with ``render._render_node`` signature blocks."""
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", (role or "").strip()) or "signature"
    return f"{_SIGMARK_PREFIX}{safe}"


def collect_signature_roles(editor_document: Dict[str, Any]) -> List[str]:
    return [b["role"] for b in collect_signature_blocks(editor_document)]


def collect_signature_blocks(
    editor_document: Dict[str, Any],
    *,
    token_metadata: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Walk editor JSON for signaturePlaceholder nodes."""
    meta_by_role: Dict[str, Dict[str, Any]] = {}
    for spec in (token_metadata or {}).get("signatures") or []:
        if isinstance(spec, dict):
            role = str(spec.get("role") or "").strip()
            if role:
                meta_by_role[role] = spec

    blocks: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") == "signaturePlaceholder":
            attrs = node.get("attrs") or {}
            role = str(attrs.get("role") or "employee_signature").strip()
            mode = str(attrs.get("mode") or "runtime").strip().lower()
            block_key = (role, mode)
            if not role or block_key in seen:
                return
            seen.add(block_key)
            merged = dict(meta_by_role.get(role) or {})
            blocks.append(
                {
                    "role": role,
                    "mode": merged.get("mode") or mode,
                    "label": str(
                        merged.get("label")
                        or attrs.get("label")
                        or role.replace("_", " ").title()
                    ),
                    "width": float(merged.get("width") or attrs.get("width") or 220),
                    "height": float(merged.get("height") or attrs.get("height") or 48),
                    "embedded_png_b64": str(
                        merged.get("embedded_png_b64")
                        or attrs.get("embeddedPngB64")
                        or attrs.get("embedded_png_b64")
                        or ""
                    ).strip(),
                }
            )
        for child in node.get("content") or []:
            walk(child)

    walk(editor_document or {})
    if not blocks:
        return [
            {
                "role": "employee_signature",
                "mode": "runtime",
                "label": "Employee Signature",
                "width": 220.0,
                "height": 48.0,
                "embedded_png_b64": "",
            }
        ]
    return blocks


def _page_index_for_signature_block(
    pdf_bytes: bytes,
    *,
    role: str,
    label: str,
    page_count: int,
) -> int:
    """Find which PDF page contains a signature block (ReportLab omits SIGMARK often)."""
    marker = signature_marker_text(role)
    needles = [marker]
    if label:
        needles.append(label)
    try:
        from pypdf import PdfReader

        reader = PdfReader(__import__("io").BytesIO(pdf_bytes))
        for page_index, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            if any(n and n in text for n in needles):
                return page_index
    except Exception:  # noqa: BLE001
        pass
    return 0


def _places_from_editor_fallback(
    editor_document: Dict[str, Any],
    *,
    page_count: int = 1,
    token_metadata: Optional[Dict[str, Any]] = None,
    pdf_bytes: Optional[bytes] = None,
) -> List[Dict[str, Any]]:
    blocks = collect_signature_blocks(editor_document, token_metadata=token_metadata)
    places: List[Dict[str, Any]] = []
    y = 120.0
    for block in blocks:
        role = str(block.get("role") or "").strip()
        label = str(block.get("label") or role).strip()
        if pdf_bytes:
            page_index = _page_index_for_signature_block(
                pdf_bytes, role=role, label=label, page_count=page_count
            )
        else:
            page_index = 0
        places.append(
            {
                "role": block["role"],
                "mode": block.get("mode") or "runtime",
                "label": label or block["role"],
                "page": page_index,
                "x": 72.0,
                "y": y,
                "width": float(block.get("width") or 220),
                "height": float(block.get("height") or 48),
            }
        )
        y += float(block.get("height") or 48) + 24.0
    return places


def _css_px_to_pt(value: float) -> float:
    return float(value) * 72.0 / 96.0


def _reportlab_y_for_line_rect(rect: Any, *, page_height_pt: float) -> float:
    """Map a PyMuPDF underline rect to ReportLab ``drawImage`` bottom-left y.

    ReportLab's ``y`` is the bottom edge of the image — align it with the
    bottom of the placeholder underline row.
    """
    return max(page_height_pt - float(rect.y1), 36.0)


def _underline_rect_near_marker(page: Any, marker_rect: Any) -> Any:
    """Pick the underline row that belongs to the same signature block as ``marker_rect``."""
    underlines = page.search_for("______________________________")
    if not underlines:
        return marker_rect
    my0 = float(marker_rect.y0)
    my1 = float(marker_rect.y1)
    candidates = [
        rect
        for rect in underlines
        if abs(float(rect.y0) - my0) <= 10.0
        or (float(rect.y0) <= my1 + 6.0 and float(rect.y1) >= my0 - 6.0)
    ]
    if not candidates:
        return marker_rect
    return min(candidates, key=lambda rect: abs(float(rect.y0) - my0))


def _place_from_marker_rect(
    page: Any,
    page_index: int,
    block: Dict[str, Any],
    marker_rect: Any,
) -> Dict[str, Any]:
    role = str(block.get("role") or "").strip()
    label = str(block.get("label") or role.replace("_", " ").title()).strip()
    width = float(block.get("width") or 220)
    height = float(block.get("height") or 48)
    page_height_pt = float(page.rect.height)
    line_rect = _underline_rect_near_marker(page, marker_rect)
    return {
        "role": role,
        "mode": block.get("mode") or "runtime",
        "label": label,
        "page": page_index,
        "x": float(line_rect.x0),
        "y": _reportlab_y_for_line_rect(line_rect, page_height_pt=page_height_pt),
        "width": max(width, float(line_rect.width), 120.0),
        "height": height,
    }


def _find_underline_rect_for_label(
    page: Any,
    label: str,
    *,
    used_label_tops: Optional[set[float]] = None,
) -> Any:
    """Return the underline rect above a signature label (next unused occurrence)."""
    if not label:
        return None
    label_hits = page.search_for(label)
    if not label_hits:
        return None
    underlines = page.search_for("______________________________")
    for label_rect in sorted(label_hits, key=lambda rect: float(rect.y0)):
        top_key = round(float(label_rect.y0), 1)
        if used_label_tops and top_key in used_label_tops:
            continue
        candidates = [
            rect for rect in underlines if float(rect.y1) <= float(label_rect.y0) + 4.0
        ]
        if not candidates:
            continue
        underline = min(
            candidates, key=lambda rect: float(label_rect.y0) - float(rect.y0)
        )
        if used_label_tops is not None:
            used_label_tops.add(top_key)
        return underline
    return None


def _refine_places_from_pdf_markers(
    places: List[Dict[str, Any]],
    pdf_bytes: bytes,
) -> List[Dict[str, Any]]:
    """Snap overlay coords to rendered signature blocks in the PDF."""
    if not places or not pdf_bytes:
        return places
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return places

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:  # noqa: BLE001
        logger.exception("PyMuPDF open failed during signature place refinement")
        return places

    refined: List[Dict[str, Any]] = []
    used_markers: set[tuple[int, float, float]] = set()
    used_by_page: Dict[int, set[float]] = {}
    try:
        for place in places:
            merged = dict(place)
            role = str(merged.get("role") or "").strip()
            marker = signature_marker_text(role)
            snapped = False
            for page_index in range(len(doc)):
                page = doc[page_index]
                for hit in page.search_for(marker):
                    key = (
                        page_index,
                        round(float(hit.x0), 1),
                        round(float(hit.y0), 1),
                    )
                    if key in used_markers:
                        continue
                    used_markers.add(key)
                    snapped_place = _place_from_marker_rect(
                        page, page_index, merged, hit
                    )
                    merged.update(snapped_place)
                    snapped = True
                    break
                if snapped:
                    break
            if snapped:
                refined.append(merged)
                continue

            page_index = int(merged.get("page") or 0)
            if page_index < 0 or page_index >= len(doc):
                refined.append(merged)
                continue
            page = doc[page_index]
            page_height_pt = float(page.rect.height)
            used_label_tops = used_by_page.setdefault(page_index, set())
            label = str(merged.get("label") or "").strip()
            rect = _find_underline_rect_for_label(
                page, label, used_label_tops=used_label_tops
            )
            if rect is not None:
                merged["x"] = float(rect.x0)
                merged["y"] = _reportlab_y_for_line_rect(
                    rect, page_height_pt=page_height_pt
                )
                merged["width"] = max(
                    float(merged.get("width") or 220),
                    float(rect.width),
                    120.0,
                )
            refined.append(merged)
    finally:
        doc.close()
    return refined


def _place_from_weasy_anchor(
    anchor_name: str,
    pos: Any,
    *,
    page_index: int,
    page_height_css: float,
) -> Optional[Dict[str, Any]]:
    """Map a WeasyPrint ``id="sig-*"`` anchor to overlay coordinates."""
    if not str(anchor_name).startswith("sig-"):
        return None
    role = str(anchor_name)[4:] or "signature"
    coords = list(pos) if isinstance(pos, (tuple, list)) else None
    if not coords:
        return None

    width_pt = 220.0
    height_pt = 48.0
    if len(coords) >= 4:
        x1_css, y1_css, x2_css, y2_css = coords[0], coords[1], coords[2], coords[3]
        x_pt = _css_px_to_pt(float(x1_css))
        width_pt = max(_css_px_to_pt(float(x2_css) - float(x1_css)), 120.0)
        box_height_css = max(float(y2_css) - float(y1_css), 12.0)
        underline_bottom_css = float(y1_css) + min(22.0, box_height_css * 0.38)
        y_line_bottom_pt = _css_px_to_pt(underline_bottom_css)
    elif len(coords) >= 2:
        x_pt = _css_px_to_pt(float(coords[0]))
        y_line_bottom_pt = _css_px_to_pt(float(coords[1]) + 18.0)
    else:
        return None

    page_height_pt = _css_px_to_pt(page_height_css)
    y_pt = page_height_pt - y_line_bottom_pt
    return {
        "role": role,
        "mode": "runtime",
        "label": role.replace("_", " ").title(),
        "page": page_index,
        "x": x_pt,
        "y": max(y_pt, 36.0),
        "width": width_pt if len(coords) >= 4 else 220.0,
        "height": height_pt,
    }


def extract_places_from_weasyprint(html: str) -> Optional[List[Dict[str, Any]]]:
    """Return placeholder rects from WeasyPrint anchor positions, or None."""
    try:
        from weasyprint import HTML
    except ImportError:
        return None

    try:
        doc = HTML(string=html or "").render()
    except Exception:  # noqa: BLE001
        logger.exception("WeasyPrint render failed during signature place extraction")
        return None

    places: List[Dict[str, Any]] = []
    for page_index, page in enumerate(doc.pages):
        anchors = getattr(page, "anchors", None) or {}
        page_height_css = float(getattr(page, "height", 792) or 792)
        for anchor_name, pos in anchors.items():
            place = _place_from_weasy_anchor(
                str(anchor_name),
                pos,
                page_index=page_index,
                page_height_css=page_height_css,
            )
            if place:
                places.append(place)
    return places or None


def _place_from_pdfplumber_underline(
    page_height_pt: float,
    page_index: int,
    block: Dict[str, Any],
    *,
    underline_word: Dict[str, Any],
) -> Dict[str, Any]:
    """Map pdfplumber word box to ReportLab overlay coordinates (origin bottom-left)."""
    role = str(block.get("role") or "").strip()
    label = str(block.get("label") or role.replace("_", " ").title()).strip()
    width = float(block.get("width") or 220)
    height = float(block.get("height") or 48)
    x = float(underline_word.get("x0") or 72.0)
    underline_bottom = float(
        underline_word.get("bottom") or underline_word.get("top") or 0
    )
    y = max(page_height_pt - underline_bottom, 36.0)
    return {
        "role": role,
        "mode": block.get("mode") or "runtime",
        "label": label,
        "page": page_index,
        "x": x,
        "y": y,
        "width": max(width, float(underline_word.get("width") or 120), 120.0),
        "height": height,
    }


def extract_places_from_pdfplumber(
    pdf_bytes: bytes,
    *,
    blocks: List[Dict[str, Any]],
) -> Optional[List[Dict[str, Any]]]:
    """Locate signature underlines via pdfplumber (ReportLab PDFs without PyMuPDF)."""
    if not pdf_bytes or not blocks:
        return None
    try:
        import pdfplumber
    except ImportError:
        return None

    places: List[Dict[str, Any]] = []
    used_underline_tops: set[tuple[int, float]] = set()
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for block in blocks:
                role = str(block.get("role") or "").strip()
                if not role:
                    continue
                marker = signature_marker_text(role)
                label = str(
                    block.get("label") or role.replace("_", " ").title()
                ).strip()
                placed = False
                for page_index, page in enumerate(pdf.pages):
                    words = page.extract_words() or []
                    page_h = float(page.height or 792)
                    marker_top: Optional[float] = None
                    for word in words:
                        if marker in str(word.get("text") or ""):
                            marker_top = float(word["top"])
                            break
                    underlines = [
                        w for w in words if str(w.get("text") or "").startswith("___")
                    ]
                    if not underlines:
                        continue
                    if marker_top is not None:
                        candidates = [
                            w for w in underlines if float(w["top"]) >= marker_top - 1
                        ]
                    else:
                        label_top = None
                        for word in words:
                            if str(word.get("text") or "") in label.split():
                                label_top = float(word["top"])
                                break
                        if label_top is None:
                            continue
                        candidates = [
                            w
                            for w in underlines
                            if float(w["bottom"]) <= label_top + 4.0
                        ]
                    if not candidates:
                        continue
                    underline = min(candidates, key=lambda w: float(w["top"]))
                    key = (page_index, round(float(underline["top"]), 1))
                    if key in used_underline_tops:
                        continue
                    used_underline_tops.add(key)
                    places.append(
                        _place_from_pdfplumber_underline(
                            page_h, page_index, block, underline_word=underline
                        )
                    )
                    placed = True
                    break
                if not placed:
                    logger.debug(
                        "pdfplumber could not locate signature block for role=%s",
                        role,
                    )
                    return None
    except Exception:  # noqa: BLE001
        logger.exception("pdfplumber signature place extraction failed")
        return None
    return places or None


def extract_places_from_pymupdf(
    pdf_bytes: bytes,
    *,
    blocks: List[Dict[str, Any]],
) -> Optional[List[Dict[str, Any]]]:
    """Locate signature blocks in rendered PDF via label + underline markers."""
    if not pdf_bytes or not blocks:
        return None
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return None

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:  # noqa: BLE001
        logger.exception("PyMuPDF open failed during signature place extraction")
        return None

    places: List[Dict[str, Any]] = []
    used_underline_keys: set = set()
    used_marker_keys: set[tuple[int, float, float]] = set()
    try:
        for block in blocks:
            role = str(block.get("role") or "").strip()
            if not role:
                continue
            label = str(block.get("label") or role.replace("_", " ").title()).strip()
            width = float(block.get("width") or 220)
            height = float(block.get("height") or 48)
            found = False
            marker = signature_marker_text(role)
            for page_index in range(len(doc)):
                page = doc[page_index]
                for hit in page.search_for(marker):
                    key = (
                        page_index,
                        round(float(hit.x0), 1),
                        round(float(hit.y0), 1),
                    )
                    if key in used_marker_keys:
                        continue
                    used_marker_keys.add(key)
                    places.append(_place_from_marker_rect(page, page_index, block, hit))
                    found = True
                    break
                if found:
                    break
            if found:
                continue
            for page_index in range(len(doc)):
                page = doc[page_index]
                page_height_pt = float(page.rect.height)
                rect = None
                used_label_tops: set[float] = set()
                for existing in places:
                    if int(existing.get("page") or 0) != page_index:
                        continue
                    existing_label = str(existing.get("label") or "").strip()
                    if existing_label == label:
                        hits = page.search_for(label)
                        for hit in hits:
                            used_label_tops.add(round(float(hit.y0), 1))

                rect = _find_underline_rect_for_label(
                    page, label, used_label_tops=used_label_tops
                )
                if rect is None and label:
                    label_hits = page.search_for(label)
                    for label_rect in sorted(label_hits, key=lambda r: float(r.y0)):
                        top_key = round(float(label_rect.y0), 1)
                        if top_key in used_label_tops:
                            continue
                        rect = label_rect
                        used_label_tops.add(top_key)
                        break

                if rect is None:
                    for needle in (
                        f"sig-{re.sub(r'[^a-zA-Z0-9_-]', '_', role)}",
                        "______________________________",
                    ):
                        hits = page.search_for(needle)
                        for hit in hits:
                            key = (
                                page_index,
                                round(float(hit.x0), 1),
                                round(float(hit.y0), 1),
                            )
                            if key in used_underline_keys:
                                continue
                            rect = hit
                            used_underline_keys.add(key)
                            break
                        if rect is not None:
                            break

                if rect is None:
                    continue

                places.append(
                    {
                        "role": role,
                        "mode": block.get("mode") or "runtime",
                        "label": label,
                        "page": page_index,
                        "x": float(rect.x0),
                        "y": _reportlab_y_for_line_rect(
                            rect, page_height_pt=page_height_pt
                        ),
                        "width": max(width, float(rect.width), 120.0),
                        "height": height,
                    }
                )
                found = True
                break
            if not found:
                logger.debug(
                    "PyMuPDF could not locate signature block for role %s", role
                )
    finally:
        doc.close()

    return places or None


def _merge_block_metadata(
    places: List[Dict[str, Any]],
    editor_document: Dict[str, Any],
    token_metadata: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    blocks_by_role = {
        b["role"]: b
        for b in collect_signature_blocks(
            editor_document, token_metadata=token_metadata
        )
    }
    out: List[Dict[str, Any]] = []
    for place in places:
        role = str(place.get("role") or "")
        block = blocks_by_role.get(role) or {}
        merged = dict(place)
        merged["mode"] = block.get("mode") or merged.get("mode") or "runtime"
        merged["label"] = block.get("label") or merged.get("label") or role
        merged["width"] = float(block.get("width") or merged.get("width") or 220)
        merged["height"] = float(block.get("height") or merged.get("height") or 48)
        out.append(merged)
    return out


def resolve_signature_places(
    *,
    html: str,
    editor_document: Dict[str, Any],
    pdf_bytes: Optional[bytes] = None,
    token_metadata: Optional[Dict[str, Any]] = None,
    runtime_only: bool = False,
) -> List[Dict[str, Any]]:
    """Best-effort signature placeholder coordinates for overlay."""
    from pypdf import PdfReader

    page_count = 1
    if pdf_bytes:
        try:
            reader = PdfReader(__import__("io").BytesIO(pdf_bytes))
            page_count = len(reader.pages) or 1
        except Exception:  # noqa: BLE001
            page_count = 1

    blocks = collect_signature_blocks(editor_document, token_metadata=token_metadata)
    places: Optional[List[Dict[str, Any]]] = None

    if pdf_bytes:
        places = extract_places_from_pymupdf(pdf_bytes, blocks=blocks)
    if not places and pdf_bytes:
        places = extract_places_from_pdfplumber(pdf_bytes, blocks=blocks)
    if not places:
        places = extract_places_from_weasyprint(html)

    if not places:
        ids = _SIG_ID_RE.findall(html or "")
        if ids:
            blocks_by_role = {str(b.get("role") or ""): b for b in blocks}
            places = []
            y = 120.0
            for role in ids:
                block = blocks_by_role.get(role) or {}
                label = str(
                    block.get("label") or role.replace("_", " ").title()
                ).strip()
                page_index = (
                    _page_index_for_signature_block(
                        pdf_bytes,
                        role=role,
                        label=label,
                        page_count=page_count,
                    )
                    if pdf_bytes
                    else 0
                )
                places.append(
                    {
                        "role": role,
                        "mode": block.get("mode") or "runtime",
                        "label": label,
                        "page": page_index,
                        "x": 72.0,
                        "y": y,
                        "width": float(block.get("width") or 220),
                        "height": float(block.get("height") or 48),
                    }
                )
                y += 72.0

    if not places:
        places = _places_from_editor_fallback(
            editor_document,
            page_count=page_count,
            token_metadata=token_metadata,
            pdf_bytes=pdf_bytes,
        )

    places = _merge_block_metadata(places, editor_document, token_metadata)
    if pdf_bytes:
        places = _refine_places_from_pdf_markers(places, pdf_bytes)
    if runtime_only:
        places = [
            p for p in places if str(p.get("mode") or "runtime") != "pre_embedded"
        ]
    return places


def runtime_signature_places(places: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Places where a signer may draw at consumption time — never pre-embedded."""
    return [p for p in places if str(p.get("mode") or "runtime") != "pre_embedded"]


def pick_runtime_sign_place(
    places: List[Dict[str, Any]],
    *,
    preferred_roles: tuple[str, ...] = ("employee_signature", "signature"),
) -> Optional[Dict[str, Any]]:
    """Choose the employee/runtime block for onboarding accept — not My signature."""
    runtime = runtime_signature_places(places)
    for role in preferred_roles:
        match = next((p for p in runtime if str(p.get("role") or "") == role), None)
        if match is not None:
            return match
    return runtime[0] if runtime else None


def pre_embedded_signature_blocks(
    editor_document: Dict[str, Any],
    token_metadata: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    return [
        b
        for b in collect_signature_blocks(
            editor_document, token_metadata=token_metadata
        )
        if str(b.get("mode") or "") == "pre_embedded"
    ]
