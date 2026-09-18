"""Skill <-> catalogue tool mapping is consistent in BOTH directions.

* FORWARD: every SKILL.md ``allowed-tools`` entry resolves to a real catalogue
  tool (no skill references a tool that does not exist).
* REVERSE: every catalogue tool is surfaced by at least one skill's
  ``allowed-tools`` — except a small, documented allowlist of
  orchestration/meta tools that are reached another way. This catches a NEW
  catalogue tool that ships unsurfaced (callable but no SOP guides the agent
  to it).
"""

import glob
import os

import yaml  # if PyYAML unavailable, parse the simple `- name` list manually

from app.agentive.tooling import build_tool_catalogue

# Catalogue tools intentionally NOT surfaced by any SOP skill. Reachable at
# runtime (``block_raw_tool_invocation: false`` → a tool the model names
# dispatches even when unsurfaced; plus ``find_tool`` / ``use_skill``
# discovery) — simply not pinned into a skill's prompt set:
#   * integral_set_focus  — ephemeral conversation/session state; the
#     orchestrator drives it, not an SOP.
#   * integral_author_skill / integral_update_skill / integral_delete_skill —
#     agent skill-authoring; small, self-explanatory tool surface with no
#     dedicated SOP skill yet.
#   * integral_get_page_context — injected by the host from current UI state;
#     models do not select it as part of an SOP.
# Add a tool here (with a reason) only when it genuinely belongs to no SOP.
# (``integral_list_agents`` was removed with the A2A discovery surface — ADR-003.)
_UNSURFACED_BY_DESIGN = {
    "integral_set_focus",
    "integral_author_skill",
    "integral_update_skill",
    "integral_delete_skill",
    "integral_get_page_context",
}

from tests.integral_agent_paths import EMBEDDED_INTEGRAL_SKILLS_GLOB

_SKILLS_GLOB = EMBEDDED_INTEGRAL_SKILLS_GLOB


def _parse_frontmatter(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    assert text.startswith("---"), f"{path}: no frontmatter"
    fm = text.split("---", 2)[1]
    return yaml.safe_load(fm)


def test_skill_allowed_tools_exist_in_catalogue():
    """Every SKILL.md allowed-tool resolves to a tool in the manifest catalogue."""
    catalogue = {t["name"] for t in build_tool_catalogue()}
    skill_paths = sorted(glob.glob(_SKILLS_GLOB))
    assert (
        len(skill_paths) == 15
    ), f"expected 15 integral skills, found {len(skill_paths)}"
    for path in skill_paths:
        fm = _parse_frontmatter(path)
        for tool in fm.get("allowed-tools") or []:
            assert (
                tool in catalogue
            ), f"{os.path.basename(os.path.dirname(path))}: {tool!r} not in manifest catalogue"


def test_every_catalogue_tool_is_surfaced_by_a_skill_or_allowlisted():
    """Reverse coverage: no catalogue tool is orphaned (callable but surfaced by
    no SOP), except the documented orchestration/meta allowlist."""
    catalogue = {t["name"] for t in build_tool_catalogue()}
    surfaced = set()
    for path in sorted(glob.glob(_SKILLS_GLOB)):
        fm = _parse_frontmatter(path)
        surfaced.update(fm.get("allowed-tools") or [])
    orphans = catalogue - surfaced - _UNSURFACED_BY_DESIGN
    assert not orphans, (
        "catalogue tools surfaced by no skill — add each to a skill's "
        "allowed-tools, or to _UNSURFACED_BY_DESIGN with a reason: "
        f"{sorted(orphans)}"
    )
    # And the allowlist must not name a tool that no longer exists (stale entry).
    stale = _UNSURFACED_BY_DESIGN - catalogue
    assert (
        not stale
    ), f"_UNSURFACED_BY_DESIGN names non-catalogue tools: {sorted(stale)}"
