"""Retryable post-commit file and embedding cleanup for deleted graph content."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from typing import Any

from jvspatial.core.context import get_default_context

from app.exceptions import AuthorizationUnavailableError
from app.utils.time import utc_now_iso

ENTITY = "EntryCleanupFact"
logger = logging.getLogger(__name__)


async def retain_cleanup(*, entry_id: str, session_ids: list[str]) -> None:
    """Persist scalar cleanup intent in the same transaction as the deletion.

    This log-shaped record is not a graph participant (I-GRAPH-02). Files are
    never removed before the graph command commits, so rollback retains uploads.
    """
    if not entry_id or any(
        not re.fullmatch(r"[A-Za-z0-9_.-]+", value) for value in session_ids
    ):
        raise AuthorizationUnavailableError()
    database = get_default_context().database
    identity = "o.EntryCleanupFact." + hashlib.sha256(entry_id.encode()).hexdigest()
    document = {
        "id": identity,
        "entity": ENTITY,
        "context": {
            "entry_id": entry_id,
            "session_ids": sorted(set(session_ids)),
            "status": "pending",
            "created_at": utc_now_iso(),
            "attempt": 0,
        },
    }
    await database.insert_if_absent("object", document)


async def deliver_pending_cleanup(*, database: Any = None) -> int:
    """Idempotent cleanup; failed records remain pending for recovery."""
    import shutil

    from jvspatial.db import get_prime_database

    from app.services.app_operations.transaction_scope import _transaction_database
    from app.services.chunked_upload import _session_dir
    from app.services.retrieval import get_embedding_store

    database = _transaction_database(database or get_prime_database())
    rows = await database.find(
        "object", {"entity": ENTITY, "context.status": "pending"}, limit=1000
    )
    delivered = 0
    for row in rows:
        data = row.get("context")
        if not isinstance(data, dict):
            raise AuthorizationUnavailableError()
        entry_id, sessions = data.get("entry_id"), data.get("session_ids")
        if (
            not isinstance(entry_id, str)
            or not isinstance(sessions, list)
            or any(
                not isinstance(value, str)
                or not re.fullmatch(r"[A-Za-z0-9_.-]+", value)
                for value in sessions
            )
        ):
            raise AuthorizationUnavailableError()
        # Also protects recovery retries against an accidentally restored live
        # object. Restore reconciliation must remove this obsolete cleanup fact.
        if await database.get("node", entry_id) is not None:
            continue
        try:
            for session_id in sessions:
                if await database.get("node", session_id) is not None:
                    raise AuthorizationUnavailableError()
                try:
                    await asyncio.to_thread(shutil.rmtree, _session_dir(session_id))
                except FileNotFoundError:
                    pass  # Idempotent replay after partial cleanup or OS eviction.
            await get_embedding_store().soft_delete(entry_id)
            data["status"] = "delivered"
            data["delivered_at"] = utc_now_iso()
            row["context"] = data
            await database.save("object", row)
            delivered += 1
        except Exception:
            logger.exception("Entry cleanup remains pending: %s", row.get("id"))
    return delivered
