"""Decision races use shared facts, not an individual worker's cache."""

import asyncio

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.agentive import staging
from app.agentive.services import staging_apply
from app.agentive.staging import StagingError
from app.models.nodes import Entry
from tests.contract.test_staging_command_apply_postgres import proposal

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def test_terminal_closure_does_not_poison_enclosing_effect_transaction(
    postgres_raw_db, monkeypatch
):
    from app.agentive import staging_store
    from app.services.app_operations.transaction_scope import postgres_graph_transaction

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "44" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, track, sc = await proposal()
        async with postgres_graph_transaction():
            result = await staging_apply.bless_and_execute(
                user_id=auth.id, token=sc.token
            )
            assert result["consumed"] is True
            # The transient proposal row has been removed; a second notifier
            # must use the matching shared terminal marker without replay.
            await staging._push_state_and_record_closure(sc)
        assert len(await Entry.find({"track_id": track.id})) == 1
        assert (await staging_store.load_decision_state(sc.token, auth.id))[
            "state"
        ] == "consumed"


@pytest.mark.parametrize(
    "mismatch",
    [
        "user",
        "kind",
        "state",
        "target",
        "workspace",
        "payload",
        "review",
        "session",
        "key",
    ],
)
async def test_terminal_acknowledgment_cannot_change_decision(
    postgres_raw_db, monkeypatch, mismatch
):
    from dataclasses import replace

    from app.agentive.services.staging_decision_store import transition_staged_decision

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "44" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, track, sc = await proposal()
        await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        snapshot = replace(sc)
        target = "consumed"
        if mismatch == "user":
            snapshot.user_id = "another-principal"
        elif mismatch == "kind":
            snapshot.kind = "another-kind"
        elif mismatch == "state":
            snapshot.state = "revoked"
        elif mismatch == "workspace":
            snapshot.workspace_id = "another-workspace"
        elif mismatch == "payload":
            snapshot.payload = {**snapshot.payload, "title": "Unreviewed replacement"}
        elif mismatch == "review":
            snapshot.diff_machine = {"unreviewed": True}
        elif mismatch == "session":
            snapshot.session_id = "another-conversation"
        elif mismatch == "key":
            snapshot.idempotency_key = "another-intent"
        else:
            target = "blessed"
        with pytest.raises(StagingError):
            await transition_staged_decision(
                token=sc.token,
                user_id=auth.id,
                target=target,
                expected_kind=sc.kind,
                terminal_snapshot=snapshot,
            )
        assert len(await Entry.find({"track_id": track.id})) == 1


async def test_concurrent_consumption_has_one_terminal_transition(postgres_raw_db):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, _, sc = await proposal()
        await staging.bless_token(user_id=auth.id, token=sc.token)
        outcomes = await asyncio.gather(
            staging.consume_token(
                user_id=auth.id, token=sc.token, expected_kind=sc.kind
            ),
            staging.consume_token(
                user_id=auth.id, token=sc.token, expected_kind=sc.kind
            ),
            return_exceptions=True,
        )
        assert sum(isinstance(result, dict) for result in outcomes) == 1
        errors = [result for result in outcomes if isinstance(result, StagingError)]
        assert len(errors) == 1
        assert errors[0].code == "already_consumed"


async def test_consumer_sees_terminal_decision_if_live_row_disappears_before_lock(
    postgres_raw_db, monkeypatch
):
    """Force the stale-read window instead of relying on scheduler timing."""
    from jvspatial.db.postgres import PostgresTransaction

    waiting = asyncio.Event()
    resume = asyncio.Event()
    original = PostgresTransaction.find_one_and_update

    async def pause_loser(self, collection, query, update, *args, **kwargs):
        task = asyncio.current_task()
        if (
            task is not None
            and task.get_name() == "approval-race-loser"
            and collection == "object"
            and update == {"$set": {}}
        ):
            waiting.set()
            await asyncio.wait_for(resume.wait(), timeout=10)
        return await original(self, collection, query, update, *args, **kwargs)

    monkeypatch.setattr(PostgresTransaction, "find_one_and_update", pause_loser)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, _, sc = await proposal()
        await staging.bless_token(user_id=auth.id, token=sc.token)
        loser = asyncio.create_task(
            staging.consume_token(
                user_id=auth.id, token=sc.token, expected_kind=sc.kind
            ),
            name="approval-race-loser",
        )
        try:
            await asyncio.wait_for(waiting.wait(), timeout=10)
            winner = await staging.consume_token(
                user_id=auth.id, token=sc.token, expected_kind=sc.kind
            )
            assert isinstance(winner, dict)
        finally:
            resume.set()
        with pytest.raises(StagingError) as exc:
            await loser
        assert exc.value.code == "already_consumed"


