"""Qualify private Web Chat visitor session CAS across PostgreSQL pools."""

import asyncio
import hashlib
import json
from unittest.mock import AsyncMock

import pytest
from jvspatial.db.factory import create_database

from app.agentive.connectors.external_chat import ExternalChatProvision
from app.agentive.services import public_web_chat
from app.agentive.services.connection_profiles import ProfileDenied
from app.agentive.services.web_chat_visitor_sessions import (
    PostgresBrowserVisitorSessions,
)

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def test_digest_only_and_one_rotation_with_independent_postgres_pools(
    postgres_raw_db,
    monkeypatch,
):
    """Issue, resume using a fresh adapter, and race two exact old credentials."""
    provision = ExternalChatProvision(
        "disposable-workspace", "public_web_chat", "private-route"
    )
    epoch = "9"
    racing = False
    arrived = 0
    gate = asyncio.Event()
    ready = AsyncMock(return_value=True)

    async def admit(
        workspace, visitor, *, existing, expected_provision_id, transport_ready
    ):
        scope = public_web_chat.PublicWebChatScope(workspace, visitor, "public", 2, 9)
        if (
            not transport_ready
            or workspace != provision.workspace_id
            or expected_provision_id != provision.provision_id
            or (existing is not None and existing != scope)
        ):
            raise ProfileDenied("Not admitted by the isolated fixture")
        nonlocal arrived
        if racing and existing is not None and arrived < 2:
            arrived += 1
            if arrived == 2:
                gate.set()
            await asyncio.wait_for(gate.wait(), timeout=5)
        return scope

    monkeypatch.setattr(public_web_chat, "admit", admit)
    creator = PostgresBrowserVisitorSessions(database=postgres_raw_db, readiness=ready)
    first_request = creator.bind(provision, epoch, None)
    first = await first_request.open("resume")
    assert first.kind == "current"
    original = first_request.take_issued_credential()
    assert original
    [stored] = await postgres_raw_db.find(
        "object", {"entity": "WebChatVisitorSession"}, limit=2
    )
    assert (
        stored["context"]["credential_digest"]
        == hashlib.sha256(original.encode()).hexdigest()
    )
    assert original not in json.dumps(stored, default=str)

    second_pool = create_database(db_type="postgres")
    try:
        # New process-style adapter/context and independent connection pool.
        reopened = PostgresBrowserVisitorSessions(database=second_pool, readiness=ready)
        assert await reopened.bind(provision, epoch, original).current() == first
        rescoped = (
            PostgresBrowserVisitorSessions(database=postgres_raw_db, readiness=ready),
            reopened,
        )
        requests = [store.bind(provision, epoch, original) for store in rescoped]
        racing = True
        results = await asyncio.gather(*(request.open("new") for request in requests))
        assert arrived == 2
        assert sorted(result.kind for result in results) == ["current", "expired"]
        [replacement] = [
            token for req in requests if (token := req.take_issued_credential())
        ]
        assert replacement != original
        winner = next(result for result in results if result.kind == "current")
        assert winner.session.conversation != first.session.conversation
        for store in rescoped:
            assert (
                await store.bind(provision, epoch, original).current()
            ).kind == "expired"
            assert await store.bind(provision, epoch, replacement).current() == winner
        [updated] = await postgres_raw_db.find(
            "object", {"entity": "WebChatVisitorSession"}, limit=2
        )
        assert updated["id"] == stored["id"]
        assert (
            updated["context"]["credential_digest"]
            == hashlib.sha256(replacement.encode()).hexdigest()
        )
        assert all(
            token not in json.dumps(updated, default=str)
            for token in (original, replacement)
        )
    finally:
        await second_pool.close()
