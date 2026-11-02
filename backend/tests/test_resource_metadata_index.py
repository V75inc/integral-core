"""Incremental discovery selects metadata, but discloses only live graph facts."""

from unittest.mock import AsyncMock

import pytest
from jvspatial.core.context import get_default_context

from app.models.edges import COLLABORATES_ON, IS_MEMBER_OF
from app.models.nodes import User
from app.services import resource_metadata_index as index
from app.services import workspace_resource_search as search
from app.services.app_graph import catalog_user
from tests.test_resource_location import graph

pytestmark = pytest.mark.asyncio


async def ready(workspace_id):
    for _ in range(100):
        state = await index.reconcile_metadata_page(workspace_id)
        if state["ready"]:
            return
    pytest.fail("Index bootstrap did not complete")


async def test_bootstrap_update_and_restart_use_current_records(monkeypatch):
    user, workspace, app, track, _ = await graph()
    app.name = "Customer renewals"
    app.description = "Agreements and contract expiry planning"
    await app.save()
    track.title = "Upcoming renewals"
    await track.save()
    assert not (await index.request_metadata_index(workspace.id))["ready"]
    await ready(workspace.id)
    for name in ("callable_apps_for_scope", "accessible_tracks_for_scope"):
        monkeypatch.setattr(
            search, name, AsyncMock(side_effect=AssertionError("Full inventory scan"))
        )
    result = await search.discover_workspace_resources(
        user_id=user.id,
        workspace_id=workspace.id,
        query="Where do I manage customer renewals?",
    )
    assert [row["id"] for row in result["results"]] == [app.id]
    assert result["results"][0]["name"] == "Customer renewals"
    assert result["index_coverage"]["kind"] == "eventually_consistent_metadata"
    # Graph edits made outside the event path are reloaded before disclosure.
    app.name = "Client agreements"
    await app.save()
    updated = await search.discover_workspace_resources(
        user_id=user.id, workspace_id=workspace.id, query="customer renewals"
    )
    assert updated["results"][0]["name"] == "Client agreements"
    await index.refresh_resource_metadata(app.id)
    row = await get_default_context().database.get(
        "object", index.identity(index.DOCUMENT, app.id)
    )
    assert row["context"]["name"] == "Client agreements"
    # Durable state is sufficient for another process; no process-local snapshot.
    assert (await index.request_metadata_index(workspace.id))["ready"]


async def test_deleted_and_revoked_sources_do_not_survive_index(monkeypatch):
    user, workspace, app, track, _ = await graph()
    await ready(workspace.id)
    guest = await User.create(display_name="Metadata access QA")
    await catalog_user(guest)
    await guest.connect(workspace, edge=IS_MEMBER_OF, role="guest")
    result = await search.discover_workspace_resources(
        user_id=guest.id, workspace_id=workspace.id, query="Scope QA"
    )
    assert result["results"] == []
    # A Track-only share must not disclose the inaccessible parent's identity.
    await guest.connect(track, edge=COLLABORATES_ON, role="viewer")
    shared = await search.discover_workspace_resources(
        user_id=guest.id, workspace_id=workspace.id, query="Scope QA"
    )
    assert [row["id"] for row in shared["results"]] == [track.id]
    assert "app_id" not in shared["results"][0]
    # Delete only the disposable test's primary rows, leaving a stale projection.
    database = get_default_context().database
    await database.delete("node", track.id)
    deleted = await search.discover_workspace_resources(
        user_id=guest.id, workspace_id=workspace.id, query="Scope QA"
    )
    assert deleted["results"] == []
    await ready(workspace.id)
    # A complete further source+tombstone cycle repairs the retained projection.
    await index.reconcile_metadata_page(workspace.id)
    await index.reconcile_metadata_page(workspace.id)
    assert (
        await database.get("object", index.identity(index.DOCUMENT, track.id)) is None
    )


async def test_source_refresh_is_incremental_and_unchanged_sources_do_not_write(
    monkeypatch,
):
    _, workspace, app, track, _ = await graph()
    await ready(workspace.id)
    database = get_default_context().database
    original = database.find_one_and_update
    writes = []

    async def observed(collection, query, update):
        writes.append((collection, query["id"]))
        return await original(collection, query, update)

    monkeypatch.setattr(database, "find_one_and_update", observed)
    assert not await index.refresh_resource_metadata(app.id)
    assert not await index.refresh_resource_metadata(track.id)
    assert not writes
    app.description = "Renewal planning"
    await app.save()
    writes.clear()
    assert await index.refresh_resource_metadata(app.id)
    assert writes == [("object", index.identity(index.DOCUMENT, app.id))]


async def test_read_contracts_include_current_exact_query_key_and_schema(monkeypatch):
    from app.services.app_queries import registry

    user, workspace, app, _, _ = await graph()
    monkeypatch.setitem(registry._QUERIES, workspace.id, {})
    registry.register_app_queries(
        workspace.id,
        app.id,
        "qa-contract",
        [
            {
                "key": "renewals.due",
                "name": "Due renewals",
                "description": "Find contracts due for renewal",
                "input_schema": {
                    "type": "object",
                    "properties": {"days": {"type": "integer"}},
                },
                "output_schema": {
                    "type": "object",
                    "properties": {"count": {"type": "integer"}},
                },
            }
        ],
    )
    await ready(workspace.id)
    result = await search.discover_workspace_resources(
        user_id=user.id,
        workspace_id=workspace.id,
        query="Which contracts are due for renewal?",
    )
    descriptor = result["results"][0]["read_queries"][0]
    assert descriptor["capability_key"].endswith(".renewals.due")
    assert descriptor["input_schema"]["properties"]["days"]["type"] == "integer"
    assert descriptor["invoke_with"] == "integral_governed_query"
    invocation = {
        **result["results"][0]["query_invoke_args"],
        **descriptor.get("arguments", {}),
        "capability_key": descriptor["capability_key"],
    }
    assert invocation["mode"] == "declared_capability"
    assert invocation["app_id"] == app.id
    assert invocation["catalogue_generation"]


