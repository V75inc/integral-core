"""The native resident discovers portable Core instructions and tool contracts."""

from __future__ import annotations

import glob
import os

import yaml

from app.agentive.services.agent_skills import list_core_skills
from tests.integral_agent_paths import (
    EMBEDDED_INTEGRAL_ACTION_SKILLS_DIR,
    EMBEDDED_INTEGRAL_SKILLS_GLOB,
)

_INTEGRAL_SKILLS = (
    "integral-artifacts",
    "integral-identity",
    "integral-workspace",
    "integral-entries",
    "integral-models",
    "integral-insights",
    "integral-filing",
    "integral-attachments",
    "integral-organize",
    "integral-onboard",
    "integral-model",
    "integral-review",
    "integral-scaffold",
    "integral-scheduling",
    "integral-dashboards",
    "integral-navigation",
)


def test_integral_skills_on_portable_filesystem():
    paths = sorted(glob.glob(EMBEDDED_INTEGRAL_SKILLS_GLOB))
    names = {os.path.basename(os.path.dirname(p)) for p in paths}
    assert names == set(_INTEGRAL_SKILLS)


def test_native_discovers_every_portable_core_skill():
    rows = list_core_skills()
    by_key = {row["key"]: row for row in rows}
    for name in _INTEGRAL_SKILLS:
        assert name in by_key
        assert by_key[name]["resolved_body"]
        if name != "integral-navigation":
            assert by_key[name]["tools_required"]
        else:
            assert by_key[name]["tools_required"] == []


def test_each_skill_uses_only_standard_frontmatter():
    for name in _INTEGRAL_SKILLS:
        path = os.path.join(EMBEDDED_INTEGRAL_ACTION_SKILLS_DIR, name, "SKILL.md")
        text = open(path, encoding="utf-8").read()
        fm = yaml.safe_load(text.split("---", 2)[1])
        assert set(fm) <= {
            "name",
            "description",
            "license",
            "compatibility",
            "metadata",
            "allowed-tools",
        }
