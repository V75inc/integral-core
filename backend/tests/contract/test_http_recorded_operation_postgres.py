"""Post-auth HTTP boundary uses actual identity and workspace membership facts."""

import json

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async
from starlette.requests import Request

from app.api.app_extensions import invoke_operation
from app.api.errors import InsufficientPermissionsError
from app.services.app_operations.registry import unregister_app_operations
from tests.contract.test_recorded_operation_results_postgres import prepared

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


def request(actor, workspace, key):
    async def receive():
        return {
            "type": "http.request",
            "body": json.dumps({"input": {}}).encode(),
            "more_body": False,
        }

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/extensions/app/operations/record",
            "headers": [
                (b"x-integral-scope", f"ws:{workspace}".encode()),
                (b"idempotency-key", key.encode()),
            ],
            "state": {"user": actor},
        },
        receive,
    )


@pytest.mark.parametrize("change", ["none", "removed", "wrong_workspace"])
async def test_native_http_completion_is_not_a_second_broker_invocation(
    postgres_raw_db, monkeypatch, change
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, app, scope, _, first, calls = await prepared(monkeypatch)
        before = await postgres_raw_db.get("object", first["operation_receipt"]["id"])

        async def forbidden(*args, **kwargs):
            pytest.fail("Known completion entered snapshot or effect broker")

        monkeypatch.setattr(
            "app.agentive.services.execution_runs.build_capability_snapshot", forbidden
        )
        monkeypatch.setattr(
            "app.agentive.services.capability_broker.invoke_declared_capability",
            forbidden,
        )
        try:
            if change == "removed":
                unregister_app_operations(scope.workspace_id, app.id)
            bound_workspace = (
                "not-a-member" if change == "wrong_workspace" else scope.workspace_id
            )
            req = request(actor, bound_workspace, "one-intent")
            if change == "wrong_workspace":
                with pytest.raises(InsufficientPermissionsError):
                    await invoke_operation(req, app.id, "record")
            else:
                result = await invoke_operation(req, app.id, "record")
                assert result["operation_receipt"] == {
                    **first["operation_receipt"],
                    "replayed": True,
                }
                assert result["current_state_verified"] is False
                if change == "none":
                    assert result["output"] == first["output"]
                else:
                    assert set(result) == {
                        "operation_receipt",
                        "output_disposition",
                        "current_state_verified",
                    }
                    assert result["output_disposition"] == "withheld"
            assert calls == ["effect"]
            assert await postgres_raw_db.get("object", before["id"]) == before
        finally:
            unregister_app_operations(scope.workspace_id, app.id)
