"""Query results carry the authorization state that governed their read."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.schemas.governed_query import QuerySpec
from app.schemas.policy import Decision
from app.services.app_queries.dispatch import invoke_app_query
from app.services.governed_query.engine import _run_declared
from app.services.hooks.errors import ToolValidationFailedError


@pytest.mark.contract
@pytest.mark.asyncio
async def test_declared_query_template_runs_with_exact_app_scope() -> None:
    workspace_id = "ws-query-template"
    app_id = "n.App.query-template"
    app = type(
        "AppStub",
        (),
        {"id": app_id, "workspace_id": workspace_id, "lifecycle_state": "active"},
    )()
    query_result = {"items": [{"id": "n.Entry.scoped"}], "next_cursor": None}
    execute = AsyncMock(
        return_value=SimpleNamespace(model_dump=lambda **_kwargs: query_result)
    )

    with (
        patch(
            "app.services.app_queries.dispatch.App.get",
            new=AsyncMock(return_value=app),
        ),
        patch(
            "app.services.app_queries.dispatch.can_access_workspace",
            new=AsyncMock(return_value="owner"),
        ),
        patch(
            "app.services.app_queries.dispatch.resolve_role",
            new=AsyncMock(return_value="owner"),
        ),
        patch(
            "app.services.app_queries.dispatch.get_app_query",
            return_value={
                "key": "read_records",
                "policy_action": "app.read",
                "input_schema": {
                    "type": "object",
                    "properties": {"cursor": {"type": "string"}},
                },
                "query_template": {
                    "resource": "entry",
                    "select": ["id"],
                    "limit": 10,
                },
            },
        ),
        patch(
            "app.services.app_queries.dispatch.policy_evaluate",
            new=AsyncMock(return_value=Decision(allowed=True, reason="test")),
        ),
        patch("app.agentive.services.query_spec.execute_query_spec", new=execute),
    ):
        result = await invoke_app_query(
            user_id="user-1",
            workspace_id=workspace_id,
            app_id=app_id,
            query_key="read_records",
            params={"cursor": "opaque-cursor"},
        )

    assert result["output"] == query_result
    assert execute.await_args.kwargs["declared_app_id"] == app_id
    assert execute.await_args.kwargs["workspace_id"] == workspace_id
    assert execute.await_args.kwargs["spec"].cursor == "opaque-cursor"


@pytest.mark.contract
@pytest.mark.asyncio
async def test_governed_query_unwraps_query_spec_items_and_scopes_refs() -> None:
    workspace_id = "ws-query-template-result"
    app_id = "n.App.query-template-result"
    rows = [{"id": "n.Entry.scoped", "title": "Saved venture"}]
    invoked = {
        "output": {"items": rows, "next_cursor": "next", "total_estimate": 4},
        "policy_decision_id": "decision-1",
    }
    spec = QuerySpec(
        mode="declared_capability",
        capability_key="venture_journey__current_status",
        app_id=app_id,
    )

    with (
        patch(
            "app.services.app_queries.dispatch.invoke_app_query",
            new=AsyncMock(return_value=invoked),
        ),
        patch(
            "app.services.governed_query.engine.get_or_compile_catalogue",
            new=AsyncMock(return_value=SimpleNamespace(generation_id="catalogue-1")),
        ),
    ):
        result = await _run_declared(
            user_id="user-1",
            workspace_id=workspace_id,
            spec=spec,
            catalogue_generation="catalogue-1",
        )

    assert result.rows == rows
    assert result.cursor == "next"
    assert result.total_estimate == 4
    assert result.object_refs[0].id == "n.Entry.scoped"
    assert result.object_refs[0].app_id == app_id
    assert result.evidence.applied_scope == f"app:{app_id}"


@pytest.mark.contract
@pytest.mark.asyncio
async def test_query_result_carries_current_policy_revision() -> None:
    """A query response makes its evaluated policy state inspectable."""
    workspace_id = "ws-query-policy"
    app_id = "n.App.query-policy"
    app = type(
        "AppStub",
        (),
        {"id": app_id, "workspace_id": workspace_id, "lifecycle_state": "active"},
    )()

    async def handler(_params, _context):
        return {"count": 1}

    with (
        patch(
            "app.services.app_queries.dispatch.App.get",
            new=AsyncMock(return_value=app),
        ),
        patch(
            "app.services.app_queries.dispatch.can_access_workspace",
            new=AsyncMock(return_value="owner"),
        ),
        patch(
            "app.services.app_queries.dispatch.resolve_role",
            new=AsyncMock(return_value="owner"),
        ),
        patch(
            "app.services.app_queries.dispatch.get_app_query",
            return_value={
                "key": "count",
                "policy_action": "app.read",
                "handler_ref": "test.query_handler",
                "input_schema": {},
            },
        ),
        patch(
            "app.services.app_queries.dispatch.policy_evaluate",
            new=AsyncMock(
                return_value=Decision(allowed=True, reason="default_human_policy")
            ),
        ),
        patch(
            "app.services.app_queries.dispatch.resolve_handler", return_value=handler
        ),
    ):
        result = await invoke_app_query(
            user_id="user-1",
            workspace_id=workspace_id,
            app_id=app_id,
            query_key="count",
        )

    assert result["output"] == {"count": 1}
    assert result["policy_revision"].startswith("policy-sha256:")


@pytest.mark.contract
@pytest.mark.asyncio
async def test_query_context_is_read_only_and_output_schema_is_enforced() -> None:
    workspace_id = "ws-query-readonly"
    app_id = "n.App.query-readonly"
    app = type(
        "AppStub",
        (),
        {"id": app_id, "workspace_id": workspace_id, "lifecycle_state": "active"},
    )()
    observed = {}

    async def handler(_params, context):
        observed["read_only"] = context.read_only
        observed["write"] = await context.update_entry_fields("entry-outside", {"x": 1})
        observed["attachment"] = await context.put_attachment(
            "entry-outside", b"file", "test.txt"
        )
        observed["audit"] = await context.emit_audit("entry.update", {"x": 1})
        try:
            await context.invoke("mutating-operation", {})
        except ValueError:
            observed["invoke_denied"] = True
        return {"count": 1}

    patches = (
        patch(
            "app.services.app_queries.dispatch.App.get", new=AsyncMock(return_value=app)
        ),
        patch(
            "app.services.app_queries.dispatch.can_access_workspace",
            new=AsyncMock(return_value="owner"),
        ),
        patch(
            "app.services.app_queries.dispatch.resolve_role",
            new=AsyncMock(return_value="owner"),
        ),
        patch(
            "app.services.app_queries.dispatch.get_app_query",
            return_value={
                "key": "count",
                "policy_action": "app.read",
                "handler_ref": "test.query_handler",
                "input_schema": {},
                "output_schema": {
                    "type": "object",
                    "required": ["total"],
                    "properties": {"total": {"type": "integer"}},
                },
            },
        ),
        patch(
            "app.services.app_queries.dispatch.policy_evaluate",
            new=AsyncMock(return_value=Decision(allowed=True, reason="test")),
        ),
        patch(
            "app.services.app_queries.dispatch.resolve_handler", return_value=handler
        ),
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        with pytest.raises(ToolValidationFailedError, match="output schema mismatch"):
            await invoke_app_query(
                user_id="user-1",
                workspace_id=workspace_id,
                app_id=app_id,
                query_key="count",
            )
    assert observed == {
        "read_only": True,
        "write": False,
        "attachment": None,
        "audit": None,
        "invoke_denied": True,
    }
