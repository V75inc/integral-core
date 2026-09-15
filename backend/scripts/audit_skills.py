#!/usr/bin/env python3
"""Regenerate docs/backend/skill-bundle-audit.md from skill_compliance."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "backend"))

from app.agentive.tooling.catalogue import build_tool_catalogue  # noqa: E402
from app.services.skill_compliance import (  # noqa: E402
    audit_all_skills,
    format_audit_markdown,
)


def main() -> int:
    tools = {t["name"] for t in build_tool_catalogue()}
    reports = audit_all_skills(known_tool_names=tools)
    md = format_audit_markdown(reports)
    out = _REPO / "docs" / "backend" / "skill-bundle-audit.md"
    out.write_text(md, encoding="utf-8")
    errors = sum(1 for r in reports if not r.ok)
    print(f"Wrote {out} ({len(reports)} skills, {errors} with errors)")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
