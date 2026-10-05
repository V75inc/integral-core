"""ProseMirror / TipTap JSON → sanitized HTML for document templates."""

from __future__ import annotations

import html
import re
from typing import Any, Dict, List, Optional


_ALLOWED_TAGS = frozenset(
    {
        "p",
        "br",
        "strong",
        "b",
        "em",
        "i",
        "u",
        "s",
        "h1",
        "h2",
        "h3",
        "h4",
        "ul",
        "ol",
        "li",
        "blockquote",
        "a",
        "img",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
        "hr",
        "div",
        "span",
        "sup",
        "sub",
    }
)


def _escape(text: str) -> str:
    return html.escape(text or "", quote=True)


_NAMED_COLORS = frozenset(
    {
        "black",
        "white",
        "red",
        "green",
        "blue",
        "yellow",
        "gray",
        "grey",
        "transparent",
    }
)

_SAFE_STYLE = {
    "text-align": re.compile(r"^(left|right|center|justify)$", re.I),
    "line-height": re.compile(r"^[\d.]+$"),
    "padding-left": re.compile(r"^\d+(?:px|em|rem)$", re.I),
    "color": re.compile(
        r"^(#[0-9a-fA-F]{3,8}|rgb\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*\)|"
        r"[a-zA-Z]+)$",
        re.I,
    ),
    "background-color": re.compile(
        r"^(#[0-9a-fA-F]{3,8}|rgb\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*\)|"
        r"[a-zA-Z]+)$",
        re.I,
    ),
    "font-family": re.compile(
        r"^([\"']?[a-zA-Z0-9 \-]+[\"']?(?:,\s*[\"']?[a-zA-Z0-9 \-]+[\"']?)*)$",
        re.I,
    ),
    "font-size": re.compile(r"^\d+(?:\.\d+)?(?:pt|px|%)$", re.I),
    "font-weight": re.compile(
        r"^(normal|bold|bolder|lighter|[1-9]00)$",
        re.I,
    ),
    "font-style": re.compile(r"^(normal|italic|oblique)$", re.I),
    "text-decoration": re.compile(
        r"^(none|underline|line-through|overline)(?:\s+(?:underline|line-through))?$",
        re.I,
    ),
}

_SIGNATURE_STYLE = {
    **dict(_SAFE_STYLE),
    "position": re.compile(r"^relative$", re.I),
    "min-width": re.compile(r"^\d+(?:px|pt|em|rem|%)?$", re.I),
    "min-height": re.compile(r"^\d+(?:px|pt|em|rem|%)?$", re.I),
    "margin": re.compile(r"^[\d.]+(?:px|pt|em|rem)?(?:\s+[\d.]+(?:px|pt|em|rem)?){0,3}$", re.I),
    "width": re.compile(r"^\d+(?:px|pt|em|rem|%)?$", re.I),
    "height": re.compile(r"^\d+(?:px|pt|em|rem|%)?$", re.I),
}

_SIG_ID_RE = re.compile(r"^sig-[a-zA-Z0-9_-]+$")


def _safe_inline_style(tag_html: str, *, style_allowlist: Optional[Dict] = None) -> str:
    style_m = re.search(
        r'style\s*=\s*("([^"]*)"|\'([^\']*)\')', tag_html, flags=re.I
    )
    if not style_m:
        return ""
    raw = style_m.group(2) or style_m.group(3) or ""
    allowed_map = style_allowlist or _SAFE_STYLE
    kept: List[str] = []
    for decl in raw.split(";"):
        if ":" not in decl:
            continue
        prop, val = decl.split(":", 1)
        prop = prop.strip().lower()
        val = val.strip()
        if "url(" in val.lower() or "javascript:" in val.lower():
            continue
        allowed = allowed_map.get(prop)
        if allowed and allowed.match(val):
            if prop in ("color", "background-color") and val.lower() not in _NAMED_COLORS:
                if not (
                    val.startswith("#")
                    or val.lower().startswith("rgb(")
                ):
                    continue
            kept.append(f"{prop}:{val}")
    if not kept:
        return ""
    return f' style="{_escape(";".join(kept))}"'


def _attr_value(tag_html: str, name: str) -> str:
    m = re.search(
        rf'{name}\s*=\s*("([^"]*)"|\'([^\']*)\')',
        tag_html,
        flags=re.I,
    )
    if not m:
        return ""
    return m.group(2) or m.group(3) or ""


