"""Disposable session adapter tests using an in-memory fake for PostgreSQL CAS."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from jvspatial.db.postgres import PostgresDB

from app.agentive.connectors.external_chat import ExternalChatProvision
from app.agentive.services import public_web_chat, web_chat_visitor_sessions
from app.agentive.services.connection_profiles import ProfileDenied
from app.agentive.services.web_chat_visitor_sessions import (
    PostgresBrowserVisitorSessions,
)
from app.models.web_chat_visitor_session import WebChatVisitorSession

ROUTE = ExternalChatProvision("ws", "public_web_chat", "public-provision")


@pytest.fixture
def ports(monkeypatch):
    rows = {}
    db = AsyncMock(spec=PostgresDB)
    db.get.side_effect = lambda _, identity: deepcopy(rows.get(identity))

    async def insert(*, id, **fields):
        created = id not in rows
        if created:
            rows[id] = {"id": id, "entity": "WebChatVisitorSession", "context": fields}
        return SimpleNamespace(id=id), created

    async def update(collection, query, change):
        row = rows.get(query["id"])
        if row is None or any(
            row["context"].get(key.removeprefix("context.")) != value
            for key, value in query.items()
            if key.startswith("context.")
        ):
            return None
        row["context"].update(
            {key.removeprefix("context."): val for key, val in change["$set"].items()}
        )
        return deepcopy(row)

    db.find_one_and_update.side_effect = update
    monkeypatch.setattr(WebChatVisitorSession, "create_if_absent", insert)
    active = {"version": 8, "generation": 4}
    ready = AsyncMock(return_value=True)

    async def admit(
        workspace, visitor, *, existing, expected_provision_id, transport_ready
    ):
        scope = public_web_chat.PublicWebChatScope(
            workspace, visitor, "public", active["version"], active["generation"]
        )
        if (
            not transport_ready
            or expected_provision_id != ROUTE.provision_id
            or (existing is not None and existing != scope)
        ):
            raise ProfileDenied("Denied")
        return scope

    monkeypatch.setattr(public_web_chat, "admit", admit)
    return SimpleNamespace(
        manager=PostgresBrowserVisitorSessions(database=db, readiness=ready),
        ready=ready,
        active=active,
        rows=rows,
        db=db,
    )


@pytest.mark.asyncio
async def test_credential_digest_and_stable_resume_stay_private(ports):
    bound = ports.manager.bind(ROUTE, "4", None)
    assert (await bound.ports.current()).kind == "expired"
    first = await bound.ports.open("resume")
    assert first.kind == "current"
    token = bound.take_issued_credential()
    assert token and bound.take_issued_credential() is None
    assert token not in repr(ports.rows) + repr(first)
    next_request = ports.manager.bind(ROUTE, "4", token)
    assert await next_request.ports.open("resume") == first
    assert next_request.take_issued_credential() is None


@pytest.mark.asyncio
async def test_explicit_new_rotates_one_row_and_rejects_old_credential(ports):
    first = ports.manager.bind(ROUTE, "4", None)
    original = await first.ports.open("resume")
    previous = first.take_issued_credential()
    rotating = ports.manager.bind(ROUTE, "4", previous)
    competing = ports.manager.bind(ROUTE, "4", previous)
    updated = await rotating.ports.open("new")
    assert updated.kind == "current" and updated != original
    assert updated.session.conversation != original.session.conversation
    assert updated.session.scope.visitor_id != original.session.scope.visitor_id
    replacement = rotating.take_issued_credential()
    assert replacement and replacement != previous and len(ports.rows) == 1
    assert (await competing.ports.open("new")).kind == "expired"
    assert (await competing.ports.current()).kind == "expired"
    assert (
        await ports.manager.bind(ROUTE, "4", replacement).ports.current()
    ) == updated


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    ["workspace", "provision", "epoch", "revoked", "publication", "unready", "expiry"],
)
async def test_session_verification_fails_closed_after_scope_or_state_change(
    ports, failure, monkeypatch
):
    binding = ports.manager.bind(ROUTE, "4", None)
    assert (await binding.ports.open("resume")).kind == "current"
    token = binding.take_issued_credential()
    route, epoch = ROUTE, "4"
    if failure == "workspace":
        route = replace(ROUTE, workspace_id="other")
    elif failure == "provision":
        route = replace(ROUTE, provision_id="another")
    elif failure == "epoch":
        epoch = "5"
    elif failure == "revoked":
        next(iter(ports.rows.values()))["context"]["revoked"] = True
    elif failure == "publication":
        ports.active["version"] += 1
    elif failure == "unready":
        ports.ready.return_value = False
    else:
        actual = web_chat_visitor_sessions.time.time()
        monkeypatch.setattr(
            web_chat_visitor_sessions.time, "time", lambda: actual + 90_000
        )
    next_request = ports.manager.bind(route, epoch, token)
    assert (await next_request.ports.open("resume")).kind == "expired"
    assert next_request.take_issued_credential() is None
    ports.db.find_one_and_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_simultaneous_rotation_reaches_cas_but_only_one_commits(ports):
    import asyncio

    first = ports.manager.bind(ROUTE, "4", None)
    assert (await first.ports.open("resume")).kind == "current"
    token = first.take_issued_credential()
    requests = [ports.manager.bind(ROUTE, "4", token) for _ in range(2)]
    both = asyncio.Event()
    original = ports.db.find_one_and_update.side_effect
    waiting = 0

    async def collide(*args):
        nonlocal waiting
        waiting += 1
        if waiting == 2:
            both.set()
        await asyncio.wait_for(both.wait(), timeout=2)
        return await original(*args)

    ports.db.find_one_and_update.side_effect = collide
    results = await asyncio.gather(*(request.ports.open("new") for request in requests))
    assert sorted(result.kind for result in results) == ["current", "expired"]
    assert sum(bool(request.take_issued_credential()) for request in requests) == 1
    assert len(ports.rows) == 1


@pytest.mark.asyncio
async def test_fresh_session_cannot_cross_request_publication_pin(ports):
    """Even a fresh cookie cannot move from the resolved publication to a newer one."""
    bound = ports.manager.bind(ROUTE, "4", None, publication=("public", 8))
    ports.active["version"] = 9
    assert (await bound.ports.open("resume")).kind != "current"
    assert bound.take_issued_credential() is None
    assert not ports.rows
    current = ports.manager.bind(ROUTE, "4", None, publication=("public", 9))
    assert (await current.ports.open("resume")).kind == "current"
