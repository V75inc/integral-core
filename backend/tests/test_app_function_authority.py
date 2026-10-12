"""Declared App functions must keep ordinary authority throughout execution."""

import asyncio
from types import SimpleNamespace

import pytest

from app.middleware.permissions_cache import isolated_permissions_cache
from app.models.edges import COLLABORATES_ON
from app.schemas.policy import Resource, Subject
from app.services.app_operations.dispatch import invoke_app_operation
from app.services.app_operations.registry import (
    register_app_operations,
    unregister_app_operations,
)
from app.services.app_queries.dispatch import invoke_app_query
from app.services.app_queries.registry import (
    register_app_queries,
    unregister_app_queries,
)
from app.services.hooks.registry import (
    register_workspace_tools,
    unregister_bundle_registrations,
)
from app.services.permissions import can_view_entry, get_user_accessible_entries
from app.services.policy_engine import evaluate
from app.services.workspace_governance import owner_governance_allowed
from tests.test_workspace_owner_governance import private_member_graph

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]

FUNCTION_PATHS = ("operation", "query", "operation_tool", "query_tool")


async def invoke_probe(monkeypatch, mode, owner, workspace, app, handler):
    spec = {
        "key": "authority_probe",
        "kind": "read",
        "handler_ref": "fixture:authority_probe",
        "policy_action": "app.read",
    }
    operation = mode.startswith("operation")
    module = (
        "app.services.app_operations.dispatch"
        if operation
        else "app.services.app_queries.dispatch"
    )
    if mode.endswith("_tool"):
        spec.pop("handler_ref")
        spec["tool"] = "authority_fixture__probe"
        register_workspace_tools(
            workspace.id,
            "authority-fixture",
            [
                {
                    "key": spec["tool"],
                    "handler_ref": "fixture:authority_probe",
                }
            ],
        )
        monkeypatch.setattr(
            "app.services.hooks.tool_dispatch.resolve_handler", lambda _: handler
        )
    else:
        monkeypatch.setattr(module + ".resolve_handler", lambda _: handler)
    if operation:
        register_app_operations(workspace.id, app.id, "authority-fixture", [spec])
    else:
        register_app_queries(workspace.id, app.id, "authority-fixture", [spec])
    try:
        if operation:
            return await invoke_app_operation(
                user_id=owner.id,
                workspace_id=workspace.id,
                app_id=app.id,
                operation_key=spec["key"],
                payload={},
            )
        return await invoke_app_query(
            user_id=owner.id,
            workspace_id=workspace.id,
            app_id=app.id,
            query_key=spec["key"],
            params={},
        )
    finally:
        if operation:
            unregister_app_operations(workspace.id, app.id)
        else:
            unregister_app_queries(workspace.id, app.id)
        if mode.endswith("_tool"):
            unregister_bundle_registrations(workspace.id, "authority-fixture")


@pytest.mark.parametrize("mode", FUNCTION_PATHS)
@pytest.mark.parametrize("direct_entry", (False, True))
async def test_function_handler_never_borrows_workspace_stewardship(
    monkeypatch, mode, direct_entry
):
    owner, _, workspace, app, _, entry = await private_member_graph()
    await owner.connect(app, edge=COLLABORATES_ON, role="viewer")
    if direct_entry:
        await owner.connect(entry, edge=COLLABORATES_ON, role="viewer")

    async def handler(_payload, context):
        with isolated_permissions_cache():
            decision = await evaluate(
                subject=Subject(kind="human", id=context.user_id),
                action="entry.read",
                resource=Resource(
                    kind="entry", id=entry.id, scope=f"track:{entry.track_id}"
                ),
            )
            rows = await get_user_accessible_entries(
                context.user_id,
                app_id=app.id,
                workspace_id=workspace.id,
            )
            return {
                "point": await can_view_entry(context.user_id, entry.id),
                "policy": decision.allowed,
                "ids": [row.id for row in rows],
                "governance_mode": owner_governance_allowed(),
            }

    result = await invoke_probe(monkeypatch, mode, owner, workspace, app, handler)
    assert result["output"] == {
        "point": direct_entry,
        "policy": direct_entry,
        "ids": [entry.id] if direct_entry else [],
        "governance_mode": False,
    }
    assert owner_governance_allowed()
    assert await can_view_entry(owner.id, entry.id)


