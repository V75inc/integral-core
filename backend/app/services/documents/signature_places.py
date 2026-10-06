"""Locate signature placeholder boxes for PDF overlay."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SIG_ID_RE = re.compile(r'id="sig-([^"]+)"')


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
                        (
                            merged.get("embedded_png_b64")
                            if mode == "pre_embedded"
                            else (
                                merged.get("embedded_png_b64")
                                or attrs.get("embeddedPngB64")
                                or attrs.get("embedded_png_b64")
                            )
                        )
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


def _places_from_editor_fallback(
    editor_document: Dict[str, Any],
    *,
    page_count: int = 1,
    token_metadata: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    blocks = collect_signature_blocks(editor_document, token_metadata=token_metadata)
    last_page = max(page_count - 1, 0)
    places: List[Dict[str, Any]] = []
    y = 120.0
    for block in blocks:
        places.append(
            {
                "role": block["role"],
                "mode": block.get("mode") or "runtime",
                "label": block.get("label") or block["role"],
                "page": last_page,
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
    """Snap WeasyPrint-derived coords to rendered underline positions in the PDF."""
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
    used_by_page: Dict[int, set[float]] = {}
    try:
        for place in places:
            merged = dict(place)
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
        x1_css, y1_css, y2_css = coords[0], coords[1], coords[3]
        x_pt = _css_px_to_pt(float(x1_css))
        # Underline row sits below the anchor top; approximate before PDF refine.
        underline_bottom_css = float(y1_css) + min(
            20.0, max(12.0, (float(y2_css) - float(y1_css)) * 0.45)
        )
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
        "width": width_pt,
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
    try:
        for block in blocks:
            role = str(block.get("role") or "").strip()
            if not role:
                continue
            label = str(block.get("label") or role.replace("_", " ").title()).strip()
            width = float(block.get("width") or 220)
            height = float(block.get("height") or 48)
            found = False
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

    weasy = extract_places_from_weasyprint(html)
    if weasy:
        places = weasy
    elif pdf_bytes:
        places = extract_places_from_pymupdf(pdf_bytes, blocks=blocks)

    if not places:
        ids = _SIG_ID_RE.findall(html or "")
        if ids:
            last_page = max(page_count - 1, 0)
            places = []
            y = 120.0
            for role in ids:
                places.append(
                    {
                        "role": role,
                        "mode": "runtime",
                        "label": role.replace("_", " ").title(),
                        "page": last_page,
                        "x": 72.0,
                        "y": y,
                        "width": 220.0,
                        "height": 48.0,
                    }
                )
                y += 72.0

    if not places:
        places = _places_from_editor_fallback(
            editor_document,
            page_count=page_count,
            token_metadata=token_metadata,
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
