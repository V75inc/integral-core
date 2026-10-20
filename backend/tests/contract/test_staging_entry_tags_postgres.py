"""Exact tag-delta approvals join canonical Entry effects and recover by receipt."""

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.agentive import staging
from app.agentive.services import staging_apply, staging_execution
from app.agentive.staging import StagingError, create_staged_change
from app.services.agent_scope import current_scope_workspace_id
from tests.contract.test_staging_command_apply_postgres import proposal

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def receipt_key(monkeypatch):
    """Protect test receipts with an isolated synthetic key."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "aa" * 32)


async def tag_proposal(kind, tag_scope="track"):
    """Create real rooted records and freeze one exact tag delta."""
    from app.agentive.staging_executors import _call_endpoint
    from app.api.entries import add_tag_to_entry
    from app.services.tag_service import create_tag_for_scope

    auth, workspace, track, create = await proposal()
    created = await staging_apply.bless_and_execute(user_id=auth.id, token=create.token)
    assert created["consumed"], created
    entry_id = created["execute_result"]["entry"]["id"]
    from app.services.resource_location import locate_resource

    location = await locate_resource("track", track.id)
    owner = (
        {"track_id": track.id} if tag_scope == "track" else {"app_id": location.app_id}
    )
    tag = await create_tag_for_scope(
        auth.id,
        name="Reviewed",
        name_fold="reviewed",
        color="#123456",
        **owner,
    )
    reset = current_scope_workspace_id.set(workspace.id)
    try:
        if kind == "remove_entry_tag":
            result = await _call_endpoint(
                add_tag_to_entry, auth.id, entry_id=entry_id, tag_id=tag.id
            )
            assert not result.get("error"), result
        change = await create_staged_change(
            user_id=auth.id,
            session_id=None,
            kind=kind,
            summary="Change the reviewed tag",
            diff_human="One exact tag delta",
            diff_machine={},
            payload={"entry_id": entry_id, "tag_id": tag.id},
        )
    finally:
        current_scope_workspace_id.reset(reset)
    return auth, workspace, entry_id, tag, change


@pytest.mark.parametrize("kind", ["add_entry_tag", "remove_entry_tag"])
@pytest.mark.parametrize("tag_scope", ["track", "app"])
async def test_tag_completion_loss_uses_exact_receipt_without_replaying_delta(
    postgres_raw_db, monkeypatch, kind, tag_scope
):
    """Receipt recovery does not repeat a tag mutation or change its Entry revision."""
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, entry_id, _, change = await tag_proposal(kind, tag_scope)
        consume = staging_apply.consume_token

        async def unavailable(**kwargs):
            raise StagingError("completion_unavailable", "Injected lost tag completion")

        monkeypatch.setattr(staging_apply, "consume_token", unavailable)
        first = await staging_apply.bless_and_execute(
            user_id=auth.id, token=change.token
        )
        assert first["application_state"] == "reconciliation_required", first
        record = await postgres_raw_db.get(
            "object", staging_execution.execution_record_id(change.token)
        )
        assert record is not None and record["context"]["state"] == "applied"
        before = await postgres_raw_db.get("node", entry_id)
        before_edges = await postgres_raw_db.find("edge", {}, sort=[("id", 1)])
        outboxes = await postgres_raw_db.find(
            "object", {"entity": "OperationEventOutbox"}
        )
        staging._tokens.clear()
        monkeypatch.setattr(staging_apply, "consume_token", consume)
        recovered = await staging_apply.bless_and_execute(
            user_id=auth.id, token=change.token
        )
        assert recovered["consumed"], recovered
        assert recovered["execute_result"]["execution_receipt"]["replayed"] is True
        assert await postgres_raw_db.get("node", entry_id) == before
        assert await postgres_raw_db.find("edge", {}, sort=[("id", 1)]) == before_edges
        assert (
            await postgres_raw_db.find("object", {"entity": "OperationEventOutbox"})
            == outboxes
        )


@pytest.mark.parametrize("kind", ["add_entry_tag", "remove_entry_tag"])
async def test_tag_receipt_failure_rolls_back_entry_edge_and_event(
    postgres_raw_db, monkeypatch, kind
):
    """A tag delta cannot escape a failed exact-command receipt commit."""
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, entry_id, _, change = await tag_proposal(kind)
        before = await postgres_raw_db.get("node", entry_id)
        edges = await postgres_raw_db.find("edge", {}, sort=[("id", 1)])
        outboxes = await postgres_raw_db.find(
            "object", {"entity": "OperationEventOutbox"}
        )

        async def unavailable(**kwargs):
            raise RuntimeError("Injected failed tag receipt")

        monkeypatch.setattr(
            staging_execution, "record_staging_execution_result", unavailable
        )
        result = await staging_apply.bless_and_execute(
            user_id=auth.id, token=change.token
        )
        assert result["consumed"] is False, result
        assert result["execute_result"]["error"], result
        assert await postgres_raw_db.get("node", entry_id) == before
        assert await postgres_raw_db.find("edge", {}, sort=[("id", 1)]) == edges
        assert (
            await postgres_raw_db.find("object", {"entity": "OperationEventOutbox"})
            == outboxes
        )


async def stage_delta(auth, workspace, entry_id, tag_id, kind):
    """Stage a separate immutable delta under the authenticated workspace."""
    reset = current_scope_workspace_id.set(workspace.id)
    try:
        return await create_staged_change(
            user_id=auth.id,
            session_id=None,
            kind=kind,
            summary="Change one tag",
            diff_human="One reviewed tag delta",
            diff_machine={},
            payload={"entry_id": entry_id, "tag_id": tag_id},
        )
    finally:
        current_scope_workspace_id.reset(reset)


@pytest.mark.parametrize("kind", ["add_entry_tag", "remove_entry_tag"])
async def test_tag_recovery_reads_current_state_after_a_later_opposite_change(
    postgres_raw_db, monkeypatch, kind
):
    """A saved receipt never restores its old tag list over a later edit."""
    from app.agentive.staging_executors import _call_endpoint
    from app.api.entries import add_tag_to_entry, remove_tag_from_entry
    from app.models.nodes import Entry

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, entry_id, tag, change = await tag_proposal(kind)
        consume = staging_apply.consume_token

        async def unavailable(**kwargs):
            raise StagingError("completion_unavailable", "Lost completion")

        monkeypatch.setattr(staging_apply, "consume_token", unavailable)
        first = await staging_apply.bless_and_execute(
            user_id=auth.id, token=change.token
        )
        assert first["application_state"] == "reconciliation_required", first
        monkeypatch.setattr(staging_apply, "consume_token", consume)
        opposite = (
            remove_tag_from_entry if kind == "add_entry_tag" else add_tag_to_entry
        )
        reset = current_scope_workspace_id.set(workspace.id)
        try:
            later = await _call_endpoint(
                opposite, auth.id, entry_id=entry_id, tag_id=tag.id
            )
        finally:
            current_scope_workspace_id.reset(reset)
        assert not later.get("error"), later
        current = await postgres_raw_db.get("node", entry_id)
        staging._tokens.clear()
        recovered = await staging_apply.bless_and_execute(
            user_id=auth.id, token=change.token
        )
        assert recovered["consumed"], recovered
        assert recovered["execute_result"]["execution_receipt"]["replayed"]
        assert recovered["execute_result"]["entry"]["id"] == entry_id
        assert await postgres_raw_db.get("node", entry_id) == current
        assert (tag.id in (await Entry.get(entry_id)).tags) is (
            kind == "remove_entry_tag"
        )


@pytest.mark.parametrize("kind", ["add_entry_tag", "remove_entry_tag"])
async def test_foreign_taxonomy_tag_cannot_change_an_authorized_entry(
    postgres_raw_db, kind
):
    """Knowing a foreign tag ID does not grant authority over its taxonomy."""
    from app.services.tag_service import create_tag_for_scope

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, entry_id, _, _ = await tag_proposal(kind)
        other_auth, _, other_track, _ = await proposal()
        other_tag = await create_tag_for_scope(
            other_auth.id,
            name="Confidential taxonomy",
            name_fold="confidential taxonomy",
            color="#123456",
            track_id=other_track.id,
        )
        change = await stage_delta(auth, workspace, entry_id, other_tag.id, kind)
        before = await postgres_raw_db.get("node", entry_id)
        edges = await postgres_raw_db.find("edge", {}, sort=[("id", 1)])
        result = await staging_apply.bless_and_execute(
            user_id=auth.id, token=change.token
        )
        assert not result["consumed"], result
        assert result["execute_result"]["error"], result
        assert "Confidential taxonomy" not in str(result)
        assert await postgres_raw_db.get("node", entry_id) == before
        assert await postgres_raw_db.find("edge", {}, sort=[("id", 1)]) == edges


async def test_tag_event_is_offered_only_after_effect_and_receipt_commit(
    postgres_raw_db, monkeypatch
):
    """External event delivery never runs while holding Entry authority locks."""
    from app.services.app_operations import event_outbox
    from app.services.app_operations.transaction_scope import (
        OperationTransactionUnavailable,
        current_graph_transaction_database,
    )

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, _, _, change = await tag_proposal("add_entry_tag")
        offered = []

        async def deliver(*, outbox_id):
            with pytest.raises(OperationTransactionUnavailable):
                current_graph_transaction_database()
            receipt = await postgres_raw_db.get(
                "object", staging_execution.execution_record_id(change.token)
            )
            assert receipt["context"]["state"] == "applied"
            offered.append(outbox_id)

        monkeypatch.setattr(event_outbox, "deliver_operation_event", deliver)
        result = await staging_apply.bless_and_execute(
            user_id=auth.id, token=change.token
        )
        assert result["consumed"], result
        assert len(offered) == 1


@pytest.mark.parametrize("same_approval", [False, True])
async def test_independent_tag_delta_workers_preserve_both_assignments(
    postgres_raw_db, same_approval
):
    """Two pools serialize deltas without replacing the other worker's tag list."""
    import asyncio
    from contextvars import Context

    from jvspatial.db.factory import create_database

    from app.models.edges import TAGGED_WITH
    from app.models.nodes import Entry
    from app.services.tag_service import create_tag_for_scope

    second = create_database(db_type="postgres", min_size=1, max_size=1)
    tasks = []
    try:
        async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
            auth, workspace, entry_id, first_tag, first = await tag_proposal(
                "add_entry_tag"
            )
            entry = await Entry.get(entry_id)
            other = await create_tag_for_scope(
                auth.id,
                name="Second reviewed",
                name_fold="second reviewed",
                color="#123456",
                track_id=entry.track_id,
            )
            next_change = (
                first
                if same_approval
                else await stage_delta(
                    auth, workspace, entry_id, other.id, "add_entry_tag"
                )
            )
            before = await postgres_raw_db.get("node", entry_id)
            for collection in ("node", "edge", "object"):
                await second.find(collection, {"id": ""}, limit=1)

            async def apply(database, change):
                async with scoped_default_context_async(
                    GraphContext(database=database)
                ):
                    return await staging_apply.bless_and_execute(
                        user_id=auth.id, token=change.token
                    )

            tasks = [
                asyncio.create_task(apply(db, change), context=Context())
                for db, change in ((postgres_raw_db, first), (second, next_change))
            ]
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), 20
            )
            assert any(
                isinstance(result, dict) and result.get("consumed")
                for result in results
            ), results
            if not same_approval:
                assert all(
                    isinstance(result, dict) and result["consumed"]
                    for result in results
                ), results
            else:
                for result in results:
                    if isinstance(result, Exception):
                        assert isinstance(result, StagingError), result
                        assert result.code in {"already_executing", "already_consumed"}
                staging._tokens.clear()
                with pytest.raises(StagingError) as terminal:
                    await staging_apply.bless_and_execute(
                        user_id=auth.id, token=first.token
                    )
                assert terminal.value.code == "already_consumed"
                receipt = await postgres_raw_db.get(
                    "object", staging_execution.execution_record_id(first.token)
                )
                assert receipt["context"]["state"] == "applied"
            current = await postgres_raw_db.get("node", entry_id)
            expected_tags = (
                {first_tag.id} if same_approval else {first_tag.id, other.id}
            )
            assert set(current["context"]["tags"]) == expected_tags
            assert current["context"]["title"] == before["context"]["title"]
            assert current["context"]["record_revision"] == before["context"][
                "record_revision"
            ] + (1 if same_approval else 2)
            edges = await postgres_raw_db.find(
                "edge", {"entity": TAGGED_WITH._entity_name(), "source": entry_id}
            )
            assert len(edges) == len(expected_tags)
            assert {edge["target"] for edge in edges} == expected_tags
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await second.close()


