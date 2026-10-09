"""A half-created Entry must not be left hanging off nothing (I-GRAPH-01).

``create_entry`` does six-plus sequential graph writes with no cross-entity
transaction. The first two are ``Entry.create`` and the ``CONTAINS`` wire that
makes the Entry reachable from Root. If the wire failed, the row still existed
but was attached to nothing -- invisible to walkers, to cascade-delete, and to
graph backup/restore, and unreachable through the very API that had just
half-created it.

``docs/INVARIANTS.md`` § I-GRAPH-01 admits no exceptions, and
``services/share_links.py::_wire_share_link_to_resource`` already had the right
shape ("wire HAS_SHARE_LINK **or roll back** the ShareLink node"). These tests
hold both entry-create paths to it.

The JSON fallback only removes an orphan when the structural edge fails.
PostgreSQL rolls back the whole failed creation command, including hooks and
its event fact, so none of that failed command becomes visible.
"""

import pytest

# Part of the per-PR smoke gate (see pyproject [tool.pytest.ini_options] markers).
# CI bills only this subset; the full suite runs locally via `make verify` and nightly.
pytestmark = pytest.mark.smoke


async def _bootstrap_track(email: str):
    """AuthUser + User + personal workspace + one track. Returns (auth_id, ws, track)."""
    from jvspatial.api.auth.models import UserCreate

    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.auth import _get_auth_service
    from app.api.tracks import create_track
    from app.models.nodes import User
    from app.services.app_graph import catalog_user
    from app.services.personal_workspace import ensure_personal_workspace

    auth_service = _get_auth_service()
    resp = await auth_service.register_user(
        UserCreate(email=email, password="testpassword123")
    )
    node = await User.create(user_id=resp.id, display_name="Rollback Test")
    await catalog_user(node)
    ws = await ensure_personal_workspace(node)
    created = await invoke_route_in_process(
        create_track,
        principal_id=resp.id,
        scope=ws.id,
        title="Rollback Track",
        visibility="private",
    )
    return resp.id, ws.id, created["track"]["id"]


async def _entries_titled(title: str):
    """Every Entry with this title, regardless of whether it is wired up."""
    from app.models.nodes import Entry

    found = await Entry.find({"context.title": title})
    return list(found or [])


@pytest.mark.asyncio
async def test_failed_contains_wire_leaves_no_orphan_entry(monkeypatch):
    """The core case: Entry.create succeeds, the CONTAINS wire does not."""
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.entries import create_entry
    from app.models import nodes as nodes_mod

    user_id, ws_id, track_id = await _bootstrap_track("orphan-a@example.com")
    title = "orphan-probe-entry"

    original_connect = nodes_mod.Track.connect

    async def failing_connect(self, *args, **kwargs):
        if kwargs.get("edge") is not None and "CONTAINS" in str(kwargs.get("edge")):
            raise RuntimeError("simulated CONTAINS wire failure")
        return await original_connect(self, *args, **kwargs)

    monkeypatch.setattr(nodes_mod.Track, "connect", failing_connect, raising=False)

    with pytest.raises(Exception):
        await invoke_route_in_process(
            create_entry,
            principal_id=user_id,
            scope=ws_id,
            track_id=track_id,
            title=title,
        )

    monkeypatch.undo()

    survivors = await _entries_titled(title)
    assert not survivors, (
        "a failed CONTAINS wire left an orphaned Entry with no path to Root "
        f"(I-GRAPH-01): {[e.id for e in survivors]}"
    )


@pytest.mark.asyncio
async def test_successful_create_is_reachable_from_its_track():
    """The rollback must not fire on the happy path."""
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.entries import create_entry
    from app.models.edges import CONTAINS
    from app.models.nodes import Entry, Track

    user_id, ws_id, track_id = await _bootstrap_track("orphan-b@example.com")
    created = await invoke_route_in_process(
        create_entry,
        principal_id=user_id,
        scope=ws_id,
        track_id=track_id,
        title="rooted-entry",
    )
    entry_id = created["entry"]["id"]

    entry = await Entry.get(entry_id)
    track = await Track.get(track_id)
    ctx = await entry.get_context()
    edges = await ctx.find_edges_between(
        source_id=track.id, target_id=entry.id, edge_class=CONTAINS
    )
    assert edges, "successful create left the Entry unwired from its Track"


@pytest.mark.asyncio
async def test_hook_failure_does_not_delete_already_rooted_entry(monkeypatch):
    """Hook failure preserves JSON's rooted fallback or rolls back PG's command."""
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api import entries as entries_mod
    from app.services import entry_create as entry_create_mod

    user_id, ws_id, track_id = await _bootstrap_track("orphan-c@example.com")
    title = "survives-hook-failure"

    async def boom(**kwargs):
        raise RuntimeError("simulated bundle hook failure")

    # HTTP create_entry delegates to create_entry_in_track, which owns the
    # save-hook call — patch the service binding, not the API module.
    monkeypatch.setattr(entry_create_mod, "run_entry_save_hooks", boom)

    with pytest.raises(Exception):
        await invoke_route_in_process(
            create_entry_handler := entries_mod.create_entry,
            principal_id=user_id,
            scope=ws_id,
            track_id=track_id,
            title=title,
        )
    assert create_entry_handler is entries_mod.create_entry

    monkeypatch.undo()

    survivors = await _entries_titled(title)
    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
    )

    if graph_transaction_available():
        # PostgreSQL rolls the entire failed creation command back, including
        # its rooted entry, hooks and event fact. JSON's fallback only removes
        # an orphan when the structural edge fails.
        assert not survivors, "failed transactional creation committed a partial entry"
    else:
        assert survivors, (
            "a failing save hook deleted an Entry that was already correctly "
            "wired into the graph; the rollback is scoped to the structural edge"
        )


