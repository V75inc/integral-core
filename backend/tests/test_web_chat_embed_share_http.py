"""Exercise detached authenticated embed-share boundary over synthetic ASGI."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.agentive.connectors.external_chat import ExternalChatProvision
from app.api.errors import InsufficientPermissionsError
from app.web import web_chat_share
from app.web.web_chat_embed_policy import ManagedWebChatEmbedPolicy
from app.web.web_chat_embed_share import (
    IssuedWebChatEmbedShare,
    create_managed_web_chat_embed_share_router,
)

PATH = "/api/agentive/managed-channels/public_web_chat/embed-share"
PARENT = "https://parent.example.invalid"
HOST = "https://visitor.example.invalid"
POLICY = ManagedWebChatEmbedPolicy(
    ExternalChatProvision("workspace", "public_web_chat", "provision"),
    "4",
    "profile",
    3,
    2,
    PARENT,
    HOST,
    "embed-key",
)
URL = f"{HOST}/embed/{POLICY.embed_reference}"
QUERY = {
    "workspace_id": "workspace",
    "provision_id": "provision",
    "profile_key": "profile",
    "route_epoch": "4",
    "published_version": "3",
    "embed_policy_revision": "2",
    "parent_origin": PARENT,
}
HEADERS = {"X-Integral-Scope": "ws:workspace", "Authorization": "Bearer synthetic"}


@pytest.fixture
def ports(monkeypatch):
    """Supply synthetic principal and fresh host reads only inside this ASGI app."""
    app = FastAPI()

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        if request.headers.get("authorization") == HEADERS["Authorization"]:
            request.state.user = {"id": "manager"}
        return await call_next(request)

    monkeypatch.setattr(
        web_chat_share,
        "get_user_node",
        AsyncMock(return_value=SimpleNamespace(id="manager")),
    )
    manager = AsyncMock()
    monkeypatch.setattr(web_chat_share, "require_profile_manager", manager)
    read = AsyncMock(return_value=IssuedWebChatEmbedShare(POLICY, URL))
    current = AsyncMock(return_value=POLICY)
    assert all("embed-share" not in route.path for route in app.routes)
    app.include_router(
        create_managed_web_chat_embed_share_router(
            read_issued=read, read_current=current
        )
    )
    return SimpleNamespace(app=app, manager=manager, read=read, current=current)


async def _get(ports, headers=HEADERS, query=QUERY):
    async with AsyncClient(
        transport=ASGITransport(app=ports.app), base_url=HOST
    ) as client:
        return await client.get(PATH, headers=headers, params=query)


def _unavailable(response, status):
    assert response.status_code == status
    assert response.json() == {"kind": "unavailable"}
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers


@pytest.mark.asyncio
async def test_exact_current_manager_gets_only_frontend_proof(ports):
    """Return only the approved proof for the exact observed setup."""
    response = await _get(ports)
    assert response.status_code == 200
    expected = {
        **QUERY,
        "published_version": 3,
        "embed_policy_revision": 2,
        "kind": "approved-web-chat-embed-v1",
        "slug": "public_web_chat",
        "embed_page_url": URL,
    }
    assert response.json() == expected
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    ports.manager.assert_awaited_with("manager", "workspace")
    ports.read.assert_awaited_with(POLICY.provision, "4", ("profile", 3), 2, PARENT)
    assert ports.current.await_count == ports.manager.await_count == 2


@pytest.mark.asyncio
async def test_unverified_scope_or_query_does_not_access_host(ports):
    """Unknown caller, scope, or query fields never reach the trusted host."""
    _unavailable(await _get(ports, headers={}), 401)
    _unavailable(await _get(ports, query={**QUERY, "workspace_id": "other"}), 400)
    _unavailable(await _get(ports, query={**QUERY, "parent_origin": PARENT + "/"}), 400)
    _unavailable(await _get(ports, query={**QUERY, "embed_policy_revision": "02"}), 400)
    _unavailable(
        await _get(ports, query=[*QUERY.items(), ("parent_origin", PARENT)]), 400
    )
    ports.manager.side_effect = InsufficientPermissionsError("Private detail")
    _unavailable(await _get(ports), 403)
    ports.read.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "drift", ["revoked", "policy", "pub", "epoch", "parent", "manager", "outage"]
)
async def test_stale_or_unavailable_host_or_manager_never_returns_link(ports, drift):
    """Stale or lost policy and late manager loss never expose the link."""
    changes = {
        "revoked": None,
        "policy": replace(POLICY, policy_revision=3),
        "pub": replace(POLICY, published_version=4),
        "epoch": replace(POLICY, route_epoch="5"),
        "parent": replace(POLICY, parent_origin="https://other.example.invalid"),
    }
    if drift == "manager":
        ports.manager.side_effect = [
            None,
            InsufficientPermissionsError("Private detail"),
        ]
    elif drift == "outage":
        ports.current.side_effect = RuntimeError("Private diagnostics")
    else:
        ports.current.side_effect = [POLICY, changes[drift]]
    _unavailable(await _get(ports), {"manager": 403, "outage": 503}.get(drift, 404))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        URL + "?secret=fixture",
        URL + "#fragment",
        f"{HOST}/chat/embed-key",
        "https://internal.example.invalid/embed/embed-key",
        "https://user:pass@visitor.example.invalid/embed/embed-key",
    ],
)
async def test_url_must_be_exact_approved_embed_page(ports, url):
    """No credentials, decorated paths or other routes pass the projection."""
    ports.read.return_value = IssuedWebChatEmbedShare(POLICY, url)
    _unavailable(await _get(ports), 404)
    ports.current.assert_not_awaited()