async def test_delayed_refresh_cannot_overwrite_newer_projection(monkeypatch):
    import asyncio

    _, workspace, app, _, _ = await graph()
    await ready(workspace.id)
    database = get_default_context().database
    app.description = "First revision"
    await app.save()
    original = database.find_one_and_update
    delayed = asyncio.Event()
    proceed = asyncio.Event()

    async def observed(collection, query, update, **kwargs):
        if (
            update.get("$set", {}).get("context", {}).get("description")
            == "First revision"
        ):
            delayed.set()
            await proceed.wait()
        return await original(collection, query, update, **kwargs)

    monkeypatch.setattr(database, "find_one_and_update", observed)
    first = asyncio.create_task(index.refresh_resource_metadata(app.id))
    await asyncio.wait_for(delayed.wait(), timeout=5)
    app.description = "Second revision"
    await app.save()
    assert await index.refresh_resource_metadata(app.id)
    proceed.set()
    assert not await first
    rows = await database.find(
        "object", {"id": index.identity(index.DOCUMENT, app.id)}, limit=1
    )
    assert rows[0]["context"]["description"] == "Second revision"


async def test_malformed_scope_cannot_be_promoted_to_a_completed_index():
    from app.exceptions import AuthorizationUnavailableError

    _, workspace, app, _, _ = await graph()
    database = get_default_context().database
    await database.find_one_and_update(
        "node", {"id": app.id}, {"$set": {"context.workspace_id": ""}}
    )
    await index.reconcile_metadata_page(workspace.id)
    with pytest.raises(AuthorizationUnavailableError):
        await index.reconcile_metadata_page(workspace.id)
    assert not (await index.request_metadata_index(workspace.id))["ready"]


async def test_live_lifecycle_and_foreign_scope_override_retained_metadata():
    user, workspace, app, _, _ = await graph()
    await ready(workspace.id)
    database = get_default_context().database
    await database.find_one_and_update(
        "node", {"id": app.id}, {"$set": {"context.lifecycle_state": "uninstalled"}}
    )
    result = await search.discover_workspace_resources(
        user_id=user.id,
        workspace_id=workspace.id,
        query="Scope QA",
        ranking_mode="lexical",
    )
    assert not result["results"]
    foreign = await index.indexed_inventory(
        user_id=user.id,
        workspace_id="n.Workspace.not-mine",
        query="Scope QA",
        offset=0,
        limit=5,
        ranking_mode="lexical",
    )
    assert foreign is None


async def test_delayed_progress_cannot_rewind_newer_worker_cursor(monkeypatch):
    import asyncio

    _, workspace, _, _, _ = await graph()
    database = get_default_context().database
    first_ready, release = asyncio.Event(), asyncio.Event()
    original = database.find_one_and_update
    paused = False

    async def delayed(collection, query, update):
        nonlocal paused
        if query.get("entity") == index.STATE and not paused:
            paused = True
            first_ready.set()
            await release.wait()
        return await original(collection, query, update)

    monkeypatch.setattr(database, "find_one_and_update", delayed)
    old = asyncio.create_task(index.reconcile_metadata_page(workspace.id))
    await first_ready.wait()
    await index.reconcile_metadata_page(workspace.id)
    newer = await index.reconcile_metadata_page(workspace.id)
    release.set()
    assert (await old)["revision"] == newer["revision"]
    assert (await index.request_metadata_index(workspace.id))["phase"] == newer["phase"]


async def test_one_failed_workspace_does_not_stop_the_shared_repair_worker(monkeypatch):
    await index.request_metadata_index("failed-tenant")
    await index.request_metadata_index("healthy-tenant")
    calls = []

    async def repair(workspace_id):
        calls.append(workspace_id)
        if workspace_id == "failed-tenant":
            raise RuntimeError("injected storage fault")

    monkeypatch.setattr(index, "reconcile_metadata_page", repair)
    await index.reconcile_requested_metadata()
    assert {"failed-tenant", "healthy-tenant"}.issubset(calls)


@pytest.mark.parametrize("mode", ["lexical", "hybrid"])
async def test_discovery_preserves_indexed_track_purpose_ranking(monkeypatch, mode):
    """Formatting must not replace the index's admitted source ordering."""
    from app.models.edges import OWNS
    from app.models.nodes import App
    from app.services.app_graph import catalog_app

    user, workspace, selected, track, _ = await graph()
    selected.name = "Portfolio"
    selected.description = ""
    await selected.save()
    track.title = "Catalog"
    track.purpose = "Approved funding allocations for initiatives"
    await track.save()
    distractor = await App.create(name="Funding journal", workspace_id=workspace.id)
    await catalog_app(distractor)
    await user.connect(distractor, edge=OWNS)
    await ready(workspace.id)
    indexed = await index.indexed_inventory(
        user_id=user.id,
        workspace_id=workspace.id,
        query="funding allocations",
        offset=0,
        limit=6,
        ranking_mode="lexical",
    )
    assert indexed["resources"][0].id == selected.id
    # Hybrid ranks may differ from a second lexical pass over App titles.
    # The formatter consumes the already-admitted order in either mode.
    indexed["mode"] = mode
    monkeypatch.setattr(index, "indexed_inventory", AsyncMock(return_value=indexed))
    result = await search.discover_workspace_resources(
        user_id=user.id,
        workspace_id=workspace.id,
        query="funding allocations",
        ranking_mode=mode,
    )
    assert result["mode"] == mode
    assert result["results"][0]["id"] == selected.id
