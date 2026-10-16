"""Approved dashboard recovery retains current authority and stable batch identity."""

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.agentive import staging
from app.agentive.services import staging_apply
from app.agentive.staging import StagingError, create_staged_change
from app.models.edges import CONTAINS
from app.models.nodes import App, Entry
from app.services.agent_scope import current_scope_workspace_id
from app.services.resource_location import locate_resource
from tests.contract.test_staging_dashboard_postgres import (
    dashboard_proposal,
    receipt_key,
)

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def test_dashboard_receipt_withholds_after_membership_revocation(postgres_raw_db):
    """A saved applied fact cannot release current data after access is removed."""
    from app.agentive.services.staging_dashboard_commands import readback
    from tests.contract.test_release_admission_postgres import remove_membership

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, sc = await dashboard_proposal("create_dashboard")
        first = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert first["consumed"] is True, first
        dashboard_id = first["execute_result"]["id"]
        await remove_membership(auth, workspace)
        result = await readback(
            sc,
            workspace.id,
            {
                "app_id": sc.payload["app_id"],
                "dashboard_id": dashboard_id,
            },
        )
        assert result["applied"] is True and result["result_access"] == "withheld"
        assert dashboard_id not in str(result) and sc.payload["app_id"] not in str(
            result
        )
        assert "Approved board" not in str(result)
        assert current_scope_workspace_id.get() is None


async def test_dashboard_receipt_preserves_authorization_outage(
    postgres_raw_db, monkeypatch
):
    """An unavailable policy read is not a completed denial or an empty result."""
    from app.agentive.services.staging_dashboard_commands import readback
    from app.api import apps_dashboards
    from app.exceptions import AuthorizationUnavailableError

    async def unavailable(*args, **kwargs):
        raise AuthorizationUnavailableError()

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, sc = await dashboard_proposal("create_dashboard")
        first = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert first["consumed"] is True, first
        monkeypatch.setattr(apps_dashboards, "get_app_dashboard", unavailable)
        with pytest.raises(AuthorizationUnavailableError):
            await readback(
                sc,
                workspace.id,
                {
                    "app_id": sc.payload["app_id"],
                    "dashboard_id": first["execute_result"]["id"],
                },
            )
        assert current_scope_workspace_id.get() is None


async def test_dashboard_validation_error_is_classified_and_rolls_back(postgres_raw_db):
    """Invalid widgets cannot leave a newly created registry or change authority."""
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, original = await dashboard_proposal("create_dashboard")
        await staging.revoke_token(user_id=auth.id, token=original.token)
        reset = current_scope_workspace_id.set(workspace.id)
        try:
            sc = await create_staged_change(
                user_id=auth.id,
                session_id=None,
                kind="create_dashboard",
                summary="Invalid board",
                diff_human="Invalid widget",
                diff_machine={},
                payload={
                    **original.payload,
                    "widgets": [{"id": "bad", "type": "unsupported-widget"}],
                },
            )
        finally:
            current_scope_workspace_id.reset(reset)
        nodes = await postgres_raw_db.find("node", {}, sort=[("id", 1)])
        edges = await postgres_raw_db.find("edge", {}, sort=[("id", 1)])
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert result["consumed"] is False, result
        assert result["execute_result"]["status_code"] == 400, result
        assert await postgres_raw_db.find("node", {}, sort=[("id", 1)]) == nodes
        assert await postgres_raw_db.find("edge", {}, sort=[("id", 1)]) == edges


@pytest.mark.parametrize("field, value", [("name", "Unapproved"), ("is_default", True)])
async def test_dashboard_effect_refuses_changed_approved_fields(
    postgres_raw_db, field, value
):
    """Immutable approved configuration is checked at the actual effect boundary."""
    from app.agentive.services.staging_command_apply import apply_staged_command_once
    from app.agentive.staging_executors import _x_create_dashboard

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, sc = await dashboard_proposal("create_dashboard")
        await staging.bless_token(user_id=auth.id, token=sc.token)
        nodes = await postgres_raw_db.find("node", {}, sort=[("id", 1)])

        async def altered():
            return await _x_create_dashboard(auth.id, {**sc.payload, field: value})

        with pytest.raises(StagingError) as exc:
            await apply_staged_command_once(sc=sc, request=None, execute=altered)
        assert exc.value.code == "approval_unavailable"
        assert await postgres_raw_db.find("node", {}, sort=[("id", 1)]) == nodes


async def test_mixed_dashboard_batch_reconciles_after_approved_deletion(
    postgres_raw_db, monkeypatch
):
    """Rebuild private named references even after the created dashboard is deleted."""
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, original = await dashboard_proposal("create_dashboard")
        location = await locate_resource("app", original.payload["app_id"])
        app = await App.get(location.app_id)
        track = (await app.nodes(edge=[CONTAINS], node=["Track"]))[0]
        reset = current_scope_workspace_id.set(workspace.id)
        try:
            sc = await create_staged_change(
                user_id=auth.id,
                session_id=None,
                kind="batch",
                summary="Build, revise and remove a dashboard",
                diff_human="Four changes",
                diff_machine={},
                payload={
                    "operations": [
                        {
                            "kind": "create_dashboard",
                            "payload": {
                                **original.payload,
                                "name": "  Approved board  ",
                            },
                        },
                        {
                            "kind": "update_dashboard",
                            "payload": {
                                "app_id": app.id,
                                "dashboard_id": "{{dashboard.id:Approved board}}",
                                "name": "Revised",
                            },
                        },
                        {
                            "kind": "delete_dashboard",
                            "payload": {
                                "app_id": app.id,
                                "dashboard_id": "{{dashboard.id:Approved board}}",
                            },
                        },
                        {
                            "kind": "create_entry",
                            "payload": {"track_id": track.id, "title": "One record"},
                        },
                    ]
                },
            )
        finally:
            current_scope_workspace_id.reset(reset)
        consume = staging_apply.consume_token

        async def unavailable(**kwargs):
            raise StagingError("completion_unavailable", "Lost batch completion")

        monkeypatch.setattr(staging_apply, "consume_token", unavailable)
        first = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert first["application_state"] == "reconciliation_required", first
        before_nodes = await postgres_raw_db.find("node", {}, sort=[("id", 1)])
        before_edges = await postgres_raw_db.find("edge", {}, sort=[("id", 1)])
        staging._tokens.clear()
        monkeypatch.setattr(staging_apply, "consume_token", consume)
        recovered = await staging_apply.bless_and_execute(
            user_id=auth.id, token=sc.token
        )
        assert recovered["consumed"] is True, recovered
        assert recovered["execute_result"]["completed"] == 4
        assert await postgres_raw_db.find("node", {}, sort=[("id", 1)]) == before_nodes
        assert await postgres_raw_db.find("edge", {}, sort=[("id", 1)]) == before_edges
        assert len(await Entry.find({"track_id": track.id})) == 1
