"""ADR-0020: resident integral_* skills live on the action overlay."""

from __future__ import annotations

import glob
import os

import yaml
from jvagent.scaffold.skill_resolve import resolve_merged_skill_bundles

from tests.integral_agent_paths import (
    EMBEDDED_INTEGRAL_ACTION_SKILLS_DIR,
    EMBEDDED_INTEGRAL_SKILLS_GLOB,
    INTEGRAL_AGENT_APP_ROOT,
)

_INTEGRAL_SKILLS = (
    "integral_artifacts",
    "integral_identity",
    "integral_workspace",
    "integral_entries",
    "integral_models",
    "integral_insights",
    "integral_filing",
    "integral_attachments",
    "integral_organize",
    "integral_onboard",
    "integral_model",
    "integral_review",
    "integral_scaffold",
    "integral_scheduling",
    "integral_dashboards",
    "integral_navigation",
)


def test_integral_skills_on_action_overlay_filesystem():
    paths = sorted(glob.glob(EMBEDDED_INTEGRAL_SKILLS_GLOB))
    names = {os.path.basename(os.path.dirname(p)) for p in paths}
    assert names == set(_INTEGRAL_SKILLS)


def test_merged_bundles_discover_action_overlay_skills():
    bundles = resolve_merged_skill_bundles(
        INTEGRAL_AGENT_APP_ROOT,
        "integral",
        "integral_agent",
        include_builtin=False,
    )
    for name in _INTEGRAL_SKILLS:
        assert name in bundles, f"{name} missing from merged skill bundles"
        bundle = bundles[name]
        assert bundle.get("source") == "app"
        assert bundle.get("extends") == "action:integral/embedded_integral_action"
        assert "EmbeddedIntegralAction" in (bundle.get("requires_actions") or ())


def test_each_skill_declares_extends_in_frontmatter():
    for name in _INTEGRAL_SKILLS:
        path = os.path.join(EMBEDDED_INTEGRAL_ACTION_SKILLS_DIR, name, "SKILL.md")
        text = open(path, encoding="utf-8").read()
        fm = yaml.safe_load(text.split("---", 2)[1])
        assert fm.get("extends") == "action:integral/embedded_integral_action"
