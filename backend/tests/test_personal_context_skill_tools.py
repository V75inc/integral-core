"""A Personal Context skill can emit the writes its SOP requires of it.

The failure this defends against is quiet and expensive to diagnose. The
``context_correct`` SOP mandates that a correction stage as ONE card carrying
both halves -- the replacement fact and the supersede -- "so the person sees
the whole swap rather than a create with an invisible side effect". A single
card spanning two writes is a batch. But ``allowed-tools`` granted no batch
tools, so the skill could ground correctly, identify the exact fact, describe
the swap in full, and then have no way to emit it.

Live, that read as the agent asking for permission it was never supposed to
need: four consecutive turns ended at "the card isn't up yet -- say go ahead
and I'll stage it", across two different models, while ``context_promote`` --
which does hold the batch tools -- staged its card on the first attempt.

Nothing errored. The manifest and the frontmatter agreed with each other; they
were simply both missing the same three tools, which is why parity between them
is not the property worth asserting here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

_BUNDLE = Path(__file__).resolve().parents[1] / "app" / "profiles" / "personal-context"
_MANIFEST = _BUNDLE / "profile.yaml"
_BATCH_TOOLS = {
    "integral_begin_batch",
    "integral_commit_batch",
    "integral_cancel_batch",
}

# Skills whose SOP commits them to landing several writes as one reviewable
# card. Add a skill here when its SKILL.md promises a single card for a
# multi-write change.
#
# ``context_correct`` was here until the correction was split in two: it now
# resolves the swap and hands off, and ``context_correct_apply`` performs it in
# the next turn. Grounding plus a two-write batch did not fit one turn's budget
# -- six consecutive attempts described the card and staged none of them.
_BATCHING_SKILLS = {"context_correct_apply", "context_promote"}

# Tools that mutate. The resolving half of a correction must hold none of them.
_WRITE_TOOLS = {
    "integral_create_entry",
    "integral_update_entry",
    "integral_begin_batch",
    "integral_commit_batch",
    "integral_cancel_batch",
}


def _manifest_skills() -> dict[str, dict]:
    data = yaml.safe_load(_MANIFEST.read_text())
    # app-scope manifests nest the skill list under `app:`
    skills = (data.get("app") or {}).get("skills") or data.get("skills") or []
    return {s["key"]: s for s in skills}


def _frontmatter_tools(skill_key: str) -> set[str]:
    text = (_BUNDLE / "skills" / skill_key / "SKILL.md").read_text()
    _, _, rest = text.partition("---\n")
    front, _, _ = rest.partition("\n---")
    return set(yaml.safe_load(front).get("allowed-tools") or [])


@pytest.mark.parametrize("skill_key", sorted(_BATCHING_SKILLS))
def test_batching_skill_is_granted_the_batch_tools(skill_key):
    """A skill promising one card for a multi-write change can open a batch."""
    granted = _frontmatter_tools(skill_key)
    missing = _BATCH_TOOLS - granted
    assert not missing, (
        f"{skill_key} stages multiple writes as one card but its SKILL.md "
        f"allowed-tools is missing {sorted(missing)}"
    )


@pytest.mark.parametrize("skill_key", sorted(_BATCHING_SKILLS))
def test_batching_skill_manifest_matches_frontmatter(skill_key):
    """The manifest grant must not lag the SOP's own tool list.

    Both are consulted; a tool present in only one is a grant that silently
    depends on which path resolved the skill.
    """
    manifest_tools = set(_manifest_skills()[skill_key].get("tools_required") or [])
    assert _BATCH_TOOLS <= manifest_tools, (
        f"{skill_key} tools_required is missing "
        f"{sorted(_BATCH_TOOLS - manifest_tools)}"
    )


def test_apply_skill_states_the_two_step_dependency():
    """A correction is two cards, and the SOP has to say why — and not stop.

    It cannot be one card: `superseded_by` must carry the replacement's entry
    id, and that id does not exist until the create executes. The failure this
    guards is not a crash but a false report — step 1 files a replacement,
    step 2 never runs because blessing a card does not start a new turn, and
    the agent says "Applied" while both facts still read as live. Observed
    exactly that before the SOP was rewritten.
    """
    sop = (_BUNDLE / "skills" / "context_correct_apply" / "SKILL.md").read_text()
    assert "step 1 of 2" in sop
    # The dependency, stated: the id is why it cannot be one batch.
    assert "superseded_by" in sop and "does not exist until" in sop
    # And the rule that keeps a half-applied correction from being reported
    # as a whole one.
    assert "Never report the correction as applied until step 2" in sop


def test_neither_correction_skill_still_promises_one_card():
    """Both halves must describe the same shape, or the SOPs argue with each other."""
    for key in ("context_correct", "context_correct_apply"):
        sop = (_BUNDLE / "skills" / key / "SKILL.md").read_text()
        assert "single card" not in sop, f"{key} still promises a single card"


def test_resolving_half_of_a_correction_cannot_write():
    """``context_correct`` resolves and hands off; it must not be able to write.

    The split is only worth anything if the first turn genuinely cannot spend
    its budget writing. A write tool creeping back into the resolving half
    would quietly restore the failure the split was made to fix.
    """
    granted = _frontmatter_tools("context_correct")
    assert not (_WRITE_TOOLS & granted), (
        "context_correct is the read-only half of a correction but was granted "
        f"{sorted(_WRITE_TOOLS & granted)}"
    )
    manifest_tools = set(
        _manifest_skills()["context_correct"].get("tools_required") or []
    )
    assert not (_WRITE_TOOLS & manifest_tools)


def test_the_two_halves_of_a_correction_are_both_registered():
    """A handoff to a skill that is not installed is a dead end."""
    keys = set(_manifest_skills())
    assert {"context_correct", "context_correct_apply"} <= keys


def test_apply_half_can_hand_back_when_its_precondition_is_unmet():
    """A skill with a precondition needs a door out of it.

    ``context_correct_apply`` acts on a resolution a previous turn stated. It
    has no query tools by design and forbids re-grounding, so a turn that
    enters it WITHOUT a resolution has no legal move -- it cannot obtain the
    target and cannot act without one. Observed live: an open-ended "supersede
    anything still dangling" activated this skill on a fresh thread, and the
    turn burned its budget and died on the repeat guard having staged nothing.

    The fix is an exit, not a warning: step 0 hands back to context_correct.
    That exit is only reachable if ``use_skill`` is granted, so the grant is
    part of the contract rather than an incidental extra.
    """
    sop = (_BUNDLE / "skills" / "context_correct_apply" / "SKILL.md").read_text()
    # Assert the INSTRUCTION, not the label: a heading called "Step 0" that no
    # longer tells the agent to leave is exactly the regression this guards.
    assert "check the precondition, and leave if it is not met" in sop
    # And it must name where it goes, or it is just another warning.
    assert "`personal-context__context_correct` and stop" in sop


def test_apply_half_cannot_re_ground():
    """The write half has no search tools -- re-grounding is what it avoids.

    It also must not be able to guess: without query tools it can only act on
    the resolution the previous turn stated, which is the point.
    """
    granted = _frontmatter_tools("context_correct_apply")
    assert "integral_query_entries" not in granted
    assert "integral_get_related" not in granted
    assert "integral_list_tracks" not in granted


def test_declared_tools_are_real_catalogue_tools():
    """A skill may only declare tools the live tool surface actually has.

    This is the check that would have caught a mistake I shipped: step 0's exit
    calls ``use_skill``, so I declared it in allowed-tools. ``use_skill`` is an
    orchestrator LOOP tool, not a catalogue tool -- registration refused the
    skill outright ("declares unknown tools: ['use_skill']"), and because
    registration is part of the operational-layer sync, that failure took the
    whole sync down with it: no skill in the bundle updated, silently, on a
    path whose only symptom was one WARNING line.

    The model reaches ``use_skill`` regardless -- it is how it enters a skill
    in the first place -- so the SOP instruction alone is the fix and the
    declaration was pure downside.

    Validated against tool_manifest.yaml rather than build_tool_catalogue()
    because building the catalogue mutates os.environ, which conftest fails.
    The manifest is reconciled against the live surface by
    .ci/tool_manifest_check.sh, so it is an equivalent source of truth.
    """
    manifest_path = (
        Path(__file__).resolve().parents[1] / "app" / "agentive" / "tool_manifest.yaml"
    )
    surface = set(re.findall(r"^\s*-\s+name:\s*(\S+)", manifest_path.read_text(), re.M))
    assert "integral_get_related" in surface, "manifest parse looks wrong"
    assert "use_skill" not in surface, (
        "use_skill is an orchestrator loop tool; if it ever becomes a "
        "catalogue tool this test's premise needs revisiting"
    )

    for key in sorted(_manifest_skills()):
        for tool in _manifest_skills()[key].get("tools_required") or []:
            assert tool in surface, (
                f"{key} declares {tool!r}, which is not on the tool surface — "
                "skill registration will refuse it and abort the whole sync"
            )
