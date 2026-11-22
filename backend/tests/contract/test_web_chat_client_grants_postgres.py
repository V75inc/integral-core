"""Detached grant lifecycle on disposable PostgreSQL without a serving host."""

import asyncio
import hashlib
import re
from dataclasses import replace
from secrets import token_urlsafe
from unittest.mock import AsyncMock

import pytest

from app.agentive.connectors.external_chat import (
    ExternalChatAssignment,
    ExternalChatProvision,
)
from app.web.web_chat_client_grants import (
    PostgresWebChatClientGrants,
    WebChatClientGrant,
    WebChatGrantReview,
)

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


@pytest.fixture
def authority(monkeypatch):
    """Inject exact, independently changeable review and active assignments."""
    from app.web import web_chat_client_grants

    now = [10_000]
    # Substitute this module's time binding, not the global time module.
    monkeypatch.setattr(
        web_chat_client_grants,
        "time",
        type("Clock", (), {"time": staticmethod(lambda: now[0])}),
    )
    assignment = ExternalChatAssignment(
        ExternalChatProvision("workspace", "public_web_chat", "provision"),
        "persona",
        "7",
        "11",
    )
    approved: set[WebChatGrantReview] = set()
    active = {assignment}
    review = AsyncMock(side_effect=lambda change: change in approved)
    current = AsyncMock(side_effect=lambda value: value in active)
    return now, assignment, approved, active, review, current


def _store(database, state):
    return PostgresWebChatClientGrants(
        database=database, review_authority=state[4], current_authority=state[5]
    )


def _issue(assignment, expires_at=10_300):
    return WebChatGrantReview(
        "manager",
        "issue",
        WebChatClientGrant(token_urlsafe(32), assignment, expires_at),
    )


async def test_approved_grant_persists_only_digest_and_revokes_across_instances(
    postgres_raw_db, authority
):
    _, assignment, approved, _, review, _ = authority
    store = _store(postgres_raw_db, authority)
    change = _issue(assignment)
    approved.add(change)

    first, second = await asyncio.gather(store.issue(change), store.issue(change))
    issued = first if first is not None else second
    assert issued is not None
    assert (first is None) != (second is None)
    assert re.fullmatch(r"iwcg_[A-Za-z0-9_-]{43}", issued.secret)
    assert issued.secret not in repr(issued)
    assert await store.current(issued.grant.grant_id) is None

    digest = hashlib.sha256(issued.secret.encode("ascii")).hexdigest()
    record = await postgres_raw_db.get(
        "object", f"o.WebChatClientGrantRecord.{issued.grant.grant_id}"
    )
    pointer = await postgres_raw_db.get(
        "object", f"o.WebChatClientGrantDigest.{digest}"
    )
    assert record["context"]["credential_digest"] == digest
    assert pointer["context"]["grant_id"] == issued.grant.grant_id
    assert issued.secret not in repr((record, pointer))
    assert record["context"]["connector_kind"] == "public_web_chat"

    other = _store(postgres_raw_db, authority)
    assert await other.current(issued.secret) == issued.grant
    assert await other.is_current(issued.grant) is True
    review.assert_awaited_with(change)
    revoke = replace(change, action="revoke")
    assert await other.revoke(revoke) is False
    assert await store.current(issued.secret) == issued.grant

    approved.add(revoke)
    assert await other.revoke(revoke) is True
    assert await store.current(issued.secret) is None
    assert await store.is_current(issued.grant) is False
    assert await store.issue(change) is None
    assert await other.revoke(revoke) is True


