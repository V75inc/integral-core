"""Portable Core skill asset locations."""

import os

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
EMBEDDED_INTEGRAL_ACTION_SKILLS_DIR = os.path.join(_REPO_ROOT, "agent", "skills")
EMBEDDED_INTEGRAL_SKILLS_GLOB = os.path.join(
    EMBEDDED_INTEGRAL_ACTION_SKILLS_DIR, "integral-*", "SKILL.md"
)
INTEGRAL_AGENT_APP_ROOT = os.path.join(_REPO_ROOT, "agent")
