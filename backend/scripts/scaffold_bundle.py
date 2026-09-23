#!/usr/bin/env python3
"""Scaffold a new App bundle under backend/app/packages/{slug}/.

Usage:
  python backend/scripts/scaffold_bundle.py my-app [--trusted]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROFILES_ROOT = _REPO_ROOT / "backend" / "app" / "packages"


def _slug_to_py(slug: str) -> str:
    return slug.replace("-", "_")


def scaffold_bundle(slug: str, *, trusted: bool = False) -> Path:
    bundle_dir = _PROFILES_ROOT / slug
    if bundle_dir.exists():
        raise SystemExit(f"Bundle directory already exists: {bundle_dir}")

    bundle_dir.mkdir(parents=True)
    skills_dir = bundle_dir / "skills" / "example_skill"
    skills_dir.mkdir(parents=True)
    agents_dir = bundle_dir / "agents"
    agents_dir.mkdir()

    (skills_dir / "SKILL.md").write_text(
        """---
name: example_skill
description: >-
  Example App-bundled skill — replace with third-person discovery prose
  (what it does and when to route here).
spec: jv
extends: action:integral/embedded_integral_action
requires-actions:
  - EmbeddedIntegralAction
allowed-tools:
  - integral_query_entries
tags: [example]
---

## When to use

Describe user intents that route here.

## When NOT to use — delegate

Adjacent skills that own neighboring intents.

## Grounding (read before write)

Read tools to call before mutations.

## Procedure

Numbered tool sequence.

## Staging discipline

Propose/bless and batching rules.

## Forbidden patterns

Shortcuts to reject.

## Example

One end-to-end walkthrough.
""",
        encoding="utf-8",
    )

    (agents_dir / "example.yaml").write_text(
        """name: Example Agent
description: Example resident agent for this App.

engine:
  model: gpt-4o-mini
persona:
  model: gpt-4o-mini
  system_prompt: |
    You are the example agent for this App.
""",
        encoding="utf-8",
    )

    tools_block = ""
    if trusted:
        py_pkg = _PROFILES_ROOT / _slug_to_py(slug) / "tools"
        py_pkg.mkdir(parents=True)
        (py_pkg / "__init__.py").write_text("", encoding="utf-8")
        (py_pkg / "example.py").write_text(
            '''"""Example bundle tool — async handler for hook dispatch."""

from __future__ import annotations

from typing import Any, Dict

from app.services.hooks.registry import ToolContext


async def run(payload: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    _ = ctx
    return {"ok": True, "echo": payload}
''',
            encoding="utf-8",
        )
        tools_block = """
  # Requires package.trust_tier: trusted
  tools:
    - key: example_tool
      handler_ref: tools.example:run
      parameters_schema:
        type: object
      output_schema:
        type: object
  hooks:
    - point: entry.precompute
      key: example_precompute
      mode: tool
      tool: example_tool
"""

    trust_line = "  trust_tier: trusted\n" if trusted else ""

    (bundle_dir / "operational-model.yaml").write_text(
        f"""integral_operational_model_version: 3
scope: app
package:
  name: {slug.replace("-", " ").title()}
  slug: {slug}
  version: 0.1.0
  description: Replace with a one-paragraph description of this App.
  tags: []
{trust_line}
app:
  description: Replace with App-level description.
  tracks: []
  skills:
    - example_skill
  agents:
    - key: example_agent
      name: Example Agent
      persona_ref: agents/example.yaml
      skills:
        - example_skill
      scope: app
      staging: optional
{tools_block}
""",
        encoding="utf-8",
    )

    return bundle_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scaffold an App bundle")
    parser.add_argument("slug", help="Bundle slug (directory name, e.g. my-app)")
    parser.add_argument(
        "--trusted",
        action="store_true",
        help="Also scaffold Python tools package + sample hook",
    )
    args = parser.parse_args(argv)

    slug = args.slug.strip()
    if not slug or "/" in slug:
        raise SystemExit("slug must be a single path segment (e.g. my-app)")

    bundle_dir = scaffold_bundle(slug, trusted=args.trusted)
    print(f"Created bundle at {bundle_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
