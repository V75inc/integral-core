"""Reconcile abandoned generated-blob attempts without deleting a winner."""

from __future__ import annotations

import hashlib
import logging
import math
import re
import time

from jvspatial.db import get_prime_database

from app.models.harness_records import (
    HarnessDraftBlobAttempt,
    HarnessDraftPreparationRecord,
)
from app.services.app_operations.transaction_scope import (
    graph_transaction_available,
    postgres_graph_transaction,
    require_outside_graph_transaction,
)
from app.services.attachment_storage import get_attachment_storage_service

logger = logging.getLogger(__name__)
_RECHECK_SECONDS = 300.0


def _safe_attempt_key(record: dict) -> str:
    """Only delete keys derivable from this attempt's own immutable identity."""
    data = record.get("context") or {}
    attempt = re.fullmatch(
        r"o\.HarnessDraftBlobAttempt\.([0-9a-f]{32})", record.get("id") or ""
    )
    thread_id = data.get("thread_id")
    key = data.get("storage_key")
    if (
        not attempt
        or not isinstance(thread_id, str)
        or not re.fullmatch(r"n\.ChatThread\.[\w-]+", thread_id)
        or not isinstance(key, str)
    ):
        return ""
    storage = get_attachment_storage_service()
    for filename in ("draft.md", "draft.docx", "draft.pdf", "draft.pptx"):
        expected = storage.build_storage_key(
            f"draft-preparation/{thread_id}", attempt.group(1), filename
        )
        if key == expected:
            return key
    return ""


async def reconcile_draft_blob_attempts(*, limit: int = 50) -> int:
    """Tombstone and repeatedly delete abandoned attempt keys in bounded pages.

    A tombstone remains after deletion: a displaced process may finish an old
    write late, and a later pass must delete that key again. Published attempts
    are never candidates. No graph transaction spans a storage operation.
    """
    require_outside_graph_transaction()
    if not graph_transaction_available():
        return 0
    if not 1 <= limit <= 200:
        raise ValueError("Cleanup page limit must be between 1 and 200")
    database = get_prime_database()
    now = time.time()
    candidates = await database.find(
        "object",
        {
            "entity": HarnessDraftBlobAttempt._entity_name(),
            "context.state": {"$in": ["reserved", "abandoned"]},
            "$or": [
                {"context.next_cleanup_at": {"$lte": now}},
                {"context.next_cleanup_at": {"$exists": False}},
            ],
        },
        sort=[("context.next_cleanup_at", 1), ("id", 1)],
        limit=limit,
    )
    removed = 0
    for candidate in candidates:
        attempt_id = candidate.get("id", "")
        identity = (candidate.get("context") or {}).get("preparation_id", "")
        if not isinstance(identity, str) or not re.fullmatch(
            r"o\.HarnessDraftPreparationRecord\.[0-9a-f]{64}", identity
        ):
            logger.error(
                "Generated file cleanup refused an invalid preparation identity"
            )
            continue
        key = ""
        async with postgres_graph_transaction() as transaction:
            # Publisher locks preparation before attempt. Preserve this order.
            parent = await transaction.find_one_and_update(
                "object",
                {
                    "id": identity,
                    "entity": HarnessDraftPreparationRecord._entity_name(),
                },
                {"$set": {}},
            )
            attempt = await transaction.find_one_and_update(
                "object",
                {
                    "id": attempt_id,
                    "entity": HarnessDraftBlobAttempt._entity_name(),
                    "context.preparation_id": identity,
                    "context.state": {"$in": ["reserved", "abandoned"]},
                    "$or": [
                        {"context.next_cleanup_at": {"$lte": now}},
                        {"context.next_cleanup_at": {"$exists": False}},
                    ],
                },
                {"$set": {"context.next_cleanup_at": now + _RECHECK_SECONDS}},
            )
            if not attempt:
                continue
            data = attempt.get("context") or {}
            token = (parent or {}).get("_jv_claim")
            claim_digest = data.get("claim_digest")
            if not isinstance(claim_digest, str) or not re.fullmatch(
                r"[0-9a-f]{64}", claim_digest
            ):
                logger.error("Generated file cleanup refused an invalid claim identity")
                continue
            expiry = (parent or {}).get("_jv_claim_until")
            valid_expiry = type(expiry) in (int, float) and math.isfinite(expiry)
            active = (
                (parent or {}).get("context", {}).get("state") == "pending"
                and isinstance(token, str)
                and hashlib.sha256(token.encode()).hexdigest() == claim_digest
                and (not valid_expiry or expiry > now)
            )
            if active:
                continue
            key = _safe_attempt_key(attempt)
            if not key:
                logger.error("Generated file cleanup refused an invalid attempt key")
                continue
            await transaction.find_one_and_update(
                "object",
                {"id": attempt_id, "context.state": {"$in": ["reserved", "abandoned"]}},
                {"$set": {"context.state": "abandoned"}},
            )
        try:
            if await get_attachment_storage_service().delete_attachment(key):
                removed += 1
        except Exception:  # Keep the tombstone eligible for a later pass.
            logger.exception("Generated file cleanup failed; it will retry")
    return removed
