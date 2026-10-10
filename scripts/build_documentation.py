"""Build a local static reading edition and regenerate the API declaration inventory."""

from __future__ import annotations

import argparse
import ast
import html
import itertools
import json
import re
import shutil
import textwrap
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def inventory() -> None:
    """Extract literal route declarations without booting the application."""
    routes = []
    for directory in (ROOT / "backend/app/api", ROOT / "backend/app/agentive/api"):
        for path in sorted(directory.rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text())):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for decorator in node.decorator_list:
                    if not isinstance(decorator, ast.Call):
                        continue
                    func = decorator.func
                    name = getattr(func, "id", getattr(func, "attr", ""))
                    if name != "endpoint":
                        continue
                    keywords = {item.arg: item.value for item in decorator.keywords}
                    try:
                        declared = ast.literal_eval(
                            decorator.args[0]
                            if decorator.args
                            else keywords.get("path")
                        )
                        methods = (
                            ast.literal_eval(keywords["methods"])
                            if "methods" in keywords
                            else []
                        )
                    except (ValueError, TypeError):
                        continue
                    if isinstance(declared, str):
                        routes.append(
                            {
                                "path": declared,
                                "methods": methods,
                                "source": str(path.relative_to(ROOT)),
                                "handler": node.name,
                                "line": node.lineno,
                            }
                        )
    routes.sort(key=lambda row: (row["source"], row["path"], str(row["methods"])))
    (ROOT / "docs/generated/api-routes.json").write_text(
        json.dumps(routes, indent=2) + "\n"
    )
    reference = ROOT / "docs/backend/API_REFERENCE.md"
    # Retain the authored explanation; replace only the source-derived tables.
    prefix = reference.read_text().split("\n## ", 1)[0].rstrip() + "\n\n"
    parts = [prefix]
    for source, group in itertools.groupby(routes, key=lambda row: row["source"]):
        parts.append(
            f"## {Path(source).stem}\n\n| Methods | Declared path | Handler |\n|---|---|---|\n"
        )
        for row in group:
            parts.append(
                f"| {', '.join(row['methods'])} | `{row['path']}` | "
                f"[{row['handler']}](../../{source}) |\n"
            )
        parts.append("\n")
    reference.write_text("".join(parts).rstrip() + "\n")
    print(f"Inventoried {len(routes)} literal endpoint declarations")


def diagram(source: str) -> str:
    """Render the simple DAG notation used in the authored overview diagrams."""
    labels, edges = {}, []
    pattern = r"(\w+)(?:\[([^]]+)\])?\s*-->\s*(\w+)(?:\[([^]]+)\])?"
    for a, label_a, b, label_b in re.findall(pattern, source):
        labels[a] = label_a or labels.get(a, a)
        labels[b] = label_b or labels.get(b, b)
        edges.append((a, b))
    if not edges:
        return "<pre>" + html.escape(source) + "</pre>"
    ranks = dict.fromkeys(labels, 0)
    for _ in range(len(labels)):
        previous = dict(ranks)
        for a, b in edges:
            ranks[b] = max(ranks[b], ranks[a] + 1)
        if ranks == previous:
            break
    levels = {}
    for key, rank in ranks.items():
        levels.setdefault(rank, []).append(key)
    positions = {}
    width = max(800, max(map(len, levels.values())) * 240)
    for rank, keys in levels.items():
        for column, key in enumerate(keys):
            positions[key] = ((column + 0.5) * width / len(keys), rank * 130 + 55)
    height = (max(levels) + 1) * 130
    parts = [
        f'<figure class="diagram">'
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Integral '
        f'architecture relationships">'
        f"<defs>"
        f'<marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" '
        f'markerHeight="6" orient="auto">'
        f'<path d="M0 0L10 5L0 10" fill="#888888"/>'
        f"</marker>"
        f"</defs>"
    ]
    for a, b in edges:
        x, y = positions[a]
        xx, yy = positions[b]
        parts.append(
            f'<path d="M{x} {y+35} C{x} {y+75} {xx} {yy-75} {xx} {yy-35}" fill="none" '
            f'stroke="#888888" stroke-width="2" marker-end="url(#arrow)"/>'
        )
    for key, (x, y) in positions.items():
        parts.append(
            f'<rect x="{x-108}" y="{y-35}" width="216" height="70" rx="12" '
            f'fill="#f0f0f0" stroke="#cccccc"/>'
        )
        lines = textwrap.wrap(labels[key], 28)[:3]
        for index, line in enumerate(lines):
            yy = y + (index - (len(lines) - 1) / 2) * 17 + 5
            parts.append(
                f'<text x="{x}" y="{yy}" text-anchor="middle" fill="#181818" font-size="14" '
                f'font-family="system-ui,sans-serif">{html.escape(line)}</text>'
            )
    parts.append(
        "</svg><figcaption>Relationships in the shared operational foundation."
        "</figcaption></figure>"
    )
    return "".join(parts)


STYLE = (ROOT / "scripts/documentation/reader.css").read_text()