async def test_stale_pending_cache_cannot_bless_a_revoked_token(postgres_raw_db):
    import copy

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, _, sc = await proposal()
        stale = copy.deepcopy(sc)
        await staging.revoke_token(user_id=auth.id, token=sc.token)
        staging._tokens[sc.token] = stale
        with pytest.raises(StagingError) as exc:
            await staging.bless_token(user_id=auth.id, token=sc.token)
        assert exc.value.code == "already_revoked"


async def test_rejection_after_graph_commit_cannot_claim_to_undo_effect(
    postgres_raw_db, monkeypatch
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "44" * 32)
    entered, unblock = asyncio.Event(), asyncio.Event()
    linked_decisions = []

    async def record_linked(**kwargs):
        linked_decisions.append(kwargs["decision"])

    monkeypatch.setattr(
        staging_apply, "_maybe_decide_linked_work_approval", record_linked
    )

    async def wait_after_commit(**kwargs):
        entered.set()
        await unblock.wait()

    monkeypatch.setattr(
        "app.services.notification_router.notify_entry_watchers", wait_after_commit
    )
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, track, sc = await proposal()
        applying = asyncio.create_task(
            staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        )
        await asyncio.wait_for(entered.wait(), timeout=10)
        try:
            with pytest.raises(StagingError) as exc:
                await staging.revoke_token(user_id=auth.id, token=sc.token)
            assert exc.value.code == "already_applied"
            assert "rejected" not in linked_decisions
            assert len(await Entry.find({"track_id": track.id})) == 1
        finally:
            unblock.set()
        result = await applying
        assert result["consumed"] is True
        assert result["staged_change"]["state"] == "consumed"


@pytest.mark.parametrize("expiry", ["not-a-date", "2026-10-14T00:00:00", None])
async def test_malformed_expiry_refuses_decision(postgres_raw_db, expiry):
    from app.agentive import staging_store

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, _, sc = await proposal()
        rows = await postgres_raw_db.find(
            "object",
            {
                "entity": staging_store.StagedChangeRecord._entity_name(),
                "context.token": sc.token,
            },
        )
        await postgres_raw_db.find_one_and_update(
            "object",
            {"id": rows[0]["id"]},
            {"$set": {"context.expires_at": expiry}},
        )
        with pytest.raises(StagingError) as exc:
            await staging.bless_token(user_id=auth.id, token=sc.token)
        assert exc.value.code == "approval_unavailable"
        row = await postgres_raw_db.get("object", rows[0]["id"])
        assert row["context"]["state"] == "pending"


async def age_proposal(database, sc):
    from datetime import datetime, timedelta, timezone

    from app.agentive import staging_store

    sc.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    rows = await database.find(
        "object",
        {
            "entity": staging_store.StagedChangeRecord._entity_name(),
            "context.token": sc.token,
        },
    )
    await database.find_one_and_update(
        "object",
        {"id": rows[0]["id"]},
        {
            "$set": {"context.expires_at": sc.expires_at.isoformat()},
        },
    )


async def test_pending_expiry_has_atomic_terminal_marker(postgres_raw_db):
    from app.agentive import staging_store

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, _, sc = await proposal()
        await age_proposal(postgres_raw_db, sc)
        observed = await staging.get_token(sc.token)
        assert observed.state == "expired"
        assert await staging_store.load_current(sc.token) is None
        marker = await staging_store.load_decision_state(sc.token, auth.id)
        assert marker["state"] == "expired"


