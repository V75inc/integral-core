"""Approved Entry effects and restart recovery through the actual Core API."""

import asyncio
import uuid

import pytest
from jvspatial.api.auth.models import User as AuthUser
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.agentive import staging
from app.agentive.services import staging_apply
from app.agentive.services.staging_execution import execution_record_id
from app.agentive.staging import StagingError, create_staged_change
from app.models.edges import CONTAINS, IS_MEMBER_OF, OWNS
from app.models.nodes import App, Entry, Track, User, Workspace
from app.services.agent_scope import current_scope_workspace_id
from app.services.app_graph import (
    catalog_app,
    catalog_track,
    catalog_user,
    catalog_workspace,
    ensure_integral_app_graph,
)

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def proposal():
    suffix = uuid.uuid4().hex
    await ensure_integral_app_graph(include_library=False)
    auth = await AuthUser.create(
        email=f"staging-{suffix}@example.com", password_hash="fixture"
    )
    user = await User.create(user_id=auth.id, display_name="Staging QA")
    await catalog_user(user)
    workspace = await Workspace.create(name="Staging QA", kind="organization")
    await catalog_workspace(workspace)
    await user.connect(workspace, edge=IS_MEMBER_OF, role="owner")
    app = await App.create(name="Staging QA", workspace_id=workspace.id)
    await catalog_app(app)
    await user.connect(app, edge=OWNS)
    track = await Track.create(title="Staging QA", workspace_id=workspace.id)
    await catalog_track(track)
    await user.connect(track, edge=OWNS)
    await app.connect(track, edge=CONTAINS)
    reset = current_scope_workspace_id.set(workspace.id)
    try:
        sc = await create_staged_change(
            user_id=auth.id,
            session_id=None,
            kind="create_entry",
            summary="Create one entry",
            diff_human="Create one entry",
            diff_machine={"op": "create_entry"},
            payload={"track_id": track.id, "title": "Exactly once"},
        )
    finally:
        current_scope_workspace_id.reset(reset)
    return auth, workspace, track, sc


