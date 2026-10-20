"""Collection writes use reviewed canonical Entry commands and current grants."""

import pytest

from app.agentive.tooling import bindings
from app.api.errors import BadRequestError, InsufficientPermissionsError
from app.services.agent_scope import current_scope_workspace_id
from app.services.bulk_entry_update import admit_update_selection, selected_entry_ids
from tests.test_resource_location import graph

pytestmark = pytest.mark.smoke


@pytest.mark.parametrize("ids", [[], "entry", [None], [""], [" e1"], ["e1"] * 501])
def test_collection_selection_refuses_invalid_or_unbounded_ids(ids):
    with pytest.raises(BadRequestError):
        selected_entry_ids(ids)


@pytest.mark.asyncio
@pytest.mark.parametrize("updates", [{}, {"handled": "yes"}, {"status": None}, []])
async def test_collection_patch_requires_real_changes(updates):
    with pytest.raises(BadRequestError):
        await bindings._stage_bulk_update_entries(
            {"entry_ids": ["e1"], "updates": updates}
        )


@pytest.mark.asyncio
async def test_collection_uses_one_review_and_canonical_revision_bound_commands(
    monkeypatch,
):
    owner, workspace, _, track, entry = await graph()
    from app.models.edges import CONTAINS
    from app.models.nodes import Entry

    second = await Entry.create(title="Second", track_id=track.id)
    await track.connect(second, edge=CONTAINS)

    async def stage(args):
        return {
            "kind": "update_entry",
            "summary": args["entry_id"],
            "diff_human": "Change status",
            "diff_machine": {},
            "payload": {
                "entry_id": args["entry_id"],
                **args["updates"],
                "expected_record_revision": 1,
            },
        }

    monkeypatch.setattr(bindings, "_stage_update_entry", stage)
    p = bindings._propose_principal.set(owner.id)
    w = current_scope_workspace_id.set(workspace.id)
    try:
        staged = await bindings._stage_bulk_update_entries(
            {
                "entry_ids": [entry.id, second.id, entry.id],
                "updates": {"status": "done", "user_id": "smuggled"},
            }
        )
    finally:
        bindings._propose_principal.reset(p)
        current_scope_workspace_id.reset(w)
    assert staged["kind"] == "batch"
    operations = staged["payload"]["operations"]
    assert len(operations) == 2
    assert all(op["kind"] == "update_entry" for op in operations)
    assert all(op["payload"]["expected_record_revision"] == 1 for op in operations)
    assert all("user_id" not in op["payload"] for op in operations)
    assert "Change status" in staged["diff_human"]
    from types import SimpleNamespace

    from app.agentive.services.staging_command_batch import supports_canonical_batch

    assert supports_canonical_batch(
        SimpleNamespace(kind="batch", payload=staged["payload"])
    )


@pytest.mark.asyncio
async def test_collection_denies_foreign_workspace_or_non_editor():
    owner, workspace, _, _, entry = await graph()
    other, foreign, _, _, foreign_entry = await graph()
    with pytest.raises(InsufficientPermissionsError):
        await admit_update_selection(
            owner.id, [entry.id, foreign_entry.id], workspace.id
        )
    with pytest.raises(InsufficientPermissionsError):
        await admit_update_selection(other.id, [entry.id], workspace.id)
    with pytest.raises(InsufficientPermissionsError):
        await admit_update_selection(owner.id, [entry.id], "")


@pytest.mark.asyncio
async def test_batched_counts_preserve_entry_exclusions():
    from app.models.edges import COLLABORATES_ON, CONTAINS, EXCLUDED_FROM, IS_MEMBER_OF
    from app.models.nodes import Entry, User
    from app.services.app_graph import catalog_user
    from app.services.permissions import (
        count_user_accessible_entries,
        count_user_accessible_entries_for_tracks,
    )

    _, workspace, app, track, entry = await graph()
    reader = await User.create(display_name="Collection reader")
    await catalog_user(reader)
    await reader.connect(workspace, edge=IS_MEMBER_OF, role="member")
    await reader.connect(app, edge=COLLABORATES_ON, role="editor")
    other = await Entry.create(title="Visible", track_id=track.id)
    await track.connect(other, edge=CONTAINS)
    await reader.connect(entry, edge=EXCLUDED_FROM)
    assert await count_user_accessible_entries(reader.id, track.id) == 1
    assert await count_user_accessible_entries_for_tracks(reader.id, [track]) == {
        track.id: 1
    }


@pytest.mark.asyncio
async def test_collection_tool_dispatch_flattens_into_an_open_review():
    from app.agentive import staging
    from app.agentive.tooling.dispatch import dispatch_tool
    from tests.test_tooling_dispatch_propose import _bootstrap_principal_and_track

    principal, workspace, track_id = await _bootstrap_principal_and_track()
    from app.models.nodes import Entry, Track
    from app.services.entry_create import create_entry_in_track

    track = await Track.get(track_id)
    entries = [
        await create_entry_in_track(
            track=track, user_id=principal, workspace_id=workspace, title=name
        )
        for name in ("One", "Two")
    ]
    session = "collection-dispatch-proof"
    await staging.open_batch(
        user_id=principal, session_id=session, label="Update collection"
    )
    result = await dispatch_tool(
        "integral_bulk_update_entries",
        {
            "entry_ids": [e.id for e in entries],
            "updates": {"body": "Approved together"},
        },
        principal_id=principal,
        scope=workspace,
        session_id=session,
    )
    assert not result.is_error, result
    assert result.data["batched"]
    assert staging.peek_open_batch(principal, session)["op_count"] == 2
    change = await staging.commit_batch(user_id=principal, session_id=session)
    assert all(op["kind"] == "update_entry" for op in change.payload["operations"])
    for entry in entries:
        assert (await Entry.get(entry.id)).body != "Approved together"


@pytest.mark.asyncio
async def test_unknown_record_type_cannot_be_reported_as_an_applied_update(monkeypatch):
    from types import SimpleNamespace

    from app.agentive import staging_executors
    from app.models.nodes import Entry

    async def current_entry(*args, **kwargs):
        return SimpleNamespace(track_id="track")

    async def unresolved(*args, **kwargs):
        assert kwargs["exact"] is True
        return None

    async def forbidden_write(*args, **kwargs):
        pytest.fail("An unresolved type change reached a mutation handler")

    monkeypatch.setattr(Entry, "get", current_entry)
    monkeypatch.setattr(staging_executors, "_resolve_entry_type_id", unresolved)
    monkeypatch.setattr(staging_executors, "_call_endpoint", forbidden_write)
    result = await staging_executors._x_update_entry(
        "principal", {"entry_id": "record", "entry_type": "Missing"}
    )
    assert result["error"] and result["error_code"] == "entry_type_unresolved"
