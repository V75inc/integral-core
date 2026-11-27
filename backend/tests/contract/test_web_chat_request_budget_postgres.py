"""Opt-in atomic cap and deadline tests against two real PostgreSQL clients."""

import asyncio
import time

import pytest
from jvspatial.db.factory import create_database
from jvspatial.db.transaction import transaction_context

from app.agentive.connectors.external_chat import (
    ExternalChatConversation,
    ExternalChatProvision,
)
from app.agentive.services.public_web_chat import PublicWebChatScope
from app.agentive.services.web_chat_admission import VerifiedFullPageSession
from app.agentive.services.web_chat_request_budget import (
    _NAMESPACE,
    PostgresWebChatRequestBudget,
    WebChatBudgetPolicy,
)

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


@pytest.fixture
async def budgets(postgres_raw_db):
    """Use independent pools against the disposable fixture database."""
    other = create_database(db_type="postgres")
    args = dict(
        policy=WebChatBudgetPolicy(60, 7, 7, 5, 5, 4, 4, 1),
        acquire_seconds=0.15,
        max_parallel_acquisitions=8,
        clock=lambda: 120.0,
    )
    try:
        yield (
            PostgresWebChatRequestBudget(database=postgres_raw_db, **args),
            PostgresWebChatRequestBudget(database=other, **args),
            postgres_raw_db,
        )
    finally:
        await other.close()


def _session(provision, visitor):
    return VerifiedFullPageSession(
        ExternalChatConversation(provision, "fixture-conversation"),
        PublicWebChatScope(provision.workspace_id, visitor, "public-fixture", 1, 1),
    )


async def test_shared_budget_atomic_across_connections(budgets):
    """Cross-pool races cannot double grant a visitor slot or spend on denial."""
    a, b, database = budgets
    route = ExternalChatProvision("workspace", "public_web_chat", "provision")
    visitor = _session(route, "same-visitor")
    await database.get("object", "o.missing")  # isolate warm transaction races
    assert sorted(
        await asyncio.gather(a("send", route, visitor), b("send", route, visitor))
    ) == [False, True]
    for other in ("two", "three", "four"):
        assert await b("send", route, _session(route, other))
    assert not await a("send", route, _session(route, "excess"))
    next_route = ExternalChatProvision(
        "workspace", "public_web_chat", "other-provision"
    )
    assert await a("send", next_route, _session(next_route, "different"))
    assert not await b("send", next_route, _session(next_route, "another"))
    third = ExternalChatProvision("other-workspace", "public_web_chat", "another")
    assert await a("send", third, _session(third, "allowance-one"))
    assert await b("send", third, _session(third, "allowance-two"))
    assert not await a("send", third, _session(third, "globally-denied"))


async def test_held_shared_lock_deadline_then_recovery_does_not_spend_capacity(budgets):
    """A waiting request denies promptly; cancellation rolls back its transaction."""
    a, b, database = budgets
    route = ExternalChatProvision("workspace", "public_web_chat", "provision")
    async with transaction_context(database) as tx:
        await tx.advisory_lock(_NAMESPACE, "public")
        started = time.monotonic()
        assert not await asyncio.wait_for(b("start", route, None), 1.0)
        assert time.monotonic() - started < 0.8
    await asyncio.wait_for(asyncio.gather(*b._active, return_exceptions=True), 2)
    for adapter in (a, b, a, b):
        assert await adapter("start", route, None)
    assert not await b("start", route, None)
