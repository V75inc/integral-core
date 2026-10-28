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
        "app.agentive.harness.capability_search._definition_vector",
        AsyncMock(return_value=[1.0, 0.0]),
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
