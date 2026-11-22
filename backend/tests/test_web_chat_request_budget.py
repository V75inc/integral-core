"""Source-only public budget policy, isolation and deadline checks."""

import asyncio
import time
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace

import pytest
from jvspatial.db.postgres import PostgresDB

from app.agentive.connectors.external_chat import (
    ExternalChatConversation,
    ExternalChatProvision,
)
from app.agentive.services import web_chat_request_budget as budget
from app.agentive.services.public_web_chat import PublicWebChatScope
from app.agentive.services.web_chat_admission import VerifiedFullPageSession


def provision(workspace="workspace-one", key="installed-provision"):
    return ExternalChatProvision(workspace, "public_web_chat", key)


def session(scope, visitor, conversation="conversation"):
    return VerifiedFullPageSession(
        ExternalChatConversation(scope, conversation),
        PublicWebChatScope(scope.workspace_id, visitor, "public-profile", 2, 3),
    )


class FakeTransaction:
    """Track all-or-none business logic without claiming PostgreSQL guarantees."""

    def __init__(self, store):
        self.store = store
        self.pending = {}
        self.locks = []

    async def advisory_lock(self, namespace, key):
        self.locks.append((namespace, key))

    async def get(self, collection, record_id):
        assert collection == "object"
        assert self.locks == [(budget._NAMESPACE, "public")]
        return deepcopy(self.store.get(record_id))

    async def save(self, collection, row):
        assert collection == "object"
        self.pending[row["id"]] = deepcopy(row)


@pytest.fixture
def checked_budget(monkeypatch):
    store = {}
    fault = set()
    clock = [120.0]
    database = PostgresDB(dsn="postgresql://fixture@127.0.0.1:1/unused")

    @asynccontextmanager
    async def transaction(selected):
        assert selected is database
        tx = FakeTransaction(store)
        yield tx
        if fault:
            raise RuntimeError("unconfirmed commit")
        store.update(tx.pending)

    monkeypatch.setattr(budget, "transaction_context", transaction)
    monkeypatch.setattr(budget, "PostgresTransaction", FakeTransaction)
    adapter = budget.PostgresWebChatRequestBudget(
        database=database,
        policy=budget.WebChatBudgetPolicy(60, 4, 5, 3, 4, 2, 3, 1),
        acquire_seconds=0.1,
        max_parallel_acquisitions=2,
        clock=lambda: clock[0],
    )
    return adapter, store, clock, fault, database


def test_invalid_configuration_cannot_select_unbounded_defaults():
    p = budget.WebChatBudgetPolicy(60, 10, 12, 5, 5, 2, 3, 2)
    with pytest.raises(ValueError):
        replace(p, visitor_sends=4)
    with pytest.raises(ValueError):
        replace(p, public_starts=True)
    with pytest.raises(ValueError):
        replace(p, window_seconds=0)
    with pytest.raises(TypeError):
        budget.PostgresWebChatRequestBudget(
            database=object(),
            policy=p,
            acquire_seconds=0.1,
            max_parallel_acquisitions=1,
        )


@pytest.mark.asyncio
async def test_verified_scope_no_raw_identity_and_independent_start_and_send(
    checked_budget,
):
    adapter, rows, _, _, database = checked_budget
    a = provision()
    b = provision("another-workspace")
    v = session(a, "verified-opaque-visitor")
    assert not await adapter("send", a, None)
    assert not await adapter("send", b, v)
    assert not await adapter("send", replace(a, provision_id="other"), v)
    assert not await adapter(
        "send", a, replace(v, scope=replace(v.scope, workspace_id="wrong"))
    )
    assert not await adapter("start", a, v)
    assert not await adapter("unknown", a, None)
    async with database.tenant("ambient-scope"):
        assert not await adapter("start", a, None)
    assert rows == {}
    assert await adapter("send", a, v)
    assert not await adapter(
        "send", a, session(a, v.scope.visitor_id, "another-conversation")
    )
    assert await adapter("start", a, None)
    assert await adapter("start", a, None)
    assert not await adapter("start", a, None)
    assert await adapter("send", a, session(a, "other-verified-visitor"))
    assert await adapter("send", b, session(b, v.scope.visitor_id))
    stored = repr(rows)
    for private in (
        "verified-opaque-visitor",
        "workspace-one",
        "installed-provision",
        "public-profile",
    ):
        assert private not in stored


@pytest.mark.asyncio
async def test_denied_provision_does_not_spend_workspace_or_public_allowance(
    checked_budget,
):
    adapter, rows, clock, _, _ = checked_budget
    first, second, other = (
        provision(),
        provision(key="replacement"),
        provision("other-workspace"),
    )
    assert await adapter("start", first, None)
    assert await adapter("start", first, None)
    assert not await adapter("start", first, None)
    assert await adapter("start", second, None)
    assert not await adapter(
        "start", second, None
    )  # workspace cap survives reprovisioning
    assert await adapter("start", other, None)
    assert not await adapter("start", other, None)  # deployment cap
    assert len(rows) == 6
    clock[0] = 180.0
    assert await adapter("start", first, None)
    assert len(rows) == 6
    clock[0] = 179.0
    assert not await adapter("start", first, None)
    clock[0] = 180.0
    assert await adapter("start", first, None)
    assert not await adapter("start", first, None)


@pytest.mark.asyncio
async def test_unconfirmed_commit_and_mixed_policy_fail_closed(checked_budget):
    adapter, rows, _, fault, database = checked_budget
    route = provision()
    fault.add("uncertain-commit")
    assert not await adapter("start", route, None)
    assert rows == {}
    fault.clear()
    assert await adapter("start", route, None)
    different = budget.PostgresWebChatRequestBudget(
        database=database,
        policy=replace(adapter._policy, public_starts=9),
        acquire_seconds=0.1,
        max_parallel_acquisitions=2,
        clock=lambda: 120.0,
    )
    previous = deepcopy(rows)
    assert not await different("start", route, None)
    assert rows == previous
    assert await adapter("start", route, None)


@pytest.mark.asyncio
async def test_deadline_bounds_caller_and_cancelled_cleanup_holds_capacity(
    monkeypatch, checked_budget
):
    adapter, rows, _, _, _ = checked_budget
    route = provision()
    adapter._max_parallel = 1
    adapter._timeout = 0.025
    started = asyncio.Event()
    cleanup = asyncio.Event()
    released = asyncio.Event()

    async def slow(_operation, _buckets):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup.set()
            await released.wait()

    monkeypatch.setattr(adapter, "_take", slow)
    time_start = time.monotonic()
    running = asyncio.create_task(adapter("start", route, None))
    await asyncio.wait_for(started.wait(), 1)
    assert not await adapter("start", route, None)
    assert not await asyncio.wait_for(running, 0.5)
    assert time.monotonic() - time_start < 0.4
    await asyncio.wait_for(cleanup.wait(), 1)
    assert not await adapter("start", route, None)
    assert len(adapter._active) == 1
    released.set()
    await asyncio.gather(*adapter._active, return_exceptions=True)
    assert not adapter._active
    assert rows == {}
