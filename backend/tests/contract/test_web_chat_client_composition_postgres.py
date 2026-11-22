"""Qualify an unmounted Web Chat API in synthetic PostgreSQL/ASGI only."""

import asyncio
import time
from secrets import token_urlsafe
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.agentive.connectors.external_chat import (
    ExternalChatAssignment,
    ExternalChatProvision,
)
from app.agentive.services import profile_management, public_web_chat
from app.web.web_chat_client_composition import compose_web_chat_client_api
from app.web.web_chat_client_grants import (
    PostgresWebChatClientGrants,
    WebChatClientGrant,
    WebChatGrantReview,
)
from app.web.web_chat_client_sessions import PostgresWebChatClientSessions

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]
ORIGIN = "https://client.example.test"
API = "/api/v1/web-chat"


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


async def test_detached_client_api_partitions_and_rechecks_inference(
    postgres_raw_db, monkeypatch
):
    """Durable receipts stay visitor-scoped and never outlive grant authority."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "9a" * 32)
    workspace, provision_id, profile = "fixture-workspace", "fixture-route", "public"
    assignment = ExternalChatAssignment(
        ExternalChatProvision(workspace, "public_web_chat", provision_id),
        profile,
        "2",
        "3",
    )
    record = SimpleNamespace(
        public_web_chat={
            "provision_id": provision_id,
            "generation": 3,
            "enabled": True,
            "profile_key": profile,
        },
        profiles={
            profile: {
                "published": {
                    "key": profile,
                    "audience": "public",
                    "version": 2,
                    "tools": [],
                    "persona": "Concise test persona.",
                }
            }
        },
    )
    monkeypatch.setattr(profile_management, "record", AsyncMock(return_value=record))
    global_ready = AsyncMock(
        side_effect=AssertionError("Never use the browser/global readiness hook")
    )
    monkeypatch.setattr(public_web_chat, "transport_ready", global_ready)

    async def reviewed(change):
        return change.actor_id == "isolated-admin"

    async def route_authority(target):
        return (
            target == assignment
            and record.public_web_chat["enabled"]
            and str(record.public_web_chat["generation"]) == target.route_epoch
        )

    grants = PostgresWebChatClientGrants(
        database=postgres_raw_db,
        review_authority=reviewed,
        current_authority=route_authority,
    )
    expiry = int(time.time()) + 600
    first = WebChatClientGrant(token_urlsafe(32), assignment, expiry)
    second = WebChatClientGrant(token_urlsafe(32), assignment, expiry)
    key1 = await grants.issue(WebChatGrantReview("isolated-admin", "issue", first))
    key2 = await grants.issue(WebChatGrantReview("isolated-admin", "issue", second))
    assert key1 and key2

    starts, metered, calls = [], [], []
    held, release = asyncio.Event(), asyncio.Event()

    async def start_budget(grant):
        starts.append(grant.grant_id)
        return starts.count(grant.grant_id) <= 2

    async def request_budget(operation, session):
        metered.append((operation, session.grant.grant_id))
        return True

    sessions = PostgresWebChatClientSessions(
        database=postgres_raw_db, grants=grants, start_budget=start_budget
    )

    async def complete(prompt):
        sleep_test = prompt.visitor_text == "Revoke during inference"
        calls.append(prompt)
        assert prompt.persona == "Concise test persona."
        assert not hasattr(prompt, "workspace_id")
        if sleep_test:
            held.set()
            await release.wait()
            return "Unpublished result must remain private."
        return "Synthetic response."

    app = FastAPI()
    app.include_router(
        compose_web_chat_client_api(
            origin=ORIGIN,
            database=postgres_raw_db,
            grants=grants,
            sessions=sessions,
            request_budget=request_budget,
            complete=complete,
        )
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as api:

        async def start(key):
            response = await api.post(f"{API}/sessions", headers=_bearer(key.secret))
            assert response.status_code == 201
            assert "set-cookie" not in response.headers
            assert response.json()["messages"] == []
            assert key.secret not in response.text
            return response.json()["session_token"]

        async def read(token):
            return await api.get(f"{API}/session", headers=_bearer(token))

        async def send(token, event_id, text):
            return await api.post(
                f"{API}/session/messages",
                json={"id": event_id, "text": text},
                headers=_bearer(token),
            )

        visitor_a = await start(key1)
        visitor_b = await start(key1)
        visitor_c = await start(key2)
        assert len({visitor_a, visitor_b, visitor_c}) == 3
        assert (
            await api.post(f"{API}/sessions", headers=_bearer(key1.secret))
        ).status_code == 401
        result = await send(visitor_a, "event", "User A")
        assert result.json()["messages"][-1]["text"] == "Synthetic response."
        assert (await send(visitor_a, "event", "User A")).json() == result.json()
        assert (await send(visitor_a, "event", "Changed")).status_code == 409
        assert len(calls) == 1
        assert (await read(visitor_a)).json() == result.json()
        for other in (visitor_b, visitor_c):
            assert (await read(other)).json() == {"kind": "ready", "messages": []}

        running = asyncio.create_task(
            send(visitor_a, "fresh-event", "Revoke during inference")
        )
        try:
            await asyncio.wait_for(held.wait(), timeout=5)
            pending = await send(visitor_a, "fresh-event", "Revoke during inference")
            assert pending.status_code == 202
            assert pending.json() == {"kind": "pending"}
            assert await grants.revoke(
                WebChatGrantReview("isolated-admin", "revoke", first)
            )
        finally:
            release.set()
            refused = await asyncio.wait_for(running, timeout=5)
        assert refused.status_code == 401
        assert refused.json() == {"kind": "unavailable"}
        assert len(calls) == 2
        assert (await read(visitor_b)).status_code == 401
        assert (await read(visitor_c)).json() == {"kind": "ready", "messages": []}
        record.public_web_chat["generation"] = 4
        assert (await read(visitor_c)).status_code == 401
    assert ("read", second.grant_id) in metered
    assert ("send", first.grant_id) in metered
    global_ready.assert_not_awaited()
