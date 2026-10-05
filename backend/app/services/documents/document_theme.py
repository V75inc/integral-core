"""Shared Google Docs–aligned typography and page layout for document templates."""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional, Tuple

PageSizeKey = Literal["letter", "legal", "a4"]

PAGE_SIZE_INCHES: Dict[PageSizeKey, Tuple[float, float]] = {
    "letter": (8.5, 11.0),
    "legal": (8.5, 14.0),
    "a4": (8.27, 11.69),
}

DEFAULT_PAGE_SIZE: PageSizeKey = "letter"
DEFAULT_MARGINS_IN: Dict[str, float] = {
    "top": 1.0,
    "right": 1.0,
    "bottom": 1.0,
    "left": 1.0,
}

BODY_FONT_FAMILY = "Arial, Helvetica, sans-serif"
BODY_FONT_SIZE = "11pt"
BODY_LINE_HEIGHT = "1.15"
BODY_COLOR = "#111111"

HEADING_FONT_FAMILY = "Arial, Helvetica, sans-serif"
H1_SIZE = "20pt"
H2_SIZE = "16pt"
H3_SIZE = "14pt"

# 96 CSS px per inch — keep in sync with frontend documentTheme.ts
PX_PER_INCH = 96


def normalize_page_size(raw: Optional[str]) -> PageSizeKey:
    key = (raw or DEFAULT_PAGE_SIZE).strip().lower()
    if key == "us_legal":
        key = "legal"
    if key in PAGE_SIZE_INCHES:
        return key  # type: ignore[return-value]
    return DEFAULT_PAGE_SIZE


def normalize_margins(raw: Optional[Dict[str, Any]]) -> Dict[str, float]:
    base = dict(DEFAULT_MARGINS_IN)
    if not raw:
        return base
    for side in ("top", "right", "bottom", "left"):
        try:
            val = float(raw.get(side, base[side]))
        except (TypeError, ValueError):
            continue
        base[side] = max(0.25, min(3.0, val))
    return base


def page_content_box_css(page_size: PageSizeKey, margins: Dict[str, float]) -> Dict[str, str]:
    """Editor page card width/height and padding in px."""
    w_in, h_in = PAGE_SIZE_INCHES[page_size]
    return {
        "width_px": str(int(round(w_in * PX_PER_INCH))),
        "min_height_px": str(int(round(h_in * PX_PER_INCH))),
        "padding_top_px": str(int(round(margins["top"] * PX_PER_INCH))),
        "padding_right_px": str(int(round(margins["right"] * PX_PER_INCH))),
        "padding_bottom_px": str(int(round(margins["bottom"] * PX_PER_INCH))),
        "padding_left_px": str(int(round(margins["left"] * PX_PER_INCH))),
    }


def _margin_css(margins: Dict[str, float]) -> str:
    t, r, b, l = (margins[k] for k in ("top", "right", "bottom", "left"))
    return f"{t}in {r}in {b}in {l}in"


