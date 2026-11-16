"""Committed, retryable binary cleanup for retired saved signatures."""

import asyncio
import hashlib
import json
import logging
from collections import OrderedDict
from contextlib import asynccontextmanager
from typing import Any
from weakref import WeakKeyDictionary

from jvspatial.core.context import get_default_context
from jvspatial.db.postgres import current_tenant

from app.exceptions import AuthorizationUnavailableError
from app.models.user_signature_cleanup import UserSignatureCleanupFact
from app.services.attachment_storage import get_attachment_storage_service
from app.services.entry_write_scope import (
    entry_write_scope,
    is_postgres_entry_transaction,
)
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)
_ENTITY = UserSignatureCleanupFact._entity_name()
_CLEANUP_PASS_SECONDS = 5
_CLEANUP_PAGE_SIZE = 1000
_cursors: WeakKeyDictionary[Any, OrderedDict[str | None, str]] = WeakKeyDictionary()


@asynccontextmanager
async def signature_write_scope(user_id):
    """Serialize one profile’s signature writes through the graph transaction."""
    from app.services.identity_admission import identity_admission

    async with entry_write_scope(f"user-signature:{user_id}") as graph:
        # Identity rows precede resource fences; PG retains their shared locks
        # until the complete graph transaction commits or rolls back.
        async with identity_admission((user_id,), shared=True):
            if is_postgres_entry_transaction(graph.database):
                try:
                    async with asyncio.timeout(30):
                        await graph.database.advisory_lock(
                            "integral:user-signatures", user_id
                        )
                except TimeoutError as exc:
                    raise AuthorizationUnavailableError() from exc
            yield graph


def _identity(user_id, attachment_id, storage_key):
    digest = hashlib.sha256(
        json.dumps(
            [user_id, attachment_id, storage_key], separators=(",", ":")
        ).encode()
    ).hexdigest()
    return "o.UserSignatureCleanupFact." + digest


async def retain_signature_cleanup(*, user_id, attachment_id, filename, storage_key):
    """Retain exact file retirement intent with its graph deletion."""
    if not storage_key:
        return
    storage = get_attachment_storage_service()
    if storage_key != storage.build_storage_key(user_id, attachment_id, filename):
        raise AuthorizationUnavailableError()
    await get_default_context().database.insert_if_absent(
        "object",
        {
            "id": _identity(user_id, attachment_id, storage_key),
            "entity": _ENTITY,
            "context": {
                "user_id": user_id,
                "attachment_id": attachment_id,
                "storage_key": storage_key,
                "status": "pending",
                "created_at": utc_now_iso(),
            },
        },
    )


async def deliver_pending_signature_cleanup(*, database=None):
    """Retry committed retirements while protecting live or restored nodes."""
    from jvspatial.db import get_prime_database

    from app.services.app_operations.transaction_scope import (
        OperationTransactionUnavailable,
        _transaction_database,
        graph_transaction_available,
        postgres_graph_transaction,
        require_outside_graph_transaction,
    )

    require_outside_graph_transaction()
    database = _transaction_database(database or get_prime_database())
    if is_postgres_entry_transaction(database):
        raise OperationTransactionUnavailable(
            "Signature cleanup requires committed database state"
        )
    tenant = current_tenant()
    cursors = _cursors.setdefault(database, OrderedDict())
    if tenant in cursors:
        cursors.move_to_end(tenant)
    after = cursors.get(tenant, "")
    query: dict[str, Any] = {"entity": _ENTITY, "context.status": "pending"}
    if after:
        query["id"] = {"$gt": after}
    deadline = asyncio.get_running_loop().time() + _CLEANUP_PASS_SECONDS
    try:
        async with asyncio.timeout(_CLEANUP_PASS_SECONDS):
            rows = await database.find(
                "object", query, limit=_CLEANUP_PAGE_SIZE, sort=[("id", 1)]
            )
            if not rows:
                cursors.pop(tenant, None)
                return 0
            storage = await asyncio.to_thread(get_attachment_storage_service)
    except TimeoutError:
        return 0

    @asynccontextmanager
    async def scope():
        if graph_transaction_available(database):
            async with postgres_graph_transaction(database) as transaction:
                yield transaction
        else:
            yield database

    delivered = 0
    attempted = 0
    for row in rows:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            break
        # Advance on every attempt, not only success: a restored or slow
        # first page cannot indefinitely hide later eligible retirements.
        if isinstance(row.get("id"), str):
            cursors[tenant] = row["id"]
            cursors.move_to_end(tenant)
            if len(cursors) > 64:
                cursors.popitem(last=False)
        attempted += 1
        try:
            async with asyncio.timeout(remaining):
                data = row.get("context") or {}
                user_id, attachment_id, key = (
                    data.get("user_id"),
                    data.get("attachment_id"),
                    data.get("storage_key"),
                )
                if not all(
                    isinstance(value, str) and value
                    for value in [user_id, attachment_id, key]
                ) or row.get("id") != _identity(user_id, attachment_id, key):
                    raise AuthorizationUnavailableError()
                async with scope() as transaction:
                    if graph_transaction_available(database):
                        async with asyncio.timeout(30):
                            await transaction.advisory_lock(
                                "integral:user-signatures", user_id
                            )
                    current = await transaction.get("object", row["id"])
                    if (
                        not current
                        or (current.get("context") or {}).get("status") != "pending"
                    ):
                        continue
                    # A restored or still-live attachment is never garbage, even if
                    # a cleanup intent survived an interrupted command or restore.
                    if await transaction.get("node", attachment_id) is not None:
                        continue
                    async with asyncio.timeout(30):
                        removed = await storage.delete_attachment(key)
                        if not removed and await storage.get_metadata(key) is not None:
                            raise OSError("Signature file deletion was not confirmed")
                    current["context"]["status"] = "delivered"
                    current["context"]["delivered_at"] = utc_now_iso()
                    await transaction.save("object", current)
                    delivered += 1
        except Exception:
            logger.exception("Signature cleanup remains pending: %s", row.get("id"))
    if attempted == len(rows) and len(rows) < _CLEANUP_PAGE_SIZE:
        cursors.pop(tenant, None)
    return delivered
