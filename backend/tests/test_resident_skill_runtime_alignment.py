"""Resident skill instructions must agree with the dispatchable tool surface."""

from __future__ import annotations

from pathlib import Path

import yaml


def test_workspace_skill_describes_scope_tools_as_dispatchable() -> None:
    root = Path(__file__).resolve().parents[2]
    skill_path = (
        root
        / "agent/agents/integral/integral_agent/actions/integral"
        / "embedded_integral_action/skills/integral_workspace/SKILL.md"
    )
    manifest_path = root / "backend/app/agentive/tool_manifest.yaml"
    raw = skill_path.read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(raw.split("---", 2)[1])
    tools = set(frontmatter["allowed-tools"])

    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    catalogue = {
        row["name"]: row.get("status")
        for domain in manifest["domains"].values()
        for row in domain.get("tools", [])
    }

    for name in ("integral_get_scope", "integral_list_workspaces"):
        assert name in tools
        assert catalogue[name] == "existing"
    assert "not-yet-available" not in raw.lower()
