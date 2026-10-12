"""Pending terminal CAS orders independent approval and expiration writers."""

import asyncio
from contextvars import Context
from datetime import datetime, timezone

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async
from jvspatial.db.factory import create_database

from app.services import approval_executor, approval_ttl
from tests.contract.test_approved_effect_postgres import intent

pytestmark = [pytest.mark.asyncio, pytest.mark.contract, pytest.mark.postgres]


@pytest.mark.parametrize("first", ["approve", "expire"])
async def test_expiry_and_effect_have_one_ordered_terminal(
    first, postgres_raw_db, monkeypatch
):
    """Only the first held pending claim wins; expiry never overwrites effects."""
    from app.services.app_operations import event_outbox

    second = create_database(db_type="postgres", min_size=1, max_size=1)
    held, release, following = asyncio.Event(), asyncio.Event(), asyncio.Event()
    tasks = []
    try:
        async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
            owner, _, _, track, _, _, _, approval_id, _ = await intent()
            for collection in ("node", "edge", "object"):
                await second.find(collection, {"id": ""}, limit=1)
            before = await postgres_raw_db.find(
                "node", {"entity": "Entry", "context.track_id": track.id}
            )
            monkeypatch.setattr(
                approval_ttl,
                "utc_now",
                lambda: datetime(2099, 1, 1, tzinfo=timezone.utc),
            )
            if first == "approve":
                original = approval_executor.WRITE_ACTION_DISPATCH["entry.create"]

                async def hold_effect(**kwargs):
                    result = await original(**kwargs)
                    held.set()
                    await asyncio.wait_for(release.wait(), timeout=15)
                    return result

                monkeypatch.setitem(
                    approval_executor.WRITE_ACTION_DISPATCH, "entry.create", hold_effect
                )
            else:
                original = event_outbox.insert_revision_event

                async def hold_expiration(**kwargs):
                    result = await original(**kwargs)
                    if (
                        kwargs["namespace"] == "approval-expiration-v1"
                        and kwargs["subject_id"] == approval_id
                    ):
                        held.set()
                        await asyncio.wait_for(release.wait(), timeout=15)
                    return result

                monkeypatch.setattr(
                    event_outbox, "insert_revision_event", hold_expiration
                )

            async def run(kind, db, signal=None):
                async with scoped_default_context_async(GraphContext(database=db)):
                    if signal:
                        signal.set()
                    if kind == "approve":
                        return await approval_executor.execute_approval(
                            approval_id=approval_id, approver_user_id=owner.id
                        )
                    return await approval_ttl.expire_old_approvals()

            tasks.append(
                asyncio.create_task(run(first, postgres_raw_db), context=Context())
            )
            await asyncio.wait_for(held.wait(), timeout=15)
            tasks.append(
                asyncio.create_task(
                    run(
                        "expire" if first == "approve" else "approve", second, following
                    ),
                    context=Context(),
                )
            )
            await asyncio.wait_for(following.wait(), timeout=15)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(tasks[1]), timeout=0.15)
            release.set()
            await asyncio.wait_for(tasks[0], timeout=15)
            first_rows = await postgres_raw_db.find(
                "node", {"entity": "Approval", "id": approval_id}, limit=2
            )
            assert len(first_rows) == 1
            assert first_rows[0]["context"]["status"] == (
                "approved" if first == "approve" else "expired"
            ), first_rows
            if first == "approve":
                await asyncio.wait_for(tasks[1], timeout=15)
                # Other session-owned expired candidates do not decide this intent.
            else:
                with pytest.raises(approval_executor.ApprovalAlreadyDecided):
                    await asyncio.wait_for(tasks[1], timeout=15)
            row = await postgres_raw_db.get("node", approval_id)
            assert row["context"]["status"] == (
                "approved" if first == "approve" else "expired"
            )
            after = await postgres_raw_db.find(
                "node", {"entity": "Entry", "context.track_id": track.id}
            )
            assert len(after) == len(before) + (1 if first == "approve" else 0)
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await second.close()
