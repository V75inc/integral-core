"""Integral skill projections stay within the Agent Skills format."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pydantic_ai.workspaces import LocalWorkspaceBackend
from pydantic_ai_harness import Skills

from app.agentive.harness.skill_sources import (
    materialize_standard_skill_library,
    project_skill_references,
)


@pytest.mark.asyncio
async def test_materialized_library_loads_as_standard_agent_skills(
    tmp_path: Path,
) -> None:
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

    capability = Skills(root, include=names, workspace=LocalWorkspaceBackend(tmp_path))
    assert [item.name for item in (await capability.load()).skills] == [
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


@pytest.mark.asyncio
async def test_saved_schedule_references_load_with_native_skills(
    tmp_path: Path,
) -> None:
    """Legacy keys and colliding/long keys resolve to the actual native IDs."""
    keys = ["team__review", "team-review", "team__" + "very_long_key_" * 8]
    names = materialize_standard_skill_library(
        [(key, "Review current records.", "Read current records.") for key in keys],
        root=tmp_path / "skills",
    )
    original = "Run skills " + ", ".join(f"`{key}`" for key in keys) + "."
    projected = project_skill_references(original, iter(keys))
    aliases = json.loads(projected.rsplit("\n", 1)[-1])
    ids = [aliases.get(key, key) for key in keys]
    catalog = await Skills(
        tmp_path / "skills", include=ids, workspace=LocalWorkspaceBackend(tmp_path)
    ).load()
    assert {item.name for item in catalog.skills} == names
    assert projected.startswith(original + "\n\n")


def test_saved_skill_projection_is_exact_and_currently_authorized():
    instruction = (
        "Run `team__review`, not `other__review` or `team__review_extra`. "
        "Keep xteam__review and prefix.team__review unchanged."
    )
    projected = project_skill_references(instruction, ["team__review"])
    assert projected.startswith(instruction + "\n\n")
    assert json.loads(projected.rsplit("\n", 1)[-1]) == {"team__review": "team-review"}
    assert project_skill_references(instruction, []) == instruction
    assert project_skill_references(
        "Write the literal value `team__review`.", ["team__review"]
    ).startswith("Write the literal value `team__review`.\n\n")


@pytest.mark.asyncio
async def test_duplicate_installations_remain_loadable_without_guessing_alias(tmp_path):
    keys = ["team__review"] * 3
    names = materialize_standard_skill_library(
        [
            (key, "Review records.", f"Workflow {index}.")
            for index, key in enumerate(keys)
        ],
        root=tmp_path / "skills",
    )
    assert len(names) == 3
    catalog = await Skills(
        tmp_path / "skills", include=names, workspace=LocalWorkspaceBackend(tmp_path)
    ).load()
    assert len(catalog.skills) == 3
    assert (
        project_skill_references("Run `team__review`.", keys) == "Run `team__review`."
    )


@pytest.mark.asyncio
async def test_harness_discovers_all_authorized_skills_without_text_routing(
    tmp_path: Path,
) -> None:
    """Harness discovery, not user-text matching, selects the relevant skill."""
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

    names = materialize_standard_skill_library(
        [scaffold, model, entries], root=tmp_path / "authorized-skills"
    )
    capability = Skills(
        tmp_path / "authorized-skills",
        include=names,
        workspace=LocalWorkspaceBackend(tmp_path),
    )

    assert {item.name for item in (await capability.load()).skills} == names
