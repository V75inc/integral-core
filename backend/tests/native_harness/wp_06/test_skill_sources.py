"""Integral skill projections stay within the Agent Skills format."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic_ai_harness import Skills

from app.agentive.harness.skill_sources import materialize_standard_skill_library


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
