"""Markdown subset for document bodies.

The document editor and the assistant write markdown. The docx / pdf / pptx
engines turn it into real structure instead of printing the markup: headings,
nested bullet and numbered lists, tables, quotes (callouts), code blocks, rules,
and bold / italic / code / link emphasis. A body with no markdown is plain
paragraphs, exactly as before: blocks are separated by blank lines.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import List, NamedTuple, Optional, Tuple


class Run(NamedTuple):
    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False
    url: str = ""


_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_NUMBER = re.compile(r"^(\s*)\d+[.)]\s+(.*)$")
_RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_FENCE = re.compile(r"^\s*```")
_QUOTE = re.compile(r"^\s*>\s?(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_INLINE = re.compile(
    r"(\*\*[^*\n]+?\*\*|__[^_\n]+?__|\*[^*\n]+?\*|(?<!\w)_[^_\n]+?_(?!\w)|`[^`\n]+?`|\[[^\]\n]+?\]\([^)\n]+?\))"
)
# The editor writes an empty paragraph as a lone &nbsp; and escapes some
# characters as entities; neither belongs in a rendered file.
_BLANK_ENTITY_LINE = re.compile(r"^(\s|&nbsp;|&#160;| )*$")


@dataclass
class Block:
    kind: str  # heading|paragraph|bullet|number|quote|code|table|rule
    runs: List[Run] = field(default_factory=list)
    level: int = 0  # heading level 1-6, or list depth 0-2
    rows: List[List[List[Run]]] = field(default_factory=list)  # table cells as runs
    text_value: str = ""  # code block text

    @property
    def text(self) -> str:
        if self.kind == "code":
            return self.text_value
        return "".join(r.text for r in self.runs)


def parse_inline(text: str) -> List[Run]:
    """Split a line of markdown into styled runs."""
    runs: List[Run] = []
    text = html.unescape(text).replace(" ", " ")
    pos = 0
    for m in _INLINE.finditer(text):
        if m.start() > pos:
            runs.append(Run(text[pos : m.start()]))
        tok = m.group(0)
        if tok.startswith("**") or tok.startswith("__"):
            runs.append(Run(tok[2:-2], bold=True))
        elif tok.startswith("`"):
            runs.append(Run(tok[1:-1], code=True))
        elif tok.startswith("["):
            label = tok[1 : tok.index("](")]
            url = tok[tok.index("](") + 2 : -1].strip()
            runs.append(Run(label, url=url))
        else:
            runs.append(Run(tok[1:-1], italic=True))
        pos = m.end()
    if pos < len(text):
        runs.append(Run(text[pos:]))
    return runs or [Run("")]


def _split_row(line: str) -> List[str]:
    cells = line.strip()
    if cells.startswith("|"):
        cells = cells[1:]
    if cells.endswith("|"):
        cells = cells[:-1]
    return [c.strip() for c in cells.split("|")]


def parse_blocks(body: Optional[str]) -> List[Block]:
    """Parse a document body into blocks."""
    lines = (body or "").replace("\r\n", "\n").split("\n")
    blocks: List[Block] = []
    para: List[str] = []
    i = 0

    def flush() -> None:
        if para:
            blocks.append(Block("paragraph", parse_inline("\n".join(para))))
            para.clear()

    while i < len(lines):
        line = lines[i].rstrip()
        if _BLANK_ENTITY_LINE.match(line):
            flush()
            i += 1
            continue
        if _FENCE.match(line):
            flush()
            code: List[str] = []
            i += 1
            while i < len(lines) and not _FENCE.match(lines[i]):
                code.append(lines[i].rstrip("\n"))
                i += 1
            i += 1  # closing fence
            blocks.append(Block("code", text_value="\n".join(code)))
            continue
        if _RULE.match(line):
            flush()
            blocks.append(Block("rule"))
            i += 1
            continue
        m = _HEADING.match(line)
        if m:
            flush()
            blocks.append(Block("heading", parse_inline(m.group(2)), len(m.group(1))))
            i += 1
            continue
        # table: a pipe row followed by a separator row
        if (
            "|" in line
            and i + 1 < len(lines)
            and _TABLE_SEP.match(lines[i + 1].rstrip())
        ):
            flush()
            rows = [[parse_inline(c) for c in _split_row(line)]]
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append([parse_inline(c) for c in _split_row(lines[i].rstrip())])
                i += 1
            blocks.append(Block("table", rows=rows))
            continue
        m = _QUOTE.match(line)
        if m:
            flush()
            quote = [m.group(1)]
            i += 1
            while i < len(lines) and _QUOTE.match(lines[i].rstrip()):
                quote.append(_QUOTE.match(lines[i].rstrip()).group(1))
                i += 1
            blocks.append(Block("quote", parse_inline("\n".join(q for q in quote))))
            continue
        m = _BULLET.match(line)
        if m:
            flush()
            blocks.append(
                Block(
                    "bullet",
                    parse_inline(m.group(2)),
                    min(2, len(m.group(1).replace("\t", "    ")) // 2),
                )
            )
            i += 1
            continue
        m = _NUMBER.match(line)
        if m:
            flush()
            blocks.append(
                Block(
                    "number",
                    parse_inline(m.group(2)),
                    min(2, len(m.group(1).replace("\t", "    ")) // 2),
                )
            )
            i += 1
            continue
        para.append(line.strip())
        i += 1
    flush()
    return blocks


def plain_text(blocks: List[Block]) -> str:
    """Blocks as readable plain text (bullets as lines), for slides."""
    out: List[str] = []
    n = 0
    for b in blocks:
        pad = "  " * b.level if b.kind in ("bullet", "number") else ""
        if b.kind == "bullet":
            out.append(f"{pad}• {b.text}")
        elif b.kind == "number":
            n += 1
            out.append(f"{pad}{n}. {b.text}")
        elif b.kind == "table":
            n = 0
            for row in b.rows:
                out.append(" | ".join("".join(r.text for r in cell) for cell in row))
        elif b.kind in ("paragraph", "heading", "quote", "code"):
            n = 0
            out.append(b.text)
    return "\n".join(out)


def split_into_slides(
    blocks: List[Block],
) -> Tuple[List[Block], List[Tuple[str, List[Block]]]]:
    """Blocks before the first heading, then (heading text, blocks) per slide.

    A slide starts at the shallowest heading level the document uses, so a
    document written with ``#`` sections and ``##`` sub-sections gives one slide
    per ``#`` section, with each ``##`` kept inside it as a sub-heading. A
    document that only uses ``##`` gets a slide per ``##``. This is what makes a
    well-structured document convert to a sensible deck without rewriting it.
    """
    levels = [b.level for b in blocks if b.kind == "heading"]
    slide_level = min(levels) if levels else 1
    intro: List[Block] = []
    slides: List[Tuple[str, List[Block]]] = []
    for b in blocks:
        if b.kind == "heading" and b.level == slide_level:
            slides.append((b.text, []))
        elif slides:
            slides[-1][1].append(b)
        else:
            intro.append(b)
    return intro, slides
