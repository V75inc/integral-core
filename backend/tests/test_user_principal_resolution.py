"""Tests for principal-id → User node batch resolution."""

from unittest.mock import AsyncMock, patch

import pytest

from app.models.nodes import User
from app.services.permissions import batch_resolve_users_by_principal_ids


def _user(*, node_id: str, auth_id: str = "", display_name: str = "Alice") -> User:
    user = User(
        id=node_id,
        user_id=auth_id,
        display_name=display_name,
    )
    return user


@pytest.mark.asyncio
async def test_batch_resolve_maps_auth_user_id_to_user_node():
    auth_id = "o.User.auth123"
    node_id = "n.User.graph456"
    user = _user(node_id=node_id, auth_id=auth_id)

    with patch(
        "app.services.permissions.User.find",
        new_callable=AsyncMock,
        side_effect=[
            [],  # node-id pass
            [user],  # user_id pass
        ],
    ):
        result = await batch_resolve_users_by_principal_ids([auth_id])

    assert result[auth_id] is user
    assert result[node_id] is user


@pytest.mark.asyncio
async def test_batch_resolve_maps_graph_node_id_to_user_node():
    auth_id = "o.User.auth123"
    node_id = "n.User.graph456"
    user = _user(node_id=node_id, auth_id=auth_id)

    with patch(
        "app.services.permissions.User.find",
        new_callable=AsyncMock,
        return_value=[user],
    ):
        result = await batch_resolve_users_by_principal_ids([node_id])

    assert result[node_id] is user
    assert result[auth_id] is user
