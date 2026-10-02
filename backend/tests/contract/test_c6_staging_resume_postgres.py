"""Postgres proof that an approved multi-op build resumes after a restart."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from jvspatial.core import Object
from jvspatial.core.context import GraphContext, set_default_context

from app.agentive import staging, staging_executors, staging_store
from app.agentive.services import staging_apply
from app.agentive.staging import StagingError
from app.contracts.operations import OperationIdentity, canonical_request_hash
from app.models.edges import CONTAINS
from app.models.nodes import App, Entry, Track
from app.services.app_operations.execution_receipts import (
    execute_operation_once,
    receipt_object_id,
)


@pytest.fixture
def postgres_graph_context(postgres_raw_db):
    """Bind jvspatial Object helpers to the isolated Postgres test database."""
    from jvspatial.core.context import _default_context_var

    token = set_default_context(GraphContext(database=postgres_raw_db))
    try:
        yield
    finally:
        _default_context_var.reset(token)


class C6ApprovedEffect(Object):
    """Durable test effect committed with its operation receipt."""

    revision: str = ""


@pytest.fixture(autouse=True)
def _clean_staging():
    staging._reset_for_tests()
    yield
    staging._reset_for_tests()


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_a08_batch_progress_survives_restart_without_duplicate_effects(
    authenticated_client, test_user, monkeypatch
) -> None:
    """A separate process resumes real App/Track/Entry work from Postgres."""
    token_effects = [
        {"kind": "create_app", "payload": {"name": "C6 restart app"}},
        {
            "kind": "create_track",
            "payload": {"title": "C6 restart track", "app_id": "{{app.id}}"},
        },
        {
            "kind": "create_entry",
            "payload": {"title": "C6 restart entry", "track_id": "{{track.id}}"},
        },
    ]
    user_id = getattr(test_user, "user_id", None) or test_user.id
    staged = await staging.create_staged_change(
        user_id=user_id,
        session_id="s-c6-a08",
        kind="batch",
        summary="Build C6 restart fixture",
        diff_human="Create one App, Track, and Entry",
        diff_machine={"op_count": len(token_effects)},
        payload={"operations": token_effects},
    )
    await staging.bless_token(user_id=user_id, token=staged.token)

    calls: list[str] = []
    fail_entry = True
    real_dispatch = staging_executors.dispatch

    async def dispatch_with_restart_boundary(*, user_id: str, kind: str, payload: dict):
        nonlocal fail_entry
        calls.append(kind)
        if kind == "create_entry" and fail_entry:
            return {"error": True, "message": "injected restart boundary"}
        return await real_dispatch(user_id=user_id, kind=kind, payload=payload)

    monkeypatch.setattr(staging_executors, "dispatch", dispatch_with_restart_boundary)
    from app.agentive.services.staging_apply import bless_and_execute

    first = await bless_and_execute(user_id=user_id, token=staged.token)

    assert first["consumed"] is False
    assert first["execute_result"]["error"] is True
    assert first["execute_result"]["completed"] == 2
    assert calls == ["batch", "create_app", "create_track", "create_entry"]
    persisted = await staging_store.load(staged.token)
    assert persisted is not None
    assert persisted.progress is not None
    assert persisted.progress["completed"] == 2

    # A fresh interpreter has no access to this process's token cache or
    # ContextVars. It must reload the blessed change, saved cursor, and prior
    # result ids from Postgres, then execute the real staging approval path.
    worker = """
import asyncio, json, os
from jvspatial.core.context import GraphContext, set_default_context
from jvspatial.db.factory import create_database
from app.agentive.services.staging_apply import bless_and_execute
from app.agentive.staging import get_token

async def main():
    database = create_database(db_type="postgres")
    set_default_context(GraphContext(database=database))
    token = os.environ["C6_A08_TOKEN"]
    user_id = os.environ["C6_A08_USER"]
    staged = await get_token(token)
    assert staged is not None and staged.progress["completed"] == 2
    result = await bless_and_execute(user_id=user_id, token=token)
    print(json.dumps({"pid": os.getpid(), "result": result}))
    await database.close()

