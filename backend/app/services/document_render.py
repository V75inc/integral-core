"""Deterministic document render engines (docx / pptx / pdf / markdown).

Format conversion lives in the substrate so App bundles compose and attach
artifacts without owning renderer implementations. Privilege redaction,
source fingerprinting, and attachment wiring stay in the calling bundle
tool - this module only turns title / body / sections / detail rows into
bytes.

The body is markdown (see ``document_markdown``). Output is styled with a
``Theme`` (coloured heading hierarchy, a title block, shaded tables and
callouts, page-numbered footer). A ``template`` (a .docx or .pptx) is used as
the base file, so a company letterhead keeps its header, footer and styles.

Reached from trusted bundle tools via ``ToolContext.document_render``
(I-HOOK-01: bundles do not import this module directly).
"""

from __future__ import annotations

import io
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.services.document_markdown import (
    Block,
    Run,
    parse_blocks,
    plain_text,
    split_into_slides,
)
from app.services.document_theme import Theme, make_theme

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


def _all_blocks(body: str, sections: Sequence[Dict[str, str]]) -> List[Block]:
    """Body blocks, then each section as a heading plus its (markdown) body."""
    blocks = parse_blocks(body)
    for sec in sections:
        if sec.get("title"):
            blocks.append(Block("heading", [Run(sec["title"])], 2))
        if sec.get("body"):
            blocks += parse_blocks(sec["body"])
    return blocks


# ----------------------------------------------------------------------------
# markdown
# ----------------------------------------------------------------------------


