"""Real staged commands commit their canonical receipt and decision together."""

import copy

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.agentive import staging, staging_store
from app.agentive.services import staging_apply, staging_decision_store
from app.agentive.services.app_operation_staging import prepare_agent_app_operation
from app.models.edges import HAS_APPLICATION_DEFINITION
from app.models.nodes import ApplicationDefinition, Comment
from app.services.app_operations import dispatch
from app.services.app_operations.registry import (
    register_app_operations,
    unregister_app_operations,
)
from app.services.application_definitions import definition_fingerprint
from tests.contract.test_operation_nested_result_postgres import operation_context
from tests.contract.test_operation_parent_result_postgres import child_reference

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def proposal(monkeypatch):
    staging._reset_for_tests()
    actor, workspace, app, visible, _ = await operation_context(role="editor")
    spec = {"key": "record", "kind": "execute", "handler_ref": "reference:record"}
    manifest = {"app": {"operations": [spec]}}
    definition = await ApplicationDefinition.create(
        app_id=app.id,
        workspace_id=workspace.id,
        revision=1,
        status="active",
        canonical_manifest=manifest,
        manifest_fingerprint=definition_fingerprint(manifest),
    )
    await app.connect(definition, edge=HAS_APPLICATION_DEFINITION)
    app.active_definition_id, app.active_definition_revision = definition.id, 1
    await app.save()
    calls = []

    async def handler(payload, context):
        calls.append("effect")
        child = await child_reference("comment", visible, actor)
        return {"created": {"kind": "comment", "id": child.id}}

    register_app_operations(workspace.id, app.id, "reference", [spec])
    monkeypatch.setattr(dispatch, "resolve_handler", lambda ref: handler)
    prepared = await prepare_agent_app_operation(
        {
            "app_id": app.id,
            "operation_key": "record",
            "input": {},
            "idempotency_key": "one-reviewed-intent",
        },
        principal_id=actor.id,
        workspace_id=workspace.id,
    )
    sc = await staging.create_staged_change(
        user_id=actor.id,
        workspace_id=workspace.id,
        session_id=None,
        **prepared,
    )
    return actor, workspace, app, sc, calls


