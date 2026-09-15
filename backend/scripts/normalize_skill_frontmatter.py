#!/usr/bin/env python3
"""Normalize Integral SKILL.md frontmatter to jvagent JV + Anthropic discovery contract.

Inserts explicit ``spec: jv`` when missing and rewrites discovery ``description``
lines that fail third-person / when-to-use heuristics (core skills only by default).

Usage:
  python3 backend/scripts/normalize_skill_frontmatter.py          # dry-run
  python3 backend/scripts/normalize_skill_frontmatter.py --write
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_CORE_GLOB = (
    "agent/agents/integral/integral_agent/actions/integral"
    "/embedded_integral_action/skills/integral_*/SKILL.md"
)
_BUNDLE_GLOB = "backend/app/profiles/*/skills/*/SKILL.md"

_CORE_DESCRIPTION_FIXES: dict[str, str] = {
    "integral_filing": (
        "Files freeform user content into the right track and entry shape. "
        "Grounds on the workspace Content Profile via read tools before staging. "
        "Use when the user provides factual content — notes, observations, email pastes, "
        "meeting summaries — without asking clarifying questions first; stage and let "
        "the user approve the card."
    ),
    "integral_onboard": (
        "Guides a new user or workspace through first setup across multiple turns — "
        "asks clarifying questions, provisions apps/tracks, and delegates schema work "
        "to integral_scaffold or integral_model as needed."
    ),
    "integral_organize": (
        "Bulk-reorganizes, migrates, or archives existing entries — selects a set with "
        "a query, then applies one batched change so the user blesses the whole reorg "
        "once. Use for cross-entry status moves, archival sweeps, and tag migrations. "
        "Delegates single-entry edits to integral_entries and schema changes to integral_model."
    ),
    "integral_profiles": (
        "Inspects, authors, and modifies Integral Content Profiles — the schema layer "
        "defining a track or app's EntryTypes, Tags, and Views. Use when the user asks "
        "about profile structure, draft/publish lifecycle, or library merges."
    ),
    "integral_scaffold": (
        "Stands up a new app or domain from user intent in one approval — creates the app, "
        "tracks, starter profile, and optional seeds. Use for greenfield workspace setup; "
        "delegates ongoing schema tuning to integral_model."
    ),
    "integral_scheduling": (
        "Creates, lists, pauses, resumes, edits, and cancels recurring routines — standing "
        "instructions replayed as agent turns on a cron cadence in the same chat thread. "
        "Use when the user asks for scheduled or repeating agent work."
    ),
    "integral_insights": (
        "Queries, analyzes, ranks, and synthesizes across the user's Integral substrate — "
        "counts, superlatives, breakdowns, comparisons, and activity digests. Use for "
        '"what\'s happening", top/bottom rankings, and saving a useful query as a View.'
    ),
    "integral_review": (
        "Produces periodic synthesis over the workspace — counts, digests, and queries to "
        "answer status rollups or recurring reviews, optionally persisting a saved view. "
        "Delegates bulk mutations to integral_organize and one-off entry reads to integral_entries."
    ),
    "integral_attachments": (
        "Lists, reads, and delivers files attached to Integral entries. Use when the user "
        "asks what is attached, wants file contents summarized, or needs an attachment linked."
    ),
}

_ANCHOR_KEYS = (
    "extends:",
    "requires-actions:",
    "allowed-tools:",
    "always-active:",
    "task-lock:",
    "tags:",
    "metadata:",
)


def _iter_paths(*, core_only: bool) -> list[Path]:
    paths = sorted(_REPO.glob(_CORE_GLOB))
    if not core_only:
        paths.extend(sorted(_REPO.glob(_BUNDLE_GLOB)))
    return paths


def _split_frontmatter(raw: str) -> tuple[str, str, str] | None:
    if not raw.startswith("---"):
        return None
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return None
    return parts[0], parts[1], parts[2]


def _inject_spec(fm_text: str) -> tuple[str, bool]:
    if re.search(r"^spec:\s*", fm_text, re.MULTILINE):
        return fm_text, False
    lines = fm_text.splitlines()
    insert_at = len(lines)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if any(stripped.startswith(k) for k in _ANCHOR_KEYS):
            insert_at = i
            break
    lines.insert(insert_at, "spec: jv")
    return "\n".join(lines), True


def _replace_description_block(fm_text: str, new_desc: str) -> tuple[str, bool]:
    """Replace entire description field (single- or multi-line) with one quoted scalar."""
    quoted = new_desc.replace("\\", "\\\\").replace('"', '\\"')
    pattern = re.compile(
        r"^description:\s*(?:>[-+]?\s*\n(?:[ \t]+.+\n?)*|(?:[|>][-+]?\s*\n)?(?:[ \t]+.+\n?)*|.+)$",
        re.MULTILINE,
    )
    new_fm, n = pattern.subn(f'description: "{quoted}"', fm_text, count=1)
    return new_fm, n > 0


def normalize_file(path: Path, *, write: bool) -> list[str]:
    changes: list[str] = []
    raw = path.read_text(encoding="utf-8")
    split = _split_frontmatter(raw)
    if not split:
        return changes
    _, fm_text, body = split

    new_fm, spec_added = _inject_spec(fm_text)
    if spec_added:
        changes.append("add spec: jv")

    skill_key = path.parent.name
    if skill_key in _CORE_DESCRIPTION_FIXES:
        new_fm, desc_changed = _replace_description_block(
            new_fm, _CORE_DESCRIPTION_FIXES[skill_key]
        )
        if desc_changed:
            changes.append("rewrite description (third-person discovery)")

    if not changes:
        return changes

    new_raw = f"---\n{new_fm}\n---{body}"
    if write:
        path.write_text(new_raw, encoding="utf-8")
    return changes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Normalize SKILL.md frontmatter")
    parser.add_argument("--write", action="store_true", help="Apply changes")
    parser.add_argument(
        "--core-only", action="store_true", help="Only core integral_* skills"
    )
    args = parser.parse_args(argv)

    touched = 0
    for path in _iter_paths(core_only=args.core_only):
        ch = normalize_file(path, write=args.write)
        if ch:
            touched += 1
            mode = "WROTE" if args.write else "WOULD"
            print(f"{mode} {path.relative_to(_REPO)}: {', '.join(ch)}")

    print(f"{'Updated' if args.write else 'Would update'} {touched} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
