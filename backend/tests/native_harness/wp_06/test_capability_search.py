"""Unified capability search over authorized skill and tool metadata."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.agentive.harness.capability_search import (
    _search_catalog,
    build_search_capabilities_tool,
    pydantic_tool_search_strategy,
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

    returned = await tool.function(query="equipment register app", limit=6)
    result = returned.return_value
    assert "integral_propose_design" in returned.tools

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
    assert result["recommendation"]["skill"]["name"] == "integral-scaffold"
    assert result["recommendation"]["tool"]["name"] == "integral_propose_design"
    assert "authorizes every tool call" in result["recommendation"]["instruction"]
    assert "candidates, not instructions" in result["recommendation"]["instruction"]


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


def test_existing_record_lookup_prefers_insights_over_app_scaffolding(
    tmp_path: Path,
) -> None:
    """A lookup about a domain must not route as a request to create a system."""
    skill_library = tmp_path / "authorized-skills"
    materialize_standard_skill_library(
        [
            (
                "integral-insights",
                "Search existing Integral records and current workspace state to "
                "answer one-time questions. Find whether information is already "
                "filed, search entries by meaning, summarize, count, rank, compare, "
                "or break down current data; optionally save a query as a View. "
                "For a new operational app, use integral-scaffold.",
                "",
            ),
            (
                "integral-scaffold",
                "Designs and delivers a new operational app when the user wants "
                "to create a new system for a business process. Use for new "
                "registers, inventory and asset management, equipment and tool "
                "tracking, maintenance schedules, staff check-outs, inspections, "
                "and reminders when the user wants the process set up.",
                "",
            ),
        ],
        root=skill_library,
    )

    result = search_capabilities_for_turn(
        query=(
            "concept/meaning search across entries — find anything filed about "
            "keeping equipment serviceable (maintenance, servicing, upkeep, "
            "repairs, inspections)"
        ),
        skill_library=skill_library,
        catalogue=[],
    )

    assert result["recommendation"]["skill"]["name"] == "integral-insights"


def test_mower_servicing_tracker_routes_to_scaffold_over_modeling(
    tmp_path: Path,
) -> None:
    """A plain tracker request is app delivery, not schema-only modeling."""
    skill_library = tmp_path / "authorized-skills"
    materialize_standard_skill_library(
        [
            (
                "integral-model",
                "Owns domain schema design and evolution for an existing App or "
                "Track: shape EntryTypes, fields, and reference patterns from "
                "the operational need. Advise integral-scaffold during a "
                "new-App build without taking over its end-to-end delivery.",
                "",
            ),
            (
                "integral-scaffold",
                "Designs and delivers an operational app or app extension from a "
                "work need. Use when someone wants a simple way to keep track of "
                "a process, a tracker, register, or system for their team, "
                "including equipment and tool inventory, mower or vehicle "
                "servicing, maintenance history, due dates, checkouts, "
                "inspections, and reminders. Check whether an existing app fits, "
                "propose the smallest useful design, build it after approval, "
                "and verify the result.",
                "",
            ),
            (
                "integral-dashboards",
                "Compose and customize app-scoped analytics dashboards, "
                "including bar charts and KPI tiles.",
                "",
            ),
        ],
        root=skill_library,
    )

    result = search_capabilities_for_turn(
        query="propose a mower servicing tracker app",
        skill_library=skill_library,
        catalogue=[],
    )

    assert result["recommendation"]["skill"]["name"] == "integral-scaffold"


def test_single_item_added_to_existing_track_routes_to_entries() -> None:
    """An item create in a named existing list is record work, not scaffolding."""
    backend_root = Path(__file__).resolve().parents[3]
    skill_library = backend_root / "app/resident_harness/" / "skills"
    result = search_capabilities_for_turn(
        query=(
            "Add a mower to our Tools list. Call it QA Mower Alpha, "
            "serial QA-MOWER-001."
        ),
        skill_library=skill_library,
        catalogue=[],
    )

    assert result["recommendation"]["skill"]["name"] == "integral-entries"


def test_scaffold_workflow_discloses_its_proposal_tool_with_noisy_semantic_ranks() -> (
    None
):
    """Skill procedure relevance keeps required tools discoverable."""
    skill = {
        "name": "integral-scaffold",
        "description": "Designs and delivers an operational app.",
        "body": (
            "Check the blueprint with integral_check_design_coverage. "
            "Call integral_propose_design with a concise design. "
            "Use integral_build_approved_design after the user affirms."
        ),
    }

    def tool(name: str, description: str, *parameters: str) -> dict[str, Any]:
        return {
            "name": name,
            "description": description,
            "input_schema": {
                "type": "object",
                "properties": {parameter: {} for parameter in parameters},
            },
        }

    tools = [
        tool(
            "integral_propose_design",
            "Record a new app design proposal.",
            "proposal",
            "blueprint",
        ),
        tool(
            "integral_check_design_coverage",
            "Check an app design blueprint.",
            "blueprint",
        ),
        tool("integral_delete_app", "Delete an app.", "app_id"),
        tool("integral_get_app", "Fetch an app.", "app_id"),
        tool(
            "integral_build_approved_design", "Build an approved design.", "operations"
        ),
    ]

    result = _search_catalog(
        query="propose a mower servicing tracker app",
        limit=8,
        skills=[skill],
        tools=tools,
        immediately_available_tools=(),
        ranked_tool_ids=[
            ("integral_delete_app", 0.99),
            ("integral_get_app", 0.98),
            ("integral_build_approved_design", 0.97),
            ("integral_check_design_coverage", 0.96),
            ("integral_propose_design", 0.95),
        ],
    )

    disclosed = {item["name"] for item in result["results"] if item["kind"] == "tool"}
    assert "integral_propose_design" in disclosed
    assert "do not replace a required" in result["recommendation"]["instruction"]


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
    assert set(tool_names[:2]) == {
        "integral_check_design_coverage",
        "integral_propose_design",
    }
    assert "integral_author_model" not in tool_names[:2]


@pytest.mark.asyncio
async def test_real_scaffold_skill_search_finds_the_proposal_tool_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lay-user tracker wording resolves through semantic capability search."""
    from app.agentive.harness import capability_search
    from app.agentive.harness.capability_search import build_search_capabilities_tool
    from app.agentive.tooling.catalogue import build_tool_catalogue

    backend_root = Path(__file__).resolve().parents[3]
    skill_library = backend_root / "app/resident_harness/" / "skills"
    query = (
        "tool tracker for maintenance company record tool serial number "
        "condition storage place purchase date photo current holder"
    )

    async def semantic_embedding(text: str) -> list[float]:
        if text == query or text.startswith("integral-scaffold"):
            return [1.0, 0.0]
        if text.startswith("integral-entries"):
            return [0.8, 0.6]
        if text.startswith("integral_propose_design"):
            return [1.0, 0.0]
        if text.startswith("integral_check_design_coverage"):
            return [0.9, 0.1]
        return [0.0, 1.0]

    monkeypatch.setattr(
        capability_search,
        "_embed_text_for_capability_search",
        semantic_embedding,
    )
    search_tool = build_search_capabilities_tool(
        skill_library=skill_library,
        catalogue=build_tool_catalogue(),
        immediately_available_tools=("integral_propose_design",),
    )
    returned = await search_tool.function(query=query)
    result = returned.return_value

    assert result["ranking_method"] == "hybrid_semantic_lexical"
    assert result["recommendation"]["skill"]["name"] == "integral-scaffold"
    tool_names = [item["name"] for item in result["results"] if item["kind"] == "tool"]
    assert tool_names[0] == "integral_propose_design"
    assert "integral_author_model" not in tool_names
    assert "integral_build_approved_design" not in tool_names
    assert "integral_describe_substrate" not in tool_names
    recommendation = result["recommendation"]
    assert recommendation["skill"]["name"] == "integral-scaffold"
    assert recommendation["tool"]["name"] == "integral_propose_design"