asyncio.run(main())
"""
    child_env = {
        **os.environ,
        "C6_A08_TOKEN": staged.token,
        "C6_A08_USER": user_id,
    }
    child = subprocess.run(
        [sys.executable, "-c", worker],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
        env=child_env,
    )
    child_result = json.loads(child.stdout.strip().splitlines()[-1])
    assert child_result["pid"] != os.getpid()
    resumed = child_result["result"]
    assert resumed["consumed"] is True
    assert resumed["execute_result"]["completed"] == 3
    apps = await App.find({"context.name": "C6 restart app"})
    tracks = await Track.find({"context.title": "C6 restart track"})
    entries = await Entry.find({"context.title": "C6 restart entry"})
    assert len(apps) == len(tracks) == len(entries) == 1
    linked_tracks = await apps[0].nodes(edge=[CONTAINS], node=["Track"])
    linked_entries = await tracks[0].nodes(edge=[CONTAINS], node=["Entry"])
    assert [item.id for item in linked_tracks] == [tracks[0].id]
    assert [item.id for item in linked_entries] == [entries[0].id]
    # Successful consume is terminal and the durable staging row is removed;
    # exactly one of each rooted graph object proves the prior cursor/results
    # were sufficient for the separate process to finish.
    assert await staging_store.load(staged.token) is None


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_a07_corrected_approved_revision_executes_once_and_closes_states(
    postgres_raw_db, postgres_graph_context, monkeypatch
) -> None:
    """Approval retries preserve revision identity and terminal outcomes."""
    calls: list[str] = []

    async def apply_revision(
        *, request, user_id: str, kind: str, payload: dict, preferred_workspace_id
    ):
        revision = str(payload["revision"])
        calls.append(revision)
        if revision == "draft-1":
            return {
                "error": True,
                "error_code": "revision_conflict",
                "message": "The approved revision needs correction.",
            }
        identity = OperationIdentity.create(
            workspace_id="ws-c6-a07",
            app_id="n.App.c6-a07",
            operation_key="c6.approved_revision",
            principal_id=user_id,
            idempotency_key=str(payload["idempotency_key"]),
        )

        async def create_effect():
            effect = await C6ApprovedEffect.create(revision=revision)
            return {"effect_id": effect.id, "revision": revision}

        result = await execute_operation_once(
            identity=identity,
            request_hash=canonical_request_hash(payload),
            execute=create_effect,
            database=postgres_raw_db,
        )
        return {"revision": revision, "result": result.result}

    monkeypatch.setattr(staging_apply, "_dispatch_kind", apply_revision)
    first_revision = await staging.create_staged_change(
        user_id="u-c6-a07",
        session_id="s-c6-a07",
        kind="create_entry",
        summary="Apply draft revision 1",
        diff_human="Create corrected record",
        diff_machine={"revision": 1},
        payload={"revision": "draft-1", "idempotency_key": "a07-draft-1"},
    )
    refused = await staging_apply.bless_and_execute(
        user_id="u-c6-a07", token=first_revision.token
    )
    assert refused["consumed"] is False
    assert refused["execute_result"]["error_code"] == "revision_conflict"
    retained = await staging_store.load(first_revision.token)
    assert retained is not None and retained.last_error is not None

    await staging.revoke_token(user_id="u-c6-a07", token=first_revision.token)
    corrected = await staging.create_staged_change(
        user_id="u-c6-a07",
        session_id="s-c6-a07",
        kind="create_entry",
        summary="Apply corrected revision 2",
        diff_human="Create corrected record",
        diff_machine={"revision": 2},
        payload={"revision": "draft-2", "idempotency_key": "a07-draft-2"},
    )
    applied = await staging_apply.bless_and_execute(
        user_id="u-c6-a07", token=corrected.token
    )
    assert applied["consumed"] is True
    assert applied["execute_result"]["revision"] == "draft-2"
    identity = OperationIdentity.create(
        workspace_id="ws-c6-a07",
        app_id="n.App.c6-a07",
        operation_key="c6.approved_revision",
        principal_id="u-c6-a07",
        idempotency_key="a07-draft-2",
    )
    receipt = await postgres_raw_db.get("object", receipt_object_id(identity))
    assert receipt is not None
    assert receipt["context"]["status"] == "succeeded"
    effects = await postgres_raw_db.find(
        "object", {"entity": "C6ApprovedEffect", "context.revision": "draft-2"}
    )
    assert len(effects) == 1
    with pytest.raises(StagingError) as consumed:
        await staging_apply.bless_and_execute(user_id="u-c6-a07", token=corrected.token)
    assert consumed.value.code == "already_consumed"
    assert calls == ["draft-1", "draft-2"]

    expired = await staging.create_staged_change(
        user_id="u-c6-a07",
        session_id="s-c6-a07-expired",
        kind="create_entry",
        summary="Expired approval",
        diff_human="No effect",
        diff_machine={},
        payload={"revision": "expired"},
        ttl_seconds=-1,
    )
    with pytest.raises(StagingError) as expiry:
        await staging_apply.bless_and_execute(user_id="u-c6-a07", token=expired.token)
    assert expiry.value.code == "already_expired"

    cancelled = await staging.create_staged_change(
        user_id="u-c6-a07",
        session_id="s-c6-a07-cancelled",
        kind="create_entry",
        summary="Cancelled approval",
        diff_human="No effect",
        diff_machine={},
        payload={"revision": "cancelled"},
    )
    await staging.revoke_token(user_id="u-c6-a07", token=cancelled.token)
    with pytest.raises(StagingError) as cancellation:
        await staging_apply.bless_and_execute(user_id="u-c6-a07", token=cancelled.token)
    assert cancellation.value.code == "already_revoked"