def build_document_css(
    *,
    page_size: PageSizeKey = DEFAULT_PAGE_SIZE,
    margins: Optional[Dict[str, float]] = None,
    page_numbers: bool = False,
    running_header_footer: bool = False,
    highlight_field_tokens: bool = False,
) -> str:
    """Return CSS rules for merged document HTML (screen preview + WeasyPrint PDF)."""
    margins = normalize_margins(margins)
    size_key = normalize_page_size(page_size)
    w_in, h_in = PAGE_SIZE_INCHES[size_key]
    page_margin = _margin_css(margins)
    size_decl = f"{w_in}in {h_in}in"

    running_hf = ""
    if running_header_footer:
        running_hf = (
            "@top-left { content: element(doc-header); vertical-align: bottom; "
            "font-size: 9pt; color: #555; } "
            "@bottom-center { content: element(doc-footer); vertical-align: top; "
            "font-size: 9pt; color: #555; } "
        )
        if page_numbers:
            running_hf += (
                "@bottom-right { content: counter(page); font-size: 9pt; color: #555; } "
            )

    return (
        f"@page {{ size: {size_decl}; margin: {page_margin}; {running_hf} }}\n"
        "html,body{margin:0;padding:0;}\n"
        f".doc-body{{font-family:{BODY_FONT_FAMILY};font-size:{BODY_FONT_SIZE};"
        f"line-height:{BODY_LINE_HEIGHT};color:{BODY_COLOR};}}"
        f".doc-body h1,.doc-body h2,.doc-body h3{{font-family:{HEADING_FONT_FAMILY};"
        "font-weight:normal;margin:0 0 0.5em;}"
        ".doc-body strong,.doc-body b{font-weight:700;}"
        ".doc-body em,.doc-body i{font-style:italic;}"
        ".doc-body u{text-decoration:underline;}"
        ".doc-body s,.doc-body strike{text-decoration:line-through;}"
        f".doc-body h1{{font-size:{H1_SIZE};}}"
        f".doc-body h2{{font-size:{H2_SIZE};}}"
        f".doc-body h3{{font-size:{H3_SIZE};}}"
        ".doc-body p,.doc-body blockquote{margin:0 0 0.75em;}"
        ".doc-body blockquote{border-left:3px solid #ccc;padding-left:12px;color:#444;}"
        ".doc-body ul,.doc-body ol{margin:0 0 0.75em;padding-left:1.5em;}"
        ".doc-body li{margin:0 0 0.25em;line-height:1.15;}"
        ".doc-body ul ul,.doc-body ol ol,.doc-body ul ol,.doc-body ol ul{margin:0.25em 0;}"
        ".doc-body table{border-collapse:collapse;width:100%;margin:0 0 0.75em;}"
        ".doc-body td,.doc-body th{border:1px solid #ccc;padding:6px 8px;vertical-align:top;}"
        ".doc-body th{font-weight:700;}"
        + (
            ".doc-field-token{background:#eef6ff;border-radius:3px;padding:0 2px;}"
            if highlight_field_tokens
            else ""
        )
        + (
            ".doc-signature{margin:24px 0;min-width:220px;min-height:48px;"
            "padding:8pt;border:1px solid #ccc;}"
            ".doc-signature-line{font-size:11pt;letter-spacing:1px;margin-bottom:4pt;color:#111;}"
            ".doc-signature-label{font-size:9pt;color:#555;margin-top:2pt;}"
        )
        + ".doc-page-break{page-break-after:always;break-after:page;}"
        ".doc-running-header{position:running(doc-header);font-size:9pt;color:#555;}"
        ".doc-running-footer{position:running(doc-footer);font-size:9pt;color:#555;}"
        ".doc-static-header,.doc-static-footer{font-size:9pt;color:#555;margin:0 0 12px;}"
        ".doc-static-footer{margin:12px 0 0;}"
        ".doc-letterhead-row{display:flex;align-items:flex-start;gap:10px;width:100%;}"
        ".doc-letterhead-row--logo-right{flex-direction:row-reverse;}"
        ".doc-letterhead-row--stacked{flex-direction:column;align-items:flex-start;gap:6px;}"
        ".doc-letterhead-aside{flex:1;min-width:0;}"
        ".doc-letterhead-aside hr{border:none;border-top:1px solid #222;margin:4pt 0 6pt;width:100%;}"
        ".doc-letterhead-columns{display:flex;gap:16pt;width:100%;align-items:flex-start;}"
        ".doc-letterhead-column{flex:1;min-width:0;}"
        ".doc-letterhead-column p{margin:0 0 2pt;}"
        ".doc-letterhead-img{display:block;max-height:56pt;max-width:56pt;width:auto;height:auto;object-fit:contain;flex-shrink:0;}"
        ".doc-letterhead-row--stacked .doc-letterhead-img{max-height:48pt;max-width:100%;}"
        ".doc-letterhead-row p{margin:0;}"
        ".doc-letterhead-aside p{margin:0 0 2pt;}"
    )
