"""Prove detached reference persistence and revision CAS in disposable PostgreSQL."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from jvspatial.db.factory import create_database

from app.agentive.connectors.external_chat import ExternalChatProvision
from app.web.web_chat_reference_store import PostgresWebChatReferenceStore

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def test_rotation_revocation_and_exact_lookup_across_pools(postgres_raw_db):
    """Normal host lifecycle is durable across independent pool handles."""
    install = AsyncMock(return_value=True)
    transport = AsyncMock(return_value=True)
    exact = ExternalChatProvision("ref-workspace", "public_web_chat", "ref-provision")
    other = ExternalChatProvision("other-workspace", "public_web_chat", "ref-provision")

    def store(database):
        return PostgresWebChatReferenceStore(
            database=database, install_readiness=install, transport_readiness=transport
        )

    async def issue(host, revision, epoch="4", version=3):
        return await host.issue(
            exact,
            expected_revision=revision,
            origin="https://example.invalid",
            epoch=epoch,
            profile_key="public",
            published_version=version,
        )

    one = store(postgres_raw_db)
    assert (await one.observe(exact)).revision == 0
    assert await one.install_ready(exact.workspace_id)
    install.assert_awaited_once_with(exact.workspace_id)
    first = await issue(one, 0)
    assert first.revision == 1 and len(first.approved.key) == 43
    assert await one.by_provision(other) is None
    pool = create_database(db_type="postgres")
    try:
        two = store(pool)
        assert await two.by_key(first.approved.key) == first.approved
        assert await two.by_provision(exact) == first.approved
        assert await two.transport_ready(first.approved)
        results = await asyncio.gather(issue(one, 1, "5", 4), issue(two, 1, "6", 5))
        (winner,) = (result for result in results if result is not None)
        assert winner.revision == 2 and winner.approved.key != first.approved.key
        for host in (one, two):
            assert await host.by_key(first.approved.key) is None
            assert await host.by_key(winner.approved.key) == winner.approved
            assert await host.by_provision(exact) == winner.approved
            assert not await host.transport_ready(first.approved)
        removed = await two.revoke(exact, expected_revision=2)
        assert removed.revision == 3 and removed.approved is None
        assert await one.by_key(winner.approved.key) is None
        assert await one.by_provision(exact) is None
        assert await issue(one, 2) is None and await issue(two, 0) is None
        assert await one.observe(exact) == removed
        assert not await postgres_raw_db.find(
            "object", {"entity": "WebChatRouteKey"}, limit=1
        )
    finally:
        await pool.close()


async def test_issuer_rejects_unapproved_metadata_and_scopes(postgres_raw_db):
    """Malformed host-provided metadata cannot become a reference."""
    host = PostgresWebChatReferenceStore(
        database=postgres_raw_db,
        install_readiness=AsyncMock(return_value=False),
        transport_readiness=AsyncMock(return_value=False),
    )
    provision = ExternalChatProvision("workspace", "public_web_chat", "provision")
    assert not await host.install_ready(provision.workspace_id)
    assert await host.by_key("not-an-issued-reference") is None
    with pytest.raises(ValueError, match="Invalid Web Chat host approval"):
        await host.issue(
            provision,
            expected_revision=0,
            origin="http://example.invalid",
            epoch="4",
            profile_key="public",
            published_version=1,
        )
    assert (await host.observe(provision)).revision == 0

    blocked = await host.revoke(provision, expected_revision=0)
    assert blocked.revision == 1 and blocked.approved is None
    assert (
        await host.issue(
            provision,
            expected_revision=0,
            origin="https://example.invalid",
            epoch="4",
            profile_key="public",
            published_version=1,
        )
        is None
    )