async def test_revoked_member_cannot_apply_a_tag_delta(postgres_raw_db):
    """An earlier approval never substitutes for current workspace authority."""
    from tests.contract.test_release_admission_postgres import remove_membership

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, entry_id, _, change = await tag_proposal("add_entry_tag")
        await remove_membership(auth, workspace)
        before = await postgres_raw_db.get("node", entry_id)
        edges = await postgres_raw_db.find("edge", {}, sort=[("id", 1)])
        result = await staging_apply.bless_and_execute(
            user_id=auth.id, token=change.token
        )
        assert result["consumed"] is False, result
        assert result["execute_result"]["error"], result
        assert await postgres_raw_db.get("node", entry_id) == before
        assert await postgres_raw_db.find("edge", {}, sort=[("id", 1)]) == edges


async def test_detached_tag_taxonomy_cannot_authorize_delta(postgres_raw_db):
    """A copied Track field does not replace the taxonomy's containment edge."""
    from app.models.edges import CONTAINS

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, entry_id, tag, change = await tag_proposal("add_entry_tag")
        owners = await postgres_raw_db.find(
            "edge", {"entity": CONTAINS._entity_name(), "target": tag.id}
        )
        assert len(owners) == 1
        await postgres_raw_db.delete("edge", owners[0]["id"])
        before = await postgres_raw_db.get("node", entry_id)
        edges = await postgres_raw_db.find("edge", {}, sort=[("id", 1)])
        result = await staging_apply.bless_and_execute(
            user_id=auth.id, token=change.token
        )
        assert not result["consumed"], result
        assert result["execute_result"]["error"], result
        assert await postgres_raw_db.get("node", entry_id) == before
        assert await postgres_raw_db.find("edge", {}, sort=[("id", 1)]) == edges


