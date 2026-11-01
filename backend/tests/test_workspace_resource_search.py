"""Resource discovery never turns ranked metadata into authority."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.errors import BadRequestError
from app.services import workspace_resource_search as search


@pytest.mark.asyncio
async def test_live_scope_and_hidden_parent_are_preserved(monkeypatch):
    """Denied, foreign and hidden parent metadata do not reach results."""
    apps = [
        SimpleNamespace(
            id="a", name="Visible app", description="staff", lifecycle_state="active"
        )
    ]
    tracks = [
        SimpleNamespace(id="t1", title="Staff directory", purpose="staff"),
        SimpleNamespace(id="t2", title="Staff secret", purpose="staff"),
        SimpleNamespace(id="t3", title="Staff direct share", purpose="staff"),
        SimpleNamespace(id="t4", title="Staff foreign", purpose="staff"),
    ]
    monkeypatch.setattr(search, "callable_apps_for_scope", AsyncMock(return_value=apps))
    monkeypatch.setattr(
        search, "accessible_tracks_for_scope", AsyncMock(return_value=tracks)
    )

    async def locations(kind, ids, **kwargs):
        assert kwargs["workspace_hints"] == ["ws"]
        if kind == "app":
            return {"a": SimpleNamespace(workspace_id="ws")}
        return {
            "t1": SimpleNamespace(workspace_id="ws", app_id="a"),
            "t2": SimpleNamespace(workspace_id="ws", app_id="a"),
            "t3": SimpleNamespace(workspace_id="ws", app_id="hidden"),
            "t4": SimpleNamespace(workspace_id="elsewhere", app_id="a"),
        }

    async def policy(**kwargs):
        return SimpleNamespace(allowed=kwargs["resource"].id != "t2")

    monkeypatch.setattr(search, "locate_resources", locations)
    monkeypatch.setattr(search, "evaluate", policy)
    monkeypatch.setattr(
        search,
        "list_workspace_tools",
        AsyncMock(return_value={"tools": [], "has_more": False}),
    )
    monkeypatch.setattr(
        "app.agentive.harness.capability_search._embed_text_for_capability_search",
        AsyncMock(side_effect=RuntimeError("offline")),
    )
    result = await search.discover_workspace_resources(
        user_id="u", workspace_id="ws", query="staff", ranking_mode="auto"
    )
    by_id = {row["id"]: row for row in result["results"]}
    assert set(by_id) == {"a", "t3"}
    assert [track["id"] for track in by_id["a"]["tracks"]] == ["t1"]
    assert "app_id" not in by_id["t3"] and "app_name" not in by_id["t3"]
    assert result["mode"] == "lexical" and result["degraded"]
    first = await search.discover_workspace_resources(
        user_id="u", workspace_id="ws", query="staff", limit=1, ranking_mode="lexical"
    )
    second = await search.discover_workspace_resources(
        user_id="u",
        workspace_id="ws",
        query="staff",
        limit=1,
        offset=first["next_offset"],
        ranking_mode=first["mode"],
    )
    assert first["results"][0]["id"] != second["results"][0]["id"]
    assert first["has_more"] and first["next_offset"] == 1
    with pytest.raises(BadRequestError):
        await search.discover_workspace_resources(
            user_id="u", workspace_id="ws", query="staff", offset=1, ranking_mode="auto"
        )


@pytest.mark.asyncio
async def test_hybrid_finds_paraphrase_and_bounds_catalogue(monkeypatch):
    """Semantic matches supplement lexical results with whole bounded schemas."""
    apps = [
        SimpleNamespace(
            id="a", name="People", description="personnel", lifecycle_state="active"
        )
    ]
    monkeypatch.setattr(search, "callable_apps_for_scope", AsyncMock(return_value=apps))
    monkeypatch.setattr(
        search, "accessible_tracks_for_scope", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        search,
        "locate_resources",
        AsyncMock(
            side_effect=lambda kind, ids, **kwargs: (
                {"a": SimpleNamespace(workspace_id="ws")} if kind == "app" else {}
            )
        ),
    )
    monkeypatch.setattr(
        search,
        "list_workspace_tools",
        AsyncMock(
            return_value={
                "tools": [
                    {"key": "small", "parameters_schema": {}},
                    {"key": "large", "parameters_schema": {"x": "a" * 5000}},
                ],
                "has_more": False,
            }
        ),
    )
    monkeypatch.setattr(
        "app.agentive.harness.capability_search._embed_text_for_capability_search",
        AsyncMock(return_value=[1.0, 0.0]),
    )
    monkeypatch.setattr(
        "app.agentive.harness.capability_search._definition_vectors",
        AsyncMock(return_value=[[1.0, 0.0]]),
    )
    result = await search.discover_workspace_resources(
        user_id="u", workspace_id="ws", query="employees", ranking_mode="hybrid"
    )
    assert result["mode"] == "hybrid" and result["results"][0]["id"] == "a"
    assert result["results"][0]["read_tools"] == [
        {"key": "small", "parameters_schema": {}}
    ]
    assert result["results"][0]["tools_incomplete"]


@pytest.mark.asyncio
async def test_requires_explicit_workspace_and_bounded_input():
    """An unbound or unreasonable lookup does not fall back to union scope."""
    with pytest.raises(BadRequestError):
        await search.discover_workspace_resources(user_id="u", query="staff")
    with pytest.raises(BadRequestError):
        await search.discover_workspace_resources(
            user_id="u", workspace_id="ws", query="staff", limit=11
        )


@pytest.mark.asyncio
async def test_app_with_many_tracks_does_not_crowd_out_other_apps(monkeypatch):
    """Ranked cross-App candidates remain visible on a small first page."""
    apps = [
        SimpleNamespace(id=key, name="staff", description="", lifecycle_state="active")
        for key in ("a", "b", "c")
    ]
    tracks = [SimpleNamespace(id=f"t{n}", title="staff", purpose="") for n in range(5)]
    monkeypatch.setattr(search, "callable_apps_for_scope", AsyncMock(return_value=apps))
    monkeypatch.setattr(
        search, "accessible_tracks_for_scope", AsyncMock(return_value=tracks)
    )

    async def locations(kind, ids, **kwargs):
        return {id: SimpleNamespace(workspace_id="ws", app_id="a") for id in ids}

    monkeypatch.setattr(search, "locate_resources", locations)
    monkeypatch.setattr(
        search, "evaluate", AsyncMock(return_value=SimpleNamespace(allowed=True))
    )
    monkeypatch.setattr(
        search,
        "list_workspace_tools",
        AsyncMock(return_value={"tools": [], "has_more": False}),
    )
    result = await search.discover_workspace_resources(
        user_id="u", workspace_id="ws", query="staff", limit=2, ranking_mode="lexical"
    )
    assert {"a", "b"}.issubset({row["id"] for row in result["results"]})
    assert result["has_more"]


@pytest.mark.asyncio
async def test_default_manifest_is_live_and_does_not_require_embeddings(monkeypatch):
    """Unmatched terms still expose current metadata for the model to interpret."""
    app = SimpleNamespace(
        id="a", name="People", description="Staff directory", lifecycle_state="active"
    )
    monkeypatch.setattr(
        search, "callable_apps_for_scope", AsyncMock(return_value=[app])
    )
    monkeypatch.setattr(
        search, "accessible_tracks_for_scope", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        search,
        "locate_resources",
        AsyncMock(return_value={"a": SimpleNamespace(workspace_id="ws")}),
    )
    monkeypatch.setattr(
        search,
        "list_workspace_tools",
        AsyncMock(return_value={"tools": [], "has_more": False}),
    )
    embed = AsyncMock(side_effect=AssertionError("default lookup must not embed"))
    monkeypatch.setattr(
        "app.agentive.harness.capability_search._embed_text_for_capability_search",
        embed,
    )

    first = await search.discover_workspace_resources(
        user_id="u", workspace_id="ws", query="colleagues"
    )
    assert first["mode"] == "lexical" and not first["degraded"]
    assert first["results"][0]["name"] == "People"
    app.name = "Team"
    second = await search.discover_workspace_resources(
        user_id="u", workspace_id="ws", query="colleagues"
    )
    assert second["results"][0]["name"] == "Team"
    embed.assert_not_awaited()


@pytest.mark.asyncio
async def test_track_policy_checks_are_bounded_and_denials_remain_hidden(monkeypatch):
    """Parallel policy evaluation remains bounded and never discloses denied Tracks."""
    tracks = [
        SimpleNamespace(id=f"t{i}", title=f"Track {i}", purpose="") for i in range(20)
    ]
    monkeypatch.setattr(search, "callable_apps_for_scope", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        search, "accessible_tracks_for_scope", AsyncMock(return_value=tracks)
    )
    monkeypatch.setattr(
        search,
        "locate_resources",
        AsyncMock(
            side_effect=lambda kind, ids, **kwargs: {
                id: SimpleNamespace(workspace_id="ws", app_id=None) for id in ids
            }
        ),
    )
    active = 0
    peak = 0

    async def policy(**kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.001)
        active -= 1
        return SimpleNamespace(allowed=kwargs["resource"].id != "t0")

    monkeypatch.setattr(search, "evaluate", policy)
    found = []
    offset = 0
    while offset is not None:
        result = await search.discover_workspace_resources(
            user_id="u", workspace_id="ws", query="track", limit=10, offset=offset
        )
        found.extend(row["id"] for row in result["results"])
        offset = result["next_offset"]
    assert len(found) == 19 and "t0" not in found
    assert 1 < peak <= 8


@pytest.mark.asyncio
async def test_manifest_pages_fit_harness_preview_and_cursor_advances(monkeypatch):
    """Large Unicode descriptors never hide later Apps behind a lost cursor."""
    apps = [
        SimpleNamespace(
            id=str(i),
            name="App " + str(i),
            description="人" * 512,
            lifecycle_state="active",
        )
        for i in range(5)
    ]
    monkeypatch.setattr(search, "callable_apps_for_scope", AsyncMock(return_value=apps))
    monkeypatch.setattr(
        search, "accessible_tracks_for_scope", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        search,
        "locate_resources",
        AsyncMock(
            side_effect=lambda kind, ids, **kwargs: {
                id: SimpleNamespace(workspace_id="ws") for id in ids
            }
        ),
    )
    monkeypatch.setattr(
        search,
        "list_workspace_tools",
        AsyncMock(return_value={"tools": [], "has_more": False}),
    )
    seen = []
    offset = 0
    while offset is not None:
        result = await search.discover_workspace_resources(
            user_id="u", workspace_id="ws", query="app", offset=offset
        )
        assert result["results"]
        assert len(json.dumps(result["results"]).encode("utf-8")) <= 7000
        seen.extend(row["id"] for row in result["results"])
        offset = result["next_offset"]
        assert offset is None or offset == len(seen)
    assert len(seen) == len(set(seen)) == 5


def test_preview_reserves_general_read_and_never_slices_input_schemas():
    from app.services.workspace_resource_search import _preview_read_contracts

    reports = [
        {
            "capability_key": f"report-{number}",
            "input_schema": {"type": "object", "properties": {}},
            "output_schema": {},
        }
        for number in range(5)
    ]
    schema = {
        "type": "object",
        "properties": {
            key: {"type": "string"}
            for key in ["track", "filters", "count_only", "group_by"]
        },
    }
    general = {
        "capability_key": "general",
        "input_schema": schema,
        "output_schema": {"description": "x" * 6000},
    }
    selected = _preview_read_contracts(reports + [general])
    assert [item["capability_key"] for item in selected] == [
        "report-0",
        "report-1",
        "general",
    ]
    assert selected[-1]["input_schema"] == schema
    assert selected[-1]["input_schema_complete"]
    assert selected[-1]["output_schema_omitted"]
    assert "output_schema" in general


async def test_directory_does_not_crowd_out_complete_read_contracts(monkeypatch):
    """A wide App keeps report and general query contracts before extra names."""
    import json

    from app.services import agent_capabilities
    from app.services.walkers import resource_schema_preview

    app = SimpleNamespace(
        id="app",
        name="Workspace overview",
        description="Overview",
        lifecycle_state="active",
    )
    monkeypatch.setattr(
        search, "callable_apps_for_scope", AsyncMock(return_value=[app])
    )
    monkeypatch.setattr(
        search, "accessible_tracks_for_scope", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        search,
        "locate_resources",
        AsyncMock(return_value={"app": SimpleNamespace(workspace_id="ws")}),
    )
    monkeypatch.setattr(
        search,
        "list_workspace_tools",
        AsyncMock(return_value={"tools": [], "has_more": False}),
    )
    monkeypatch.setattr(
        resource_schema_preview,
        "app_schema_preview",
        AsyncMock(
            return_value={
                "tracks": [],
                "incomplete": False,
                "directory": [
                    {
                        "id": f"n.Track.{i:032x}",
                        "title": f"Collection {i} working records",
                        "purpose": "Tracks current available capacity, distinct from requests awaiting review.",
                        "purpose_incomplete": False,
                    }
                    for i in range(16)
                ],
                "directory_incomplete": False,
            }
        ),
    )
    report_schema = {"type": "object", "properties": {}}
    general_schema = {
        "type": "object",
        "properties": {
            "track": {"type": "string"},
            "filters": {"type": "object"},
            "count_only": {"type": "boolean"},
            "group_by": {"type": "string"},
        },
        "description": "Contract documentation. " * 35,
    }
    contracts = [
        {
            "capability_key": f"overview-report-{i}",
            "input_schema": report_schema,
            "output_schema": {},
        }
        for i in range(2)
    ] + [
        {
            "capability_key": "general",
            "input_schema": general_schema,
            "output_schema": {},
        }
    ]
    for contract in contracts:
        contract["name"] = contract["capability_key"]
        contract["description"] = "Workspace overview read"
        contract["arguments"] = {
            "mode": "declared_capability",
            "app_id": "app",
            "catalogue_generation": "g" * 64,
            "capability_key": contract["capability_key"],
        }
    monkeypatch.setattr(
        agent_capabilities,
        "app_read_contracts",
        AsyncMock(return_value={"app": contracts}),
    )
    result = await search.discover_workspace_resources(
        user_id="u", workspace_id="ws", query="workspace overview"
    )
    item = result["results"][0]
    assert len(json.dumps(item).encode()) <= 6144
    assert {row["capability_key"] for row in item["read_queries"]} == {
        "overview-report-0",
        "overview-report-1",
        "general",
    }
    assert all(row["input_schema_complete"] for row in item["read_queries"])
    assert (
        next(row for row in item["read_queries"] if row["capability_key"] == "general")[
            "input_schema"
        ]
        == general_schema
    )
    assert not item["schema_preview"]["directory_incomplete"]
    assert len(item["schema_preview"]["directory"]) == 16
    assert all(
        "current available capacity" in row.get("purpose", "")
        for row in item["schema_preview"]["directory"]
    )
    assert item["query_invoke_args"]["app_id"] == "app"


def test_shared_bindings_preserve_scopes_schemas_and_original_contracts():
    import copy

    from app.services.workspace_resource_search import _shared_read_bindings

    originals = [
        {
            "capability_key": key,
            "input_schema": {
                "type": "object",
                "properties": {"limit": {"maximum": 50}},
            },
            "arguments": {
                "mode": "declared_capability",
                "app_id": "app",
                "catalogue_generation": "generation",
                "capability_key": key,
            },
        }
        for key in ["first", "second"]
    ]
    before = copy.deepcopy(originals)
    common, compact = _shared_read_bindings(originals)
    for original, contract in zip(originals, compact):
        rebuilt = {
            **common,
            **contract.get("arguments", {}),
            "capability_key": contract["capability_key"],
        }
        assert rebuilt == original["arguments"]
        assert contract["input_schema"] == original["input_schema"]
    assert originals == before
    originals[1]["arguments"]["app_id"] = "other-app"
    common, compact = _shared_read_bindings(originals)
    assert "app_id" not in common
    assert compact[0]["arguments"]["app_id"] == "app"
    assert compact[1]["arguments"]["app_id"] == "other-app"


@pytest.mark.asyncio
async def test_shared_bindings_accept_live_contract_bridge_shape(monkeypatch):
    from app.services import agent_capabilities, capability_catalogue
    from app.services.app_queries import registry

    schema = {
        "type": "object",
        "properties": {"limit": {"type": "integer", "maximum": 50}},
    }
    capabilities = [
        SimpleNamespace(
            app_id="app",
            kind="query",
            effects="read",
            availability="active",
            discoverable=True,
            capability_id=key,
            name=key,
            description="Read current records",
            input_schema=schema,
            output_schema={},
        )
        for key in ["package.first", "package.second"]
    ]
    monkeypatch.setattr(
        registry, "all_workspace_query_apps", lambda workspace_id: ["app"]
    )
    monkeypatch.setattr(
        capability_catalogue,
        "get_or_compile_catalogue",
        AsyncMock(
            return_value=SimpleNamespace(
                capabilities=capabilities, generation_id="generation"
            )
        ),
    )
    contracts = (
        await agent_capabilities.app_read_contracts(workspace_id="ws", app_ids=["app"])
    )["app"]
    common, compact = search._shared_read_bindings(contracts)
    assert common == {
        "mode": "declared_capability",
        "app_id": "app",
        "catalogue_generation": "generation",
    }
    for original, projected in zip(contracts, compact):
        assert {
            **common,
            **projected.get("arguments", {}),
            "capability_key": projected["capability_key"],
        } == original["arguments"]
        assert projected["input_schema"] == schema
        assert "arguments" not in projected
        assert original["arguments"]["app_id"] == "app"
