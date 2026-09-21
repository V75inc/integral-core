"""WP-01: HTTP capability bridges bind one immutable execution scope."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.contracts.runtime import ExecutionScope
from app.schemas.governed_query import QueryResult


class _Request:
    """Small request double for direct endpoint contract tests."""

    method = "POST"

    def __init__(self, body: dict, headers: dict[str, str] | None = None) -> None:
        self._body = body
        self.headers = headers or {}
        self.query_params = {}

    async def json(self) -> dict:
        return self._body


@pytest.mark.asyncio
async def test_execution_scope_helper_rejects_unresolved_workspace() -> None:
    """A governed HTTP boundary cannot continue with an empty workspace."""
    from app.api.errors import BadRequestError
    from app.services.request_scope import resolve_execution_scope_from_request

    with patch(
        "app.services.request_scope.resolve_workspace_id_from_request",
        new=AsyncMock(return_value=None),
    ):
        with pytest.raises(BadRequestError, match="principal and workspace"):
            await resolve_execution_scope_from_request(
                _Request({}), "user-1", origin="test"
            )


@pytest.mark.asyncio
async def test_governed_query_uses_bound_scope_for_the_downstream_read() -> None:
    """A representative HTTP read passes normalized scope through unchanged."""
    from app.api.capabilities import post_query

    scope = ExecutionScope.create(
        principal_id="user-1", workspace_id="ws-1", origin="http_governed_query"
    )
    query_result = QueryResult(mode="core_open")
    request = _Request({"query": {"mode": "core_open", "resource": "entry"}})

    with (
        patch("app.api.capabilities.resolve_principal_id", return_value=" user-1 "),
        patch(
            "app.api.capabilities.resolve_execution_scope_from_request",
            new=AsyncMock(return_value=scope),
        ) as bind_scope,
        patch(
            "app.services.governed_query.execute_query",
            new=AsyncMock(return_value=query_result),
        ) as execute_query,
    ):
        result = await post_query(request)

    assert result["mode"] == "core_open"
    assert bind_scope.await_args.kwargs["origin"] == "http_governed_query"
    assert execute_query.await_args.kwargs["user_id"] == "user-1"
    assert execute_query.await_args.kwargs["workspace_id"] == "ws-1"


@pytest.mark.asyncio
async def test_extension_operation_uses_bound_scope_for_the_effect() -> None:
    """A representative HTTP effect cannot substitute caller or workspace."""
    from app.api.app_extensions import invoke_operation

    scope = ExecutionScope.create(
        principal_id="user-1", workspace_id="ws-1", origin="http_extension_operation"
    )
    request = _Request({"input": {"name": "Updated"}})
    snapshot = {"apps": [{"app_id": "n.App.one", "operations": [{"key": "rename"}]}]}
    broker_result = SimpleNamespace(
        ok=True,
        data={
            "app_id": "n.App.one",
            "operation_key": "rename",
            "output": {"changed": True},
        },
        receipt=None,
    )

    with (
        patch("app.api.app_extensions.resolve_principal_id", return_value=" user-1 "),
        patch(
            "app.api.app_extensions.resolve_execution_scope_from_request",
            new=AsyncMock(return_value=scope),
        ) as bind_scope,
        patch(
            "app.agentive.services.execution_runs.build_capability_snapshot",
            new=AsyncMock(return_value=snapshot),
        ),
        patch(
            "app.agentive.services.capability_broker.invoke_declared_capability",
            new=AsyncMock(return_value=broker_result),
        ) as invoke_capability,
    ):
        result = await invoke_operation(request, "n.App.one", "rename")

    assert result["output"] == {"changed": True}
    assert bind_scope.await_args.kwargs["origin"] == "http_extension_operation"
    assert invoke_capability.await_args.kwargs["principal_id"] == "user-1"
    assert invoke_capability.await_args.kwargs["workspace_id"] == "ws-1"
