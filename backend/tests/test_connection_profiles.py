"""Synthetic connection policy contracts; no transport or model calls."""

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from app.agentive.services.connection_profiles import (
    ConnectionGrant,
    ConnectionProfile,
    ConnectionProfileExecutor,
    ProfileDenied,
)


@pytest.fixture
def policy():
    grant = ConnectionGrant(
        "staff-line", "verified-user", "workspace", "staff", "staff", True
    )
    resolve = AsyncMock(return_value=grant)
    authorize = AsyncMock(return_value=True)
    read = AsyncMock(return_value={"text": "permitted"})
    write = AsyncMock()
    executor = ConnectionProfileExecutor(
        (
            ConnectionProfile(
                "staff", "staff", frozenset({"read", "write"}), frozenset({"write"})
            ),
        ),
        resolve,
        authorize,
        {"read": read, "write": write},
    )
    return executor, grant, resolve, authorize, read, write


@pytest.mark.asyncio
async def test_current_permission_and_explicit_omissions(policy):
    executor, grant, _, authorize, read, write = policy
    session = await executor.open(grant.connection_id, grant.principal_id)
    assert await executor.execute(
        grant.connection_id, grant.principal_id, session, "read", {}
    ) == {"text": "permitted"}
    for tool in ("write", "unknown"):
        with pytest.raises(ProfileDenied):
            await executor.execute(
                grant.connection_id, grant.principal_id, session, tool, {}
            )
    write.assert_not_called()
    authorize.return_value = False
    with pytest.raises(ProfileDenied):
        await executor.execute(
            grant.connection_id, grant.principal_id, session, "read", {}
        )
    assert read.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"verified_staff": False},
        {"enabled": False},
        {"workspace_id": "other"},
        {"principal_id": "other"},
        {"connection_id": "public-line"},
        {"profile_key": "unknown"},
        {"audience": "public"},
    ],
)
async def test_changed_authority_never_reuses_history_or_executes(policy, change):
    executor, grant, resolve, _, read, _ = policy
    session = await executor.open(grant.connection_id, grant.principal_id)
    resolve.return_value = replace(grant, **change)
    with pytest.raises(ProfileDenied):
        await executor.execute(
            grant.connection_id, grant.principal_id, session, "read", {}
        )
    read.assert_not_called()


@pytest.mark.asyncio
async def test_connections_and_principals_have_separate_sessions(policy):
    executor, grant, resolve, _, _, _ = policy
    session = await executor.open(grant.connection_id, grant.principal_id)
    for changed in (
        replace(grant, connection_id="other-line"),
        replace(grant, principal_id="other-user"),
    ):
        resolve.return_value = changed
        other = await executor.open(changed.connection_id, changed.principal_id)
        assert other != session
        with pytest.raises(ProfileDenied):
            await executor.execute(
                changed.connection_id, changed.principal_id, session, "read", {}
            )


@pytest.mark.asyncio
async def test_human_only_operation_refuses_agent_confirmation_arguments():
    grant = ConnectionGrant("public", "visitor", "workspace", "public", "public")
    handler = AsyncMock(return_value="receipt")
    executor = ConnectionProfileExecutor(
        (
            ConnectionProfile(
                "public",
                "public",
                frozenset({"confirm"}),
                human_only_tools=frozenset({"confirm"}),
            ),
        ),
        AsyncMock(return_value=grant),
        AsyncMock(return_value=True),
        {"confirm": handler},
    )
    session = await executor.open("public", "visitor")
    with pytest.raises(ProfileDenied):
        await executor.execute(
            "public",
            "visitor",
            session,
            "confirm",
            {"confirmed": True, "origin": "human"},
        )
    handler.assert_not_called()
    assert (
        await executor.execute(
            "public", "visitor", session, "confirm", {}, origin="human"
        )
        == "receipt"
    )