def build(output: Path) -> None:
    """Render current guides and local assets into a reviewable reader."""
    from markdown_it import MarkdownIt

    output.mkdir(parents=True, exist_ok=True)
    md = MarkdownIt("commonmark", {"html": True}).enable("table")
    slug_counts = {}

    def heading(tokens: list[Any], index: int, options: dict, env: dict) -> str:
        label = tokens[index + 1].content
        slug = re.sub(r"[^\w\-\s]", "", label.lower()).replace(" ", "-")
        count = slug_counts.get(slug, 0)
        slug_counts[slug] = count + 1
        if count:
            slug += f"-{count}"
        tokens[index].attrSet("id", slug)
        return md.renderer.renderToken(tokens, index, options, env)

    def fence(tokens: list[Any], index: int, options: dict, env: dict) -> str:
        token = tokens[index]
        if token.info.strip() == "mermaid":
            return diagram(token.content)
        return "<pre><code>" + html.escape(token.content) + "</code></pre>"

    md.renderer.rules["heading_open"] = heading
    md.renderer.rules["fence"] = fence
    sources = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
    linked_assets: set[Path] = set()
    root_logo = (
        ROOT / "docs/branding/logo-exploration-2026-10-06/integral-logo-white.svg"
    )
    (output / "assets").mkdir(exist_ok=True)
    shutil.copy2(root_logo, output / "assets/mark.svg")
    for source in sources:
        relative = source.relative_to(ROOT)
        destination = (
            Path("index.html")
            if relative == Path("README.md")
            else relative.with_suffix(".html")
        )
        prefix = "../" * (len(destination.parts) - 1)
        text = source.read_text()
        slug_counts.clear()
        content = md.render(text)
        title = re.search(r"^# (.+)", text, re.M).group(1)

        # Rewrite known Markdown destinations, preserve fragments and external URLs.
        def rewrite_link(
            match: re.Match[str], source: Path = source, prefix: str = prefix
        ) -> str:
            ref = match[1]
            if re.match(r"\w+:", ref) or ref.startswith("#"):
                return match.group(0)
            path, separator, fragment = ref.partition("#")
            target = (source.parent / path).resolve()
            if path.endswith(".md"):
                if target == ROOT / "README.md":
                    path = prefix + "index.html"
                elif target in sources:
                    path = path[:-3] + ".html"
            if (
                target.is_file()
                and target.is_relative_to(ROOT)
                and target not in sources
            ):
                linked_assets.add(target)
            return 'href="' + path + (separator + fragment if separator else "") + '"'

        content = re.sub(r'href="([^"]+)"', rewrite_link, content)
        toc = []
        for slug, label in re.findall(r'<h2 id="([^"]+)">(.*?)</h2>', content):
            toc.append(f'<a href="#{html.escape(slug)}">{label}</a>')
        if relative == Path("README.md"):
            content = content.replace(
                '<h2 id="start-here">Start here</h2>',
                '<div class="cards">'
                '<a class="card" href="docs/product/INTRODUCTION.html">'
                "<b>Meet Integral</b>"
                "<span>A shared world for people, software, and AI.</span>"
                "</a>"
                '<a class="card" href="docs/product/WHITE_PAPER.html">'
                "<b>Read the white paper</b>"
                "<span>The concept, layers, and architecture in full.</span>"
                "</a>"
                '<a class="card" href="docs/user-guide/README.html">'
                "<b>Start working</b>"
                "<span>A practical guide to the experience.</span>"
                "</a>"
                "</div>"
                '<h2 id="start-here">Explore the guides</h2>',
            )
        category = (
            "White paper"
            if source.name == "WHITE_PAPER.md"
            else (
                "User guide"
                if "user-guide" in source.parts
                else "Integral documentation"
            )
        )
        page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} · Integral</title>
<style>{STYLE}</style>
</head>
<body>
<header>
<a class="brand" href="{prefix}index.html">
<img src="{prefix}assets/mark.svg" alt="">integral</a>
<nav>
<a href="{prefix}docs/product/INTRODUCTION.html">Introduction</a>
<a href="{prefix}docs/product/WHITE_PAPER.html">White paper</a>
<a href="{prefix}docs/user-guide/README.html">Guide</a>
<a href="{prefix}docs/README.html">All docs</a>
</nav>
</header>
<main>
<article>
<div class="eyebrow">{category}</div>{content}</article>
<aside>
<h3>In this guide</h3>{''.join(toc)}</aside>
</main>
<footer>Integral Core · Current documentation edition · Pre-1.0 architecture
· Local review build</footer>
</body>
</html>"""
        path = output / destination
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(page)
    # Preserve local download/source destinations without copying private deployment state.
    for source in [*(ROOT / "docs").rglob("*"), *linked_assets]:
        if source.is_file() and (source.suffix != ".md" or source in linked_assets):
            path = output / source.relative_to(ROOT)
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, path)
    print(f"Built {len(sources)} reader pages at {output}")


def main() -> None:
    """Run inventory generation and the optional local reader build."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory-only", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / ".local/documentation")
    args = parser.parse_args()
    inventory()
    if not args.inventory_only:
        build(args.output.resolve())


if __name__ == "__main__":
    main()
