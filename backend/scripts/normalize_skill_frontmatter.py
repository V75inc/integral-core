#!/usr/bin/env python3
"""Normalize skill frontmatter to the Agent Skills standard, preserving bodies.

Run with --write to apply; the default is a dry-run. Vendor fields are removed,
not relocated into metadata. Directory/name migrations require explicit updates
of resource references before running this normalizer.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[2]
_FIELDS = {
    "name",
    "description",
    "license",
    "compatibility",
    "metadata",
    "allowed-tools",
}


def _iter_paths(*, core_only: bool) -> list[Path]:
    core = _REPO / "agent/agents/integral/integral_agent"
    paths = sorted(core.rglob("SKILL.md"))
    if not core_only:
        for root in (_REPO / "backend/app/packages", _REPO / "examples"):
            paths.extend(sorted(root.glob("*/skills/*/SKILL.md")))
    return paths


def normalize_file(path: Path, *, write: bool) -> list[str]:
    """Remove vendor fields and normalize standard tool declarations."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path}: missing YAML frontmatter")
    _, head, body = text.split("---", 2)
    meta = yaml.safe_load(head)
    if not isinstance(meta, dict):
        raise ValueError(f"{path}: frontmatter must be a mapping")
    removed = sorted(set(meta) - _FIELDS)
    normalized = {key: value for key, value in meta.items() if key in _FIELDS}
    changes = [f"remove {key}" for key in removed]
    tools = normalized.get("allowed-tools")
    if isinstance(tools, list):
        normalized["allowed-tools"] = " ".join(str(tool) for tool in tools)
        changes.append("use standard allowed-tools string")
    if changes and write:
        path.write_text(
            "---\n"
            + yaml.safe_dump(
                normalized, sort_keys=False, allow_unicode=True, width=1000
            )
            + "---"
            + body,
            encoding="utf-8",
        )
    return changes


def main() -> int:
    """Run the command and report its result."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--core-only", action="store_true")
    args = parser.parse_args()
    changed = 0
    for path in _iter_paths(core_only=args.core_only):
        changes = normalize_file(path, write=args.write)
        if changes:
            changed += 1
            print(f"{path.relative_to(_REPO)}: {', '.join(changes)}")
    print(f"{'Updated' if args.write else 'Would update'} {changed} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
