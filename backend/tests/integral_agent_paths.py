"""Shared paths for Integral resident agent skill packages (ADR-0020 overlay)."""

import os

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

EMBEDDED_INTEGRAL_SKILLS_GLOB = os.path.join(
    _REPO_ROOT,
    "agent",
    "agents",
    "integral",
    "integral_agent",
    "actions",
    "integral",
    "embedded_integral_action",
    "skills",
    "integral_*",
    "SKILL.md",
)

EMBEDDED_INTEGRAL_ACTION_SKILLS_DIR = os.path.join(
    _REPO_ROOT,
    "agent",
    "agents",
    "integral",
    "integral_agent",
    "actions",
    "integral",
    "embedded_integral_action",
    "skills",
)

INTEGRAL_AGENT_APP_ROOT = os.path.join(_REPO_ROOT, "agent")
