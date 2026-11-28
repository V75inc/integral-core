"""Detached ASGI integration of real persistence with an injected text-only model."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.agentive.connectors.external_chat import ExternalChatProvision
from app.agentive.services import profile_management, public_web_chat
from app.agentive.services.web_chat_request_budget import WebChatBudgetPolicy
from app.web.full_page_chat_composition import (
    ResolvedWebChatRoute,
    compose_full_page_web_chat,
)

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]
ORIGIN = "https://visit.example.test"
PATH = "/api/public/web-chat"


async def test_composed_route_uses_exact_visitor_state_and_durable_one_turn(
    postgres_raw_db,
    monkeypatch,
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "8a" * 32)
    provision = ExternalChatProvision("workspace", "public_web_chat", "provision")
    other = replace(provision, provision_id="other")
    routes = {
        "first": ResolvedWebChatRoute(provision, ORIGIN, "first", "3"),
        "second": ResolvedWebChatRoute(other, ORIGIN, "second", "3"),
    }
    route = {
        "provision_id": "provision",
        "generation": 3,
        "enabled": True,
        "profile_key": "public",
    }
    published = {
        "key": "public",
        "audience": "public",
        "version": 2,
        "tools": [],
        "persona": "Speak plainly.",
    }
    record = SimpleNamespace(
        public_web_chat=route, profiles={"public": {"published": published}}
    )
    monkeypatch.setattr(
        profile_management,
        "live_public_service_identity",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(profile_management, "record", AsyncMock(return_value=record))
    # No global readiness override or ProfileHost registration may help this app.
    global_ready = AsyncMock(
        side_effect=AssertionError("Must use request-bound transport authority")
    )
    monkeypatch.setattr(public_web_chat, "transport_ready", global_ready)

    async def resolve(key):
        return routes.get(key)

    async def ready(current):
        return routes.get(current.route_key) == current

    calls = []

    async def model(prompt):
        calls.append(prompt)
        assert set(vars(prompt)) == {
            "instructions",
            "persona",
            "history",
            "visitor_text",
        }
        assert (
            "no tools" in prompt.instructions
            and "private workspace" in prompt.instructions
        )
        assert prompt.persona == "Speak plainly."
        if prompt.visitor_text == "Change during inference":
            routes["first"] = replace(routes["first"], route_epoch="4")
            route["generation"] = 4
            return "Must not be exposed."
        return "One durable plain answer."

    # Like the serving host, initialize the standard object collection before
    # transaction-only rate budget requests. No connector state is written.
    await postgres_raw_db.get("object", "o.missing")
    app = FastAPI()
    app.include_router(
        compose_full_page_web_chat(
            database=postgres_raw_db,
            resolve_route=resolve,
            readiness=ready,
            complete=model,
            budget_policy=WebChatBudgetPolicy(60, 100, 100, 50, 50, 25, 25, 20),
            budget_timeout_seconds=1.0,
            max_parallel_budget_acquisitions=4,
        )
    )
    headers = {"Origin": ORIGIN, "Sec-Fetch-Site": "same-origin"}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=ORIGIN, headers=headers
    ) as browser:
        start = await browser.post(f"{PATH}/first/session", json={"mode": "resume"})
        assert start.json() == {"kind": "ready", "messages": []}, start.status_code
        original_cookie = start.cookies.get("__Secure-integral_web_chat")
        assert original_cookie and original_cookie not in start.text
        body = {"id": "first-event", "text": "Hello"}
        sent = await browser.post(f"{PATH}/first/messages", json=body)
        assert sent.json()["messages"][-1]["text"] == "One durable plain answer."
        assert original_cookie not in sent.text
        await browser.post(f"{PATH}/first/messages", json=body)
        assert len(calls) == 1
        resumed = await browser.post(f"{PATH}/first/session", json={"mode": "resume"})
        assert resumed.json() == sent.json() and "set-cookie" not in resumed.headers
        foreign = await browser.post(
            f"{PATH}/second/session",
            json={"mode": "resume"},
            headers={"Cookie": f"__Secure-integral_web_chat={original_cookie}"},
        )
        assert foreign.json()["kind"] != "ready"
        assert len(calls) == 1
        rotated = await browser.post(f"{PATH}/first/session", json={"mode": "new"})
        assert rotated.json() == {"kind": "ready", "messages": []}
        replacement = rotated.cookies.get("__Secure-integral_web_chat")
        assert (
            replacement
            and replacement != original_cookie
            and replacement not in rotated.text
        )
        old = await browser.post(
            f"{PATH}/first/session",
            json={"mode": "resume"},
            headers={"Cookie": f"__Secure-integral_web_chat={original_cookie}"},
        )
        assert old.json() == {"kind": "expired"}
        revoked = await browser.post(
            f"{PATH}/first/messages",
            json={"id": "revoked-event", "text": "Change during inference"},
        )
        assert revoked.json() in ({"kind": "expired"}, {"kind": "unavailable"})
        assert "Must not be exposed" not in revoked.text
        again = await browser.post(f"{PATH}/first/session", json={"mode": "resume"})
        assert again.json() == {"kind": "expired"}
        assert len(calls) == 2
    global_ready.assert_not_awaited()
