"""Markdown subset for document bodies.

The document editor writes markdown (headings, bullet and numbered lists, bold,
italic, inline code, links). The docx / pdf / pptx engines turn that into real
headings, lists and emphasis instead of printing the markup. A body with no
markdown is plain paragraphs, exactly as before: blocks are separated by blank
lines.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# (text, bold, italic, code)
Run = Tuple[str, bool, bool, bool]

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_NUMBER = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_INLINE = re.compile(
    r"(\*\*[^*\n]+?\*\*|__[^_\n]+?__|\*[^*\n]+?\*|_[^_\n]+?_|`[^`\n]+?`|\[[^\]\n]+?\]\([^)\n]+?\))"
)


@dataclass
class Block:
    kind: str  # heading | paragraph | bullet | number | rule
    runs: List[Run] = field(default_factory=list)
    level: int = 0  # heading level 1-6

    @property
    def text(self) -> str:
        return "".join(r[0] for r in self.runs)


# The editor writes an empty paragraph as a lone &nbsp; and escapes some
# characters as entities; neither belongs in a rendered file.
_BLANK_ENTITY_LINE = re.compile(r"^(\s|&nbsp;|&#160;|\u00a0)*$")


def parse_inline(text: str) -> List[Run]:
    """Split a line of markdown into styled runs."""
    runs: List[Run] = []
    text = html.unescape(text).replace("\u00a0", " ")
    pos = 0
    for m in _INLINE.finditer(text):
        if m.start() > pos:
            runs.append((text[pos : m.start()], False, False, False))
        tok = m.group(0)
        if tok.startswith("**") or tok.startswith("__"):
            runs.append((tok[2:-2], True, False, False))
        elif tok.startswith("`"):
            runs.append((tok[1:-1], False, False, True))
        elif tok.startswith("["):
            label = tok[1 : tok.index("](")]
            runs.append((label, False, False, False))
        else:
            runs.append((tok[1:-1], False, True, False))
        pos = m.end()
    if pos < len(text):
        runs.append((text[pos:], False, False, False))
    return runs or [("", False, False, False)]


def parse_blocks(body: Optional[str]) -> List[Block]:
    """Parse a document body into blocks."""
    blocks: List[Block] = []
    para: List[str] = []

    def flush() -> None:
        if para:
            blocks.append(Block("paragraph", parse_inline("\n".join(para))))
            para.clear()

    for raw in (body or "").replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        if _BLANK_ENTITY_LINE.match(line):
            flush()
            continue
        if _RULE.match(line):
            flush()
            blocks.append(Block("rule"))
            continue
        m = _HEADING.match(line)
        if m:
            flush()
            blocks.append(Block("heading", parse_inline(m.group(2)), len(m.group(1))))
            continue
        m = _BULLET.match(line)
        if m:
            flush()
            blocks.append(Block("bullet", parse_inline(m.group(1))))
            continue
        m = _NUMBER.match(line)
        if m:
            flush()
            blocks.append(Block("number", parse_inline(m.group(1))))
            continue
        para.append(line.strip())
    flush()
    return blocks


def plain_text(blocks: List[Block]) -> str:
    """Blocks as readable plain text (bullets as lines), for slides."""
    out: List[str] = []
    n = 0
    for b in blocks:
        if b.kind == "bullet":
            out.append(f"• {b.text}")
        elif b.kind == "number":
            n += 1
            out.append(f"{n}. {b.text}")
        elif b.kind in ("paragraph", "heading"):
            n = 0
            out.append(b.text)
    return "\n".join(out)


def split_into_slides(blocks: List[Block]) -> Tuple[List[Block], List[Tuple[str, List[Block]]]]:
    """Blocks before the first heading, then (heading text, blocks) per heading."""
    intro: List[Block] = []
    slides: List[Tuple[str, List[Block]]] = []
    for b in blocks:
        if b.kind == "heading":
            slides.append((b.text, []))
        elif slides:
            slides[-1][1].append(b)
        else:
            intro.append(b)
    return intro, slides