async def test_separate_processes_cannot_repeat_one_tag_approval(postgres_raw_db):
    """Fresh interpreter caches still share one durable exact-command outcome."""
    import asyncio
    import json
    import os
    import sys

    worker = r"""
import asyncio, json, sys
from jvspatial.core.context import GraphContext, scoped_default_context_async
from jvspatial.db.factory import create_database
from app.api import entries
from app.agentive.services import staging_apply
from app.agentive.staging import StagingError
from app.services.app_operations import event_outbox

async def main():
    database = create_database(db_type="postgres", min_size=1, max_size=1)
    try:
        async with scoped_default_context_async(GraphContext(database=database)):
            for collection in ("node", "edge", "object"):
                await database.find(collection, {"id": ""}, limit=1)
            # Keep this claim/effect probe independent of external event delivery;
            # the real outbox intent still commits with the mutation and receipt.
            async def defer_delivery(**kwargs):
                return None
            event_outbox.deliver_operation_event = defer_delivery
            print("READY", flush=True)
            await asyncio.to_thread(sys.stdin.readline)
            try:
                result = await staging_apply.bless_and_execute(
                    user_id=sys.argv[1], token=sys.argv[2]
                )
                print(json.dumps({"consumed": bool(result.get("consumed")),
                                  "state": result.get("application_state"),
                                  "execute_result": result.get("execute_result")}), flush=True)
            except StagingError as exc:
                print(json.dumps({"refused": exc.code}), flush=True)
    finally:
        await database.close()
asyncio.run(main())
"""
    processes = []
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, entry_id, tag, change = await tag_proposal("add_entry_tag")
        before = await postgres_raw_db.get("node", entry_id)
        try:
            for _ in range(2):
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-c",
                    worker,
                    auth.id,
                    change.token,
                    # The parent's test_in_memory vector driver cannot cross a
                    # process boundary. This probe owns only approval effects.
                    env={
                        **os.environ,
                        "TESTING": "1",
                        "EMBEDDING_STORE_DRIVER": "null",
                    },
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                processes.append(process)
            ready = await asyncio.wait_for(
                asyncio.gather(*(p.stdout.readline() for p in processes)), 30
            )
            assert ready == [b"READY\n", b"READY\n"], ready
            outputs = await asyncio.wait_for(
                asyncio.gather(*(p.communicate(b"go\n") for p in processes)), 40
            )
            assert all(p.returncode == 0 for p in processes), [
                stderr.decode() for _, stderr in outputs
            ]
            results = [json.loads(stdout) for stdout, _ in outputs]
            assert any(result.get("consumed") for result in results), results
            assert all(
                result.get("consumed")
                or result.get("refused") in {"already_executing", "already_consumed"}
                or result.get("state") == "in_progress"
                for result in results
            ), results
            current = await postgres_raw_db.get("node", entry_id)
            assert current["context"]["tags"] == [tag.id]
            assert (
                current["context"]["record_revision"]
                == before["context"]["record_revision"] + 1
            )
            receipt = await postgres_raw_db.get(
                "object", staging_execution.execution_record_id(change.token)
            )
            assert receipt["context"]["state"] == "applied"
        finally:
            for process in processes:
                if process.returncode is None:
                    process.kill()
            await asyncio.gather(*(process.wait() for process in processes))
