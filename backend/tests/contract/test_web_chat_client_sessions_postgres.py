"""Detached client-session tests with grants and disposable PostgreSQL only."""

import hashlib
import re
from dataclasses import replace
from secrets import token_urlsafe
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agentive.connectors.external_chat import (
    ExternalChatAssignment,
    ExternalChatProvision,
)
from app.web import web_chat_client_grants, web_chat_client_sessions
from app.web.web_chat_client_grants import (
    PostgresWebChatClientGrants,
    WebChatClientGrant,
    WebChatGrantReview,
)
from app.web.web_chat_client_sessions import PostgresWebChatClientSessions

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


@pytest.fixture
def authority(monkeypatch):
    now = [10_000]
    clock = SimpleNamespace(time=lambda: now[0])
    monkeypatch.setattr(web_chat_client_grants, "time", clock)
    monkeypatch.setattr(web_chat_client_sessions, "time", clock)
    assignment = ExternalChatAssignment(
        ExternalChatProvision("workspace", "public_web_chat", "provision"),
        "public",
        "7",
        "11",
    )
    active = {assignment}
    approved = set()
    current = AsyncMock(side_effect=lambda exact: exact in active)
    return now, assignment, active, approved, current


def _stores(database, authority, *, ttl=900):
    _, _, _, approved, current = authority
    grant_store = PostgresWebChatClientGrants(
        database=database,
        review_authority=AsyncMock(side_effect=lambda exact: exact in approved),
        current_authority=current,
    )
    budget = AsyncMock(return_value=True)
    return (
        grant_store,
        budget,
        PostgresWebChatClientSessions(
            database=database, grants=grant_store, start_budget=budget, ttl_seconds=ttl
        ),
    )


async def _issue(grants, authority, *, assignment=None, expires=11_000):
    _, default, _, approved, _ = authority
    review = WebChatGrantReview(
        "manager",
        "issue",
        WebChatClientGrant(token_urlsafe(32), assignment or default, expires),
    )
    approved.add(review)
    issued = await grants.issue(review)
    assert issued is not None
    return issued


def _session_record(secret):
    digest = hashlib.sha256(secret.encode("ascii")).hexdigest()
    return f"o.WebChatClientSessionRecord.{digest}"


async def test_independent_visitor_sessions_and_digest_only_durable_lookup(
    postgres_raw_db, authority
):
    grants, budget, sessions = _stores(postgres_raw_db, authority)
    first = await _issue(grants, authority)
    other_assignment = replace(
        first.grant.assignment,
        provision=replace(first.grant.assignment.provision, provision_id="another"),
    )
    authority[2].add(other_assignment)
    second = await _issue(grants, authority, assignment=other_assignment)

    opened = [
        await sessions.open(grant) for grant in (first.grant, first.grant, second.grant)
    ]
    assert all(item is not None for item in opened)
    assert len({item.secret for item in opened}) == 3
    assert len({item.session.visitor_id for item in opened}) == 3
    assert len({item.session.conversation for item in opened}) == 3
    assert [call.args[0] for call in budget.await_args_list] == [
        first.grant,
        first.grant,
        second.grant,
    ]

    second_reader = PostgresWebChatClientSessions(
        database=postgres_raw_db,
        grants=grants,
        start_budget=AsyncMock(
            side_effect=AssertionError("Read consumes no start budget")
        ),
    )
    for item in opened:
        assert re.fullmatch(r"iwcs_[A-Za-z0-9_-]{43}", item.secret)
        assert await second_reader.current(item.secret) == item.session
        row = await postgres_raw_db.get("object", _session_record(item.secret))
        assert item.secret not in repr(row) and item.secret not in repr(item)
        assert first.secret not in repr(row) and second.secret not in repr(row)

    assert opened[0].session.grant == first.grant
    assert opened[2].session.grant == second.grant
    assert await second_reader.current(first.secret) is None
    assert await second_reader.current(opened[0].session.visitor_id) is None
    assert await grants.current(opened[0].secret) is None


async def test_expiry_caps_at_grant_or_fifteen_minutes_and_rejects_invalid_ttl(
    postgres_raw_db, authority
):
    now = authority[0]
    grants, _, sessions = _stores(postgres_raw_db, authority)
    long = await _issue(grants, authority, expires=12_000)
    short = await _issue(grants, authority, expires=10_045)
    long_session = await sessions.open(long.grant)
    short_session = await sessions.open(short.grant)
    assert long_session.session.expires_at == 10_900
    assert short_session.session.expires_at == 10_045
    now[0] = 10_044
    assert await sessions.current(short_session.secret) == short_session.session
    now[0] = 10_045
    assert await sessions.current(short_session.secret) is None
    assert await sessions.open(short.grant) is None
    now[0] = 10_900
    assert await sessions.current(long_session.secret) is None
    assert await grants.is_current(long.grant) is True
    for invalid in (0, 901, True):
        with pytest.raises(ValueError, match="lifetime"):
            _stores(postgres_raw_db, authority, ttl=invalid)


async def test_revocation_and_new_epoch_end_existing_sessions(
    postgres_raw_db, authority
):
    _, assignment, active, approved, _ = authority
    grants, budget, sessions = _stores(postgres_raw_db, authority)
    first = await _issue(grants, authority)
    second = await _issue(grants, authority)
    one = await sessions.open(first.grant)
    two = await sessions.open(second.grant)
    revoke = WebChatGrantReview("manager", "revoke", first.grant)
    approved.add(revoke)
    assert await grants.revoke(revoke) is True
    assert await sessions.current(one.secret) is None
    assert await sessions.current(two.secret) == two.session

    active.clear()
    active.add(replace(assignment, route_epoch="12"))
    assert await sessions.current(two.secret) is None
    budget.reset_mock()
    assert await sessions.open(second.grant) is None
    budget.assert_not_awaited()


async def test_budget_denial_creates_no_session(
    postgres_raw_db, authority, monkeypatch
):
    grants, budget, sessions = _stores(postgres_raw_db, authority)
    issued = await _issue(grants, authority)
    mint = AsyncMock(
        side_effect=AssertionError("Denied start must not generate a credential")
    )
    monkeypatch.setattr(web_chat_client_sessions, "token_urlsafe", mint)
    budget.return_value = False
    assert await sessions.open(issued.grant) is None
    budget.assert_awaited_once_with(issued.grant)
    mint.assert_not_called()


async def test_authority_loss_after_persisted_start_returns_no_session(
    postgres_raw_db, authority, monkeypatch
):
    _, assignment, _, _, current = authority
    grants, budget, sessions = _stores(postgres_raw_db, authority)
    issued = await _issue(grants, authority)

    part = token_urlsafe(32)
    secrets = iter((part, token_urlsafe(32), token_urlsafe(32)))
    monkeypatch.setattr(
        web_chat_client_sessions, "token_urlsafe", lambda _: next(secrets)
    )
    current.reset_mock()
    current.side_effect = [True, True, False, False]
    assert await sessions.open(issued.grant) is None
    budget.assert_awaited_once_with(issued.grant)
    assert current.await_count == 3
    assert all(call.args == (assignment,) for call in current.await_args_list)
    internal_secret = f"iwcs_{part}"
    row = await postgres_raw_db.get("object", _session_record(internal_secret))
    assert row is not None and internal_secret not in repr(row)
    assert await sessions.current(internal_secret) is None
