"""Persist an uncommitted build batch.

The row is an Object (I-GRAPH-02), not a graph Node. Staging keeps the
in-memory batch and calls this so agentive code does not create records.
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.models.open_build_batch import BATCH_SCHEMA_REVISION, OpenBuildBatch


async def upsert_open_build_batch(
    *,
    user_id: str,
    session_id: str,
    label: str,
    ops: List[Dict[str, Any]],
    created_at: str,
    auto_continuation_attempts: int,
) -> None:
    """Create or replace the stored batch for one user and session."""
    found = list(
        await OpenBuildBatch.find(
            {"context.user_id": user_id, "context.session_id": session_id}
        )
    )
    row = found[0] if found else None
    if row is None:
        await OpenBuildBatch.create(
            user_id=user_id,
            session_id=session_id,
            schema_revision=BATCH_SCHEMA_REVISION,
            label=label,
            ops=ops,
            created_at=created_at,
            auto_continuation_attempts=auto_continuation_attempts,
        )
        return
    row.schema_revision = BATCH_SCHEMA_REVISION
    row.label = label
    row.ops = ops
    row.auto_continuation_attempts = auto_continuation_attempts
    await row.save()
