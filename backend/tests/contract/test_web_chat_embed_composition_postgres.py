"""Prove the detached embed path with built assets and synthetic inputs only."""

import os
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.agentive.connectors.external_chat import ExternalChatProvision
from app.agentive.services import profile_management, public_web_chat
from app.web.web_chat_embed_composition import compose_web_chat_embed
from app.web.web_chat_embed_page_shell import load_web_chat_embed_assets
from app.web.web_chat_embed_policy import ManagedWebChatEmbedPolicy

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]
ORIGIN = "https://embed.example.invalid"
PARENT = "https://test-website.example.invalid"
REF = "embed-reference"
API = f"/api/public/web-chat-embed/{REF}"
PAGE = f"/embed/{REF}"


async def test_built_embed_without_cookies_and_policy_invalidation(
    postgres_raw_db, monkeypatch
):
    """A real built iframe flow consumes one seed; scoped sessions do not cross."""
    directory = os.environ.get("INTEGRAL_TEST_WEB_CHAT_EMBED_DIST")
    if directory is None:
        pytest.skip("Set INTEGRAL_TEST_WEB_CHAT_EMBED_DIST to the standalone build")
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "7e" * 32)
    provision = ExternalChatProvision("fixture", "public_web_chat", "fixture-route")
    record = SimpleNamespace(
        public_web_chat={
            "provision_id": "fixture-route",
            "generation": 7,
            "enabled": True,
            "profile_key": "public",
        },
        profiles={
            "public": {
                "published": {
                    "key": "public",
                    "audience": "public",
                    "version": 5,
                    "tools": [],
                    "persona": "Fixture persona.",
                }
            }
        },
    )
    monkeypatch.setattr(profile_management, "record", AsyncMock(return_value=record))
    unavailable_global = AsyncMock(
        side_effect=AssertionError("Global hook is not embed authority")
    )
    monkeypatch.setattr(public_web_chat, "transport_ready", unavailable_global)
    policy = ManagedWebChatEmbedPolicy(
        provision, "7", "public", 5, 2, PARENT, ORIGIN, REF
    )
    counters = []
    model_calls = []

    async def host_ready(origin):
        return origin == ORIGIN

    async def read_policy(route):
        return policy if policy is not None and route == REF else None

    async def budget(op, current_policy, visitor):
        assert current_policy == policy
        assert (visitor is None) == (op in ("page", "start"))
        counters.append(op)
        return True

    async def complete(prompt):
        nonlocal policy
        assert prompt.persona == "Fixture persona."
        model_calls.append(prompt)
        if prompt.visitor_text == "Policy changes during inference":
            policy = replace(policy, policy_revision=3)
            return "A reply that must stay private."
        return "A real test reply."

    assets = load_web_chat_embed_assets(Path(directory).resolve())
    embed = compose_web_chat_embed(
        origin=ORIGIN,
        database=postgres_raw_db,
        assets=assets,
        host_ready=host_ready,
        read_current_policy=read_policy,
        budget=budget,
        complete=complete,
    )
    app = FastAPI(redirect_slashes=False)
    app.include_router(embed.page_router)
    app.include_router(embed.api_router)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=ORIGIN
    ) as browser:
        iframe = {"Sec-Fetch-Dest": "iframe"}
        same_origin = {"Origin": ORIGIN, "Sec-Fetch-Site": "same-origin"}

        async def bootstrap():
            page = await browser.get(PAGE, headers=iframe)
            assert page.status_code == 200
            assert page.headers["content-security-policy"].endswith(
                f"frame-ancestors {PARENT}"
            )
            assert page.headers["cache-control"] == "no-store"
            assert "set-cookie" not in page.headers
            [seed] = re.findall(
                r'data-web-chat-embed-bootstrap-seed="(iweb_[A-Za-z0-9_-]{86})"',
                page.text,
            )
            for part in (assets.script, assets.stylesheet):
                path = "/web-chat-embed-assets/assets/" + part.filename
                assert path in page.text
                served = await browser.get(path)
                assert served.status_code == 200 and served.content == part.content
                assert seed not in served.text
            body = {"mode": "resume", "bootstrap_seed": seed}
            start = await browser.post(f"{API}/session", headers=same_origin, json=body)
            assert start.status_code == 200, start.text
            assert start.json()["messages"] == []
            assert "set-cookie" not in start.headers
            assert seed not in start.text and not browser.cookies
            replay = await browser.post(
                f"{API}/session", headers=same_origin, json=body
            )
            assert replay.status_code == 401
            return start.json()["session_token"]

        first = await bootstrap()
        second = await bootstrap()
        assert first != second

        async def send(token, event, text):
            return await browser.post(
                f"{API}/messages",
                headers={**same_origin, "Authorization": f"Bearer {token}"},
                json={"id": event, "text": text},
            )

        response = await send(first, "turn-1", "Hello")
        assert response.json()["messages"][-1]["text"] == "A real test reply."
        assert (await send(first, "turn-1", "Hello")).json() == response.json()
        assert len(model_calls) == 1
        resume = await browser.post(
            f"{API}/session",
            headers={**same_origin, "Authorization": f"Bearer {second}"},
            json={"mode": "resume"},
        )
        assert resume.json()["messages"] == []
        assert resume.json()["session_token"] == second
        assert (
            await send(first, "turn-2", "Policy changes during inference")
        ).status_code == 401
        assert (await send(second, "turn-3", "Existing session")).status_code == 401
        policy = None
        assert (await browser.get(PAGE, headers=iframe)).status_code == 404
    assert set(counters) == {"page", "start", "read", "send"}
    assert len(model_calls) == 2
    unavailable_global.assert_not_awaited()
