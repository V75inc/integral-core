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
from app.models.edges import CONTAINS
from app.models.nodes import App, Entry, Track


@pytest.fixture(autouse=True)
def isolated_receipt_key(monkeypatch):
    """Supply a test-only key for encrypted durable command receipts."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "55" * 32)


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
    # The durable batch coordinator dispatches each canonical command directly.
    assert calls == ["create_app", "create_track", "create_entry"]
    persisted = await staging_store.load(staged.token)
    assert persisted is not None
    from jvspatial.core.context import get_default_context

    from app.agentive.services.staging_execution import execution_record_id

    for index in range(2):
        receipt = await get_default_context().database.get(
            "object", execution_record_id(f"{staged.token}:step:{index}")
        )
        assert receipt is not None
        assert receipt["context"]["state"] == "applied"
        assert receipt["context"]["result_ciphertext"]

    # A fresh interpreter has no access to this process's token cache or
    # ContextVars. It must reload the blessed change and durable step receipts
    # from Postgres, then execute the real staging approval path.
    worker = """
import asyncio, json, os
from jvspatial.core.context import GraphContext, set_default_context
from jvspatial.db.factory import create_database
from app.agentive.services.staging_apply import bless_and_execute
from app.agentive.staging import get_token
from app.agentive.services.staging_execution import execution_record_id

async def main():
    database = create_database(db_type="postgres")
    set_default_context(GraphContext(database=database))
    token = os.environ["C6_A08_TOKEN"]
    user_id = os.environ["C6_A08_USER"]
    staged = await get_token(token)
    assert staged is not None
    for index in range(2):
        receipt = await database.get("object", execution_record_id(f"{token}:step:{index}"))
        assert receipt is not None and receipt["context"]["state"] == "applied"
    result = await bless_and_execute(user_id=user_id, token=token)
    print(json.dumps({"pid": os.getpid(), "result": result}))
    await database.close()

asyncio.run(main())
"""
    child_env = {
        **os.environ,
        # The in-memory test driver is registered only in this interpreter.
        # Child approval recovery uses the real graph, without vector I/O.
        "EMBEDDING_STORE_DRIVER": "null",
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
    assert resumed["consumed"] is True, resumed
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
    # exactly one of each rooted graph object proves the prior step receipts
    # were sufficient for the separate process to finish.
    assert await staging_store.load(staged.token) is None


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_a07_corrected_approved_revision_executes_once_and_closes_states(
    postgres_raw_db, postgres_graph_context, monkeypatch
) -> None:
    """Real current review metadata and authority survive correction/retry."""
    from app.agentive.services.app_operation_staging import prepare_agent_app_operation
    from app.api.errors import ResourceConflictError
    from app.models.edges import HAS_APPLICATION_DEFINITION
    from app.models.nodes import ApplicationDefinition
    from app.services.app_operations import dispatch
    from app.services.app_operations.registry import (
        register_app_operations,
        unregister_app_operations,
    )
    from app.services.application_definitions import definition_fingerprint
    from tests.contract.test_operation_nested_result_postgres import operation_context

    actor, workspace, app, _, _ = await operation_context(role="editor")
    spec = {
        "key": "apply_revision",
        "kind": "execute",
        "handler_ref": "reference:apply_revision",
    }
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

    async def apply_revision(payload, context):
        revision = payload["revision"]
        calls.append(revision)
        if revision == "draft-1":
            raise ResourceConflictError(
                message="The approved revision needs correction"
            )
        await C6ApprovedEffect.create(revision=revision)
        # This private fixture record has no public resource contract. Verify
        # its persisted count below rather than exposing an unsupported ID.
        return {"revision": revision}

    async def stage(revision, *, ttl_seconds=None):
        prepared = await prepare_agent_app_operation(
            {
                "app_id": app.id,
                "operation_key": "apply_revision",
                "input": {"revision": revision},
                "idempotency_key": f"a07-{revision}",
            },
            principal_id=actor.id,
            workspace_id=workspace.id,
        )
        kwargs = {} if ttl_seconds is None else {"ttl_seconds": ttl_seconds}
        return await staging.create_staged_change(
            user_id=actor.id,
            workspace_id=workspace.id,
            session_id=None,
            **prepared,
            **kwargs,
        )

    register_app_operations(workspace.id, app.id, "reference", [spec])
    monkeypatch.setattr(dispatch, "resolve_handler", lambda ref: apply_revision)
    try:
        first_revision = await stage("draft-1")
        refused = await staging_apply.bless_and_execute(
            user_id=actor.id, token=first_revision.token
        )
        assert refused["consumed"] is False, refused
        assert refused["execute_result"]["error"]
        assert await C6ApprovedEffect.find({}) == []
        retained = await staging_store.load(first_revision.token)
        assert retained is not None and retained.last_error is not None
        await staging.revoke_token(user_id=actor.id, token=first_revision.token)

        corrected = await stage("draft-2")
        applied = await staging_apply.bless_and_execute(
            user_id=actor.id, token=corrected.token
        )
        assert applied["consumed"] is True, applied
        assert applied["execute_result"]["output"]["revision"] == "draft-2"
        receipt_id = applied["execute_result"]["operation_receipt"]["id"]
        receipt = await postgres_raw_db.get("object", receipt_id)
        assert receipt["context"]["status"] == "succeeded"
        marker = await postgres_raw_db.get(
            "object", staging_store._decision_record_id(corrected.token)
        )
        assert (
            receipt["context"]["decision_intent_fingerprint"]
            == marker["context"]["intent_fingerprint"]
        )
        effects = await C6ApprovedEffect.find({"revision": "draft-2"})
        assert len(effects) == 1
        with pytest.raises(StagingError) as consumed:
            await staging_apply.bless_and_execute(
                user_id=actor.id, token=corrected.token
            )
        assert consumed.value.code == "already_consumed"
        assert calls == ["draft-1", "draft-2"]

        expired = await stage("expired", ttl_seconds=-1)
        with pytest.raises(StagingError) as expiry:
            await staging_apply.bless_and_execute(user_id=actor.id, token=expired.token)
        assert expiry.value.code == "expired"
        cancelled = await stage("cancelled")
        await staging.revoke_token(user_id=actor.id, token=cancelled.token)
        with pytest.raises(StagingError) as cancellation:
            await staging_apply.bless_and_execute(
                user_id=actor.id, token=cancelled.token
            )
        assert cancellation.value.code == "already_revoked"
        assert calls == ["draft-1", "draft-2"]
    finally:
        unregister_app_operations(workspace.id, app.id)
