"""Integral skill projections stay within the Agent Skills format."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic_ai_harness import Skills

from app.agentive.harness.skill_sources import (
    materialize_standard_skill_library,
    scaffold_workflow_tools,
    select_relevant_skill_sources,
    split_eager_skill_sources,
)


def test_materialized_library_loads_as_standard_agent_skills(tmp_path: Path) -> None:
    """Only normalized name/description metadata reaches Pydantic Skills."""
    root = tmp_path / "skills"
    names = materialize_standard_skill_library(
        [
            (
                "example_app__prepare_asset_checkout",
                "Prepare an asset checkout.",
                "Check availability before staging the checkout.",
            )
        ],
        root=root,
    )
    assert names == {"example-app-prepare-asset-checkout"}
    path = root / "example-app-prepare-asset-checkout" / "SKILL.md"
    frontmatter = path.read_text(encoding="utf-8").split("---", 2)[1]
    metadata = yaml.safe_load(frontmatter)
    assert metadata == {
        "name": "example-app-prepare-asset-checkout",
        "description": "Prepare an asset checkout.",
    }

    capability = Skills(root, include=names)
    assert [item.id for item in capability._deferred_capabilities] == [
        "example-app-prepare-asset-checkout"
    ]


def test_normalized_name_collisions_remain_distinct(tmp_path: Path) -> None:
    """Different source keys do not overwrite one projected skill."""
    names = materialize_standard_skill_library(
        [
            ("team__review", "Review one.", "Review one."),
            ("team-review", "Review two.", "Review two."),
        ],
        root=tmp_path / "skills",
    )
    assert len(names) == 2
    assert all(len(name) <= 64 for name in names)


def test_greenfield_build_selects_scaffold_owner_only() -> None:
    """A build confirmation loads its lifecycle owner without specialist noise."""
    scaffold = (
        "integral-scaffold",
        "Owns delivery of a new operational App and staged workflow.",
        "Follow discover, propose, authorize, execute, verify.",
    )
    model = (
        "integral-model",
        "Owns existing schema and relation design.",
        "Schema specialist instructions.",
    )
    entries = (
        "integral-entries",
        "Owns existing record changes.",
        "Entry specialist instructions.",
    )

    selected = select_relevant_skill_sources(
        [scaffold, model, entries],
        user_text=(
            "I confirm this design. Build the two tracks, table views, and "
            "one linked sample item."
        ),
    )

    assert selected == (scaffold,)


def test_scaffold_is_supplied_once_as_instructions_on_build_turn() -> None:
    """The lifecycle owner cannot hit the deferred duplicate-load trap."""
    scaffold = (
        "integral-scaffold",
        "Owns delivery of a new operational App.",
        "Propose, authorize, execute, verify.",
    )
    eager, deferred = split_eager_skill_sources(
        [scaffold], user_text="I confirm this design. Build it."
    )
    assert eager == (scaffold,)
    assert deferred == ()


def test_non_scaffold_skills_keep_harness_deferred_loading() -> None:
    """Other standard skills continue to use Pydantic AI Harness Skills."""
    skill = ("integral-workspace", "Workspace orientation.", "List apps.")
    eager, deferred = split_eager_skill_sources(
        [skill], user_text="What apps are in my workspace?"
    )
    assert eager == ()
    assert deferred == (skill,)


def test_scaffold_workflow_exposes_proposal_tool_for_design_turn() -> None:
    assert scaffold_workflow_tools(
        user_text="Create a proposal for a tool library app; do not build yet.",
        has_pending_design=False,
    ) == frozenset({"integral_propose_design", "integral_list_apps"})


def test_scaffold_workflow_routes_affirmation_to_guarded_builder() -> None:
    assert scaffold_workflow_tools(
        user_text="Confirm this design and build it exactly as shown.",
        has_pending_design=True,
        affirmative=True,
    ) == frozenset({"integral_build_approved_design", "integral_verify_build"})
    assert scaffold_workflow_tools(
        user_text="Yes",
        has_pending_design=True,
        affirmative=True,
    ) == frozenset({"integral_build_approved_design", "integral_verify_build"})
    # Durable approval is enforced inside the builder. A stale provider
    # snapshot must not hide it and fall back to proposal-only tools.
    assert scaffold_workflow_tools(
        user_text="Yes",
        has_pending_design=False,
        affirmative=True,
    ) == frozenset({"integral_build_approved_design", "integral_verify_build"})
    assert scaffold_workflow_tools(
        user_text="Confirm this design, but do not build it yet.",
        has_pending_design=True,
        affirmative=False,
    ) == frozenset({"integral_propose_design", "integral_list_apps"})
    assert scaffold_workflow_tools(
        user_text="Please verify the build again.",
        has_pending_design=True,
        affirmative=False,
    ) == frozenset({"integral_verify_build"})


def test_non_build_turn_selects_relevant_skill_with_small_cap() -> None:
    """A broad Core library is reduced to skills relevant to this request."""
    workspace = (
        "integral-workspace",
        "Answer questions about the active workspace and its apps.",
        "Workspace instructions.",
    )
    filing = (
        "integral-filing",
        "File and organize documents in an existing app.",
        "Filing instructions.",
    )

    selected = select_relevant_skill_sources(
        [filing, workspace], user_text="What apps are in my workspace?"
    )

    assert selected == (workspace,)