@pytest.mark.asyncio
async def test_identical_search_reuses_the_same_disclosure() -> None:
    """Retries reuse a cached result rather than turning discovery into an error."""
    tool = build_search_capabilities_tool(skill_library=None, catalogue=[])
    first = await tool.function(query="find an app skill")

    repeated = await tool.function(query="  FIND an APP skill  ")

    assert repeated.return_value == first.return_value
    assert repeated.tools == first.tools
    assert "error" not in repeated.return_value


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
    returned = await tool.function(query="which tracks are in my workspace")
    result = returned.return_value

    assert run_state["capability_search_completed"] is True
    assert result["results"][0]["name"] == "integral_list_tracks"
    assert "discover_with" not in result["results"][0]
    # Observations can change the needed workflow; discovery is not a one-shot gate.
    other = await tool.function(query="set up a place to store messages")
    assert other.return_value.get("error") is not True


@pytest.mark.asyncio
async def test_runtime_search_uses_local_semantic_skill_ranking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lay-user paraphrase can surface its matching deferred skill."""
    from app.agentive.harness import capability_search

    skill_library = tmp_path / "authorized-skills"
    materialize_standard_skill_library(
        [
            (
                "integral-scaffold",
                "Creates an app for organizing equipment and tool inventories.",
                "",
            ),
            (
                "integral-review",
                "Reviews completed work and gives feedback.",
                "",
            ),
        ],
        root=skill_library,
    )
    vectors = {
        "lay user request": [1.0, 0.0],
        "integral-scaffold Creates an app for organizing equipment and tool inventories.": [
            0.95,
            0.05,
        ],
        "integral-review Reviews completed work and gives feedback.": [0.1, 0.9],
    }

    async def fake_embedding(text: str) -> list[float]:
        return vectors[text]

    monkeypatch.setattr(
        capability_search, "_embed_text_for_capability_search", fake_embedding
    )
    tool = build_search_capabilities_tool(
        skill_library=skill_library,
        catalogue=[],
    )

    returned = await tool.function(query="lay user request")
    result = returned.return_value

    assert result["recommendation"]["skill"]["name"] == "integral-scaffold"
    assert result["ranking_method"] == "hybrid_semantic_lexical"


@pytest.mark.asyncio
async def test_negative_skill_routing_text_does_not_create_positive_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A skill's explicit exclusions must not outrank its positive workflow."""
    from app.agentive.harness import capability_search

    skill_library = tmp_path / "authorized-skills"
    materialize_standard_skill_library(
        [
            (
                "integral-onboard",
                "Guides workspace orientation and discovery when the user's goal is unclear; "
                "do not use for a specific existing-record lookup.",
                "",
            ),
            (
                "integral-entries",
                "Manages a specific existing record or an explicitly requested record operation.",
                "",
            ),
        ],
        root=skill_library,
    )
    vectors = {
        "find a specific record by serial number in the workspace": [1.0, 0.0],
        "integral-onboard Guides workspace orientation and discovery when the user's goal is unclear;": [
            0.0,
            1.0,
        ],
        "integral-entries Manages a specific existing record or an explicitly requested record operation.": [
            0.99,
            0.1,
        ],
    }

    async def fake_embedding(text: str) -> list[float]:
        return vectors[text]

    monkeypatch.setattr(
        capability_search, "_embed_text_for_capability_search", fake_embedding
    )
    tool = build_search_capabilities_tool(skill_library=skill_library, catalogue=[])

    result = (
        await tool.function(
            query="find a specific record by serial number in the workspace"
        )
    ).return_value

    assert result["recommendation"]["skill"]["name"] == "integral-entries"


