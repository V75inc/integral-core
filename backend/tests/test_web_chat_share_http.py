"""Detached share reads using isolated ASGI and synthetic authorized host ports."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.agentive.connectors.external_chat import ExternalChatProvision
from app.api.errors import InsufficientPermissionsError
from app.web import web_chat_share
from app.web.full_page_chat_composition import ResolvedWebChatRoute
from app.web.web_chat_share import (
    IssuedFullPageShare,
    create_managed_web_chat_share_router,
)

PATH = "/api/agentive/managed-channels/public_web_chat/share"
ORIGIN = "https://visitor.example.invalid"
KEY = "test-host-opaque"
URL = f"{ORIGIN}/chat/{KEY}"
QUERY = {
    "workspace_id": "workspace",
    "provision_id": "provision",
    "route_epoch": "4",
    "profile_key": "profile",
    "published_version": "3",
}


@pytest.fixture
def ports(monkeypatch):
    """Provide independently controlled route, issued URL and manager checks."""
    app = FastAPI()

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        if request.headers.get("authorization") == "Bearer synthetic-fixture":
            request.state.user = {"id": "manager"}
        return await call_next(request)

    principal = AsyncMock(return_value=SimpleNamespace(id="manager"))
    require = AsyncMock()
    monkeypatch.setattr(web_chat_share, "get_user_node", principal)
    monkeypatch.setattr(web_chat_share, "require_profile_manager", require)
    provision = ExternalChatProvision("workspace", "public_web_chat", "provision")
    route = ResolvedWebChatRoute(provision, ORIGIN, KEY, "4", ("profile", 3))
    read = AsyncMock(return_value=IssuedFullPageShare(KEY, URL))
    resolve = AsyncMock(return_value=route)
    assert not any("share" in route.path for route in app.routes)
    app.include_router(
        create_managed_web_chat_share_router(
            read_issued=read, resolve_active_route=resolve
        )
    )
    headers = {
        "X-Integral-Scope": "ws:workspace",
        "Authorization": "Bearer synthetic-fixture",
    }
    return SimpleNamespace(
        app=app,
        route=route,
        read=read,
        resolve=resolve,
        require=require,
        headers=headers,
    )


async def _get(ports, headers=None, params=QUERY):
    async with AsyncClient(
        transport=ASGITransport(app=ports.app), base_url=ORIGIN
    ) as client:
        return await client.get(
            PATH, headers=ports.headers if headers is None else headers, params=params
        )


def _unavailable(response, status):
    assert response.status_code == status
    assert response.json() == {"kind": "unavailable"}
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers


@pytest.mark.asyncio
async def test_exact_manager_reads_only_issued_share(ports):
    """Return exact DTO with repeated checks and no credentials."""
    response = await _get(ports)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        **QUERY,
        "published_version": 3,
        "kind": "approved-full-page-share-v1",
        "slug": "public_web_chat",
        "full_page_url": URL,
    }
    assert ports.require.await_count == 2
    ports.require.assert_awaited_with("manager", "workspace")
    ports.read.assert_awaited_with(ports.route.provision, "4", ("profile", 3))


@pytest.mark.asyncio
async def test_wrong_scope_and_unverified_principal_never_query_host(ports):
    """Neither workspace selection nor a caller field grants management access."""
    _unavailable(await _get(ports, headers={}), 401)
    _unavailable(await _get(ports, params={**QUERY, "workspace_id": "other"}), 400)
    _unavailable(await _get(ports, params={**QUERY, "principal_id": "manager"}), 400)
    _unavailable(await _get(ports, params=[*QUERY.items(), ("route_epoch", "4")]), 400)
    ports.require.side_effect = InsufficientPermissionsError("private error")
    _unavailable(await _get(ports), 403)
    ports.read.assert_not_awaited()
    ports.resolve.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "provision",
        "epoch",
        "publication",
        "revoked",
        "read-removed",
        "manager",
        "host-outage",
    ],
)
async def test_drift_fails_without_revealing_share(ports, change):
    """The late read rechecks reference, route identity and current role."""
    route = ports.route
    if change == "read-removed":
        ports.read.side_effect = [IssuedFullPageShare(KEY, URL), None]
    elif change == "manager":
        ports.require.side_effect = [
            None,
            InsufficientPermissionsError("private error"),
        ]
    elif change == "host-outage":
        ports.resolve.side_effect = RuntimeError("private diagnostics")
    else:
        updated = {
            "provision": replace(
                route, provision=replace(route.provision, workspace_id="other")
            ),
            "epoch": replace(route, route_epoch="5"),
            "publication": replace(route, publication=("profile", 4)),
            "revoked": None,
        }[change]
        ports.resolve.side_effect = [route, updated, updated]
    _unavailable(
        await _get(ports), {"manager": 403, "host-outage": 503}.get(change, 404)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "suffix", ["?provider=value", "#private", "/other", "/../other"]
)
async def test_only_canonical_approved_page_link_is_returned(ports, suffix):
    """Reject extra URL parts, even when returned by a trusted host port."""
    ports.read.return_value = IssuedFullPageShare(KEY, URL + suffix)
    _unavailable(await _get(ports), 404)
