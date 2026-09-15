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

Deliberately narrow: only the structural edge triggers a rollback. Once the
Entry is rooted, a later failure (author wire, tag wire, relation sync, a bundle
save-hook) leaves valid content in place, and deleting the user's entry because
a hook raised would be worse than the partial write.
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
    """Scope check: a save-hook raising must not destroy the user's content.

    By the time hooks run the Entry is properly rooted, so the rollback is
    deliberately not extended this far.
    """
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
    assert survivors, (
        "a failing save hook deleted an Entry that was already correctly "
        "wired into the graph; the rollback is scoped to the structural edge"
    )