async def test_expiry_preserves_dispatched_unknown_outcome_on_restart(postgres_raw_db):
    from app.agentive import staging_store
    from app.agentive.services.staging_execution import (
        claim_staging_execution,
        mark_staging_external_dispatch,
    )

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, sc = await proposal()
        await staging.bless_token(user_id=auth.id, token=sc.token)
        claim = await claim_staging_execution(
            token=sc.token,
            principal_id=auth.id,
            workspace_id=workspace.id,
            kind=sc.kind,
            payload=sc.payload,
        )
        await mark_staging_external_dispatch(claim)
        with pytest.raises(StagingError) as exc:
            await staging.consume_token(
                user_id=auth.id, token=sc.token, expected_kind=sc.kind
            )
        assert exc.value.code == "application_reconciliation_required"
        await age_proposal(postgres_raw_db, sc)
        staging._tokens.clear()
        observed = await staging.get_token(sc.token)
        assert observed.state == "blessed"
        assert observed.last_error["application_state"] == "reconciliation_required"
        assert await staging_store.load_current(sc.token) is not None
        assert await staging_store.load_decision_state(sc.token, auth.id) is None
        await staging_store.remove(sc.token)
        assert await staging_store.load_current(sc.token) is not None


async def test_expiry_after_commit_preserves_evidence_and_allows_completion(
    postgres_raw_db,
    monkeypatch,
):
    from app.agentive import staging_store

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "44" * 32)
    entered, unblock = asyncio.Event(), asyncio.Event()

    async def wait_after_commit(**kwargs):
        entered.set()
        await unblock.wait()

    monkeypatch.setattr(
        "app.services.notification_router.notify_entry_watchers", wait_after_commit
    )
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, track, sc = await proposal()
        applying = asyncio.create_task(
            staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        )
        await asyncio.wait_for(entered.wait(), timeout=10)
        try:
            await age_proposal(postgres_raw_db, sc)
            observed = await staging.get_token(sc.token)
            assert observed.state == "blessed"
            assert observed.last_error["application_state"] == "reconciliation_required"
            assert observed.last_error["applied"] is True
            assert await staging_store.load_current(sc.token) is not None
            assert await staging_store.load_decision_state(sc.token, auth.id) is None
        finally:
            unblock.set()
        result = await applying
        assert result["consumed"] is True
        assert len(await Entry.find({"track_id": track.id})) == 1
        assert (await staging_store.load_decision_state(sc.token, auth.id))[
            "state"
        ] == "consumed"