async def test_exact_authority_no_scope_fallback_expired_or_stale_reviews(
    postgres_raw_db, authority
):
    now, assignment, approved, active, review, current = authority
    store = _store(postgres_raw_db, authority)
    change = _issue(assignment)
    assert await store.issue(change) is None
    assert (
        await postgres_raw_db.get(
            "object", f"o.WebChatClientGrantRecord.{change.grant.grant_id}"
        )
        is None
    )
    approved.add(change)
    active.clear()
    assert await store.issue(change) is None
    active.add(assignment)
    issued = await store.issue(change)
    assert issued is not None
    alt = _issue(assignment)
    approved.add(alt)
    second = await store.issue(alt)
    assert second is not None and second.secret != issued.secret
    for wrong in (
        replace(assignment, route_epoch="12"),
        replace(assignment, profile_key="other"),
        replace(assignment, publication_revision="8"),
        replace(
            assignment, provision=replace(assignment.provision, workspace_id="other")
        ),
        replace(
            assignment, provision=replace(assignment.provision, provision_id="other")
        ),
        replace(
            assignment,
            provision=replace(assignment.provision, connector_kind="whatsapp"),
        ),
    ):
        crossed = replace(issued.grant, assignment=wrong)
        approved_revoke = WebChatGrantReview("manager", "revoke", crossed)
        approved.add(approved_revoke)
        assert await store.revoke(approved_revoke) is False
        assert await store.is_current(crossed) is False
        requested = _issue(wrong)
        approved.add(requested)
        assert await store.issue(requested) is None

    active.clear()
    active.add(replace(assignment, publication_revision="8"))
    assert await store.current(issued.secret) is None
    assert await store.is_current(issued.grant) is False
    current.assert_awaited_with(assignment)
    revoke = replace(change, action="revoke")
    approved.add(revoke)
    assert await store.revoke(revoke) is True  # retired publication is still revocable
    active.add(assignment)
    assert await store.current(second.secret) == second.grant
    now[0] = second.grant.expires_at
    assert await store.current(second.secret) is None
    assert await store.is_current(second.grant) is False
    assert await store.issue(replace(alt, actor_id="another manager")) is None


async def test_revocation_during_authority_check_denies_credential_read(
    postgres_raw_db, authority
):
    _, assignment, approved, _, _, current = authority
    store = _store(postgres_raw_db, authority)
    change = _issue(assignment)
    revoke = replace(change, action="revoke")
    approved.update((change, revoke))
    issued = await store.issue(change)
    assert issued is not None
    started = asyncio.Event()
    finish = asyncio.Event()

    async def paused(exact):
        assert exact == assignment
        started.set()
        await finish.wait()
        return True

    current.side_effect = paused
    reading = asyncio.create_task(store.current(issued.secret))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        assert await _store(postgres_raw_db, authority).revoke(revoke) is True
    finally:
        finish.set()
    assert await reading is None


async def test_postcommit_authority_loss_discloses_no_secret_on_retry(
    postgres_raw_db, authority
):
    _, assignment, approved, _, _, current = authority
    store = _store(postgres_raw_db, authority)
    change = _issue(assignment)
    approved.add(change)
    current.side_effect = [True, False]
    assert await store.issue(change) is None
    current.side_effect = lambda exact: exact == assignment
    assert await store.issue(change) is None
    revoke = replace(change, action="revoke")
    approved.add(revoke)
    assert await store.revoke(revoke) is True


async def test_failed_grant_write_rolls_back_digest_index(
    postgres_raw_db, authority, monkeypatch
):
    from app.web import web_chat_client_grants

    _, assignment, approved, _, _, _ = authority
    store = _store(postgres_raw_db, authority)
    change = _issue(assignment)
    approved.add(change)
    fixture_secret_part = token_urlsafe(32)

    async def fail_to_export(_):
        raise RuntimeError("Synthetic record write failure")

    with monkeypatch.context() as patch:
        patch.setattr(
            web_chat_client_grants, "token_urlsafe", lambda _: fixture_secret_part
        )
        patch.setattr(
            web_chat_client_grants.WebChatClientGrantRecord, "export", fail_to_export
        )
        with pytest.raises(RuntimeError, match="Synthetic record write failure"):
            await store.issue(change)

    fixture_secret = f"iwcg_{fixture_secret_part}"
    digest = hashlib.sha256(fixture_secret.encode("ascii")).hexdigest()
    assert (
        await postgres_raw_db.get("object", f"o.WebChatClientGrantDigest.{digest}")
        is None
    )
    assert await store.current(fixture_secret) is None
    assert await store.issue(change) is not None


async def test_postcommit_manager_downgrade_discloses_no_secret(
    postgres_raw_db, authority
):
    _, assignment, approved, _, review, _ = authority
    store = _store(postgres_raw_db, authority)
    change = _issue(assignment)
    approved.add(change)
    seen = []

    async def allow_until_downgrade(exact):
        seen.append(exact)
        return exact == change and len(seen) == 1

    review.side_effect = allow_until_downgrade
    assert await store.issue(change) is None
    assert seen == [change, change]
    review.side_effect = lambda exact: exact in approved
    assert await store.issue(change) is None
    revoke = replace(change, action="revoke", actor_id="new manager")
    approved.add(revoke)
    assert await store.revoke(revoke) is True
