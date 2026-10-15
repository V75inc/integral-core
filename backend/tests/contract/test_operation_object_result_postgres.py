"""Auth-store object IDs cannot disappear from operation dependency inspection."""

import uuid

import pytest
from jvspatial.api.auth.models import User as AuthUser
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.api.errors import InsufficientPermissionsError
from app.services.app_operations.result_access import authorize_replayed_result
from tests.contract.test_operation_nested_result_postgres import operation_context

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def test_raw_auth_identity_object_is_not_released_as_reference_free_output(
    postgres_raw_db,
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, workspace, app, _, _ = await operation_context()
        other = await AuthUser.create(
            email=f"private-output-{uuid.uuid4().hex}@example.com",
            password_hash="test-only-private-hash",
        )
        assert other.id.startswith("o.User.")
        with pytest.raises(InsufficientPermissionsError):
            await authorize_replayed_result(
                {
                    "output": {
                        "source": {
                            "id": other.id,
                            "entity": "User",
                            "password_hash": other.password_hash,
                        }
                    }
                },
                user_id=actor.id,
                workspace_id=workspace.id,
                app_id=app.id,
            )
