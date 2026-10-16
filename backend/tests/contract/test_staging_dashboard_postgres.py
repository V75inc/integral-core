"""Dashboard approvals share graph, event and receipt commit/recovery boundaries."""

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.agentive import staging
from app.agentive.services import staging_apply, staging_execution
from app.agentive.staging import StagingError, create_staged_change
from app.services.agent_scope import current_scope_workspace_id
from app.services.dashboard_service import create_dashboard
from app.services.resource_location import locate_resource
from tests.contract.test_staging_command_apply_postgres import proposal

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]
KINDS = ["create_dashboard", "update_dashboard", "delete_dashboard"]


@pytest.fixture(autouse=True)
def receipt_key(monkeypatch):
    """Use a synthetic key for durable effect evidence."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "99" * 32)


async def dashboard_proposal(kind):
    """Build one authorized App and the exact proposed dashboard mutation."""
    auth, workspace, track, _ = await proposal()
    location = await locate_resource("track", track.id)
    payload = {"app_id": location.app_id}
    if kind != "create_dashboard":
        board = await create_dashboard(
            user_id=auth.id, app_id=location.app_id, name="Original board"
        )
        payload["dashboard_id"] = board.id
    if kind != "delete_dashboard":
        payload["name"] = "Approved board"
    reset = current_scope_workspace_id.set(workspace.id)
    try:
        sc = await create_staged_change(
            user_id=auth.id,
            session_id=None,
            kind=kind,
            summary="Change a dashboard",
            diff_human="One approved dashboard change",
            diff_machine={},
            payload=payload,
        )
    finally:
        current_scope_workspace_id.reset(reset)
    return auth, workspace, sc


@pytest.mark.parametrize("kind", KINDS)
async def test_dashboard_completion_failure_recovers_without_another_write(
    postgres_raw_db, monkeypatch, kind
):
    """A lost approval completion reuses a committed receipt, including deletion."""
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, sc = await dashboard_proposal(kind)
        consume = staging_apply.consume_token

        async def unavailable(**kwargs):
            raise StagingError("completion_unavailable", "Lost dashboard completion")

        monkeypatch.setattr(staging_apply, "consume_token", unavailable)
        first = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert first["application_state"] == "reconciliation_required", first
        record = await postgres_raw_db.get(
            "object", staging_execution.execution_record_id(sc.token)
        )
        assert record is not None and record["context"]["state"] == "applied"
        before_nodes = await postgres_raw_db.find("node", {}, sort=[("id", 1)])
        before_edges = await postgres_raw_db.find("edge", {}, sort=[("id", 1)])
        outboxes = await postgres_raw_db.find(
            "object", {"entity": "OperationEventOutbox"}
        )
        assert len(outboxes) == 1
        staging._tokens.clear()
        monkeypatch.setattr(staging_apply, "consume_token", consume)
        recovered = await staging_apply.bless_and_execute(
            user_id=auth.id, token=sc.token
        )
        assert recovered["consumed"] is True, recovered
        result = recovered["execute_result"]
        assert result["execution_receipt"]["replayed"] is True
        if kind == "delete_dashboard":
            assert result["deleted"] is True
            assert sc.payload["dashboard_id"] not in str(result)
        else:
            assert result["name"] == "Approved board"
        assert await postgres_raw_db.find("node", {}, sort=[("id", 1)]) == before_nodes
        assert await postgres_raw_db.find("edge", {}, sort=[("id", 1)]) == before_edges
        assert (
            len(
                await postgres_raw_db.find("object", {"entity": "OperationEventOutbox"})
            )
            == 1
        )


@pytest.mark.parametrize("kind", KINDS)
async def test_dashboard_receipt_failure_rolls_back_graph_and_events(
    postgres_raw_db, monkeypatch, kind
):
    """No dashboard, registry, parent edge or event may escape a failed receipt."""
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, sc = await dashboard_proposal(kind)
        nodes = await postgres_raw_db.find("node", {}, sort=[("id", 1)])
        edges = await postgres_raw_db.find("edge", {}, sort=[("id", 1)])

        async def unavailable(**kwargs):
            raise RuntimeError("Lost dashboard receipt")

        monkeypatch.setattr(
            staging_execution, "record_staging_execution_result", unavailable
        )
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert result["consumed"] is False, result
        assert result["execute_result"]["error"], result
        assert await postgres_raw_db.find("node", {}, sort=[("id", 1)]) == nodes
        assert await postgres_raw_db.find("edge", {}, sort=[("id", 1)]) == edges
        assert (
            await postgres_raw_db.find("object", {"entity": "OperationEventOutbox"})
            == []
        )