@pytest.mark.asyncio
async def test_capability_search_semantically_matches_tool_argument_descriptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plain-language field lookups surface the matching record search tool."""

    async def fake_embedding(text: str) -> list[float]:
        if text == "Can you find serial QA-WRENCH-1011 in my workspace?":
            return [1.0, 0.0]
        if "serial number and asset identifiers" in text:
            return [0.99, 0.01]
        if "active Integral user's profile" in text:
            return [0.05, 0.95]
        if "current workspace" in text:
            return [0.7, 0.3]
        return [0.0, 1.0]

    monkeypatch.setattr(
        "app.agentive.harness.capability_search._embed_text_for_capability_search",
        fake_embedding,
    )
    catalogue = [
        {
            "name": "integral_whoami",
            "description": "Return the active Integral user's profile.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "integral_get_scope",
            "description": "Return the active user's current workspace and role.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "integral_query_entries",
            "description": "Filter and list entries (records) across tracks.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Search keyword text in record titles and bodies, "
                            "including serial number and asset identifiers."
                        ),
                    }
                },
            },
        },
    ]
    tool = build_search_capabilities_tool(
        skill_library=None,
        catalogue=catalogue,
        immediately_available_tools=(
            "integral_whoami",
            "integral_get_scope",
            "integral_query_entries",
        ),
    )

    returned = await tool.function(
        query="Can you find serial QA-WRENCH-1011 in my workspace?"
    )

    result = returned.return_value
    assert result["recommendation"]["tool"]["name"] == "integral_query_entries"
    assert "integral_query_entries" in returned.tools
    assert result["ranking_method"] == "hybrid_semantic_lexical"


@pytest.mark.asyncio
async def test_exact_parameter_match_can_correct_a_weak_semantic_tool_rank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A generic workspace embedding must not outrank an exact query contract."""

    async def imperfect_embedding(text: str) -> list[float]:
        if text == "find a specific record by serial number in the workspace":
            return [1.0, 0.0]
        if text.startswith("integral_list_workspace_attachments"):
            return [0.99, 0.01]
        if text.startswith("integral_query_entries"):
            return [0.8, 0.6]
        return [0.0, 1.0]

    monkeypatch.setattr(
        "app.agentive.harness.capability_search._embed_text_for_capability_search",
        imperfect_embedding,
    )
    catalogue = [
        {
            "name": "integral_list_workspace_attachments",
            "description": "List every attachment across accessible tracks in a workspace.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "integral_query_entries",
            "description": "Filter and list entries (records) across tracks.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Search record titles and bodies by keyword, including "
                            "serial number and asset identifiers."
                        ),
                    }
                },
            },
        },
    ]
    tool = build_search_capabilities_tool(
        skill_library=None,
        catalogue=catalogue,
        immediately_available_tools=tuple(item["name"] for item in catalogue),
    )

    returned = await tool.function(
        query="find a specific record by serial number in the workspace"
    )

    assert returned.return_value["recommendation"]["tool"]["name"] == (
        "integral_query_entries"
    )
    assert "integral_query_entries" in returned.tools


