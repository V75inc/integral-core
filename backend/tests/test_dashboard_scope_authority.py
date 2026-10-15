"""Dashboard scope comes from unique live named parents, never scalar caches."""

import pytest
from jvspatial.api.auth.models import User as AuthUser
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.models.edges import CATALOGS, CONTAINS
from app.models.nodes import Dashboards
from app.services.app_graph import get_or_create_dashboards_registry
from app.services.dashboard_service import (
    create_dashboard,
    get_dashboard,
    list_dashboards,
)
from app.services.resource_location import locate_resource
from tests.test_resource_location import graph

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]


async def dashboard_graph():
    """Create an ordinarily owned App and a rooted dashboard."""
    owner, workspace, app, _, _ = await graph()
    identity = await AuthUser.create(
        email=f"dashboard-scope-{owner.id}@example.com", password_hash="fixture"
    )
    owner.user_id = identity.id
    await owner.save()
    dashboard = await create_dashboard(
        user_id=owner.id, app_id=app.id, name="Private board"
    )
    return owner, workspace, app, dashboard


async def test_dashboard_has_canonical_app_workspace_location():
    """A valid dashboard remains readable through its unique App parent."""
    owner, workspace, app, dashboard = await dashboard_graph()
    location = await locate_resource("dashboard", dashboard.id)
    assert location is not None
    assert (location.app_id, location.workspace_id) == (app.id, workspace.id)
    assert (
        await get_dashboard(user_id=owner.id, app_id=app.id, dashboard_id=dashboard.id)
    ).id == dashboard.id


async def test_forged_dashboard_app_cache_cannot_expose_foreign_board():
    """A caller's App permission cannot authorize another App's dashboard."""
    owner, workspace, app, _ = await dashboard_graph()
    _, _, _, foreign = await dashboard_graph()
    context = await foreign.get_context()
    await context.database.find_one_and_update(
        "node",
        {"id": foreign.id},
        {"$set": {"context.app_id": app.id, "context.workspace_id": workspace.id}},
    )
    async with scoped_default_context_async(GraphContext(database=context.database)):
        assert (
            await get_dashboard(
                user_id=owner.id, app_id=app.id, dashboard_id=foreign.id
            )
            is None
        )


@pytest.mark.parametrize(
    "fault", ["missing_catalog", "second_registry", "registry_parent"]
)
async def test_invalid_dashboard_parent_set_denies_read(fault):
    """Missing, duplicated and cross-App registry paths must fail closed."""
    owner, workspace, app, dashboard = await dashboard_graph()
    registry = await get_or_create_dashboards_registry(app)
    context = await dashboard.get_context()
    if fault == "missing_catalog":
        for edge in await context.find_edges_between(
            registry.id, dashboard.id, edge_class=CATALOGS
        ):
            await edge.delete()
    elif fault == "second_registry":
        other = await Dashboards.create(app_id=app.id, workspace_id=workspace.id)
        await app.connect(other, edge=CONTAINS)
        await other.connect(dashboard, edge=CATALOGS)
    else:
        _, _, foreign_app, _ = await dashboard_graph()
        await foreign_app.connect(registry, edge=CONTAINS)
    async with scoped_default_context_async(GraphContext(database=context.database)):
        assert (
            await get_dashboard(
                user_id=owner.id, app_id=app.id, dashboard_id=dashboard.id
            )
            is None
        )


async def test_dashboard_list_does_not_admit_foreign_catalog_link():
    """An extra catalog edge must not make foreign content part of an App."""
    owner, _, app, dashboard = await dashboard_graph()
    _, _, _, foreign = await dashboard_graph()
    registry = await get_or_create_dashboards_registry(app)
    await registry.connect(foreign, edge=CATALOGS)
    boards = await list_dashboards(user_id=owner.id, app_id=app.id)
    assert [board.id for board in boards] == [dashboard.id]


@pytest.mark.parametrize("empty_cache", [False, True])
async def test_optional_workspace_cache_never_replaces_named_parent(empty_cache):
    """Legacy empty caches derive scope from the App; conflicting ones deny."""
    owner, workspace, app, dashboard = await dashboard_graph()
    context = await dashboard.get_context()
    await context.database.find_one_and_update(
        "node",
        {"id": dashboard.id},
        {"$set": {"context.workspace_id": "" if empty_cache else "wrong-workspace"}},
    )
    current = await get_dashboard(
        user_id=owner.id, app_id=app.id, dashboard_id=dashboard.id
    )
    if empty_cache:
        assert current is not None
        location = await locate_resource("dashboard", dashboard.id)
        assert location.workspace_id == workspace.id
    else:
        assert current is None


async def test_dashboard_list_does_not_repair_or_create_graph_nodes_on_read():
    """Listing is a projection, including when a registry is missing."""
    owner, _, app, dashboard = await dashboard_graph()
    registry = await get_or_create_dashboards_registry(app)
    context = await dashboard.get_context()
    for edge in await context.find_edges_between(
        app.id, registry.id, edge_class=CONTAINS
    ):
        await edge.delete()
    before_nodes = await context.database.find("node", {}, sort=[("id", 1)])
    before_edges = await context.database.find("edge", {}, sort=[("id", 1)])
    assert await list_dashboards(user_id=owner.id, app_id=app.id) == []
    assert await context.database.find("node", {}, sort=[("id", 1)]) == before_nodes
    assert await context.database.find("edge", {}, sort=[("id", 1)]) == before_edges


@pytest.mark.unit
async def test_dashboard_candidate_windows_continue_after_withheld_last_row(
    monkeypatch,
):
    """A full candidate window is bounded, even when its last result is denied."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.services import dashboard_scope

    rows = [{"id": f"dashboard-{index:03}"} for index in range(129)]
    find = AsyncMock(side_effect=[rows[:128], rows[128:]])
    monkeypatch.setattr(
        dashboard_scope,
        "get_default_context",
        lambda: SimpleNamespace(database=SimpleNamespace(find=find)),
    )

    async def project(app_id, dashboard_id):
        assert app_id == "app-a"
        return (
            None
            if dashboard_id == "dashboard-127"
            else SimpleNamespace(id=dashboard_id)
        )

    monkeypatch.setattr(dashboard_scope, "read_scoped_dashboard", project)
    result = await dashboard_scope.list_scoped_app_dashboards("app-a")
    assert len(result) == 128
    assert result[-1].id == "dashboard-128"
    assert find.await_count == 2
    assert find.await_args_list[0].kwargs == {"limit": 128, "sort": [("id", 1)]}
    assert find.await_args_list[1].args[1]["id"] == {"$gt": "dashboard-127"}


@pytest.mark.unit
async def test_dashboard_list_read_outage_is_not_an_empty_list(monkeypatch):
    """Missing authorization/storage facts must remain unavailable."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.exceptions import AuthorizationUnavailableError
    from app.services import dashboard_scope

    monkeypatch.setattr(
        dashboard_scope,
        "get_default_context",
        lambda: SimpleNamespace(
            database=SimpleNamespace(
                find=AsyncMock(side_effect=RuntimeError("storage unavailable"))
            )
        ),
    )
    with pytest.raises(AuthorizationUnavailableError):
        await dashboard_scope.list_scoped_app_dashboards("app-a")
