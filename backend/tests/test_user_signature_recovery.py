"""Saved signature failures and rollback preserve the current graph and bytes."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from jvspatial.core.context import get_default_context

from app.models.nodes import Attachment, User
from app.services import signature_cleanup as cleanup
from app.services import user_signatures as sig
from app.services.attachment_storage import AttachmentStorageService

pytestmark = pytest.mark.smoke


class Store:
    build_storage_key = staticmethod(AttachmentStorageService.build_storage_key)

    def __init__(self):
        self.files = {}
        self.deleted = []
        self.failure = False

    async def save_attachment(
        self, *, entry_id, attachment_id, filename, content, **kw
    ):
        if self.failure:
            raise OSError("injected storage failure")
        key = self.build_storage_key(entry_id, attachment_id, filename)
        self.files[key] = content
        return {"path": key}

    async def read_attachment(self, key):
        return self.files[key]

    async def delete_attachment(self, key):
        self.deleted.append(key)
        return self.files.pop(key, None) is not None

    async def get_metadata(self, key):
        return {} if key in self.files else None


@pytest.fixture
async def vault(test_user, monkeypatch):
    store = Store()
    monkeypatch.setattr(sig, "get_attachment_storage_service", lambda: store)
    monkeypatch.setattr(cleanup, "get_attachment_storage_service", lambda: store)
    return test_user, store, get_default_context().database


@pytest.mark.asyncio
async def test_storage_failure_preserves_original_graph_and_bytes(vault):
    user, store, db = vault
    original = await sig.save_user_signature(user.id, png_bytes=b"original")
    old = (await Attachment.find_one({"id": original["id"]})).storage_key
    store.failure = True
    with pytest.raises(OSError, match="injected"):
        await sig.save_user_signature(user.id, png_bytes=b"replacement")
    assert await sig.read_user_signature_png(user.id) == b"original"
    assert (await sig.list_user_signatures(user.id))[0]["id"] == original["id"]
    assert not store.deleted
    assert store.files[old] == b"original"
    await cleanup.deliver_pending_signature_cleanup(database=db)
    assert store.files[old] == b"original"


@pytest.mark.asyncio
async def test_replacement_retires_old_file_only_through_cleanup(vault):
    user, store, db = vault
    first = await sig.save_user_signature(user.id, png_bytes=b"original")
    old = (await Attachment.find_one({"id": first["id"]})).storage_key
    second = await sig.save_user_signature(user.id, png_bytes=b"replacement")
    assert await sig.read_user_signature_png(user.id) == b"replacement"
    assert await Attachment.find_one({"id": first["id"]}) is None
    assert store.files[old] == b"original" and not store.deleted
    assert await cleanup.deliver_pending_signature_cleanup(database=db) == 1
    assert old not in store.files
    assert await sig.read_user_signature_png(user.id) == b"replacement"
    assert await cleanup.deliver_pending_signature_cleanup(database=db) == 0


@pytest.mark.asyncio
async def test_delete_queues_file_cleanup_without_touching_other_user(
    vault, test_user2
):
    user, store, db = vault
    first = await sig.save_user_signature(user.id, png_bytes=b"original")
    other = test_user2
    with pytest.raises(PermissionError):
        await sig.delete_user_signature(other.id, first["id"])
    assert await sig.read_user_signature_png(user.id) == b"original"
    assert await sig.delete_user_signature(user.id, first["id"])
    assert await sig.read_user_signature_png(user.id) is None
    assert not store.deleted
    assert await cleanup.deliver_pending_signature_cleanup(database=db) == 1
    assert not store.files


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage",
    ["attachment-create", "attachment-save", "edge-connect", "empty-storage-path"],
)
async def test_incomplete_graph_or_storage_confirmation_preserves_original(
    vault, monkeypatch, stage
):
    user, store, db = vault
    first = await sig.save_user_signature(user.id, png_bytes=b"original")
    if stage == "attachment-create":
        monkeypatch.setattr(
            Attachment,
            "create",
            AsyncMock(side_effect=OSError("injected creation failure")),
        )
    elif stage == "attachment-save":
        original = Attachment.save

        async def save(attachment, *a, **kw):
            if attachment.id != first["id"] and attachment.storage_key:
                raise OSError("injected metadata save failure")
            return await original(attachment, *a, **kw)

        monkeypatch.setattr(Attachment, "save", save)
    elif stage == "edge-connect":
        original = User.connect

        async def connect(user, *a, **kw):
            raise OSError("injected edge failure")

        monkeypatch.setattr(User, "connect", connect)
    else:
        original = store.save_attachment

        async def save(**kw):
            await original(**kw)
            return {"path": ""}

        monkeypatch.setattr(store, "save_attachment", save)
    with pytest.raises(OSError):
        await sig.save_user_signature(user.id, png_bytes=b"replacement")
    assert await sig.read_user_signature_png(user.id) == b"original"
    assert (await sig.list_user_signatures(user.id))[0]["id"] == first["id"]
    assert not store.deleted
    await cleanup.deliver_pending_signature_cleanup(database=db)
    assert await sig.read_user_signature_png(user.id) == b"original"


@pytest.mark.asyncio
async def test_cleanup_failure_remains_retryable_and_keeps_new_signature(
    vault, monkeypatch
):
    user, store, db = vault
    await sig.save_user_signature(user.id, png_bytes=b"original")
    await sig.save_user_signature(user.id, png_bytes=b"replacement")
    original = store.delete_attachment
    monkeypatch.setattr(
        store,
        "delete_attachment",
        AsyncMock(side_effect=OSError("injected cleanup failure")),
    )
    assert await cleanup.deliver_pending_signature_cleanup(database=db) == 0
    assert await sig.read_user_signature_png(user.id) == b"replacement"
    monkeypatch.setattr(store, "delete_attachment", original)
    assert await cleanup.deliver_pending_signature_cleanup(database=db) == 1
    assert await sig.read_user_signature_png(user.id) == b"replacement"


@pytest.mark.asyncio
async def test_outer_postgres_rollback_keeps_original_identity_bytes_and_cleanup(vault):
    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
        postgres_graph_transaction,
    )

    user, store, db = vault
    if not graph_transaction_available(db):
        pytest.skip("PostgreSQL outer rollback contract")
    first = await sig.save_user_signature(user.id, png_bytes=b"original")
    old = (await Attachment.find_one({"id": first["id"]})).storage_key
    with pytest.raises(RuntimeError, match="outer rollback"):
        async with postgres_graph_transaction(db):
            await sig.save_user_signature(user.id, png_bytes=b"replacement")
            raise RuntimeError("outer rollback")
    assert (await sig.list_user_signatures(user.id))[0]["id"] == first["id"]
    assert await sig.read_user_signature_png(user.id) == b"original"
    assert store.files[old] == b"original"
    assert await cleanup.deliver_pending_signature_cleanup(database=db) == 0
    assert store.files[old] == b"original"


@pytest.mark.asyncio
async def test_concurrent_replacements_leave_one_reachable_current_signature(vault):
    user, store, db = vault
    first = await sig.save_user_signature(user.id, png_bytes=b"original")
    results = await asyncio.gather(
        sig.save_user_signature(user.id, png_bytes=b"new A"),
        sig.save_user_signature(user.id, png_bytes=b"new B"),
    )
    current = await sig.list_user_signatures(user.id)
    assert len(current) == 1 and current[0]["id"] in {r["id"] for r in results}
    assert await sig.read_user_signature_png(user.id) in {b"new A", b"new B"}
    assert await cleanup.deliver_pending_signature_cleanup(database=db) == 2
    assert len(store.files) == 1


@pytest.mark.asyncio
async def test_restored_attachment_keeps_its_file_even_with_pending_cleanup(vault):
    from app.models.edges import HAS_ATTACHMENT

    user, store, db = vault
    first = await sig.save_user_signature(user.id, png_bytes=b"original")
    att = await Attachment.find_one({"id": first["id"]})
    await sig.save_user_signature(user.id, png_bytes=b"replacement")
    await att.save()
    await user.connect(att, edge=HAS_ATTACHMENT, role="signature")
    assert await cleanup.deliver_pending_signature_cleanup(database=db) == 0
    assert store.files[att.storage_key] == b"original"


@pytest.mark.asyncio
async def test_authentication_principal_resolves_its_profile_vault(vault):
    user, store, db = vault
    assert user.user_id
    result = await sig.save_user_signature(user.user_id, png_bytes=b"principal fixture")
    assert (await sig.list_user_signatures(user.id))[0]["id"] == result["id"]
    assert await sig.read_user_signature_png(user.user_id) == b"principal fixture"


@pytest.mark.asyncio
async def test_api_work_recovery_maintenance_delivers_retired_file(vault):
    from app.services.app_operations.event_outbox import deliver_pending_cleanup_events

    user, store, db = vault
    await sig.save_user_signature(user.id, png_bytes=b"original")
    await sig.save_user_signature(user.id, png_bytes=b"replacement")
    assert len(store.files) == 2
    await deliver_pending_cleanup_events()
    assert len(store.files) == 1
    assert await sig.read_user_signature_png(user.id) == b"replacement"


@pytest.mark.asyncio
async def test_unresponsive_storage_respects_cleanup_pass_budget(vault, monkeypatch):
    user, store, db = vault
    await sig.save_user_signature(user.id, png_bytes=b"original")
    await sig.save_user_signature(user.id, png_bytes=b"replacement")
    waiting = asyncio.Event()

    async def unavailable(key):
        await waiting.wait()

    # The tiny budget belongs to the unresponsive-storage probe only. The
    # successful retry uses the production budget: graph/file I/O can exceed
    # 50ms on a loaded worker without indicating a cleanup failure.
    with monkeypatch.context() as probe:
        probe.setattr(cleanup, "_CLEANUP_PASS_SECONDS", 0.05)
        probe.setattr(store, "delete_attachment", unavailable)
        assert (
            await asyncio.wait_for(
                cleanup.deliver_pending_signature_cleanup(database=db), 2
            )
            == 0
        )
    assert await sig.read_user_signature_png(user.id) == b"replacement"
    assert await cleanup.deliver_pending_signature_cleanup(database=db) == 1


@pytest.mark.asyncio
async def test_commit_failure_preserves_original_and_reclaims_only_failed_replacement(
    vault, monkeypatch
):
    from jvspatial.db.postgres import PostgresTransaction

    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
    )

    user, store, db = vault
    if not graph_transaction_available(db):
        pytest.skip("PostgreSQL commit failure contract")
    first = await sig.save_user_signature(user.id, png_bytes=b"original")
    original = PostgresTransaction.commit
    failed = False

    async def fail_once(transaction):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("injected commit failure")
        return await original(transaction)

    monkeypatch.setattr(PostgresTransaction, "commit", fail_once)
    with pytest.raises(OSError, match="injected commit failure"):
        await sig.save_user_signature(user.id, png_bytes=b"replacement")
    assert (await sig.list_user_signatures(user.id))[0]["id"] == first["id"]
    assert await sig.read_user_signature_png(user.id) == b"original"
    assert await cleanup.deliver_pending_signature_cleanup(database=db) == 1
    assert list(store.files.values()) == [b"original"]


@pytest.mark.asyncio
async def test_real_png_files_survive_failed_replacement_and_current_file_survives_cleanup(
    test_user, tmp_path, monkeypatch
):
    import struct
    import zlib

    from jvspatial.storage import create_storage

    def png(color):
        def chunk(kind, content):
            data = kind + content
            return (
                struct.pack("!I", len(content))
                + data
                + struct.pack("!I", zlib.crc32(data))
            )

        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack("!IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00" + bytes(color)))
            + chunk(b"IEND", b"")
        )

    store = AttachmentStorageService(
        create_storage("local", root_dir=str(tmp_path / "files"), create_root=True)
    )
    monkeypatch.setattr(sig, "get_attachment_storage_service", lambda: store)
    monkeypatch.setattr(cleanup, "get_attachment_storage_service", lambda: store)
    old_bytes, new_bytes = png((255, 0, 0)), png((0, 0, 255))
    first = await sig.save_user_signature(test_user.user_id, png_bytes=old_bytes)
    old = await Attachment.find_one({"id": first["id"]})
    save = store.save_attachment
    monkeypatch.setattr(
        store,
        "save_attachment",
        AsyncMock(side_effect=OSError("fixture upload failure")),
    )
    with pytest.raises(OSError):
        await sig.save_user_signature(test_user.user_id, png_bytes=new_bytes)
    assert await store.read_attachment(old.storage_key) == old_bytes
    assert await sig.read_user_signature_png(test_user.user_id) == old_bytes
    monkeypatch.setattr(store, "save_attachment", save)
    current = await sig.save_user_signature(test_user.user_id, png_bytes=new_bytes)
    assert (tmp_path / "files" / old.storage_key).read_bytes() == old_bytes
    await cleanup.deliver_pending_signature_cleanup(
        database=get_default_context().database
    )
    assert not (tmp_path / "files" / old.storage_key).exists()
    assert (await sig.list_user_signatures(test_user.user_id))[0]["id"] == current["id"]
    assert await sig.read_user_signature_png(test_user.user_id) == new_bytes


@pytest.mark.asyncio
async def test_ambiguous_existing_signatures_refuse_replacement_without_deleting_data(
    vault,
):
    from app.api.errors import ResourceConflictError
    from app.models.edges import HAS_ATTACHMENT

    user, store, db = vault
    first = await sig.save_user_signature(user.id, png_bytes=b"original")
    async with cleanup.signature_write_scope(user.id):
        current_user = await User.find_one({"id": user.id})
        second = await Attachment.create(
            filename="signature-conflict.png",
            mime_type="image/png",
            size=8,
            storage_key="",
            source_type="file",
            uploaded_by=user.id,
        )
        await current_user.connect(second, edge=HAS_ATTACHMENT, role="signature")
        second.storage_key = store.build_storage_key(
            user.id, second.id, second.filename
        )
        await second.save()
    store.files[second.storage_key] = b"conflict"
    before = dict(store.files)
    with pytest.raises(ResourceConflictError, match="needs review"):
        await sig.save_user_signature(user.id, png_bytes=b"replacement")
    assert store.files == before and not store.deleted
    assert await Attachment.find_one({"id": first["id"]}) is not None
    assert await Attachment.find_one({"id": second.id}) is not None


@pytest.mark.asyncio
async def test_disabled_canonical_profile_cannot_read_replace_or_delete(vault):
    from app.services.authentication import get_authentication_service

    user, store, db = vault
    original = await sig.save_user_signature(user.id, png_bytes=b"original")
    service = get_authentication_service()
    await service.context.database.find_one_and_update(
        "object", {"id": user.user_id}, {"$set": {"context.is_active": False}}
    )
    before = dict(store.files)
    with pytest.raises(ValueError, match="User not found"):
        await sig.save_user_signature(user.id, png_bytes=b"replacement")
    assert not await sig.delete_user_signature(user.id, original["id"])
    assert await sig.read_user_signature_png(user.id) is None
    assert await sig.list_user_signatures(user.id) == []
    assert store.files == before and not store.deleted
    assert await Attachment.find_one({"id": original["id"]}) is not None


@pytest.mark.asyncio
async def test_cleanup_refuses_uncommitted_graph_deletion_and_rollback_retains_file(
    vault,
):
    from app.services.app_operations.transaction_scope import (
        OperationTransactionUnavailable,
        graph_transaction_available,
        postgres_graph_transaction,
    )

    user, store, db = vault
    if not graph_transaction_available(db):
        pytest.skip("PostgreSQL cleanup admission contract")
    original = await sig.save_user_signature(user.id, png_bytes=b"original")
    attachment = await Attachment.find_one({"id": original["id"]})
    await cleanup.retain_signature_cleanup(
        user_id=user.id,
        attachment_id=attachment.id,
        filename=attachment.filename,
        storage_key=attachment.storage_key,
    )
    with pytest.raises(RuntimeError, match="outer rollback"):
        async with postgres_graph_transaction(db):
            current = await Attachment.find_one({"id": attachment.id})
            await current.delete()
            with pytest.raises(OperationTransactionUnavailable):
                await cleanup.deliver_pending_signature_cleanup(database=db)
            raise RuntimeError("outer rollback")
    assert await sig.read_user_signature_png(user.id) == b"original"
    assert store.files[attachment.storage_key] == b"original"
    assert not store.deleted


@pytest.mark.asyncio
async def test_explicit_raw_transaction_cannot_be_used_for_file_cleanup(vault):
    from app.services.app_operations.transaction_scope import (
        OperationTransactionUnavailable,
        _transaction_database,
        graph_transaction_available,
    )

    user, store, db = vault
    if not graph_transaction_available(db):
        pytest.skip("PostgreSQL direct-handle cleanup contract")
    original = await sig.save_user_signature(user.id, png_bytes=b"original")
    attachment = await Attachment.find_one({"id": original["id"]})
    await cleanup.retain_signature_cleanup(
        user_id=user.id,
        attachment_id=attachment.id,
        filename=attachment.filename,
        storage_key=attachment.storage_key,
    )
    transaction = await _transaction_database(db).begin_transaction()
    try:
        await transaction.delete("node", attachment.id)
        with pytest.raises(OperationTransactionUnavailable):
            await cleanup.deliver_pending_signature_cleanup(database=transaction)
    finally:
        await transaction.rollback()
    assert await sig.read_user_signature_png(user.id) == b"original"
    assert store.files[attachment.storage_key] == b"original"


@pytest.mark.asyncio
async def test_disabled_identity_commits_before_waiting_signature_write(vault):
    from contextvars import Context

    from jvspatial.core.context import GraphContext, scoped_default_context_async
    from jvspatial.db.factory import create_database

    from app.api.errors import InsufficientPermissionsError
    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
        postgres_graph_transaction,
    )

    user, store, db = vault
    if not graph_transaction_available(db):
        pytest.skip("PostgreSQL current-identity ordering contract")
    original = await sig.save_user_signature(user.id, png_bytes=b"original")
    before = dict(store.files)
    second = create_database(db_type="postgres", min_size=1, max_size=1)
    started = asyncio.Event()
    task = None

    async def write():
        async with scoped_default_context_async(GraphContext(database=second)):
            started.set()
            return await sig.save_user_signature(user.id, png_bytes=b"replacement")

    try:
        for collection in ("node", "edge", "object"):
            await second.find(collection, {"id": ""}, limit=1)
        async with postgres_graph_transaction(db) as transaction:
            await transaction.find_one_and_update(
                "object", {"id": user.user_id}, {"$set": {"context.is_active": False}}
            )
            task = asyncio.create_task(write(), context=Context())
            await asyncio.wait_for(started.wait(), 5)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), 0.15)
        with pytest.raises(InsufficientPermissionsError):
            await asyncio.wait_for(task, 10)
        assert store.files == before and not store.deleted
        assert await Attachment.find_one({"id": original["id"]}) is not None
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await second.close()


class CleanupDatabase:
    """Small persistence double that obeys explicit ordering and pending filters."""

    def __init__(self, rows, live=()):
        self.rows = {row["id"]: row for row in reversed(rows)}
        self.live = set(live)

    async def find(self, collection, query, *, limit, sort):
        rows = [
            row
            for row in self.rows.values()
            if row["context"]["status"] == "pending"
            and row["id"] > query.get("id", {}).get("$gt", "")
        ]
        if sort == [("id", 1)]:
            rows.sort(key=lambda row: row["id"])
        return rows[:limit]

    async def get(self, collection, identity):
        if collection == "node":
            return {"id": identity} if identity in self.live else None
        return self.rows.get(identity)

    async def save(self, collection, row):
        self.rows[row["id"]] = row
        return row


def cleanup_rows(store, count):
    rows = []
    for index in range(count):
        user, attachment = "n.User.cleanup-fixture", f"n.Attachment.cleanup-{index}"
        key = store.build_storage_key(user, attachment, "signature.png")
        store.files[key] = b"fixture"
        rows.append(
            {
                "id": cleanup._identity(user, attachment, key),
                "entity": "UserSignatureCleanupFact",
                "context": {
                    "user_id": user,
                    "attachment_id": attachment,
                    "storage_key": key,
                    "status": "pending",
                },
            }
        )
    return sorted(rows, key=lambda row: row["id"])


@pytest.mark.asyncio
async def test_cleanup_advances_past_a_full_page_of_restored_attachments(monkeypatch):
    store = Store()
    rows = cleanup_rows(store, 3)
    database = CleanupDatabase(
        rows, [row["context"]["attachment_id"] for row in rows[:2]]
    )
    monkeypatch.setattr(cleanup, "get_attachment_storage_service", lambda: store)
    monkeypatch.setattr(cleanup, "_CLEANUP_PAGE_SIZE", 2)
    assert await cleanup.deliver_pending_signature_cleanup(database=database) == 0
    assert await cleanup.deliver_pending_signature_cleanup(database=database) == 1
    assert all(row["context"]["storage_key"] in store.files for row in rows[:2])
    assert rows[2]["context"]["storage_key"] not in store.files


@pytest.mark.asyncio
async def test_a_slow_first_retirement_cannot_starve_a_later_one(monkeypatch):
    store = Store()
    rows = cleanup_rows(store, 2)
    database = CleanupDatabase(rows)
    delete = store.delete_attachment

    async def slow_first(key):
        if key == rows[0]["context"]["storage_key"]:
            await asyncio.Event().wait()
        return await delete(key)

    monkeypatch.setattr(store, "delete_attachment", slow_first)
    monkeypatch.setattr(cleanup, "get_attachment_storage_service", lambda: store)
    monkeypatch.setattr(cleanup, "_CLEANUP_PASS_SECONDS", 0.05)
    assert (
        await asyncio.wait_for(
            cleanup.deliver_pending_signature_cleanup(database=database), 2
        )
        == 0
    )
    assert (
        await asyncio.wait_for(
            cleanup.deliver_pending_signature_cleanup(database=database), 2
        )
        == 1
    )
    assert rows[0]["context"]["storage_key"] in store.files
    assert rows[1]["context"]["storage_key"] not in store.files


@pytest.mark.asyncio
async def test_cleanup_budget_also_bounds_initial_database_selection(monkeypatch):
    class UnavailableDatabase(CleanupDatabase):
        async def find(self, *args, **kwargs):
            await asyncio.Event().wait()

    monkeypatch.setattr(cleanup, "_CLEANUP_PASS_SECONDS", 0.05)
    assert (
        await asyncio.wait_for(
            cleanup.deliver_pending_signature_cleanup(database=UnavailableDatabase([])),
            2,
        )
        == 0
    )
