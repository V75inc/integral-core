"""Independent metadata commands overlap; exact-record conflicts remain fenced."""

import asyncio

import pytest
from jvspatial.core.context import (
    GraphContext,
    get_default_context,
    scoped_default_context_async,
)
from jvspatial.db.postgres import PostgresTransaction

from app.api.errors import ResourceConflictError
from app.models.nodes import Entry, Track
from app.services.entry_create import create_entry_in_track
from app.services.entry_update import update_entry_in_track
from tests.test_entry_update_hook_snapshot import _entry

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def test_independent_metadata_updates_enter_together(monkeypatch):
    user, workspace, first = await _entry("parallel-metadata@example.com")
    second = await create_entry_in_track(
        track=await Track.find_one({"id": first.track_id}),
        user_id=user,
        workspace_id=workspace,
        title="Second",
    )
    database = get_default_context().database
    targets = {first.id, second.id}
    admitted = set()
    both = asyncio.Event()
    original = PostgresTransaction.find_one_and_update

    async def barrier(self, collection, query, update, *args, **kwargs):
        if query.get("id") in targets and "context.record_revision" in update.get(
            "$set", {}
        ):
            admitted.add(query["id"])
            if admitted == targets:
                both.set()
            await asyncio.wait_for(both.wait(), 5)
        return await original(self, collection, query, update, *args, **kwargs)

    monkeypatch.setattr(PostgresTransaction, "find_one_and_update", barrier)

    async def write(entry_id, title):
        async with scoped_default_context_async(GraphContext(database=database)):
            return await update_entry_in_track(
                entry_id=entry_id,
                user_id=user,
                workspace_id=workspace,
                title=title,
                expected_record_revision=1,
            )

    results = await asyncio.gather(
        write(first.id, "First saved"), write(second.id, "Second saved")
    )
    assert admitted == targets
    assert {result.title for result in results} == {"First saved", "Second saved"}
    assert all(result.record_revision == 2 for result in results)


@pytest.mark.parametrize("reviewed", [False, True])
async def test_same_entry_updates_merge_or_reject_the_stale_review(reviewed):
    user, workspace, entry = await _entry(f"same-entry-{reviewed}@example.com")
    database = get_default_context().database

    async def write(**fields):
        async with scoped_default_context_async(GraphContext(database=database)):
            return await update_entry_in_track(
                entry_id=entry.id,
                user_id=user,
                workspace_id=workspace,
                expected_record_revision=1 if reviewed else None,
                **fields,
            )

    results = await asyncio.gather(
        write(title="Saved title"), write(body="Saved body"), return_exceptions=True
    )
    current = await Entry.find_one({"id": entry.id})
    if reviewed:
        assert sum(isinstance(result, ResourceConflictError) for result in results) == 1
        assert sum(isinstance(result, Entry) for result in results) == 1
        assert current.record_revision == 2
    else:
        assert all(isinstance(result, Entry) for result in results)
        assert current.record_revision == 3
        assert current.title == "Saved title" and current.body == "Saved body"


async def test_shared_release_peers_both_hold_revocation():
    from jvspatial.api.auth.models import User as AuthUser

    from app.api.errors import InsufficientPermissionsError
    from app.services.release_admission import release_admission
    from tests.contract.test_release_admission_postgres import remove_membership

    user, workspace_id, _ = await _entry("shared-release-peers@example.com")
    from app.models.nodes import Workspace

    workspace = await Workspace.find_one({"id": workspace_id})
    auth = await AuthUser.get(user)
    entered = [asyncio.Event(), asyncio.Event()]
    finish = [asyncio.Event(), asyncio.Event()]

    async def release(index):
        async with release_admission(user, workspace_id):
            entered[index].set()
            await finish[index].wait()

    peers = [asyncio.create_task(release(index)) for index in range(2)]
    revoking = None
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered)), 5)
        revoking = asyncio.create_task(remove_membership(auth, workspace))
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(revoking), 0.1)
        finish[0].set()
        await peers[0]
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(revoking), 0.1)
        finish[1].set()
        await peers[1]
        await asyncio.wait_for(revoking, 5)
        with pytest.raises(InsufficientPermissionsError):
            async with release_admission(user, workspace_id):
                pytest.fail("Revoked identity was released")
    finally:
        for event in finish:
            event.set()
        await asyncio.gather(*peers, return_exceptions=True)
        if revoking is not None and not revoking.done():
            revoking.cancel()
            await asyncio.gather(revoking, return_exceptions=True)


