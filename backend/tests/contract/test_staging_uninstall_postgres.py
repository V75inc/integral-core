"""Library uninstall archives and registries reconcile through one command."""

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.agentive import staging
from app.agentive.services import staging_apply
from app.agentive.staging import StagingError, create_staged_change
from app.models.nodes import App, Track
from app.services.agent_scope import current_scope_workspace_id
from app.services.app_lifecycle import install_app
from tests.contract.test_staging_command_apply_postgres import proposal
from tests.test_app_lifecycle import _make_library_cp, _minimal_app_manifest

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def uninstall_proposal():
    auth, workspace, _, _ = await proposal()
    lib = await _make_library_cp(_minimal_app_manifest("recovery-fixture"))
    installed = await install_app(
        workspace_id=workspace.id,
        library_cp_id=lib.id,
        actor_id=auth.id,
        include_seed_data=False,
    )
    reset = current_scope_workspace_id.set(workspace.id)
    try:
        sc = await create_staged_change(
            user_id=auth.id,
            session_id=None,
            kind="delete_app",
            summary="Uninstall",
            diff_human="Uninstall library package",
            diff_machine={},
            payload={"app_id": installed["app_id"]},
        )
    finally:
        current_scope_workspace_id.reset(reset)
    return auth, workspace, sc


async def test_library_archive_completion_recovers_without_repeating_uninstall(
    postgres_raw_db, monkeypatch
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "88" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, sc = await uninstall_proposal()
        original = staging_apply.consume_token

        async def fail(**kwargs):
            raise StagingError("completion_unavailable", "Lost completion")

        monkeypatch.setattr(staging_apply, "consume_token", fail)
        first = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert first["application_state"] == "reconciliation_required", first
        app = await App.find_one({"id": sc.payload["app_id"]})
        assert app.lifecycle_state == "uninstalled"
        timestamp = app.updated_at
        tracks = await Track.find({"workspace_id": workspace.id})
        staging._tokens.clear()
        monkeypatch.setattr(staging_apply, "consume_token", original)
        recovered = await staging_apply.bless_and_execute(
            user_id=auth.id, token=sc.token
        )
        assert recovered["consumed"] is True, recovered
        assert recovered["execute_result"]["uninstalled"] is True
        assert recovered["execute_result"]["archived"] is True
        assert (await App.find_one({"id": app.id})).updated_at == timestamp
        assert [t.id for t in await Track.find({"workspace_id": workspace.id})] == [
            t.id for t in tracks
        ]


async def test_library_archive_receipt_fault_rolls_back_lifecycle_and_cleanup(
    postgres_raw_db, monkeypatch
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "88" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, sc = await uninstall_proposal()

        async def fail(**kwargs):
            raise OSError("Archive receipt failure")

        monkeypatch.setattr(
            "app.agentive.services.staging_execution.record_staging_execution_result",
            fail,
        )
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert result["consumed"] is False
        assert result["execute_result"]["error"] is True
        assert (
            await App.find_one({"id": sc.payload["app_id"]})
        ).lifecycle_state == "active"
        assert (
            await postgres_raw_db.find(
                "object",
                {"entity": "BundleCleanupFact", "context.app_id": sc.payload["app_id"]},
            )
            == []
        )


async def test_receipt_fault_preserves_local_agent_until_graph_commit(
    postgres_raw_db, monkeypatch
):
    from app.agentive.nodes import AgentConfig
    from app.agentive.services.uplink_registry import (
        register_app_agent,
        uplink_registry,
    )

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "88" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, sc = await uninstall_proposal()
        cfg = await register_app_agent(
            sc.payload["app_id"],
            workspace.id,
            {"key": "helper", "skills": [], "scope": "app"},
        )
        await uplink_registry.register(cfg)
        try:

            from app.agentive.services.staging_execution import (
                record_staging_execution_result,
            )

            async def fail(**kwargs):
                raise OSError("Archive receipt failure")

            monkeypatch.setattr(
                "app.agentive.services.staging_execution.record_staging_execution_result",
                fail,
            )
            result = await staging_apply.bless_and_execute(
                user_id=auth.id, token=sc.token
            )
            assert result["consumed"] is False
            assert await AgentConfig.find_one({"id": cfg.id}) is not None
            assert cfg.id in uplink_registry._agents
            monkeypatch.setattr(
                "app.agentive.services.staging_execution.record_staging_execution_result",
                record_staging_execution_result,
            )
            retried = await staging_apply.bless_and_execute(
                user_id=auth.id, token=sc.token
            )
            assert retried["consumed"] is True, retried
            assert await AgentConfig.find_one({"id": cfg.id}) is None
            assert cfg.id not in uplink_registry._agents
        finally:
            await uplink_registry.unregister(cfg.id)
