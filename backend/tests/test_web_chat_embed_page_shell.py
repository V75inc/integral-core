"""Tests for the unmounted iframe-specific shell and restrictive document policy."""

from dataclasses import replace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.agentive.connectors.external_chat import ExternalChatProvision
from app.web.web_chat_embed_page_shell import (
    create_web_chat_embed_page_router,
    load_web_chat_embed_assets,
)
from app.web.web_chat_embed_policy import ManagedWebChatEmbedPolicy

ORIGIN = "https://embed.example.test"
PARENT = "https://company.example.test"
PATH = "/embed/synthetic-route"
ASSETS = "/web-chat-embed-assets/assets/"
SEED = "iweb_" + "P" * 86
IFRAME = {"Sec-Fetch-Dest": "iframe"}


@pytest.fixture
def assets(tmp_path):
    """Load known immutable test artifacts with the distinct embed naming."""
    name = "public-web-chat-embed-aabbccdd"
    build = tmp_path / "assets"
    build.mkdir()
    (build / f"{name}.js").write_text("console.info('fictional module');")
    (build / f"{name}.css").write_text("body { color: black; }")
    (tmp_path / "public-web-chat-embed.html").write_text(
        f'<script type="module" src="{ASSETS}{name}.js"></script>'
        f'<link rel="stylesheet" href="{ASSETS}{name}.css">'
    )
    return load_web_chat_embed_assets(tmp_path.resolve())


def _policy():
    return ManagedWebChatEmbedPolicy(
        ExternalChatProvision("private-workspace", "public_web_chat", "private-route"),
        "7",
        "public-profile",
        2,
        3,
        PARENT,
        ORIGIN,
        "synthetic-route",
    )


def _client(assets, ready, read, issue, *, origin=ORIGIN, mount=True):
    router = create_web_chat_embed_page_router(
        served_origin=ORIGIN,
        host_ready=ready,
        read_current=read,
        issue_seed=issue,
        assets=assets,
    )
    app = FastAPI(redirect_slashes=False)
    if mount:
        app.include_router(router)
    return AsyncClient(transport=ASGITransport(app=app), base_url=origin)


@pytest.mark.asyncio
async def test_detached_shell_only_delivers_seed_on_exact_approved_iframe(assets):
    """The shell protects embed audience without weakening full-page behavior."""
    policy = _policy()
    issued = []

    async def ready(origin):
        return origin == ORIGIN

    async def read(route):
        return policy if route == policy.embed_reference else None

    async def issue(current):
        issued.append(current)
        return SEED

    async with _client(assets, ready, read, issue, mount=False) as client:
        assert (await client.get(PATH, headers=IFRAME)).status_code == 404

    async with _client(assets, ready, read, issue) as client:
        for path, headers in (
            (PATH, {}),
            (PATH, {**IFRAME, "Cookie": "foreign=1"}),
            (PATH, {**IFRAME, "Authorization": "Bearer foreign"}),
            (PATH + "?unexpected=1", IFRAME),
            ("/embed/%73ynthetic-route", IFRAME),
            ("/chat/synthetic-route", IFRAME),
        ):
            assert (await client.get(path, headers=headers)).status_code == 404
        assert not issued
        full = await client.get(PATH, headers=IFRAME)
        assert full.status_code == 200
        assert full.headers["cache-control"] == "no-store"
        assert full.headers["referrer-policy"] == "no-referrer"
        assert full.headers["content-security-policy"].endswith(
            f"frame-ancestors {PARENT}"
        )
        assert "x-frame-options" not in full.headers
        assert "set-cookie" not in full.headers
        assert ASSETS in full.text and "integrity=" in full.text
        assert f'data-web-chat-embed-bootstrap-seed="{SEED}"' in full.text
        assert "private-workspace" not in full.text and "private-route" not in full.text
        for a in (assets.script, assets.stylesheet):
            artifact = await client.get(ASSETS + a.filename)
            assert artifact.status_code == 200
            assert artifact.content == a.content
            assert SEED not in artifact.text
        assert (await client.get(ASSETS + "arbitrary.js")).status_code == 404
        assert (await client.get("/public-web-chat-embed.html")).status_code == 404
    async with _client(assets, ready, read, issue, origin=PARENT) as client:
        assert (await client.get(PATH, headers=IFRAME)).status_code == 404
    assert issued == [policy]


@pytest.mark.asyncio
async def test_revoked_embed_shell_never_sends_minted_seed(assets):
    """Discard a seed immediately if authority changes before rendering."""
    policy = _policy()

    async def ready(_):
        return True

    async def read(_):
        return policy

    async def issue(_):
        nonlocal policy
        policy = replace(policy, policy_revision=4)
        return SEED

    async with _client(assets, ready, read, issue) as client:
        reply = await client.get(PATH, headers=IFRAME)
        assert reply.status_code == 404
        assert not reply.text
        assert "frame-ancestors 'none'" in reply.headers["content-security-policy"]
        assert "set-cookie" not in reply.headers