async def test_shared_scopes_refuse_an_exclusive_upgrade():
    from app.exceptions import AuthorizationUnavailableError
    from app.services.app_operations.transaction_scope import postgres_graph_transaction
    from app.services.authorization_scope import authorization_scope
    from app.services.identity_admission import identity_admission

    user, workspace, _ = await _entry("shared-upgrade@example.com")
    with pytest.raises(AuthorizationUnavailableError):
        async with authorization_scope(workspace, shared=True):
            async with authorization_scope(workspace, authority_mutation=True):
                pytest.fail("Shared workspace lock was upgraded")
    with pytest.raises(AuthorizationUnavailableError):
        async with (
            postgres_graph_transaction(),
            identity_admission((user,), shared=True),
        ):
            async with identity_admission((user,), shared=False):
                pytest.fail("Shared identity lock was upgraded")


@pytest.mark.parametrize("reviewed", [False, True])
async def test_delete_waiting_for_metadata_uses_the_committed_revision(
    monkeypatch, reviewed
):
    from app.services.entry_deletion import delete_entry_fast

    user, workspace, entry = await _entry(
        f"delete-after-metadata-{reviewed}@example.com"
    )
    database = get_default_context().database
    saved = asyncio.Event()
    finish = asyncio.Event()
    deleting = asyncio.Event()
    original_update = PostgresTransaction.find_one_and_update
    original_lock = PostgresTransaction.lock_record

    async def paused_update(self, collection, query, update, *args, **kwargs):
        result = await original_update(self, collection, query, update, *args, **kwargs)
        if query.get("id") == entry.id and "context.record_revision" in update.get(
            "$set", {}
        ):
            saved.set()
            await asyncio.wait_for(finish.wait(), 5)
        return result

    async def observe_exclusive(self, collection, identifier, *, shared=False):
        if (
            collection == "object"
            and identifier == user
            and not shared
            and saved.is_set()
        ):
            deleting.set()
        return await original_lock(self, collection, identifier, shared=shared)

    monkeypatch.setattr(PostgresTransaction, "find_one_and_update", paused_update)
    monkeypatch.setattr(PostgresTransaction, "lock_record", observe_exclusive)

    async def update():
        async with scoped_default_context_async(GraphContext(database=database)):
            return await update_entry_in_track(
                entry_id=entry.id,
                user_id=user,
                workspace_id=workspace,
                title="Committed before deletion",
                expected_record_revision=1,
            )

    async def delete():
        async with scoped_default_context_async(GraphContext(database=database)):
            return await delete_entry_fast(
                entry,
                actor_user_id=user,
                expected_record_revision=1 if reviewed else None,
            )

    writer = asyncio.create_task(update())
    remover = None
    try:
        await asyncio.wait_for(saved.wait(), 5)
        remover = asyncio.create_task(delete())
        await asyncio.wait_for(deleting.wait(), 5)
        assert not remover.done()
        finish.set()
        await writer
        if reviewed:
            with pytest.raises(ResourceConflictError):
                await remover
            current = await Entry.find_one({"id": entry.id})
            assert (
                current.title == "Committed before deletion"
                and current.record_revision == 2
            )
        else:
            await remover
            assert await Entry.find_one({"id": entry.id}) is None
    finally:
        finish.set()
        await asyncio.gather(
            writer, *([remover] if remover is not None else []), return_exceptions=True
        )


@pytest.mark.parametrize("command", ["tag", "attachment"])
async def test_exact_row_locks_preserve_entry_discriminator_checks(
    monkeypatch, command
):
    from app.api.errors import ResourceNotFoundError
    from app.services.entry_attachment_filing import file_chat_attachment
    from app.services.entry_tag_commands import change_entry_tag

    user, _, entry = await _entry(f"lock-discriminator-{command}@example.com")
    original = PostgresTransaction.lock_record

    async def wrong_discriminator(self, collection, identifier, *, shared=False):
        row = await original(self, collection, identifier, shared=shared)
        if collection == "node" and identifier == entry.id:
            return {**row, "entity": "Track"}
        return row

    monkeypatch.setattr(PostgresTransaction, "lock_record", wrong_discriminator)
    with pytest.raises(ResourceNotFoundError):
        if command == "tag":
            await change_entry_tag(
                user_id=user, entry_id=entry.id, tag_id="unused", present=True
            )
        else:
            await file_chat_attachment(
                user_id=user, entry_id=entry.id, attachment_id="unused"
            )
    current = await Entry.find_one({"id": entry.id})
    assert (
        current.record_revision == 1
        and current.tags == []
        and current.attachment_ids == []
    )