async def test_create_commits_effect_and_receipt_before_completion(
    postgres_raw_db, monkeypatch
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, track, sc = await proposal()
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert result["consumed"] is True, result
        assert result["execute_result"]["execution_receipt"]["status"] == "applied"
        entries = await Entry.find({"track_id": track.id})
        assert len(entries) == 1
        assert result["execute_result"]["entry"]["id"] == entries[0].id
        record = await postgres_raw_db.get("object", execution_record_id(sc.token))
        assert record["context"]["state"] == "applied"


async def test_failed_completion_reloads_and_reconciles_without_create_replay(
    postgres_raw_db, monkeypatch
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, track, sc = await proposal()
        original = staging_apply.consume_token

        async def fail(**kwargs):
            raise StagingError("completion_unavailable", "Injected completion failure")

        monkeypatch.setattr(staging_apply, "consume_token", fail)
        first = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert first.get("application_state") == "reconciliation_required", first
        first_id = first["execute_result"]["entry"]["id"]
        staging._tokens.clear()
        monkeypatch.setattr(staging_apply, "consume_token", original)
        second = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert second["consumed"] is True, second
        assert second["execute_result"]["execution_receipt"]["replayed"] is True
        assert second["execute_result"]["entry"]["id"] == first_id
        assert [entry.id for entry in await Entry.find({"track_id": track.id})] == [
            first_id
        ]


async def test_projection_failure_after_commit_uses_current_readback(
    postgres_raw_db, monkeypatch
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    from app.api import entries as api

    original = api._project_entry_computed
    calls = 0

    async def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("Injected response projection failure")
        return await original(*args, **kwargs)

    monkeypatch.setattr(api, "_project_entry_computed", fail_once)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, track, sc = await proposal()
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert result["consumed"] is True, result
        assert result["execute_result"]["execution_receipt"]["replayed"] is True
        assert len(await Entry.find({"track_id": track.id})) == 1
        assert calls == 2


async def test_cancel_after_graph_commit_cannot_replay_create(
    postgres_raw_db, monkeypatch
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    entered = asyncio.Event()
    calls = 0

    async def blocked_notification(**kwargs):
        nonlocal calls
        calls += 1
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(
        "app.services.notification_router.notify_entry_watchers", blocked_notification
    )
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, track, sc = await proposal()
        executing = asyncio.create_task(
            staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        )
        await asyncio.wait_for(entered.wait(), timeout=10)
        executing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await executing
        staging._tokens.clear()
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert result["consumed"] is True, result
        assert result["execute_result"]["execution_receipt"]["replayed"] is True
        assert len(await Entry.find({"track_id": track.id})) == 1
        assert calls == 1


async def test_receipt_failure_rolls_back_actual_entry_and_allows_one_retry(
    postgres_raw_db, monkeypatch
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    from app.agentive.services import staging_execution

    original = staging_execution.record_staging_execution_result

    async def fail(**kwargs):
        raise OSError("Injected receipt write failure")

    monkeypatch.setattr(staging_execution, "record_staging_execution_result", fail)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, track, sc = await proposal()
        first = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert first["consumed"] is False, first
        assert await Entry.find({"track_id": track.id}) == []
        receipt = await postgres_raw_db.get("object", execution_record_id(sc.token))
        assert receipt["context"]["state"] == "ready"
        monkeypatch.setattr(
            staging_execution, "record_staging_execution_result", original
        )
        second = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert second["consumed"] is True, second
        assert len(await Entry.find({"track_id": track.id})) == 1


async def test_separate_worker_cache_cannot_dispatch_the_same_create(
    postgres_raw_db, monkeypatch
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    entered, unblock = asyncio.Event(), asyncio.Event()
    original = staging_apply._dispatch_kind
    executions = 0

    async def block_first(**kwargs):
        nonlocal executions
        executions += 1
        entered.set()
        await unblock.wait()
        return await original(**kwargs)

    monkeypatch.setattr(staging_apply, "_dispatch_kind", block_first)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, track, sc = await proposal()
        first = asyncio.create_task(
            staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        )
        await asyncio.wait_for(entered.wait(), timeout=10)
        # Rehydrate a separate StagedChange, as another process would. The
        # process-local executing flag is absent from the durable staging row.
        staging._tokens.pop(sc.token)
        try:
            with pytest.raises(StagingError) as exc:
                await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
            assert exc.value.code == "already_executing"
        finally:
            unblock.set()
        completed = await first
        assert completed["consumed"] is True
        assert completed["staged_change"]["state"] == "consumed"
        assert executions == 1
        assert len(await Entry.find({"track_id": track.id})) == 1


async def test_reconciled_result_obeys_current_entry_exclusion(
    postgres_raw_db, monkeypatch
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    from app.models.edges import COLLABORATES_ON, EXCLUDED_FROM

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        _, workspace, track, original_sc = await proposal()
        # Create as an ordinary editor, so protected owner governance is not
        # confused with the direct exclusion contract being exercised.
        auth = await AuthUser.create(
            email=f"reader-{uuid.uuid4().hex}@example.com", password_hash="fixture"
        )
        reader = await User.create(user_id=auth.id, display_name="Entry editor")
        await catalog_user(reader)
        await reader.connect(workspace, edge=IS_MEMBER_OF, role="member")
        await reader.connect(track, edge=COLLABORATES_ON, role="editor")
        reset = current_scope_workspace_id.set(workspace.id)
        try:
            sc = await create_staged_change(
                user_id=auth.id,
                session_id=None,
                kind="create_entry",
                summary="Create",
                diff_human="Create",
                diff_machine={},
                payload=original_sc.payload,
            )
        finally:
            current_scope_workspace_id.reset(reset)
        original = staging_apply.consume_token

        async def fail(**kwargs):
            raise StagingError("completion_unavailable", "Injected completion failure")

        monkeypatch.setattr(staging_apply, "consume_token", fail)
        first = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        entry_id = first["execute_result"]["entry"]["id"]
        entry = await Entry.get(entry_id)
        await reader.connect(entry, edge=EXCLUDED_FROM)
        staging._tokens.clear()
        monkeypatch.setattr(staging_apply, "consume_token", original)
        second = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        result = second["execute_result"]
        assert second["consumed"] is True, second
        assert result["result_access"] == "withheld"
        assert entry_id not in str(result)
        assert len(await Entry.find({"track_id": track.id})) == 1


async def test_update_completion_recovery_does_not_increment_revision_again(
    postgres_raw_db, monkeypatch
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, track, _ = await proposal()
        from app.services.entry_create import create_entry_in_track

        entry = await create_entry_in_track(
            track=track, user_id=auth.id, workspace_id=workspace.id, title="Before"
        )
        reset = current_scope_workspace_id.set(workspace.id)
        try:
            sc = await create_staged_change(
                user_id=auth.id,
                session_id=None,
                kind="update_entry",
                summary="Update",
                diff_human="Rename",
                diff_machine={},
                payload={
                    "entry_id": entry.id,
                    "title": "After",
                    "expected_record_revision": entry.record_revision,
                },
            )
        finally:
            current_scope_workspace_id.reset(reset)
        original = staging_apply.consume_token

        async def fail(**kwargs):
            raise StagingError("completion_unavailable", "Injected completion failure")

        monkeypatch.setattr(staging_apply, "consume_token", fail)
        first = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert first.get("application_state") == "reconciliation_required", first
        applied = await Entry.get(entry.id)
        assert applied.title == "After"
        revision = applied.record_revision
        staging._tokens.clear()
        monkeypatch.setattr(staging_apply, "consume_token", original)
        second = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert second["consumed"] is True, second
        assert second["execute_result"]["execution_receipt"]["replayed"] is True
        assert (await Entry.get(entry.id)).record_revision == revision


async def test_update_receipt_failure_rolls_back_fields_and_revision(
    postgres_raw_db, monkeypatch
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    from app.agentive.services import staging_execution

    receipt_attempts = []

    async def fail(**kwargs):
        receipt_attempts.append(kwargs["result"])
        raise OSError("Injected receipt storage failure")

    monkeypatch.setattr(staging_execution, "record_staging_execution_result", fail)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, track, _ = await proposal()
        from app.services.entry_create import create_entry_in_track

        entry = await create_entry_in_track(
            track=track, user_id=auth.id, workspace_id=workspace.id, title="Before"
        )
        revision = entry.record_revision
        reset = current_scope_workspace_id.set(workspace.id)
        try:
            sc = await create_staged_change(
                user_id=auth.id,
                session_id=None,
                kind="update_entry",
                summary="Update",
                diff_human="Rename",
                diff_machine={},
                payload={"entry_id": entry.id, "title": "After"},
            )
        finally:
            current_scope_workspace_id.reset(reset)
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert result["consumed"] is False, result
        assert len(receipt_attempts) == 1
        restored = await Entry.get(entry.id)
        assert restored.title == "Before"
        assert restored.record_revision == revision


async def test_graph_principal_alias_uses_the_same_current_identity(
    postgres_raw_db, monkeypatch
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, track, original_sc = await proposal()
        user = await User.find_one({"user_id": auth.id})
        reset = current_scope_workspace_id.set(workspace.id)
        try:
            sc = await create_staged_change(
                user_id=user.id,
                session_id=None,
                kind="create_entry",
                summary="Create",
                diff_human="Create",
                diff_machine={},
                payload=original_sc.payload,
            )
        finally:
            current_scope_workspace_id.reset(reset)
        result = await staging_apply.bless_and_execute(user_id=user.id, token=sc.token)
        assert result["consumed"] is True, result
        assert len(await Entry.find({"track_id": track.id})) == 1


@pytest.mark.parametrize("outcome", ["returned", "failed", "cancelled"])
async def test_hook_intent_preserves_behavior_and_never_replays_unknown_outcome(
    postgres_raw_db, monkeypatch, outcome
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    from app.services.hooks import registry, tool_dispatch

    calls = []
    entered = asyncio.Event()

    async def handler(payload, ctx):
        calls.append(payload)
        entered.set()
        if outcome == "cancelled":
            await asyncio.Event().wait()
        if outcome == "failed":
            raise RuntimeError("Hook outcome unknown")
        return {"ok": True}

    monkeypatch.setattr(tool_dispatch, "resolve_handler", lambda ref: handler)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, track, sc = await proposal()
        registry.register_workspace_tools(
            workspace.id,
            "qualification",
            [
                {
                    "key": "save_probe",
                    "handler_ref": "qualification:save_probe",
                }
            ],
        )
        registry.register_workspace_hooks(
            workspace.id,
            "qualification",
            [
                {
                    "key": "save_probe",
                    "point": "entry.create",
                    "mode": "tool",
                    "tool": "save_probe",
                    "match": {},
                }
            ],
        )
        try:
            if outcome == "cancelled":
                applying = asyncio.create_task(
                    staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
                )
                await asyncio.wait_for(entered.wait(), timeout=10)
                applying.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await applying
            else:
                result = await staging_apply.bless_and_execute(
                    user_id=auth.id, token=sc.token
                )
                assert result["consumed"] is (outcome == "returned"), result
            record = await postgres_raw_db.get("object", execution_record_id(sc.token))
            assert record["context"]["state"] == (
                "applied" if outcome == "returned" else "dispatched"
            )
            assert len(calls) == 1
            staging._tokens.clear()
            if outcome == "returned":
                assert len(await Entry.find({"track_id": track.id})) == 1
            elif outcome == "failed":
                second = await staging_apply.bless_and_execute(
                    user_id=auth.id, token=sc.token
                )
                assert second["consumed"] is False, second
                assert second["execute_result"]["applied"] is True
                assert len(await Entry.find({"track_id": track.id})) == 1
            else:
                with pytest.raises(StagingError) as exc:
                    await staging_apply.bless_and_execute(
                        user_id=auth.id, token=sc.token
                    )
                assert exc.value.code == "application_reconciliation_required"
                assert await Entry.find({"track_id": track.id}) == []
            assert len(calls) == 1
        finally:
            registry.clear_workspace_registrations(workspace.id)