def _render_markdown(
    title: str,
    body: str,
    rows: Sequence[Tuple[str, str]],
    sections: Sequence[Dict[str, str]],
    theme: Theme,
    template: Optional[bytes],
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


# ----------------------------------------------------------------------------
# docx
# ----------------------------------------------------------------------------


def _rgb(hex_color: str):
    from docx.shared import RGBColor

    return RGBColor.from_string(hex_color.upper())


def _shade(element, fill: str) -> None:
    """Background fill on a table cell (``tcPr``) or paragraph (``pPr``)."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    shd = element.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        element.append(shd)
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)


def _borders(ppr, **sides: Tuple[str, int, int]) -> None:
    """Paragraph borders: side -> (colour, size in 1/8 pt, space in pt)."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    old = ppr.find(qn("w:pBdr"))
    if old is not None:
        ppr.remove(old)
    bdr = OxmlElement("w:pBdr")
    for side in ("top", "left", "bottom", "right"):
        if side in sides:
            color, size, space = sides[side]
            el = OxmlElement(f"w:{side}")
            el.set(qn("w:val"), "single")
            el.set(qn("w:sz"), str(size))
            el.set(qn("w:space"), str(space))
            el.set(qn("w:color"), color)
            bdr.append(el)
    ppr.append(bdr)


def _set_style_font(
    style, name: str, size: float, color: str, bold=None, italic=None
) -> None:
    from docx.oxml.ns import qn
    from docx.shared import Pt

    style.font.name = name
    rpr = style.element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is not None:
        for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
            fonts.set(qn(attr), name)
        for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
            if fonts.get(qn(attr)) is not None:
                del fonts.attrib[qn(attr)]
    style.font.size = Pt(size)
    style.font.color.rgb = _rgb(color)
    if bold is not None:
        style.font.bold = bold
    if italic is not None:
        style.font.italic = italic


def _theme_styles(doc, theme: Theme) -> None:
    """Style the base document: only when no template supplies its own look."""
    from docx.shared import Inches, Pt

    names = {s.name for s in doc.styles}

    def style(name: str):
        return doc.styles[name] if name in names else None

    normal = style("Normal")
    if normal is not None:
        _set_style_font(normal, theme.body_font, 11, theme.text)
        normal.paragraph_format.space_after = Pt(6)
        normal.paragraph_format.line_spacing = 1.15
    specs = {
        "Title": (theme.heading_font, 28, theme.accent, True, 0, 4),
        "Heading 1": (theme.heading_font, 18, theme.accent, True, 18, 6),
        "Heading 2": (theme.heading_font, 14, theme.accent_mid, True, 14, 4),
        "Heading 3": (theme.heading_font, 12, "404040", True, 10, 3),
    }
    for name, (font, size, color, bold, before, after) in specs.items():
        st = style(name)
        if st is None:
            continue
        _set_style_font(st, font, size, color, bold=bold, italic=False)
        st.paragraph_format.space_before = Pt(before)
        st.paragraph_format.space_after = Pt(after)
        st.paragraph_format.keep_with_next = True
        ppr = st.element.get_or_add_pPr()
        if name == "Title":
            _borders(ppr, bottom=(theme.accent, 18, 6))
        elif name == "Heading 1":
            _borders(ppr, bottom=(theme.rule, 6, 3))
        else:
            old = ppr.find(__import__("docx.oxml.ns", fromlist=["qn"]).qn("w:pBdr"))
            if old is not None:
                ppr.remove(old)
    for section in doc.sections:
        section.left_margin = section.right_margin = Inches(1)
        section.top_margin = section.bottom_margin = Inches(0.9)


def _page_footer(doc, title: str, theme: Theme) -> None:
    """Title and page number in the footer, when the document has none."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt

    section = doc.sections[0]
    footer = section.footer
    if any(p.text.strip() for p in footer.paragraphs) or len(footer.paragraphs) > 1:
        return
    para = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _borders(para._p.get_or_add_pPr(), top=(theme.rule, 4, 6))

    def run(text: str = ""):
        r = para.add_run(text)
        r.font.size = Pt(9)
        r.font.color.rgb = _rgb(theme.muted)
        return r

    if title:
        run(f"{title}    |    ")
    run("Page ")
    # PAGE field: begin / instruction / separate / cached result / end, each in a
    # small grey run so the number matches the rest of the footer.
    for kind, text in (
        ("begin", None),
        (None, "PAGE"),
        ("separate", None),
        ("result", "1"),
        ("end", None),
    ):
        r = run()
        if kind == "result":
            r.text = text
        elif kind:
            fld = OxmlElement("w:fldChar")
            fld.set(qn("w:fldCharType"), kind)
            r._r.append(fld)
        else:
            instr = OxmlElement("w:instrText")
            instr.set(qn("xml:space"), "preserve")
            instr.text = text
            r._r.append(instr)


def _add_hyperlink(
    par, text: str, url: str, theme: Theme, bold=False, italic=False
) -> None:
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    rid = par.part.relate_to(url, RT.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), rid)
    run = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), theme.accent_mid)
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    rpr.append(color)
    rpr.append(underline)
    if bold:
        rpr.append(OxmlElement("w:b"))
    if italic:
        rpr.append(OxmlElement("w:i"))
    run.append(rpr)
    t = OxmlElement("w:t")
    t.text = text
    t.set(qn("xml:space"), "preserve")
    run.append(t)
    link.append(run)
    par._p.append(link)


def _add_runs(
    par,
    runs: Sequence[Run],
    theme: Theme,
    size: Optional[float] = None,
    color: Optional[str] = None,
) -> None:
    from docx.shared import Pt

    for r in runs:
        if r.url:
            _add_hyperlink(par, r.text, r.url, theme, bold=r.bold, italic=r.italic)
            continue
        run = par.add_run(r.text)
        run.bold = r.bold or None
        run.italic = r.italic or None
        if size:
            run.font.size = Pt(size)
        if color:
            run.font.color.rgb = _rgb(color)
        if r.code:
            run.font.name = "Consolas"
            run.font.size = Pt((size or 11) - 1)
            run.font.color.rgb = _rgb(theme.accent)
            rpr = run._r.get_or_add_rPr()
            _shade(rpr, theme.code_bg)


def _docx_table(
    doc, rows: List[List[List[Run]]], theme: Theme, header: bool = True
) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt

    cols = max(len(r) for r in rows)
    table = doc.add_table(rows=len(rows), cols=cols)
    table.autofit = True
    tbl_pr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "4")
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), theme.rule)
        borders.append(el)
    tbl_pr.append(borders)
    margins = OxmlElement("w:tblCellMar")
    for side, w in (("top", 70), ("left", 110), ("bottom", 70), ("right", 110)):
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:w"), str(w))
        el.set(qn("w:type"), "dxa")
        margins.append(el)
    tbl_pr.append(margins)
    for ri, row in enumerate(rows):
        is_header = header and ri == 0
        if is_header:
            trpr = table.rows[ri]._tr.get_or_add_trPr()
            trpr.append(OxmlElement("w:tblHeader"))
        for ci in range(cols):
            cell = table.cell(ri, ci)
            par = cell.paragraphs[0]
            par.paragraph_format.space_after = Pt(0)
            cell_runs = row[ci] if ci < len(row) else [Run("")]
            if is_header:
                _add_runs(
                    par,
                    [r._replace(bold=True) for r in cell_runs],
                    theme,
                    size=10.5,
                    color="FFFFFF",
                )
                _shade(cell._tc.get_or_add_tcPr(), theme.accent)
            else:
                _add_runs(par, cell_runs, theme, size=10.5)
                if ri % 2 == 0:
                    _shade(cell._tc.get_or_add_tcPr(), theme.band)
    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_after = Pt(4)


def _render_docx(
    title: str,
    body: str,
    rows: Sequence[Tuple[str, str]],
    sections: Sequence[Dict[str, str]],
    theme: Theme,
    template: Optional[bytes],
) -> bytes:
    from docx import Document
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt

    if template:
        doc = Document(io.BytesIO(template))
        # Keep the letterhead's styles, header and footer; drop its sample body.
        body_el = doc.element.body
        for el in list(body_el):
            if el.tag != qn("w:sectPr"):
                body_el.remove(el)
    else:
        doc = Document()
        _theme_styles(doc, theme)
    names = {s.name for s in doc.styles}

    # Title block
    if "Title" in names:
        doc.add_heading(title or "Untitled", level=0)
    else:
        p = doc.add_paragraph()
        r = p.add_run(title or "Untitled")
        r.bold = True
        r.font.size = Pt(26)
        r.font.color.rgb = _rgb(theme.accent)

    number = 0
    for block in _all_blocks(body, sections):
        if block.kind != "number":
            number = 0
        if block.kind == "heading":
            doc.add_heading("", level=min(block.level, 3))
            _add_runs(doc.paragraphs[-1], block.runs, theme)
        elif block.kind == "rule":
            p = doc.add_paragraph()
            _borders(p._p.get_or_add_pPr(), bottom=(theme.rule, 6, 1))
        elif block.kind == "paragraph":
            p = doc.add_paragraph()
            _add_runs(p, block.runs, theme)
        elif block.kind == "bullet":
            name = (
                "List Bullet" if block.level == 0 else f"List Bullet {block.level + 1}"
            )
            if name in names:
                p = doc.add_paragraph(style=name)
            else:
                p = doc.add_paragraph()
                p.paragraph_format.left_indent = Inches(0.25 * (block.level + 1))
                p.paragraph_format.first_line_indent = Inches(-0.2)
                p.add_run("•  ")
            _add_runs(p, block.runs, theme)
            p.paragraph_format.space_after = Pt(3)
        elif block.kind == "number":
            number += 1
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Inches(0.3 * (block.level + 1))
            p.paragraph_format.first_line_indent = Inches(-0.25)
            p.paragraph_format.space_after = Pt(3)
            n = p.add_run(f"{number}.  ")
            n.bold = True
            n.font.color.rgb = _rgb(theme.accent)
            _add_runs(p, block.runs, theme)
        elif block.kind == "quote":
            p = doc.add_paragraph()
            ppr = p._p.get_or_add_pPr()
            _borders(ppr, left=(theme.accent, 24, 8))
            _shade(ppr, theme.accent_soft)
            p.paragraph_format.left_indent = Inches(0.2)
            p.paragraph_format.right_indent = Inches(0.1)
            p.paragraph_format.space_before = Pt(6)
            p.paragraph_format.space_after = Pt(8)
            _add_runs(
                p,
                [r._replace(italic=True) if not r.code else r for r in block.runs],
                theme,
            )
        elif block.kind == "code":
            for line in (block.text_value or " ").split("\n"):
                p = doc.add_paragraph()
                _shade(p._p.get_or_add_pPr(), theme.code_bg)
                p.paragraph_format.space_after = Pt(0)
                p.paragraph_format.left_indent = Inches(0.1)
                run = p.add_run(line or " ")
                run.font.name = "Consolas"
                run.font.size = Pt(9.5)
            doc.add_paragraph().paragraph_format.space_after = Pt(2)
        elif block.kind == "table" and block.rows:
            _docx_table(doc, block.rows, theme)

    if rows:
        doc.add_heading("Details", level=1)
        _docx_table(
            doc,
            [[[Run(k, bold=True)], [Run(v)]] for k, v in rows],
            theme,
            header=False,
        )
    if not template:
        _page_footer(doc, title, theme)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ----------------------------------------------------------------------------
# pptx
# ----------------------------------------------------------------------------


def _pptx_color(hex_color: str):
    from pptx.dml.color import RGBColor

    return RGBColor.from_string(hex_color.upper())


def _slide_rect(slide, left, top, width, height, fill: str, to_back: bool = False):
    from pptx.enum.shapes import MSO_SHAPE

    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, left, top, width, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = _pptx_color(fill)
    shape.line.fill.background()
    shape.shadow.inherit = False
    if to_back:
        tree = slide.shapes._spTree
        tree.remove(shape._element)
        tree.insert(2, shape._element)
    return shape


def _fill_text_frame(
    tf, blocks: List[Block], theme: Theme, size: int, light: bool = False
) -> None:
    from pptx.util import Pt

    color = "FFFFFF" if light else theme.text
    first = True
    n = 0
    for b in blocks:
        if b.kind not in ("bullet", "number"):
            n = 0
        if b.kind in ("table", "rule"):
            continue
        para = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        para.space_after = Pt(6)
        if b.kind == "heading":
            # A sub-section inside a slide: a bold accent line, not a bullet.
            n = 0
            para.space_before = Pt(10)
            head = para.add_run()
            head.text = b.text
            head.font.size = Pt(size + 2)
            head.font.bold = True
            head.font.name = theme.heading_font
            head.font.color.rgb = _pptx_color(color if light else theme.accent_mid)
            continue
        if b.kind == "bullet":
            prefix = "•  "
        elif b.kind == "number":
            n += 1
            prefix = f"{n}.  "
        else:
            prefix = ""
        para.level = b.level if b.kind in ("bullet", "number") else 0
        lead = para.add_run()
        lead.text = prefix
        lead.font.size = Pt(size)
        lead.font.bold = True
        lead.font.color.rgb = _pptx_color(color if light else theme.accent)
        for r in b.runs if b.kind != "code" else [Run(b.text_value, code=True)]:
            run = para.add_run()
            run.text = r.text
            run.font.size = Pt(size)
            run.font.bold = r.bold or None
            run.font.italic = r.italic or (b.kind == "quote") or None
            run.font.name = "Consolas" if r.code else theme.body_font
            run.font.color.rgb = _pptx_color(color)


def _pptx_table(slide, rows, theme: Theme, left, top, width) -> None:
    from pptx.util import Inches, Pt

    cols = max(len(r) for r in rows)
    shape = slide.shapes.add_table(
        len(rows), cols, left, top, width, Inches(0.45) * len(rows)
    )
    table = shape.table
    for ri, row in enumerate(rows):
        for ci in range(cols):
            cell = table.cell(ri, ci)
            runs = row[ci] if ci < len(row) else [Run("")]
            cell.text = "".join(r.text for r in runs)
            para = cell.text_frame.paragraphs[0]
            for run in para.runs:
                run.font.size = Pt(14)
                run.font.name = theme.body_font
                run.font.bold = ri == 0 or None
                run.font.color.rgb = _pptx_color("FFFFFF" if ri == 0 else theme.text)
            cell.fill.solid()
            cell.fill.fore_color.rgb = _pptx_color(
                theme.accent if ri == 0 else (theme.band if ri % 2 == 0 else "FFFFFF")
            )


def _render_pptx(
    title: str,
    body: str,
    rows: Sequence[Tuple[str, str]],
    sections: Sequence[Dict[str, str]],
    theme: Theme,
    template: Optional[bytes],
) -> bytes:
    from pptx import Presentation
    from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
    from pptx.util import Inches, Pt

    blocks = _all_blocks(body, [])
    for sec in sections:
        if sec.get("title"):
            blocks.append(Block("heading", [Run(sec["title"])], 2))
        if sec.get("body"):
            blocks += parse_blocks(sec["body"])
    if rows:
        blocks.append(Block("heading", [Run("Details")], 2))
        for k, v in rows:
            blocks.append(Block("bullet", [Run(f"{k}: ", bold=True), Run(v)]))
    intro, slides = split_into_slides(blocks)

    from_template = bool(template)
    prs = Presentation(io.BytesIO(template)) if template else Presentation()
    if not from_template:
        prs.slide_width = Inches(13.333)
        prs.slide_height = Inches(7.5)
    sw, sh = prs.slide_width, prs.slide_height
    layouts = prs.slide_layouts

    def layout(idx: int):
        return layouts[idx] if idx < len(layouts) else layouts[0]

    # --- title slide
    s = prs.slides.add_slide(layout(0))
    s.shapes.title.text = title or "Untitled"
    subtitle = plain_text(intro)[:300]
    if not from_template:
        _slide_rect(s, 0, 0, sw, sh, theme.accent, to_back=True)
        _slide_rect(s, Inches(0.8), Inches(3.75), Inches(1.4), Inches(0.08), "FFFFFF")
        t = s.shapes.title
        t.left, t.top, t.width, t.height = (
            Inches(0.8),
            Inches(1.7),
            sw - Inches(1.6),
            Inches(1.8),
        )
        t.text_frame.vertical_anchor = MSO_ANCHOR.BOTTOM
        for p in t.text_frame.paragraphs:
            p.alignment = PP_ALIGN.LEFT
            for r in p.runs:
                r.font.size = Pt(44)
                r.font.bold = True
                r.font.name = theme.heading_font
                r.font.color.rgb = _pptx_color("FFFFFF")
    if len(s.placeholders) > 1:
        ph = s.placeholders[1]
        ph.text = subtitle
        if not from_template:
            ph.left, ph.top, ph.width, ph.height = (
                Inches(0.8),
                Inches(4.05),
                sw - Inches(1.6),
                Inches(2.2),
            )
            for p in ph.text_frame.paragraphs:
                p.alignment = PP_ALIGN.LEFT
                for r in p.runs:
                    r.font.size = Pt(20)
                    r.font.name = theme.body_font
                    r.font.color.rgb = _pptx_color(_mix_hex(theme.accent))
    # --- content slides
    for number, (heading, slide_blocks) in enumerate(slides, start=2):
        s = prs.slides.add_slide(layout(1 if from_template else 5))
        s.shapes.title.text = heading or "Section"
        tables = [b for b in slide_blocks if b.kind == "table"]
        text_blocks = [b for b in slide_blocks if b.kind != "table"]
        if from_template:
            body_ph = s.placeholders[1] if len(s.placeholders) > 1 else None
            if body_ph is not None:
                tf = body_ph.text_frame
                tf.clear()
                _fill_text_frame(tf, text_blocks, theme, 20)
            continue
        _slide_rect(s, 0, 0, Inches(0.25), sh, theme.accent)
        t = s.shapes.title
        t.left, t.top, t.width, t.height = (
            Inches(0.8),
            Inches(0.45),
            sw - Inches(1.6),
            Inches(1.0),
        )
        t.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        for p in t.text_frame.paragraphs:
            p.alignment = PP_ALIGN.LEFT
            for r in p.runs:
                r.font.size = Pt(32)
                r.font.bold = True
                r.font.name = theme.heading_font
                r.font.color.rgb = _pptx_color(theme.accent)
        _slide_rect(
            s, Inches(0.8), Inches(1.5), Inches(1.2), Inches(0.06), theme.accent_mid
        )
        top = Inches(1.8)
        n_lines = sum(max(1, len(b.text) // 70 + 1) for b in text_blocks)
        size = 22 if n_lines <= 6 else 18 if n_lines <= 10 else 15
        if text_blocks:
            box = s.shapes.add_textbox(
                Inches(0.8), top, sw - Inches(1.6), sh - top - Inches(0.9)
            )
            box.text_frame.word_wrap = True
            _fill_text_frame(box.text_frame, text_blocks, theme, size)
            top = top + Inches(0.4) * n_lines + Inches(0.3)
        for tb in tables:
            _pptx_table(
                s, tb.rows, theme, Inches(0.8), min(top, Inches(4.6)), sw - Inches(1.6)
            )
        num = s.shapes.add_textbox(
            sw - Inches(1.2), sh - Inches(0.6), Inches(0.8), Inches(0.4)
        )
        num.text_frame.text = str(number)
        for r in num.text_frame.paragraphs[0].runs:
            r.font.size = Pt(12)
            r.font.color.rgb = _pptx_color(theme.muted)
        num.text_frame.paragraphs[0].alignment = PP_ALIGN.RIGHT
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def _mix_hex(accent: str) -> str:
    """A pale tint of the accent for subtitles on the accent background."""
    from app.services.document_theme import _mix

    return _mix(accent, "FFFFFF", 0.78)


# ----------------------------------------------------------------------------
# pdf
# ----------------------------------------------------------------------------


def _pdf_markup(runs: Sequence[Run], theme: Theme) -> str:
    """Runs as reportlab paragraph markup (text escaped, emphasis as tags)."""
    from xml.sax.saxutils import escape

    out: List[str] = []
    for r in runs:
        piece = escape(r.text).replace("\n", "<br/>")
        if r.code:
            piece = f'<font name="Courier" color="#{theme.accent}">{piece}</font>'
        if r.bold:
            piece = f"<b>{piece}</b>"
        if r.italic:
            piece = f"<i>{piece}</i>"
        if r.url:
            piece = f'<a href="{escape(r.url)}" color="#{theme.accent_mid}"><u>{piece}</u></a>'
        out.append(piece)
    return "".join(out)


def _render_pdf(
    title: str,
    body: str,
    rows: Sequence[Tuple[str, str]],
    sections: Sequence[Dict[str, str]],
    theme: Theme,
    template: Optional[bytes],
) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            HRFlowable,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as exc:  # pragma: no cover - optional dep
        raise RuntimeError("pdf rendering requires the 'reportlab' package") from exc

    def hexc(value: str):
        return colors.HexColor("#" + value)

    text_c, accent, mid = hexc(theme.text), hexc(theme.accent), hexc(theme.accent_mid)
    base = ParagraphStyle(
        "body",
        fontName="Helvetica",
        fontSize=10.5,
        leading=15.5,
        textColor=text_c,
        spaceAfter=6,
        alignment=TA_LEFT,
    )
    styles = {
        "title": ParagraphStyle(
            "title",
            parent=base,
            fontName="Helvetica-Bold",
            fontSize=26,
            leading=30,
            textColor=accent,
            spaceAfter=4,
        ),
        1: ParagraphStyle(
            "h1",
            parent=base,
            fontName="Helvetica-Bold",
            fontSize=17,
            leading=21,
            textColor=accent,
            spaceBefore=16,
            spaceAfter=3,
        ),
        2: ParagraphStyle(
            "h2",
            parent=base,
            fontName="Helvetica-Bold",
            fontSize=13.5,
            leading=17,
            textColor=mid,
            spaceBefore=12,
            spaceAfter=3,
        ),
        3: ParagraphStyle(
            "h3",
            parent=base,
            fontName="Helvetica-Bold",
            fontSize=11.5,
            leading=15,
            textColor=hexc("404040"),
            spaceBefore=9,
            spaceAfter=2,
        ),
    }
    cell = ParagraphStyle("cell", parent=base, fontSize=9.5, leading=12.5, spaceAfter=0)
    cell_head = ParagraphStyle(
        "cellh", parent=cell, fontName="Helvetica-Bold", textColor=colors.white
    )

    flow: List[Any] = [
        Paragraph(_pdf_markup([Run(title or "Untitled")], theme), styles["title"]),
        HRFlowable(
            width="100%", thickness=2.2, color=accent, spaceBefore=2, spaceAfter=10
        ),
    ]
    width = LETTER[0] - 2 * 0.9 * inch
    number = 0
    for block in _all_blocks(body, sections):
        if block.kind != "number":
            number = 0
        if block.kind == "heading":
            level = min(block.level, 3)
            flow.append(Paragraph(_pdf_markup(block.runs, theme), styles[level]))
            if level == 1:
                flow.append(
                    HRFlowable(
                        width="100%",
                        thickness=0.6,
                        color=hexc(theme.rule),
                        spaceBefore=0,
                        spaceAfter=6,
                    )
                )
        elif block.kind == "paragraph":
            flow.append(Paragraph(_pdf_markup(block.runs, theme), base))
        elif block.kind in ("bullet", "number"):
            if block.kind == "number":
                number += 1
            mark = "•" if block.kind == "bullet" else f"{number}."
            indent = 16 + block.level * 14
            style = ParagraphStyle(
                "li",
                parent=base,
                leftIndent=indent,
                bulletIndent=indent - 12,
                spaceAfter=3,
                bulletColor=accent,
                bulletFontName="Helvetica-Bold",
            )
            flow.append(
                Paragraph(_pdf_markup(block.runs, theme), style, bulletText=mark)
            )
        elif block.kind == "quote":
            q = Table(
                [
                    [
                        Paragraph(
                            _pdf_markup(
                                [r._replace(italic=True) for r in block.runs], theme
                            ),
                            base,
                        )
                    ]
                ],
                colWidths=[width],
            )
            q.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), hexc(theme.accent_soft)),
                        ("LINEBEFORE", (0, 0), (0, -1), 3, accent),
                        ("LEFTPADDING", (0, 0), (-1, -1), 12),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                        ("TOPPADDING", (0, 0), (-1, -1), 8),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ]
                )
            )
            flow += [q, Spacer(1, 8)]
        elif block.kind == "code":
            from xml.sax.saxutils import escape

            code_style = ParagraphStyle(
                "code", parent=base, fontName="Courier", fontSize=9, leading=12
            )
            c = Table(
                [
                    [
                        Paragraph(
                            escape(block.text_value)
                            .replace("\n", "<br/>")
                            .replace(" ", "&nbsp;"),
                            code_style,
                        )
                    ]
                ],
                colWidths=[width],
            )
            c.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), hexc(theme.code_bg)),
                        ("LEFTPADDING", (0, 0), (-1, -1), 8),
                        ("TOPPADDING", (0, 0), (-1, -1), 6),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ]
                )
            )
            flow += [c, Spacer(1, 8)]
        elif block.kind == "rule":
            flow.append(
                HRFlowable(
                    width="100%",
                    thickness=0.6,
                    color=hexc(theme.rule),
                    spaceBefore=6,
                    spaceAfter=6,
                )
            )
        elif block.kind == "table" and block.rows:
            flow += [
                _pdf_table(block.rows, theme, cell, cell_head, width, hexc),
                Spacer(1, 10),
            ]
    if rows:
        flow.append(Paragraph("Details", styles[1]))
        flow += [
            _pdf_table(
                [[[Run(k, bold=True)], [Run(v)]] for k, v in rows],
                theme,
                cell,
                cell_head,
                width,
                hexc,
                header=False,
            )
        ]

    def decorate(canvas, doc_):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(hexc(theme.muted))
        canvas.setStrokeColor(hexc(theme.rule))
        canvas.line(0.9 * inch, 0.62 * inch, LETTER[0] - 0.9 * inch, 0.62 * inch)
        canvas.drawString(0.9 * inch, 0.45 * inch, (title or "")[:80])
        canvas.drawRightString(LETTER[0] - 0.9 * inch, 0.45 * inch, f"Page {doc_.page}")
        canvas.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=LETTER,
        leftMargin=0.9 * inch,
        rightMargin=0.9 * inch,
        topMargin=0.9 * inch,
        bottomMargin=0.95 * inch,
        title=title or "Untitled",
    )
    doc.build(flow, onFirstPage=decorate, onLaterPages=decorate)
    return buf.getvalue()


def _pdf_table(rows, theme: Theme, cell, cell_head, width, hexc, header: bool = True):
    from reportlab.platypus import Paragraph, Table, TableStyle

    cols = max(len(r) for r in rows)
    data = []
    for ri, row in enumerate(rows):
        data.append(
            [
                Paragraph(
                    _pdf_markup(row[ci] if ci < len(row) else [Run("")], theme),
                    cell_head if header and ri == 0 else cell,
                )
                for ci in range(cols)
            ]
        )
    t = Table(data, colWidths=[width / cols] * cols, repeatRows=1 if header else 0)
    style = [
        ("GRID", (0, 0), (-1, -1), 0.5, hexc(theme.rule)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), hexc(theme.accent))]
        style += [
            ("BACKGROUND", (0, r), (-1, r), hexc(theme.band))
            for r in range(2, len(rows), 2)
        ]
    else:
        style += [
            ("BACKGROUND", (0, r), (-1, r), hexc(theme.band))
            for r in range(0, len(rows), 2)
        ]
    t.setStyle(TableStyle(style))
    return t


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
    theme: Optional[Dict[str, Any]] = None,
    template: Optional[bytes] = None,
) -> bytes:
    """Render title/body/sections/rows to ``renderer`` bytes.

    ``renderer`` must be one of ``docx``, ``pptx``, ``pdf``, ``markdown``.
    ``theme`` (``accent_color``, ``heading_font``, ``body_font``) restyles the
    output; ``template`` is a .docx / .pptx used as the base file (a letterhead).
    """
    key = (renderer or "").strip().lower()
    fn = _RENDERERS.get(key)
    if fn is None:
        raise ValueError(
            "renderer must be one of docx|pptx|pdf|markdown " f"(got {renderer!r})"
        )
    # A template of the wrong kind (a .docx given for a deck) is ignored.
    if template and key in ("docx", "pptx") and not _template_matches(template, key):
        template = None
    return fn(
        title, body, list(rows or ()), list(sections or ()), make_theme(theme), template
    )


def _template_matches(template: bytes, kind: str) -> bool:
    """Is ``template`` a real file of the renderer's kind? (zip with the right part)"""
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(template)) as z:
            names = set(z.namelist())
    except zipfile.BadZipFile:
        return False
    return (
        ("word/document.xml" in names)
        if kind == "docx"
        else ("ppt/presentation.xml" in names)
    )


__all__ = [
    "SUPPORTED_RENDERERS",
    "extension_for",
    "mime_for",
    "render_document",
]
