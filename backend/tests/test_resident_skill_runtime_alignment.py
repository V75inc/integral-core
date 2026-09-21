"""Resident skill instructions must agree with the dispatchable tool surface."""

from __future__ import annotations

import re
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


def test_no_core_skill_marks_an_existing_manifest_tool_unavailable() -> None:
    """Fallback prose must move in lockstep with the public tool catalogue."""
    root = Path(__file__).resolve().parents[2]
    skills = root / "agent/agents/integral/integral_agent/actions/integral"
    skills = skills / "embedded_integral_action/skills"
    manifest = yaml.safe_load(
        (root / "backend/app/agentive/tool_manifest.yaml").read_text(encoding="utf-8")
    )
    catalogue = {
        row["name"]: row.get("status")
        for domain in manifest["domains"].values()
        for row in domain.get("tools", [])
    }

    for skill_path in skills.glob("integral_*/SKILL.md"):
        raw = skill_path.read_text(encoding="utf-8")
        for block in re.findall(
            r"(?is)(?:not[- ]yet[- ]available|not yet dispatchable).*?(?=\n#{1,3}\s|\Z)",
            raw,
        ):
            for name in re.findall(r"`(integral_[a-z0-9_]+)`", block):
                assert (
                    catalogue.get(name) != "existing"
                ), f"{skill_path.name} says live tool {name} is unavailable"


def test_scaffold_is_the_single_resident_delivery_owner() -> None:
    """The live SOP exposes the complete, receipt-honest delivery sequence."""
    from app.services.skill_compliance import (
        RESIDENT_DELIVERY_OWNER,
        RESIDENT_DELIVERY_PHASES,
    )

    root = Path(__file__).resolve().parents[2]
    path = (
        root
        / "agent/agents/integral/integral_agent/actions/integral"
        / f"embedded_integral_action/skills/{RESIDENT_DELIVERY_OWNER}/SKILL.md"
    )
    body = path.read_text(encoding="utf-8").lower()

    assert all(phase in body for phase in RESIDENT_DELIVERY_PHASES)
    assert "say “verified” only after" in body
    assert "must never be rendered as a saved result" in body
    assert "explicit design-only boundary" in body
    assert "proposed — nothing has been built." in body
    assert "do **not** call" in body
