"""All Admin directories must page on the admitted PostgreSQL connection."""

from types import SimpleNamespace

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async
from starlette.requests import Request

from app.api.admin_users import admin_list_users, admin_overview
from app.api.admin_workspaces import (
    admin_list_apps,
    admin_list_tracks,
    admin_list_workspaces,
)
from app.api.errors import InsufficientPermissionsError
from app.services.app_graph import ensure_integral_app_graph
from tests.test_access_lifecycle import verified_subject
from tests.test_resource_location import graph

pytestmark = [pytest.mark.postgres, pytest.mark.contract, pytest.mark.asyncio]


def admin_request(actor):
    """Supply a cached admin claim while admission checks persisted roles."""
    request = Request(
        {"type": "http", "headers": [], "method": "GET", "path": "/api/admin"}
    )
    request.state.user = SimpleNamespace(id=actor.user_id, roles=["admin"])
    return request


@pytest.mark.parametrize(
    "endpoint",
    [
        admin_overview,
        admin_list_users,
        admin_list_workspaces,
        admin_list_apps,
        admin_list_tracks,
    ],
)
async def test_admin_read_uses_transaction_pager(postgres_raw_db, endpoint):
    """Load each directory and reject it after current authority is removed."""
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        await ensure_integral_app_graph(include_library=False)
        _, workspace, _, _, _ = await graph()
        actor = await verified_subject(workspace)
        await postgres_raw_db.find_one_and_update(
            "object",
            {"id": actor.user_id},
            {"$set": {"context.roles": ["admin", "user"]}},
        )
        result = await endpoint(admin_request(actor))
        assert isinstance(result, dict)
        assert result
        # A cached admin claim must not keep the directory accessible after demotion.
        await postgres_raw_db.find_one_and_update(
            "object", {"id": actor.user_id}, {"$set": {"context.roles": ["user"]}}
        )
        with pytest.raises(InsufficientPermissionsError):
            await endpoint(admin_request(actor))


async def test_transaction_id_paging_sees_uncommitted_rows_and_rolls_back(
    postgres_raw_db,
):
    """Page in-transaction data without escaping its rollback boundary."""
    from app.services.app_operations.transaction_scope import postgres_graph_transaction

    with pytest.raises(RuntimeError, match="rollback probe"):
        async with postgres_graph_transaction(postgres_raw_db) as transaction:
            for identifier in ("qa-c", "qa-a", "qa-b"):
                await transaction.save(
                    "object", {"id": identifier, "entity": "AdminPagerProbe"}
                )
            # Default paging uses a deterministic ID tie-breaker and applies offset.
            assert await transaction.find_ids(
                "object", {"entity": "AdminPagerProbe"}, limit=1, offset=1
            ) == ["qa-b"]
            assert await transaction.find_ids(
                "object", {"entity": "AdminPagerProbe"}, limit=2, sort=[("id", -1)]
            ) == ["qa-c", "qa-b"]
            assert await transaction.find_ids("object", {}, limit=0) == []
            with pytest.raises(ValueError):
                await transaction.find_ids("object", {}, offset=-1)
            raise RuntimeError("rollback probe")
    assert await postgres_raw_db.find("object", {"entity": "AdminPagerProbe"}) == []