@pytest.mark.asyncio
async def test_returned_entry_can_be_saved_after_create_and_update():
    """A completed command cannot hand a released transaction to its caller."""
    from jvspatial.core.context import get_default_context

    from app.models.nodes import Entry, Track
    from app.services.entry_create import create_entry_in_track
    from app.services.entry_update import update_entry_in_track

    user_id, ws_id, track_id = await _bootstrap_track("context-return@example.com")
    caller = get_default_context()
    entry = await create_entry_in_track(
        track=await Track.get(track_id),
        user_id=user_id,
        workspace_id=ws_id,
        title="returned-entry",
    )
    assert await entry.get_context() is caller
    entry.body = "caller saved after creation"
    await entry.save()
    updated = await update_entry_in_track(
        entry_id=entry.id,
        user_id=user_id,
        workspace_id=ws_id,
        title="updated-returned-entry",
        expected_record_revision=entry.record_revision,
    )
    assert await updated.get_context() is caller
    updated.body = "caller saved after update"
    await updated.save()
    await caller._evict_from_cache(entry.id)
    restored = await Entry.get(entry.id)
    assert restored.title == "updated-returned-entry"
    assert restored.body == "caller saved after update"


@pytest.mark.postgres
@pytest.mark.contract
@pytest.mark.asyncio
async def test_nested_entry_command_keeps_outer_transaction_and_rolls_back(monkeypatch):
    """Rebinding returns to the outer transaction, never escapes its rollback."""
    from jvspatial.core.context import get_default_context

    from app.models.nodes import Track
    from app.services.app_operations.transaction_scope import postgres_graph_transaction
    from app.services.entry_create import create_entry_in_track
    from app.services.entry_update import update_entry_in_track

    user_id, ws_id, track_id = await _bootstrap_track("context-nested@example.com")
    caller = get_default_context()
    emitted = []

    async def capture_event(**event):
        emitted.append(event)

    monkeypatch.setattr("app.services.entry_create.emit_change_event", capture_event)
    monkeypatch.setattr("app.services.entry_update.emit_change_event", capture_event)
    with pytest.raises(RuntimeError, match="rollback probe"):
        async with postgres_graph_transaction() as transaction:
            outer = get_default_context()
            entry = await create_entry_in_track(
                track=await Track.get(track_id),
                user_id=user_id,
                workspace_id=ws_id,
                title="nested-returned-entry",
            )
            assert await entry.get_context() is outer
            assert outer.database is transaction
            entry.body = "outer transaction save"
            await entry.save()
            updated = await update_entry_in_track(
                entry_id=entry.id,
                user_id=user_id,
                workspace_id=ws_id,
                title="nested-updated-returned-entry",
            )
            assert await updated.get_context() is outer
            updated.body = "outer transaction update"
            await updated.save()
            raise RuntimeError("rollback probe")
    assert get_default_context() is caller
    assert not await _entries_titled("nested-updated-returned-entry")
    assert not emitted, "entry events escaped the outer transaction before rollback"
    from app.services.app_operations.event_outbox import _COLLECTION

    assert not await caller.database.find(
        _COLLECTION, {"context.event.resource_id": entry.id}
    ), "rolled-back entry left a durable event fact"


@pytest.mark.postgres
@pytest.mark.contract
@pytest.mark.asyncio
async def test_nested_entry_events_deliver_only_after_outer_commit(monkeypatch):
    """A serialized outer transaction durably retains both event facts."""
    from app.models.nodes import Track
    from app.services.app_operations.event_outbox import (
        deliver_pending_operation_events,
    )
    from app.services.app_operations.transaction_scope import postgres_graph_transaction
    from app.services.entry_create import create_entry_in_track
    from app.services.entry_update import update_entry_in_track

    user_id, ws_id, track_id = await _bootstrap_track("context-commit@example.com")
    emitted = []

    async def capture_event(**event):
        emitted.append(event)

    monkeypatch.setattr("app.services.entry_create.emit_change_event", capture_event)
    monkeypatch.setattr("app.services.entry_update.emit_change_event", capture_event)
    monkeypatch.setattr("app.services.change_event.emit_change_event", capture_event)
    async with postgres_graph_transaction():
        entry = await create_entry_in_track(
            track=await Track.get(track_id),
            user_id=user_id,
            workspace_id=ws_id,
            title="nested-committed-entry",
        )
        await update_entry_in_track(
            entry_id=entry.id,
            user_id=user_id,
            workspace_id=ws_id,
            title="nested-committed-update",
        )
        assert not emitted, "uncommitted writes emitted visible event facts"
    assert not emitted
    assert await deliver_pending_operation_events() == 2
    assert sorted(event["action"] for event in emitted) == [
        "entry.create",
        "entry.update",
    ]
    assert all(event["resource_id"] == entry.id for event in emitted)
    assert await deliver_pending_operation_events() == 0
    assert len(emitted) == 2