def test_tool_search_document_includes_argument_descriptions() -> None:
    """Argument descriptions carry retrieval meaning beyond field names."""
    from app.agentive.harness.capability_search import _tool_text

    document = _tool_text(
        {
            "name": "integral_query_entries",
            "description": "Filter and list records.",
            "input_schema": {
                "properties": {
                    "query": {
                        "description": "Search by serial number or asset identifier."
                    }
                }
            },
        },
        include_schema_details=True,
    )

    assert "serial number" in document
    assert "asset identifier" in document


@pytest.mark.asyncio
async def test_capability_search_reports_lexical_fallback_when_embeddings_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unavailable local embeddings degrade to lexical retrieval honestly."""

    async def unavailable_embedding(_text: str) -> list[float]:
        raise RuntimeError("embedding model unavailable")

    monkeypatch.setattr(
        "app.agentive.harness.capability_search._embed_text_for_capability_search",
        unavailable_embedding,
    )
    tool = build_search_capabilities_tool(
        skill_library=None,
        catalogue=[
            {
                "name": "integral_list_tracks",
                "description": "List tracks in the current workspace.",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
    )

    returned = await tool.function(query="list tracks in my workspace")

    assert returned.return_value["ranking_method"] == "lexical"
    assert returned.return_value["recommendation"]["tool"]["name"] == (
        "integral_list_tracks"
    )


@pytest.mark.asyncio
async def test_pydantic_tool_search_uses_semantic_rank_without_keyword_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pydantic's deferred corpus ranks paraphrased requests semantically."""

    async def fake_embedding(text: str) -> list[float]:
        if text == "equipment register":
            return [1.0, 0.0]
        if text.startswith("asset_catalog"):
            return [0.9, 0.1]
        return [0.0, 1.0]

    monkeypatch.setattr(
        "app.agentive.harness.capability_search._embed_text_for_capability_search",
        fake_embedding,
    )
    tools = [
        SimpleNamespace(
            name="general_lookup",
            description="Search the company knowledge base",
            parameters_json_schema={"properties": {"query": {}}},
        ),
        SimpleNamespace(
            name="asset_catalog",
            description="Track property and allocations to custodians",
            parameters_json_schema={"properties": {"custodian": {}}},
        ),
    ]

    result = await pydantic_tool_search_strategy(None, ["equipment register"], tools)

    assert result[0] == "asset_catalog"
    assert set(result) == {"asset_catalog", "general_lookup"}


def test_exact_tool_addresses_survive_approximate_ranking_and_skill_slots():
    from app.agentive.harness.capability_search import _search_catalog

    names = [
        "integral_describe_capabilities",
        "integral_governed_query",
        "integral_get_track_schema",
    ]
    skills = [
        {
            "name": f"s{i}",
            "description": "Read capability query records",
            "body": "Use integral_get_track_schema",
        }
        for i in range(8)
    ]
    tools = [
        {
            "name": name,
            "description": "Read capability query records",
            "input_schema": {"type": "object", "properties": {}},
        }
        for name in names
    ]
    result = _search_catalog(
        query="integral_describe_capabilities integral_governed_query",
        limit=5,
        skills=skills,
        tools=tools,
        immediately_available_tools=(),
        ranked_tool_ids=[(name, 1.0) for name in reversed(names)],
    )
    discovered = [item["name"] for item in result["results"] if item["kind"] == "tool"]
    assert discovered[:2] == names[:2]
    result = _search_catalog(
        query="not_integral_describe_capabilities_extra",
        limit=5,
        skills=[],
        tools=tools,
        immediately_available_tools=(),
        ranked_tool_ids=[(names[2], 1.0)],
    )
    assert result["results"][0]["name"] == names[2]
