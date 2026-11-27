"""Prove one-use embed bootstrap and policy fences in isolated PostgreSQL."""

import asyncio
import hashlib
import json
import re
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from jvspatial.db.factory import create_database

from app.agentive.connectors.external_chat import ExternalChatProvision
from app.web import web_chat_embed_credentials as credentials
from app.web.web_chat_embed_policy import ManagedWebChatEmbedPolicy

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]

POLICY = ManagedWebChatEmbedPolicy(
    ExternalChatProvision("workspace", "public_web_chat", "provision"),
    "5",
    "public",
    3,
    2,
    "https://parent.example.invalid",
    "https://embed.example.invalid",
    "embed-opaque",
)


def bind(database, read, policy=POLICY):
    return credentials.PostgresWebChatEmbedCredentials(
        database=database,
        read_current_policy=read,
    ).bind(
        policy=policy,
        served_origin=policy.embed_origin,
        raw_path=f"/embed/{policy.embed_reference}",
    )


async def test_exactly_one_seed_exchange_across_independent_pools(postgres_raw_db):
    """One prompt-free exchange, digest-only storage and no cookie authority."""
    read = AsyncMock(return_value=POLICY)
    first = bind(postgres_raw_db, read)
    seed = await first.issue_seed()
    assert re.fullmatch(r"iweb_[A-Za-z0-9_-]{86}", seed)
    [record] = await postgres_raw_db.find(
        "object", {"entity": "WebChatEmbedCredential"}, limit=2
    )
    assert (
        record["context"]["credential_digest"]
        == hashlib.sha256(seed.encode()).hexdigest()
    )
    assert (
        0
        < record["context"]["expires_at"] - int(time.time())
        <= credentials.SEED_TTL_SECONDS
    )
    assert seed not in json.dumps(record, default=str)
    assert seed[5:48] not in record["id"]
    assert await first.current(seed) is None
    pool = create_database(db_type="postgres")
    try:
        second = bind(pool, read)
        results = await asyncio.gather(
            first.consume_seed(seed), second.consume_seed(seed)
        )
        [winner] = [result for result in results if result is not None]
        token = winner.credential
        assert re.fullmatch(r"iwes_[A-Za-z0-9_-]{86}", token)
        assert token not in repr(winner)
        assert winner.visitor.visitor_id != winner.visitor.conversation_id
        assert (
            0
            < winner.visitor.expires_at - int(time.time())
            <= credentials.SESSION_TTL_SECONDS
        )
        [stored] = await postgres_raw_db.find(
            "object", {"entity": "WebChatEmbedCredential"}, limit=2
        )
        assert stored["id"] == record["id"]
        assert (
            stored["context"]["credential_digest"]
            == hashlib.sha256(token.encode()).hexdigest()
        )
        assert all(
            value not in json.dumps(stored, default=str) for value in (seed, token)
        )
        for host in (first, second):
            assert await host.consume_seed(seed) is None
            assert await host.consume_seed(token) is None
            assert await host.current(token) == winner.visitor
        changed = replace(POLICY, policy_revision=3)
        read.return_value = changed
        assert await first.current(token) is None
        assert await bind(pool, read, changed).current(token) is None
    finally:
        await pool.close()


async def test_expiry_and_late_revocation_cannot_release_session(
    postgres_raw_db, monkeypatch
):
    """Double-check policy after the atomic write and deny expired exchanges."""
    clock = [int(time.time())]
    monkeypatch.setattr(credentials, "time", SimpleNamespace(time=lambda: clock[0]))
    read = AsyncMock(return_value=POLICY)
    bound = bind(postgres_raw_db, read)
    seed = await bound.issue_seed()
    clock[0] += credentials.SEED_TTL_SECONDS
    assert await bound.consume_seed(seed) is None
    second = await bound.issue_seed()
    read.side_effect = [POLICY, POLICY, None]
    assert await bound.consume_seed(second) is None
    read.side_effect = None
    assert await bound.consume_seed(second) is None
    slow = await bound.issue_seed()
    policy_reads = 0

    async def slow_exchange(_):
        nonlocal policy_reads
        policy_reads += 1
        if policy_reads == 3:
            clock[0] += credentials.SEED_TTL_SECONDS
        return POLICY

    read.side_effect = slow_exchange
    assert await bound.consume_seed(slow) is None
    read.side_effect = None
    assert await bound.consume_seed(slow) is None
    third = await bound.issue_seed()
    issued = await bound.consume_seed(third)
    assert await bound.current(issued.credential) == issued.visitor
    clock[0] += credentials.SESSION_TTL_SECONDS
    assert await bound.current(issued.credential) is None


async def test_other_valid_scope_and_absent_policy_are_refused(postgres_raw_db):
    """Scope changes and untrusted parsed page origins never authorize a seed."""
    read = AsyncMock(return_value=POLICY)
    first = bind(postgres_raw_db, read)
    seed = await first.issue_seed()
    alternate = replace(POLICY, parent_origin="https://other.example.invalid")
    read.return_value = alternate
    assert await bind(postgres_raw_db, read, alternate).consume_seed(seed) is None
    read.return_value = None
    assert await first.issue_seed() is None
    assert await first.consume_seed(seed) is None
