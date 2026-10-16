"""Bundle reads share bounded containment discovery, never a reusable allow."""

import pytest

from app.models.edges import COLLABORATES_ON, CONTAINS, EXCLUDED_FROM, IS_MEMBER_OF
from app.models.nodes import Entry, User
from app.services.app_graph import catalog_user
from app.services.app_operations.context import OperationContext
from tests.test_resource_location import graph, sibling

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]


async def reader_context():
    _, workspace, app, track, entry = await graph()
    reader = await User.create(display_name="Bulk read QA")
    await catalog_user(reader)
    membership = await reader.connect(workspace, edge=IS_MEMBER_OF, role="member")
    await reader.connect(app, edge=COLLABORATES_ON, role="viewer")
    return (
        reader,
        membership,
        workspace,
        app,
        track,
        entry,
        OperationContext(
            user_id=reader.id,
            workspace_id=workspace.id,
            scope=f"app:{app.id}",
            app_id=app.id,
        ),
    )


async def test_bulk_facade_keeps_exclusion_direct_grant_and_current_membership():
    reader, membership, _, _, track, entry, context = await reader_context()
    await reader.connect(entry, edge=EXCLUDED_FROM)
    assert await context.find_entries({}) == []
    await reader.connect(entry, edge=COLLABORATES_ON, role="viewer")
    assert [row.id for row in await context.find_entries({})] == [entry.id]
    assert [
        row.id for row in await context.find_entries_in_track_type(track.title)
    ] == [entry.id]
    await membership.delete()
    assert await context.find_entries({}) == []


async def test_bulk_facade_does_not_borrow_workspace_owner_governance():
    owner, workspace, app, _, entry = await graph()
    await sibling(owner, workspace)
    context = OperationContext(
        user_id=owner.id, workspace_id=workspace.id, scope="tool"
    )
    from app.models.edges import OWNS

    for edge in await OWNS.find({"source": owner.id}):
        if edge.target != app.id:
            await edge.delete()
    assert [row.id for row in await context.find_entries({})] == [entry.id]


async def test_bulk_facade_requires_active_complete_app_scope():
    _, _, _, app, track, entry, context = await reader_context()
    app.lifecycle_state = "paused"
    await app.save()
    assert await context.find_entries({}) == []
    app.lifecycle_state = "active"
    await app.save()
    assert [row.id for row in await context.find_entries({})] == [entry.id]
    for edge in await CONTAINS.find({"source": track.id, "target": entry.id}):
        await edge.delete()
    assert await context.find_entries({}) == []


async def test_bulk_facade_resolves_parent_role_once_per_window(monkeypatch):
    _, _, _, _, track, entry, context = await reader_context()
    ids = {entry.id}
    for i in range(30):
        extra = await Entry.create(title=f"Bulk {i}", track_id=track.id)
        await track.connect(extra, edge=CONTAINS)
        ids.add(extra.id)
    from app.services import permissions

    original = permissions.resolve_role
    calls = []
    database = (await track.get_context()).database
    find = database.find
    reads = []

    async def counted(user_id, kind, node_id):
        calls.append((kind, node_id))
        return await original(user_id, kind, node_id)

    async def counted_find(*args, **kwargs):
        reads.append(args[0])
        return await find(*args, **kwargs)

    monkeypatch.setattr(permissions, "resolve_role", counted)
    monkeypatch.setattr(database, "find", counted_find)
    assert {row.id for row in await context.find_entries({})} == ids
    assert calls.count(("track", track.id)) == 1
    assert not any(kind == "entry" for kind, _ in calls)
    assert len(reads) < 500


async def test_bulk_facade_rechecks_membership_between_windows(monkeypatch):
    _, membership, _, _, track, entry, context = await reader_context()
    for i in range(4):
        extra = await Entry.create(title=f"Window {i}", track_id=track.id)
        await track.connect(extra, edge=CONTAINS)
    from app.services import entry_access_projection as projection

    monkeypatch.setattr(projection, "WINDOW", 2)
    original = projection.hydrate_records
    windows = []

    async def revoke_after_first_window(records):
        windows.append(records)
        result = await original(records)
        if len(windows) == 1:
            await membership.delete()
        return result

    monkeypatch.setattr(projection, "hydrate_records", revoke_after_first_window)
    result = await context.find_entries({})
    assert len(result) == 2
    assert len(windows) == 1
    assert await context.find_entries({}) == []


async def test_bulk_facade_bypasses_inherited_process_permission_cache(monkeypatch):
    _, _, _, _, _, entry, context = await reader_context()
    from app.middleware.permissions_cache import live_permissions_required
    from app.services import entry_access_projection as projection
    from app.services import permissions_process_cache as process_cache
    from app.services.workspace_governance import owner_governance_allowed

    monkeypatch.setattr(process_cache, "_ENABLED", True)
    original = projection.admit_records
    calls = []

    async def admitted(*args, **kwargs):
        assert live_permissions_required()
        assert not process_cache.enabled()
        assert not owner_governance_allowed()
        calls.append(True)
        return await original(*args, **kwargs)

    monkeypatch.setattr(projection, "admit_records", admitted)
    assert [row.id for row in await context.find_entries({})] == [entry.id]
    assert calls