def _block_style_attr(attrs: Dict[str, Any]) -> str:
    styles: List[str] = []
    align = attrs.get("textAlign")
    if align in ("left", "right", "center", "justify"):
        styles.append(f"text-align:{align}")
    line_height = attrs.get("lineHeight")
    if line_height:
        raw = str(line_height).strip()
        if re.match(r"^[\d.]+$", raw):
            styles.append(f"line-height:{raw}")
    indent = attrs.get("indent")
    try:
        indent_n = int(indent or 0)
    except (TypeError, ValueError):
        indent_n = 0
    if indent_n > 0:
        styles.append(f"padding-left:{indent_n * 24}px")
    if not styles:
        return ""
    return f' style="{_escape(";".join(styles))}"'


def sanitize_html(raw: str) -> str:
    """Lightweight HTML sanitizer — strips script/style and unknown tags."""
    if not raw:
        return ""
    # Remove script/style blocks entirely.
    cleaned = re.sub(
        r"<(script|style)\b[^>]*>.*?</\1>",
        "",
        raw,
        flags=re.IGNORECASE | re.DOTALL,
    )
    # Strip on* event handlers.
    cleaned = re.sub(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", cleaned, flags=re.I)

    def _filter_tag(m: re.Match) -> str:
        full = m.group(0)
        closing = m.group(1) or ""
        name = (m.group(2) or "").lower()
        if name not in _ALLOWED_TAGS:
            return ""
        if name == "a":
            href = re.search(r'href\s*=\s*("([^"]*)"|\'([^\']*)\')', full, flags=re.I)
            url = ""
            if href:
                url = href.group(2) or href.group(3) or ""
            if url and not (
                url.startswith("http://")
                or url.startswith("https://")
                or url.startswith("mailto:")
                or url.startswith("#")
            ):
                url = "#"
            return f'<{closing}a href="{_escape(url)}">' if not closing else "</a>"
        if name == "img":
            if closing:
                return ""
            src_m = re.search(r'src\s*=\s*("([^"]*)"|\'([^\']*)\')', full, flags=re.I)
            src = ""
            if src_m:
                src = src_m.group(2) or src_m.group(3) or ""
            if src and not (
                src.startswith("http://")
                or src.startswith("https://")
                or src.startswith("data:image/")
            ):
                return ""
            alt_m = re.search(r'alt\s*=\s*("([^"]*)"|\'([^\']*)\')', full, flags=re.I)
            alt = ""
            if alt_m:
                alt = alt_m.group(2) or alt_m.group(3) or ""
            cls_m = re.search(r'class\s*=\s*("([^"]*)"|\'([^\']*)\')', full, flags=re.I)
            cls = ""
            if cls_m:
                raw_cls = cls_m.group(2) or cls_m.group(3) or ""
                safe = " ".join(
                    c for c in raw_cls.split() if c.startswith("doc-letterhead")
                )
                cls = safe
            class_attr = f' class="{_escape(cls)}"' if cls else ""
            return f'<img src="{_escape(src)}" alt="{_escape(alt)}"{class_attr} />'
        # Preserve class on span (field tokens).
        if name == "span" and not closing:
            cls_m = re.search(r'class\s*=\s*("([^"]*)"|\'([^\']*)\')', full, flags=re.I)
            cls = ""
            if cls_m:
                cls = cls_m.group(2) or cls_m.group(3) or ""
            # Only allow our token classes.
            safe = " ".join(
                c
                for c in cls.split()
                if c.startswith("doc-field-token")
                or c.startswith("doc-signature")
                or c.startswith("doc-page-break")
            )
            style = _safe_inline_style(full)
            if safe:
                return f'<span class="{_escape(safe)}"{style}>'
            if style:
                return f"<span{style}>"
            return "<span>"
        if name == "div" and not closing:
            cls = _attr_value(full, "class")
            safe_cls = " ".join(
                c
                for c in cls.split()
                if c.startswith("doc-signature")
                or c.startswith("doc-page-break")
                or c.startswith("doc-header")
                or c.startswith("doc-footer")
                or c.startswith("doc-running-header")
                or c.startswith("doc-running-footer")
                or c.startswith("doc-static-header")
                or c.startswith("doc-static-footer")
                or c.startswith("doc-body")
                or c.startswith("doc-conditional")
                or c.startswith("doc-repeat")
                or c.startswith("doc-letterhead")
            )
            sig_id = _attr_value(full, "id")
            if sig_id and not _SIG_ID_RE.match(sig_id):
                sig_id = ""
            role = _attr_value(full, "data-signature-role")
            mode = _attr_value(full, "data-signature-mode")
            parts = []
            if safe_cls:
                parts.append(f'class="{_escape(safe_cls)}"')
            if sig_id:
                parts.append(f'id="{_escape(sig_id)}"')
            if role:
                parts.append(f'data-signature-role="{_escape(role)}"')
            if mode:
                parts.append(f'data-signature-mode="{_escape(mode)}"')
            style = _safe_inline_style(full, style_allowlist=_SIGNATURE_STYLE)
            if style:
                parts.append(style.strip())
            attrs = (" " + " ".join(parts)) if parts else ""
            return f"<div{attrs}>"
        if name in {"p", "h1", "h2", "h3", "h4", "blockquote", "li", "td", "th"} and not closing:
            return f"<{name}{_safe_inline_style(full)}>"
        return f"<{closing}{name}>"

    cleaned = re.sub(r"<(/)?([a-zA-Z0-9]+)(\s[^>]*)?>", _filter_tag, cleaned)
    return cleaned


def _text_style_span(attrs: Dict[str, Any]) -> str:
    styles: List[str] = []
    color = attrs.get("color")
    if color:
        styles.append(f"color:{color}")
    elif attrs.get("textColor"):
        styles.append(f"color:{attrs.get('textColor')}")
    font_family = attrs.get("fontFamily")
    if font_family:
        styles.append(f"font-family:{font_family}")
    font_size = attrs.get("fontSize")
    if font_size:
        raw = str(font_size).strip()
        if raw and not raw.endswith(("pt", "px", "%")):
            raw = f"{raw}pt"
        styles.append(f"font-size:{raw}")
    font_weight = attrs.get("fontWeight") or attrs.get("font_weight")
    if font_weight:
        fw = str(font_weight).strip().lower()
        if fw in ("700", "bold", "600", "bolder"):
            styles.append("font-weight:700")
        elif fw in ("400", "normal", "regular"):
            styles.append("font-weight:400")
        elif re.match(r"^[1-9]00$", fw):
            styles.append(f"font-weight:{fw}")
    font_style = attrs.get("fontStyle") or attrs.get("font_style")
    if font_style and str(font_style).strip().lower() in ("italic", "oblique"):
        styles.append(f"font-style:{str(font_style).strip().lower()}")
    if not styles:
        return ""
    joined = ";".join(styles)
    safe = _safe_inline_style(f'style="{joined}"')
    return f"<span{safe}>" if safe else ""


def _marks_wrap(text: str, marks: Optional[List[Dict[str, Any]]]) -> str:
    out = text
    style_attrs: Dict[str, Any] = {}
    for mark in marks or []:
        mtype = mark.get("type")
        attrs = mark.get("attrs") or {}
        if mtype == "textStyle":
            style_attrs.update(attrs)
            continue
        if mtype in ("bold", "strong"):
            out = f"<strong>{out}</strong>"
        elif mtype in ("italic", "em"):
            out = f"<em>{out}</em>"
        elif mtype == "underline":
            out = f"<u>{out}</u>"
        elif mtype == "strike":
            out = f"<s>{out}</s>"
        elif mtype == "link":
            href = attrs.get("href") or "#"
            out = f'<a href="{_escape(str(href))}">{out}</a>'
        elif mtype == "code":
            out = f"<code>{out}</code>"
        elif mtype == "highlight":
            bg = attrs.get("color") or "#ffff00"
            style = _safe_inline_style(f'style="background-color:{bg}"')
            out = f"<span{style}>{out}</span>" if style else out
    open_span = _text_style_span(style_attrs)
    if open_span:
        out = f"{open_span}{out}</span>"
    return out


def _node_marks(node: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    """Marks on text nodes; inline atoms (field tokens) carry marks on the node."""
    marks = node.get("marks")
    if isinstance(marks, list) and marks:
        return marks
    return None


def _render_node(
    node: Dict[str, Any],
    formatted: Dict[str, str],
    *,
    eval_condition: Optional[Any] = None,
    embed_pre_signatures: bool = True,
    highlight_field_tokens: bool = True,
) -> str:
    ntype = node.get("type") or ""
    attrs = node.get("attrs") or {}
    content = node.get("content") or []

    if ntype == "text":
        return _marks_wrap(_escape(str(node.get("text") or "")), _node_marks(node))

    if ntype == "hardBreak":
        return "<br/>"

    if ntype == "fieldToken":
        key = str(attrs.get("fieldKey") or attrs.get("field_key") or "")
        label = str(attrs.get("label") or key)
        value = formatted.get(key)
        display = value if value is not None else f"[{label}]"
        escaped = _escape(display)
        if highlight_field_tokens:
            inner = (
                f'<span class="doc-field-token" data-field-key="{_escape(key)}">'
                f"{escaped}</span>"
            )
        else:
            inner = escaped
        return _marks_wrap(inner, _node_marks(node))

    if ntype == "signaturePlaceholder":
        role = str(attrs.get("role") or "employee_signature").strip()
        safe_role = re.sub(r"[^a-zA-Z0-9_-]", "_", role) or "employee_signature"
        mode = str(attrs.get("mode") or "runtime").strip().lower()
        label = str(attrs.get("label") or role.replace("_", " ").title()).strip()
        width = int(attrs.get("width") or 220)
        height = int(attrs.get("height") or 48)
        embedded_b64 = str(
            attrs.get("embeddedPngB64") or attrs.get("embedded_png_b64") or ""
        ).strip()
        inner = (
            f'<div class="doc-signature-line">______________________________</div>'
            f'<div class="doc-signature-label">{_escape(label)}</div>'
        )
        # PDF generation burns pre-embedded PNGs via ``apply_pre_embedded_signatures``
        # — embedding the image in HTML too would duplicate it in the final PDF.
        if mode == "pre_embedded" and embedded_b64 and embed_pre_signatures:
            inner = (
                f'<img class="doc-signature-image" alt="{_escape(label)}" '
                f'src="data:image/png;base64,{embedded_b64}" '
                f'style="max-width:{width}px;max-height:{height}px;" />'
                f'<div class="doc-signature-label">{_escape(label)}</div>'
            )
        return (
            f'<div class="doc-signature" id="sig-{safe_role}" '
            f'data-signature-role="{_escape(role)}" '
            f'data-signature-mode="{_escape(mode)}" '
            f'style="position:relative;min-width:{width}px;min-height:{height}px;'
            f'margin:24px 0;">'
            f"{inner}</div>"
        )

    if ntype == "pageBreak":
        return '<div class="doc-page-break" style="page-break-after:always;"></div>'

    if ntype == "conditionalSection":
        # Phase 8: evaluate attrs.condition when eval_condition provided.
        show = True
        if callable(eval_condition):
            show = bool(eval_condition(attrs))
        if not show:
            return ""
        inner = "".join(
            _render_node(
                c,
                formatted,
                eval_condition=eval_condition,
                embed_pre_signatures=embed_pre_signatures,
            highlight_field_tokens=highlight_field_tokens,
            )
            for c in content
        )
        return f'<div class="doc-conditional">{inner}</div>'

    if ntype == "repeatSection":
        # Phase 8: expand collection — for now render once as placeholder block.
        inner = "".join(
            _render_node(
                c,
                formatted,
                eval_condition=eval_condition,
                embed_pre_signatures=embed_pre_signatures,
            highlight_field_tokens=highlight_field_tokens,
            )
            for c in content
        )
        return f'<div class="doc-repeat">{inner}</div>'

    if ntype == "paragraph":
        inner = "".join(
            _render_node(
                c,
                formatted,
                eval_condition=eval_condition,
                embed_pre_signatures=embed_pre_signatures,
            highlight_field_tokens=highlight_field_tokens,
            )
            for c in content
        )
        return f"<p{_block_style_attr(attrs)}>{inner or '<br/>'}</p>"

    if ntype in ("heading",):
        level = int(attrs.get("level") or 1)
        level = min(max(level, 1), 4)
        inner = "".join(
            _render_node(
                c,
                formatted,
                eval_condition=eval_condition,
                embed_pre_signatures=embed_pre_signatures,
            highlight_field_tokens=highlight_field_tokens,
            )
            for c in content
        )
        return f"<h{level}{_block_style_attr(attrs)}>{inner}</h{level}>"

    if ntype == "blockquote":
        inner = "".join(
            _render_node(
                c,
                formatted,
                eval_condition=eval_condition,
                embed_pre_signatures=embed_pre_signatures,
            highlight_field_tokens=highlight_field_tokens,
            )
            for c in content
        )
        return f"<blockquote{_block_style_attr(attrs)}>{inner}</blockquote>"

    if ntype == "bulletList":
        inner = "".join(
            _render_node(
                c,
                formatted,
                eval_condition=eval_condition,
                embed_pre_signatures=embed_pre_signatures,
            highlight_field_tokens=highlight_field_tokens,
            )
            for c in content
        )
        return f"<ul>{inner}</ul>"

    if ntype == "orderedList":
        inner = "".join(
            _render_node(
                c,
                formatted,
                eval_condition=eval_condition,
                embed_pre_signatures=embed_pre_signatures,
            highlight_field_tokens=highlight_field_tokens,
            )
            for c in content
        )
        return f"<ol>{inner}</ol>"

    if ntype == "listItem":
        inner = "".join(
            _render_node(
                c,
                formatted,
                eval_condition=eval_condition,
                embed_pre_signatures=embed_pre_signatures,
            highlight_field_tokens=highlight_field_tokens,
            )
            for c in content
        )
        return f"<li>{inner}</li>"

    if ntype == "horizontalRule":
        return "<hr/>"

    if ntype == "table":
        inner = "".join(
            _render_node(
                c,
                formatted,
                eval_condition=eval_condition,
                embed_pre_signatures=embed_pre_signatures,
            highlight_field_tokens=highlight_field_tokens,
            )
            for c in content
        )
        return f"<table>{inner}</table>"

    if ntype == "tableRow":
        inner = "".join(
            _render_node(
                c,
                formatted,
                eval_condition=eval_condition,
                embed_pre_signatures=embed_pre_signatures,
            highlight_field_tokens=highlight_field_tokens,
            )
            for c in content
        )
        return f"<tr>{inner}</tr>"

    if ntype in ("tableCell", "tableHeader"):
        tag = "th" if ntype == "tableHeader" else "td"
        inner = "".join(
            _render_node(
                c,
                formatted,
                eval_condition=eval_condition,
                embed_pre_signatures=embed_pre_signatures,
            highlight_field_tokens=highlight_field_tokens,
            )
            for c in content
        )
        return f"<{tag}{_block_style_attr(attrs)}>{inner}</{tag}>"

    if ntype == "image":
        src = str(attrs.get("src") or "")
        alt = str(attrs.get("alt") or "")
        if not src:
            return ""
        return f'<img src="{_escape(src)}" alt="{_escape(alt)}" />'

    if ntype == "doc":
        inner = "".join(
            _render_node(
                c,
                formatted,
                eval_condition=eval_condition,
                embed_pre_signatures=embed_pre_signatures,
            highlight_field_tokens=highlight_field_tokens,
            )
            for c in content
        )
        return inner

    # Unknown node — render children if any.
    return "".join(
        _render_node(
            c,
            formatted,
            eval_condition=eval_condition,
            embed_pre_signatures=embed_pre_signatures,
            highlight_field_tokens=highlight_field_tokens,
        )
        for c in content
    )


def render_document_body_html(
    editor_document: Dict[str, Any],
    formatted: Dict[str, str],
    *,
    embed_pre_signatures: bool = True,
    highlight_field_tokens: bool = True,
) -> str:
    """Render editor JSON to HTML body fragment (before sanitize)."""
    return _render_node(
        editor_document or {"type": "doc", "content": []},
        formatted,
        embed_pre_signatures=embed_pre_signatures,
        highlight_field_tokens=highlight_field_tokens,
    )


def merge_document_to_html(
    editor_document: Dict[str, Any],
    formatted: Dict[str, str],
    *,
    header_html: str = "",
    footer_html: str = "",
    page_size: str = "letter",
    margins: Optional[Dict[str, Any]] = None,
    page_numbers: bool = False,
    title: str = "Document",
    embed_pre_signatures: bool = True,
    highlight_field_tokens: bool = True,
) -> str:
    from app.services.documents.document_theme import (
        build_document_css,
        normalize_margins,
        normalize_page_size,
    )

    body = render_document_body_html(
        editor_document,
        formatted,
        embed_pre_signatures=embed_pre_signatures,
        highlight_field_tokens=highlight_field_tokens,
    )
    header = sanitize_html(header_html) if header_html else ""
    footer = sanitize_html(footer_html) if footer_html else ""
    body = sanitize_html(body)
    ps = normalize_page_size(page_size)
    mg = normalize_margins(margins)
    use_running = bool(header or footer or page_numbers)
    css = build_document_css(
        page_size=ps,
        margins=mg,
        page_numbers=page_numbers,
        running_header_footer=use_running,
        highlight_field_tokens=highlight_field_tokens,
    )
    if use_running:
        header_block = (
            f"<div class='doc-running-header'>{header}</div>" if header else ""
        )
        footer_block = (
            f"<div class='doc-running-footer'>{footer}</div>" if footer else ""
        )
    else:
        header_block = (
            f"<div class='doc-static-header'>{header}</div>" if header else ""
        )
        footer_block = (
            f"<div class='doc-static-footer'>{footer}</div>" if footer else ""
        )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'/>"
        f"<title>{_escape(title)}</title>"
        f"<style>{css}</style></head><body>"
        f"{header_block}"
        f"<div class='doc-body'>{body}</div>"
        f"{footer_block}"
        "</body></html>"
    )
