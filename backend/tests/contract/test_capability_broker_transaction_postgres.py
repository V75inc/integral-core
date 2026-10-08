"""Actual-store capability broker reads within worker transactions."""

from __future__ import annotations

import uuid

import pytest
from jvspatial.core.context import GraphContext, set_default_context

from app.agentive.services.capability_broker import invoke
from app.schemas.capability_broker import ERR_RUN_NOT_FOUND, CapabilityInvocation
from app.services.app_operations.transaction_scope import postgres_graph_transaction


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_transactional_broker_missing_run_returns_structured_denial(
    postgres_raw_db,
):
    """The worker's transaction adapter must support the broker lookup path."""
    from jvspatial.core.context import _default_context_var

    token = set_default_context(GraphContext(database=postgres_raw_db))
    try:
        async with postgres_graph_transaction(postgres_raw_db):
            result = await invoke(
                CapabilityInvocation(
                    run_id=f"missing-{uuid.uuid4().hex}",
                    principal_id="synthetic-principal",
                    workspace_id="synthetic-workspace",
                    origin="chat",
                    capability_key="integral_get_attachment_text",
                )
            )
        assert result.ok is False
        assert result.error_code == ERR_RUN_NOT_FOUND
    finally:
        _default_context_var.reset(token)


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_transactional_run_and_receipt_queries_preserve_scope(postgres_raw_db):
    from jvspatial.core.context import _default_context_var

    from app.agentive.services.execution_runs import AgentRun, RunStep

    run_id = f"transactional-{uuid.uuid4().hex}"
    token = set_default_context(GraphContext(database=postgres_raw_db))
    try:
        run = await AgentRun.create(
            run_id=run_id, user_id="owner", workspace_id="scope"
        )
        step = await RunStep.create(
            run_id=run_id, step_key="read:1", idempotency_key="same"
        )
        async with postgres_graph_transaction(postgres_raw_db):
            loaded = await AgentRun.find_one({"run_id": run_id})
            receipt = await RunStep.find_one(
                {"run_id": run_id, "idempotency_key": "same"}
            )
            assert loaded is not None and loaded.id == run.id
            assert loaded.user_id == "owner" and loaded.workspace_id == "scope"
            assert receipt is not None and receipt.id == step.id
            assert await AgentRun.find_one({"run_id": "other-run"}) is None
            assert (
                await RunStep.find_one(
                    {"run_id": "other-run", "idempotency_key": "same"}
                )
                is None
            )
    finally:
        _default_context_var.reset(token)


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_transactional_permission_fanout_preserves_entries_and_denials(
    postgres_raw_db,
):
    from jvspatial.core.context import _default_context_var

    from app.models.edges import CONTAINS, EXCLUDED_FROM, IS_MEMBER_OF, OWNS
    from app.models.nodes import App, Entry, Track, User, Workspace
    from app.services.app_graph import (
        catalog_app,
        catalog_track,
        catalog_user,
        catalog_workspace,
    )
    from app.services.permissions import get_user_accessible_entries
    from app.utils.time import utc_now_iso

    token = set_default_context(GraphContext(database=postgres_raw_db))
    try:
        suffix = uuid.uuid4().hex
        now = utc_now_iso()
        user = await User.create(user_id=f"fanout-{suffix}")
        workspace = await Workspace.create(kind="personal", name=f"Fanout {suffix}")
        await user.connect(workspace, edge=IS_MEMBER_OF, role="owner", joined_at=now)
        await catalog_user(user)
        await catalog_workspace(workspace)
        app = await App.create(
            name=f"Fanout {suffix}",
            workspace_id=workspace.id,
            owner_user_id=user.id,
            visibility="private",
        )
        await user.connect(app, edge=OWNS, role="owner", granted_at=now)
        await catalog_app(app)
        entries = []
        for index in range(5):
            track = await Track.create(
                title=f"Track {index}", workspace_id=workspace.id, visibility="private"
            )
            await app.connect(track, edge=CONTAINS, added_at=now)
            await catalog_track(track)
            entry = await Entry.create(
                title=f"Record {index}", track_id=track.id, author_id=user.id
            )
            await track.connect(entry, edge=CONTAINS, added_at=now)
            entries.append(entry)
        await user.connect(entries[-1], edge=EXCLUDED_FROM)

        kwargs = {"app_id": app.id, "workspace_id": workspace.id, "strict": True}
        expected = {entry.id for entry in entries[:-1]}
        outside = await get_user_accessible_entries(user.id, **kwargs)
        assert {entry.id for entry in outside} == expected
        async with postgres_graph_transaction(postgres_raw_db):
            inside = await get_user_accessible_entries(user.id, **kwargs)
            assert {entry.id for entry in inside} == expected
    finally:
        _default_context_var.reset(token)