async def test_actual_approval_commits_one_effect_and_both_receipts(
    postgres_raw_db, monkeypatch
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, ws, app, sc, calls = await proposal(monkeypatch)
        try:
            result = await staging_apply.bless_and_execute(
                user_id=actor.id, token=sc.token
            )
            assert result["consumed"] is True, result
            receipt = await postgres_raw_db.get(
                "object", result["execute_result"]["operation_receipt"]["id"]
            )
            marker = await postgres_raw_db.get(
                "object", staging_store._decision_record_id(sc.token)
            )
            assert (
                receipt["context"]["decision_intent_fingerprint"]
                == marker["context"]["intent_fingerprint"]
            )
            assert calls == ["effect"]
            assert len(await Comment.find({})) == 1
        finally:
            unregister_app_operations(ws.id, app.id)


async def test_terminal_decision_write_failure_rolls_back_actual_effect_and_operation_receipt(
    postgres_raw_db, monkeypatch
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, ws, app, sc, calls = await proposal(monkeypatch)

        original_write = staging_decision_store._write_locked_decision

        async def fail(*args, **kwargs):
            result = await original_write(*args, **kwargs)
            if kwargs["updates"]["state"] == "consumed":
                raise RuntimeError("Injected decision receipt failure after its writes")
            return result

        monkeypatch.setattr(staging_decision_store, "_write_locked_decision", fail)
        try:
            result = await staging_apply.bless_and_execute(
                user_id=actor.id, token=sc.token
            )
            assert result["consumed"] is False, result
            assert calls == [
                "effect"
            ]  # The failure reached the actual commit boundary.
            assert await Comment.find({}) == []
            rows = await postgres_raw_db.find(
                "object", {"entity": "OperationExecutionReceipt"}
            )
            assert rows == []
            assert (await staging.get_token(sc.token)).state == "blessed"
        finally:
            unregister_app_operations(ws.id, app.id)


@pytest.mark.parametrize("changed", ["none", "removed", "different_review"])
async def test_post_commit_notification_failure_recovers_original_without_handler(
    postgres_raw_db, monkeypatch, changed
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, ws, app, sc, calls = await proposal(monkeypatch)

        async def fail(*args):
            raise RuntimeError("Injected post-commit notification failure")

        await staging.bless_token(user_id=actor.id, token=sc.token)
        stale = copy.deepcopy(sc)
        monkeypatch.setattr(staging, "_push_state_and_record_closure", fail)
        try:
            first = await staging_apply.execute_blessed_change(
                user_id=actor.id, token=sc.token
            )
            assert first["consumed"] is True, first
            if changed == "removed":
                unregister_app_operations(ws.id, app.id)
            elif changed == "different_review":
                stale.payload["correlation_id"] = "different-proposal"
            staging._tokens.clear()

            def no_handler(*args):
                pytest.fail("Recovery resolved a command handler")

            monkeypatch.setattr(dispatch, "resolve_handler", no_handler)
            if changed == "different_review":
                with pytest.raises(staging.StagingError):
                    await staging_apply.execute_blessed_change(
                        user_id=actor.id, token=stale.token, staged=stale
                    )
            else:
                result = await staging_apply.execute_blessed_change(
                    user_id=actor.id, token=stale.token, staged=stale
                )
                assert result["consumed"] is True, result
                assert result["execute_result"]["output_disposition"] == (
                    "withheld" if changed == "removed" else "available"
                )
            assert calls == ["effect"]
            assert len(await Comment.find({})) == 1
        finally:
            unregister_app_operations(ws.id, app.id)


@pytest.mark.parametrize("legacy", [False, True])
async def test_other_proposal_or_legacy_receipt_is_not_current_approval_proof(
    postgres_raw_db, monkeypatch, legacy
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, ws, app, sc, calls = await proposal(monkeypatch)
        try:
            if legacy:
                first = await dispatch.invoke_app_operation(
                    user_id=actor.id,
                    workspace_id=ws.id,
                    app_id=app.id,
                    operation_key="record",
                    payload={},
                    idempotency_key=sc.payload["operation_idempotency_key"],
                )
                receipt_id = first["operation_receipt"]["id"]
                current = sc
            else:
                first = await staging_apply.bless_and_execute(
                    user_id=actor.id, token=sc.token
                )
                receipt_id = first["execute_result"]["operation_receipt"]["id"]
                current = await staging.create_staged_change(
                    user_id=actor.id,
                    workspace_id=ws.id,
                    session_id=None,
                    kind=sc.kind,
                    summary=sc.summary,
                    diff_human=sc.diff_human,
                    diff_machine=sc.diff_machine,
                    payload={**sc.payload, "idempotency_key": "different-review"},
                )
                assert current.token != sc.token
            await staging.bless_token(user_id=actor.id, token=current.token)
            before = await postgres_raw_db.get("object", receipt_id)
            with pytest.raises(
                staging.StagingError, match="decision proof|reviewed proposal"
            ):
                await staging_apply.execute_blessed_change(
                    user_id=actor.id, token=current.token
                )
            assert (await staging.get_token(current.token)).state == "blessed"
            assert calls == ["effect"]
            assert await postgres_raw_db.get("object", receipt_id) == before
        finally:
            unregister_app_operations(ws.id, app.id)


async def test_competing_workers_share_the_operation_receipt(
    postgres_raw_db, monkeypatch
):
    import asyncio

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, ws, app, sc, calls = await proposal(monkeypatch)
        await staging.bless_token(user_id=actor.id, token=sc.token)
        copies = [copy.deepcopy(sc), copy.deepcopy(sc)]
        try:
            results = await asyncio.gather(
                *[
                    staging_apply.execute_blessed_change(
                        user_id=actor.id, token=sc.token, staged=snapshot
                    )
                    for snapshot in copies
                ]
            )
            assert all(result["consumed"] for result in results), results
            assert calls == ["effect"]
            assert len(await Comment.find({})) == 1
        finally:
            unregister_app_operations(ws.id, app.id)


async def test_revoked_decision_cannot_dispatch_from_a_stale_cache(
    postgres_raw_db, monkeypatch
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, ws, app, sc, calls = await proposal(monkeypatch)
        await staging.bless_token(user_id=actor.id, token=sc.token)
        stale = copy.deepcopy(sc)
        await staging.revoke_token(user_id=actor.id, token=sc.token)
        try:
            result = await staging_apply.execute_blessed_change(
                user_id=actor.id, token=sc.token, staged=stale
            )
            assert result["consumed"] is False, result
            assert result["staged_change"]["state"] == "revoked"
            assert calls == []
            assert await Comment.find({}) == []
        finally:
            unregister_app_operations(ws.id, app.id)


async def test_cancelled_local_effect_rolls_back_before_same_intent_can_retry(
    postgres_raw_db, monkeypatch
):
    import asyncio

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, ws, app, sc, calls = await proposal(monkeypatch)
        await staging.bless_token(user_id=actor.id, token=sc.token)
        entered, release = asyncio.Event(), asyncio.Event()
        handler = dispatch.resolve_handler("reference:record")

        async def paused(payload, context):
            result = await handler(payload, context)
            entered.set()
            await release.wait()
            return result

        monkeypatch.setattr(dispatch, "resolve_handler", lambda ref: paused)
        task = asyncio.create_task(
            staging_apply.execute_blessed_change(user_id=actor.id, token=sc.token)
        )
        try:
            await asyncio.wait_for(entered.wait(), timeout=10)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert await Comment.find({}) == []
            assert (await staging.get_token(sc.token)).state == "blessed"
            assert (
                await postgres_raw_db.find(
                    "object", {"entity": "OperationExecutionReceipt"}
                )
                == []
            )
            release.set()
            result = await staging_apply.execute_blessed_change(
                user_id=actor.id, token=sc.token
            )
            assert result["consumed"] is True, result
            assert len(await Comment.find({})) == 1
            assert (
                len(calls) == 2
            )  # First local graph effect was physically rolled back.
        finally:
            release.set()
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            unregister_app_operations(ws.id, app.id)


async def test_operation_apply_refuses_an_ambient_effect_transaction(
    postgres_raw_db, monkeypatch
):
    from app.services.app_operations.transaction_scope import (
        OperationTransactionUnavailable,
        postgres_graph_transaction,
    )

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, ws, app, sc, calls = await proposal(monkeypatch)
        await staging.bless_token(user_id=actor.id, token=sc.token)
        try:
            async with postgres_graph_transaction():
                with pytest.raises(OperationTransactionUnavailable):
                    await staging_apply.execute_blessed_change(
                        user_id=actor.id, token=sc.token
                    )
            assert calls == []
        finally:
            unregister_app_operations(ws.id, app.id)


async def test_inconsistent_terminal_marker_refuses_fresh_dispatch(
    postgres_raw_db, monkeypatch
):
    from datetime import datetime, timedelta, timezone

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, ws, app, sc, calls = await proposal(monkeypatch)
        await staging.bless_token(user_id=actor.id, token=sc.token)
        now = datetime.now(timezone.utc)
        await staging_store.StagingDecisionRecord.create(
            id=staging_store._decision_record_id(sc.token),
            user_id=actor.id,
            state="consumed",
            kind=sc.kind,
            intent_fingerprint=staging_store.decision_intent_fingerprint(
                staging_store._sc_to_fields(sc)
            ),
            created_at=sc.created_at.isoformat(),
            expires_at=sc.expires_at.isoformat(),
            resolved_at=now.isoformat(),
            retained_until=(now + timedelta(hours=24)).isoformat(),
        )
        try:
            with pytest.raises(
                staging.StagingError, match="decision facts|reconciliation"
            ):
                await staging_apply.execute_blessed_change(
                    user_id=actor.id, token=sc.token
                )
            assert calls == []
            assert await Comment.find({}) == []
        finally:
            unregister_app_operations(ws.id, app.id)


# Fresh interpreters share only the disposable database and immutable proposal.
# They cannot pass through an inherited GraphContext, process cache or handler.
INDEPENDENT_WORKER = r"""
import asyncio, json, os, sys
from pathlib import Path
from jvspatial.core.context import GraphContext, scoped_default_context_async
from jvspatial.db import get_database_manager
from jvspatial.db.factory import create_database
from app.agentive import staging, staging_store
from app.agentive.services import staging_apply, staged_operation_apply
from app.models.nodes import Entry
from jvspatial.api.auth.models import User as AuthUser
from app.services.app_operations import dispatch
from app.services.app_operations.registry import register_app_operations
from tests.contract.test_operation_parent_result_postgres import child_reference

async def main():
    data = json.loads(Path(sys.argv[1]).read_text())
    sc = staging_store._record_to_sc(staging_store.StagedChangeRecord(id="o.WorkerSnapshot", **data["proposal"]))
    assert sc is not None and staging._tokens == {}
    database = create_database(db_type="postgres", dsn=os.environ["JVSPATIAL_POSTGRES_DSN"])
    get_database_manager().set_prime_database(database)
    try:
        async with scoped_default_context_async(GraphContext(database=database)):
            if sys.argv[2] == "execute":
                spec = {"key": "record", "kind": "execute", "handler_ref": "reference:record"}
                register_app_operations(sc.workspace_id, sc.payload["app_id"], "reference", [spec])
                async def handler(payload, context):
                    entry = await Entry.get(data["entry_id"])
                    actor = await AuthUser.get(sc.user_id)
                    child = await child_reference("comment", entry, actor)
                    return {"created": {"kind": "comment", "id": child.id}}
                dispatch.resolve_handler = lambda ref: handler
            else:
                def forbidden(*args):
                    raise AssertionError("Restart recovery entered a handler")
                dispatch.resolve_handler = forbidden
            result = await staging_apply.execute_blessed_change(user_id=sc.user_id, token=sc.token, staged=sc)
            print("__operation_worker_result__" + json.dumps({
                "consumed": result["consumed"],
                "receipt": result["execute_result"].get("operation_receipt"),
                "disposition": result["execute_result"].get("output_disposition"),
                "source": staged_operation_apply.__file__,
            }))
    finally:
        await database.close()

asyncio.run(main())
"""


async def independent_worker(snapshot, mode, output_dir):
    import asyncio
    import json
    import os
    import sys
    from pathlib import Path

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        INDEPENDENT_WORKER,
        str(snapshot),
        mode,
        cwd=str(Path(__file__).resolve().parents[2]),
        env=os.environ.copy(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    (output_dir / f"worker-{process.pid}.log").write_bytes(stderr)
    assert (
        process.returncode == 0
    ), "Independent worker failed; diagnostic retained in disposable test directory"
    lines = [
        line.split("__operation_worker_result__", 1)[1]
        for line in stdout.decode().splitlines()
        if line.startswith("__operation_worker_result__")
    ]
    assert len(lines) == 1
    result = json.loads(lines[0])
    assert (
        Path(result["source"]).resolve()
        == Path(__file__).resolve().parents[2]
        / "app/agentive/services/staged_operation_apply.py"
    )
    return result


async def test_restart_recovers_from_only_durable_decision_and_operation_proof(
    postgres_raw_db, monkeypatch, tmp_path
):
    import json

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, ws, app, sc, calls = await proposal(monkeypatch)
        await staging.bless_token(user_id=actor.id, token=sc.token)
        snapshot = tmp_path / "original-proposal.json"
        snapshot.write_text(json.dumps({"proposal": staging_store._sc_to_fields(sc)}))
        try:
            first = await staging_apply.execute_blessed_change(
                user_id=actor.id, token=sc.token
            )
            assert first["consumed"] is True
            result = await independent_worker(snapshot, "recover", tmp_path)
            assert result["consumed"] is True
            assert (
                result["disposition"] == "withheld"
            )  # No handler registry in the restarted reader.
            assert (
                result["receipt"]["id"]
                == first["execute_result"]["operation_receipt"]["id"]
            )
            assert calls == ["effect"]
            assert len(await Comment.find({})) == 1
        finally:
            unregister_app_operations(ws.id, app.id)


async def test_independent_worker_processes_cannot_duplicate_the_effect(
    postgres_raw_db, monkeypatch, tmp_path
):
    import asyncio
    import json

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, ws, app, sc, calls = await proposal(monkeypatch)
        await staging.bless_token(user_id=actor.id, token=sc.token)
        # Both child handlers resolve this existing Entry from the real database.
        from app.models.nodes import Entry

        entries = await Entry.find({})
        entry_id = next(
            entry.id for entry in entries if entry.title == "Visible source"
        )
        snapshot = tmp_path / "original-proposal.json"
        snapshot.write_text(
            json.dumps(
                {"proposal": staging_store._sc_to_fields(sc), "entry_id": entry_id}
            )
        )
        try:
            results = await asyncio.gather(
                *[independent_worker(snapshot, "execute", tmp_path) for _ in range(2)]
            )
            assert all(result["consumed"] for result in results)
            assert results[0]["receipt"]["id"] == results[1]["receipt"]["id"]
            assert calls == []  # Neither worker called the parent process's handler.
            assert len(await Comment.find({})) == 1
        finally:
            unregister_app_operations(ws.id, app.id)
