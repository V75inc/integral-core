"""Unified capability search over authorized skill and tool metadata."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.agentive.harness.capability_search import (
    build_search_capabilities_tool,
    search_capabilities_for_turn,
)
from app.agentive.harness.skill_sources import materialize_standard_skill_library


@pytest.mark.asyncio
async def test_search_finds_skill_and_tool_for_a_plain_user_goal(
    tmp_path: Path,
) -> None:
    """The read-only search returns both a loadable skill and callable tool."""
    skill_library = tmp_path / "authorized-skills"
    materialize_standard_skill_library(
        [
            (
                "integral-scaffold",
                "Design and build an operational app from a user's requirements.",
                "Create an equipment register with tracks, fields, and views.",
            ),
            (
                "integral-entries",
                "Create and update records in existing apps.",
                "Work with entries and track fields.",
            ),
        ],
        root=skill_library,
    )
    catalogue: list[dict[str, Any]] = [
        {
            "name": "integral_propose_design",
            "description": "Save an app and track design proposal for review.",
            "input_schema": {
                "type": "object",
                "properties": {"proposal": {"type": "string"}},
            },
        },
        {
            "name": "integral_create_entry",
            "description": "Add an entry record to an existing track.",
            "input_schema": {
                "type": "object",
                "properties": {"track_id": {"type": "string"}},
            },
        },
    ]
    tool = build_search_capabilities_tool(
        skill_library=skill_library,
        catalogue=catalogue,
    )

    result = await tool.function(query="equipment register app", limit=6)

    assert result.get("error") is not True
    found = {(item["kind"], item["name"]) for item in result["results"]}
    assert ("skill", "integral-scaffold") in found
    assert ("tool", "integral_propose_design") in found
    skill_result = next(
        item for item in result["results"] if item["name"] == "integral-scaffold"
    )
    assert skill_result["load_with"] == {
        "tool": "load_capability",
        "id": "integral-scaffold",
    }
    proposal_result = next(
        item for item in result["results"] if item["name"] == "integral_propose_design"
    )
    assert proposal_result["arguments"] == ["proposal"]


def test_initial_discovery_ranks_a_skill_and_provides_schema_discovery_path(
    tmp_path: Path,
) -> None:
    """Initial discovery helps the model choose without hiding capabilities."""
    skill_library = tmp_path / "authorized-skills"
    materialize_standard_skill_library(
        [
            (
                "integral-scaffold",
                "Creates a new app to organize and track equipment, tools, assets, work, and records.",
                "Turn the need into a design and build its tracks, fields, and views.",
            )
        ],
        root=skill_library,
    )
    catalogue = [
        {
            "name": "integral_propose_design",
            "description": "Save a design proposal for a new app.",
            "input_schema": {"type": "object", "properties": {"proposal": {}}},
        }
    ]

    result = search_capabilities_for_turn(
        query=(
            "I need a simple way to keep track of the tools my maintenance crew "
            "uses, including condition, location, purchase date and checkout. "
            "Show me a design first."
        ),
        skill_library=skill_library,
        catalogue=catalogue,
        immediately_available_tools=("search_capabilities",),
    )

    skill = next(item for item in result["results"] if item["kind"] == "skill")
    proposal = next(item for item in result["results"] if item["kind"] == "tool")
    assert skill["name"] == "integral-scaffold"
    assert skill["load_with"] == {
        "tool": "load_capability",
        "id": "integral-scaffold",
    }
    assert proposal["discover_with"] == {
        "tool": "search_tools",
        "queries": ["integral_propose_design"],
    }


def test_tool_ranking_uses_selected_skill_workflow_instead_of_negative_manifest_text(
    tmp_path: Path,
) -> None:
    """Scaffold discovery should surface proposal tools, not schema/build detours."""
    from app.agentive.tooling.catalogue import build_tool_catalogue

    skill_library = tmp_path / "authorized-skills"
    materialize_standard_skill_library(
        [
            (
                "integral-scaffold",
                "Creates an app when someone needs a new equipment register or tool tracker.",
                "For a new tool-tracking system, check its design coverage with "
                "integral_check_design_coverage, then record the user's design "
                "with integral_propose_design. Use integral_build_approved_design "
                "only after the user affirms that proposal. integral_author_model "
                "creates a schema package, not the complete app.",
            )
        ],
        root=skill_library,
    )

    result = search_capabilities_for_turn(
        query=(
            "tool tracker for maintenance company record tool serial number "
            "condition storage place purchase date photo current holder"
        ),
        skill_library=skill_library,
        catalogue=build_tool_catalogue(),
        immediately_available_tools=("integral_propose_design",),
        limit=8,
    )

    tool_names = [item["name"] for item in result["results"] if item["kind"] == "tool"]
    assert tool_names[:2] == [
        "integral_check_design_coverage",
        "integral_propose_design",
    ]
    assert "integral_author_model" not in tool_names[:2]


@pytest.mark.asyncio
async def test_identical_search_is_suppressed_with_a_stop_instruction() -> None:
    """Repeated catalog queries cannot become another model/tool loop."""
    tool = build_search_capabilities_tool(skill_library=None, catalogue=[])
    await tool.function(query="find an app skill")

    repeated = await tool.function(query="  FIND an APP skill  ")

    assert repeated["error"] is True
    assert repeated["error_code"] == "repeated_capability_search_suppressed"
    assert repeated["retryable"] is False
    assert "Use the results" in repeated["message"]


@pytest.mark.asyncio
async def test_search_marks_discovery_complete_once_for_the_run() -> None:
    """Operational capabilities can be gated on an auditable search receipt."""
    run_state: dict[str, Any] = {}
    tool = build_search_capabilities_tool(
        skill_library=None,
        catalogue=[
            {
                "name": "integral_list_tracks",
                "description": "List tracks in the current workspace.",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
        immediately_available_tools=("integral_list_tracks",),
        run_state=run_state,
    )

    assert run_state["capability_search_completed"] is False
    result = await tool.function(query="which tracks are in my workspace")

    assert run_state["capability_search_completed"] is True
    assert result["results"][0]["name"] == "integral_list_tracks"
    assert "discover_with" not in result["results"][0]