async def test_expired_applied_proposal_recovers_after_worker_loss(
    postgres_raw_db, monkeypatch
):
    from app.agentive import staging_store

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "44" * 32)

    async def lose_worker(**kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(
        "app.services.notification_router.notify_entry_watchers", lose_worker
    )
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, track, sc = await proposal()
        with pytest.raises(asyncio.CancelledError):
            await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        await age_proposal(postgres_raw_db, sc)
        staging._tokens.clear()
        await staging.get_pending_for_user(auth.id)
        recovered = await staging_apply.bless_and_execute(
            user_id=auth.id, token=sc.token
        )
        assert recovered["consumed"] is True
        assert recovered["execute_result"]["execution_receipt"]["replayed"] is True
        assert len(await Entry.find({"track_id": track.id})) == 1
        assert (await staging_store.load_decision_state(sc.token, auth.id))[
            "state"
        ] == "consumed"


async def test_stale_expiry_cannot_override_a_current_deadline(postgres_raw_db):
    from copy import copy
    from datetime import timedelta

    from app.agentive import staging_store

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, _, sc = await proposal()
        stale = copy(sc)
        stale.expires_at = stale.created_at - timedelta(seconds=1)
        stale.state = "expired"
        await staging._push_state_and_record_closure(stale)
        current = await staging_store.load_current(sc.token)
        assert current.state == "pending"
        assert await staging_store.load_decision_state(sc.token, auth.id) is None
        assert (await staging.get_token(sc.token)).state == "pending"


async def test_live_owner_survives_expiry_but_lost_undispatched_owner_does_not(
    postgres_raw_db,
):
    from app.agentive import staging_store
    from app.agentive.services.staging_execution import claim_staging_execution

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, sc = await proposal()
        await staging.bless_token(user_id=auth.id, token=sc.token)
        claim = await claim_staging_execution(
            token=sc.token,
            principal_id=auth.id,
            workspace_id=workspace.id,
            kind=sc.kind,
            payload=sc.payload,
        )
        await age_proposal(postgres_raw_db, sc)
        assert (await staging.get_token(sc.token)).state == "blessed"
        assert await staging_store.load_current(sc.token) is not None
        await postgres_raw_db.find_one_and_update(
            "object",
            {"id": claim.object_id},
            {
                "$set": {"context.expires_at": sc.expires_at.isoformat()},
            },
        )
        assert (await staging.get_token(sc.token)).state == "expired"
        assert await staging_store.load_current(sc.token) is None
        assert (await staging_store.load_decision_state(sc.token, auth.id))[
            "state"
        ] == "expired"


async def test_other_workers_expiry_marker_resolves_stale_cache(postgres_raw_db):
    from app.agentive import staging_store
    from app.agentive.services.staging_decision_store import transition_staged_decision

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, _, sc = await proposal()
        await age_proposal(postgres_raw_db, sc)
        resolved = await transition_staged_decision(
            token=sc.token, user_id=auth.id, target="expired"
        )
        await staging_store.remove(sc.token)
        assert sc.state == "pending"
        observed = await staging.get_token(sc.token)
        assert observed.state == "expired"
        assert observed.resolved_at == resolved.resolved_at


@pytest.mark.parametrize("target", ["consumed", "revoked"])
async def test_shared_terminal_reads_replace_another_workers_stale_cache(
    postgres_raw_db, target
):
    import copy

    from app.agentive import staging_store
    from app.agentive.services.staging_decision_store import transition_staged_decision

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, _, sc = await proposal()
        # The session list must see the same current facts as the inbox.
        rows = await postgres_raw_db.find("object", {"context.token": sc.token})
        await postgres_raw_db.find_one_and_update(
            "object",
            {"id": rows[0]["id"]},
            {"$set": {"context.session_id": "session-read-probe"}},
        )
        sc.session_id = "session-read-probe"
        await staging.bless_token(user_id=auth.id, token=sc.token)
        stale = copy.deepcopy(sc)
        resolved = await transition_staged_decision(
            token=sc.token, user_id=auth.id, target=target
        )
        await staging_store.remove(sc.token)
        staging._tokens[sc.token] = stale
        observed = await staging.get_token(sc.token)
        assert observed.state == target
        assert observed.resolved_at == resolved.resolved_at
        staging._tokens[sc.token] = copy.deepcopy(stale)
        assert sc.token not in {
            item.token for item in await staging.get_pending_for_user(auth.id)
        }
        assert await staging.list_pending_tokens(auth.id, "session-read-probe") == []
        assert (
            await staging.list_unresolved_for_session(auth.id, "session-read-probe")
            == []
        )


async def test_shared_bless_is_visible_without_cache_reset(postgres_raw_db):
    from app.agentive.services.staging_decision_store import transition_staged_decision

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, _, sc = await proposal()
        await transition_staged_decision(
            token=sc.token, user_id=auth.id, target="blessed"
        )
        assert sc.state == "pending"
        observed = await staging.get_token(sc.token)
        assert observed.state == "blessed"
        assert (
            next(
                item
                for item in await staging.get_pending_for_user(auth.id)
                if item.token == sc.token
            ).state
            == "blessed"
        )


@pytest.mark.parametrize("reader", ["token", "inbox", "terminal"])
async def test_native_read_outage_never_returns_cached_success(
    postgres_raw_db, monkeypatch, reader
):
    from contextlib import asynccontextmanager

    from app.agentive import staging_store
    from app.api.errors import QueryUnavailableError

    @asynccontextmanager
    async def unavailable(*args, **kwargs):
        raise OSError("Injected native read outage")
        yield  # pragma: no cover

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, _, sc = await proposal()
        monkeypatch.setattr(
            "app.services.app_operations.transaction_scope.postgres_graph_transaction",
            unavailable,
        )
        with pytest.raises(QueryUnavailableError) as exc:
            if reader == "token":
                await staging.get_token(sc.token)
            elif reader == "inbox":
                await staging.get_pending_for_user(auth.id)
            else:
                await staging_store.load_decision_state(sc.token, auth.id)
        assert exc.value.status_code == 503
        assert sc.state == "pending"
