"""Additional native expiry boundaries for the sealed operation runtime."""

import asyncio
import copy
from datetime import datetime, timedelta

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.agentive import staging, staging_store
from app.agentive.services import staged_operation_apply, staging_apply
from app.models.nodes import Comment
from app.services.app_operations.registry import unregister_app_operations
from tests.contract.test_staged_app_operation_postgres import proposal

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


def advance_admission_clock(monkeypatch, sc):
    later = sc.expires_at + timedelta(seconds=1)

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return later.astimezone(tz) if tz else later.replace(tzinfo=None)

    monkeypatch.setattr(staged_operation_apply, "datetime", Later)


async def test_expired_exact_review_cannot_start_a_new_operation(
    postgres_raw_db, monkeypatch
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, ws, app, sc, calls = await proposal(monkeypatch)
        try:
            await staging.bless_token(user_id=actor.id, token=sc.token)
            stale = copy.deepcopy(sc)
            advance_admission_clock(monkeypatch, sc)
            result = await staging_apply.execute_blessed_change(
                user_id=actor.id, token=sc.token, staged=stale
            )
            assert result["consumed"] is False, result
            assert calls == []
            assert await Comment.find({}) == []
            assert (
                await postgres_raw_db.find(
                    "object", {"entity": "OperationExecutionReceipt"}
                )
                == []
            )
        finally:
            unregister_app_operations(ws.id, app.id)


async def test_expired_review_observes_its_exact_committed_completion(
    postgres_raw_db, monkeypatch
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, ws, app, sc, calls = await proposal(monkeypatch)
        try:
            await staging.bless_token(user_id=actor.id, token=sc.token)
            stale = copy.deepcopy(sc)
            first = await staging_apply.execute_blessed_change(
                user_id=actor.id, token=sc.token
            )
            assert first["consumed"] is True, first
            advance_admission_clock(monkeypatch, sc)
            staging._tokens.clear()

            def forbidden(*args):
                pytest.fail("An expired completed approval resolved a handler")

            monkeypatch.setattr(
                "app.services.app_operations.dispatch.resolve_handler", forbidden
            )
            result = await staging_apply.execute_blessed_change(
                user_id=actor.id, token=sc.token, staged=stale
            )
            assert result["consumed"] is True, result
            assert result["execute_result"]["operation_receipt"]["id"] == (
                first["execute_result"]["operation_receipt"]["id"]
            )
            assert calls == ["effect"]
            assert len(await Comment.find({})) == 1
        finally:
            unregister_app_operations(ws.id, app.id)


async def test_revocation_between_observation_and_effect_admission_wins(
    postgres_raw_db, monkeypatch
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, ws, app, sc, calls = await proposal(monkeypatch)
        entered, release = asyncio.Event(), asyncio.Event()
        original = staged_operation_apply._lock_decision

        async def wait_for_revocation(*args, **kwargs):
            if not kwargs["recovering"]:
                entered.set()
                await release.wait()
            return await original(*args, **kwargs)

        monkeypatch.setattr(
            staged_operation_apply, "_lock_decision", wait_for_revocation
        )
        applying = revoking = None
        try:
            await staging.bless_token(user_id=actor.id, token=sc.token)
            applying = asyncio.create_task(
                staging_apply.execute_blessed_change(
                    user_id=actor.id, token=sc.token, staged=copy.deepcopy(sc)
                )
            )
            await asyncio.wait_for(entered.wait(), timeout=15)
            revoking = asyncio.create_task(
                staging.revoke_token(user_id=actor.id, token=sc.token)
            )

            async def durable_revocation():
                # Synchronize on the committed authority fact. Waiting for the
                # whole API here also waits for post-commit linked notifications
                # and cold index DDL while this artificial barrier holds a writer.
                while True:
                    marker = await postgres_raw_db.get(
                        "object", staging_store._decision_record_id(sc.token)
                    )
                    if (marker or {}).get("context", {}).get("state") == "revoked":
                        return
                    await asyncio.sleep(0.02)

            await asyncio.wait_for(durable_revocation(), timeout=15)
            release.set()
            result = await asyncio.wait_for(applying, timeout=15)
            revoked = await asyncio.wait_for(revoking, timeout=15)
            assert revoked.state == "revoked", revoked
            assert result["consumed"] is False, result
            assert calls == []
            assert await Comment.find({}) == []
            assert (
                await postgres_raw_db.find(
                    "object", {"entity": "OperationExecutionReceipt"}
                )
                == []
            )
            current = await staging.get_token(sc.token)
            assert current.state == "revoked"
        finally:
            release.set()
            tasks = [t for t in (applying, revoking) if t is not None]
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            unregister_app_operations(ws.id, app.id)