@pytest.mark.parametrize("mode", FUNCTION_PATHS)
async def test_function_failure_restores_stewardship(monkeypatch, mode):
    owner, _, workspace, app, _, entry = await private_member_graph()
    await owner.connect(app, edge=COLLABORATES_ON, role="viewer")

    async def handler(_payload, _context):
        assert not owner_governance_allowed()
        assert not await can_view_entry(owner.id, entry.id)
        raise RuntimeError("fixture handler failed")

    with pytest.raises(RuntimeError, match="fixture handler failed"):
        await invoke_probe(monkeypatch, mode, owner, workspace, app, handler)
    assert owner_governance_allowed()
    assert await can_view_entry(owner.id, entry.id)


@pytest.mark.parametrize("mode", FUNCTION_PATHS)
async def test_function_task_cancellation_cannot_change_parallel_steward_request(
    monkeypatch, mode
):
    owner, _, workspace, app, _, entry = await private_member_graph()
    await owner.connect(app, edge=COLLABORATES_ON, role="viewer")
    started = asyncio.Event()
    release = asyncio.Event()
    observed = {}

    async def handler(_payload, _context):
        observed["mode"] = owner_governance_allowed()
        observed["point"] = await can_view_entry(owner.id, entry.id)
        started.set()
        await release.wait()
        return {}

    task = asyncio.create_task(
        invoke_probe(monkeypatch, mode, owner, workspace, app, handler)
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        # A concurrently serving Core stewardship request keeps its own mode.
        assert owner_governance_allowed()
        assert await can_view_entry(owner.id, entry.id)
        assert observed == {"mode": False, "point": False}
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert owner_governance_allowed()
    assert await can_view_entry(owner.id, entry.id)


@pytest.mark.parametrize("direct_entry", (False, True))
async def test_declared_template_keeps_ordinary_audience(monkeypatch, direct_entry):
    owner, _, workspace, app, _, entry = await private_member_graph()
    await owner.connect(app, edge=COLLABORATES_ON, role="viewer")
    if direct_entry:
        await owner.connect(entry, edge=COLLABORATES_ON, role="viewer")
    spec = {
        "key": "template_probe",
        "policy_action": "app.read",
        "query_template": {"resource": "entry", "select": ["id"], "limit": 10},
    }
    register_app_queries(workspace.id, app.id, "authority-fixture", [spec])

    async def execute_template(*, principal_id, workspace_id, spec, declared_app_id):
        assert principal_id == owner.id
        assert workspace_id == workspace.id and declared_app_id == app.id
        # Replace only the template adapter; exercise the actual current
        # resource selection and authority inside its invocation boundary.
        rows = await get_user_accessible_entries(
            principal_id, app_id=declared_app_id, workspace_id=workspace_id
        )
        result = {
            "items": [{"id": row.id} for row in rows],
            "mode": owner_governance_allowed(),
        }
        return SimpleNamespace(model_dump=lambda **_: result)

    monkeypatch.setattr(
        "app.agentive.services.query_spec.execute_query_spec", execute_template
    )
    try:
        result = await invoke_app_query(
            user_id=owner.id,
            workspace_id=workspace.id,
            app_id=app.id,
            query_key=spec["key"],
        )
    finally:
        unregister_app_queries(workspace.id, app.id)
    assert result["output"] == {
        "items": [{"id": entry.id}] if direct_entry else [],
        "mode": False,
    }
    assert owner_governance_allowed()
