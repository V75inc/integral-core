"""Detach the production Web Chat components for a whole lifecycle proof."""

import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from jvspatial.db.factory import create_database

from app.agentive.api.managed_channels import install_managed_channel_registry
from app.agentive.connectors.external_chat import ExternalChatProvision
from app.agentive.services import profile_management
from app.agentive.services.web_chat_package import SLUG
from app.agentive.services.web_chat_request_budget import WebChatBudgetPolicy
from app.connectors.channel_packages import ChannelPackageRegistry
from app.connectors.channel_runtime import (
    ChannelRuntimeContribution,
    register_channel_runtime,
)
from app.models.edges import IS_MEMBER_OF
from app.models.nodes import Workspace

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]

MANAGE = "/api/agentive/managed-channels"
CONNECTOR = f"{MANAGE}/{SLUG}"
ORIGIN = "https://conversation.example.invalid"
PROFILE = "public"
PERSONA = "Provide concise synthetic test replies."


def _row(response):
    assert response.status_code == 200, response.text
    [row] = response.json()["channels"]
    assert row["definition"]["slug"] == SLUG and "full_page_url" not in row
    return row


def _target(row):
    return {name: row[name] for name in ("provision_id", "route_epoch")}


class _FixturePlatform:
    """Attest only exact, explicitly approved ephemeral deployment state."""

    def __init__(self, workspace):
        self.workspace = workspace
        self.approved = set()
        self.host_up = True

    async def install_ready(self, workspace):
        # Administrative setup must work without an available public host.
        return False

    async def approved_ready_origin(self, route):
        return ORIGIN if route in self.approved else None


@pytest.fixture
async def detached_delivery(
    authenticated_client, test_user, second_user, monkeypatch, with_document
):
    """Compose an isolated visitor ASGI app and a managed test workspace."""
    if os.getenv("INTEGRAL_TEST_DB") not in ("postgres", "postgresql"):
        pytest.skip("Detached durable Web Chat test requires disposable PostgreSQL")
    assert str(authenticated_client.base_url) == "http://test"

    directory = os.environ.get("INTEGRAL_TEST_WEB_CHAT_DIST") if with_document else None
    if with_document and directory is None:
        pytest.skip(
            "Set INTEGRAL_TEST_WEB_CHAT_DIST to the reviewed standalone Vite build"
        )

    from app.main import app
    from app.web.web_chat_host_assembly import assemble_web_chat_host

    result = await authenticated_client.post(
        "/api/workspaces", json={"name": "Disposable Web Chat delivery test"}
    )
    assert result.status_code in (200, 201), result.text
    workspace_id = result.json()["workspace"]["id"]
    workspace = await Workspace.find_one({"id": workspace_id})
    await second_user.connect(workspace, edge=IS_MEMBER_OF, role="member")

    async def publish_fixture(profiles, _bindings, _native, _route):
        profiles[PROFILE] = {
            "published": {
                **profile_management.starter(PROFILE),
                "name": "Synthetic guide",
                "persona": PERSONA,
                "tools": [],
                "version": 3,
            }
        }

    await profile_management.mutate_config(
        test_user.id, workspace_id, 0, publish_fixture
    )
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "19" * 32)
    calls = []

    async def complete_text(prompt):
        assert prompt.persona == PERSONA
        assert set(vars(prompt)) == {
            "instructions",
            "persona",
            "history",
            "visitor_text",
        }
        calls.append(prompt.visitor_text)
        return f"Synthetic reply {len(calls)}"

    platform = _FixturePlatform(workspace_id)
    database = create_database(db_type="postgres")
    try:
        await database.get("object", "o.missing")
        budget = WebChatBudgetPolicy(60, 80, 80, 60, 60, 30, 30, 16)
        assets = None
        if directory is not None:
            from app.web.web_chat_connector_runtime import (
                WebChatServingConfiguration,
                compose_web_chat_connector,
            )
            from app.web.web_chat_page_shell import StandaloneWebChatAssets

            assets = StandaloneWebChatAssets.from_vite_build(Path(directory).resolve())

            async def host_ready(origin):
                return origin == ORIGIN and platform.host_up

            assembled = compose_web_chat_connector(
                WebChatServingConfiguration(
                    served_origin=ORIGIN,
                    host_ready=host_ready,
                    platform=platform,
                    database=database,
                    assets=assets,
                    complete=complete_text,
                    budget=budget,
                    budget_timeout_seconds=2.0,
                    max_parallel_budget_acquisitions=2,
                )
            )
            contribution = assembled.contribution
        else:
            assembled = assemble_web_chat_host(
                database=database,
                platform=platform,
                complete=complete_text,
                budget_policy=budget,
                budget_timeout_seconds=2.0,
                max_parallel_budget_acquisitions=2,
            )
            contribution = ChannelRuntimeContribution(
                assembled.registry.resolve(SLUG),
                (assembled.share_router,),
                (assembled.browser_router,),
            )
        registry = ChannelPackageRegistry()
        mountable = register_channel_runtime(registry, contribution)
        state_key = "integral_managed_channel_registry"
        monkeypatch.setattr(app.state, state_key, None, raising=False)
        delattr(app.state, state_key)
        install_managed_channel_registry(app, registry)
        monkeypatch.setattr(app.router, "routes", list(app.router.routes))
        for router in mountable.management:
            app.include_router(router)
        visitor_app = FastAPI()
        for router in mountable.visitor:
            visitor_app.include_router(router)
        async with AsyncClient(
            transport=ASGITransport(app=visitor_app),
            base_url=ORIGIN,
            headers={"Origin": ORIGIN, "Sec-Fetch-Site": "same-origin"},
        ) as visitor:
            yield assembled, platform, calls, workspace_id, visitor, assets
    finally:
        await database.close()


@pytest.mark.parametrize("with_document", [False, True], ids=["api", "built-document"])
async def test_configure_share_chat_retry_and_revoke_on_detached_apps(
    with_document, detached_delivery, authenticated_client, second_user_client
):
    """Exercise authenticated management and anonymous visitor I/O together."""
    from app.web.web_chat_host_assembly import ManagedWebChatHostRoute

    assembled, platform, calls, workspace_id, visitor, assets = detached_delivery
    headers = {"X-Integral-Scope": f"ws:{workspace_id}"}
    params = {"workspace_id": workspace_id}
    options = {"headers": headers, "params": params}
    setup_path = f"{CONNECTOR}/configuration"
    share_path = f"{CONNECTOR}/share"
    enabled_path = f"{CONNECTOR}/enabled"
    owner = authenticated_client
    offered = _row(await owner.get(MANAGE, **options))
    assert offered["can_install"] and not offered["connected"]
    installed = _row(await owner.post(f"{CONNECTOR}/install", **options))
    assert installed["action"] == "configure" and not installed["can_enable"]
    assert installed["state"] == "inactive" and installed["connected"]
    assert installed["route_epoch"] == "1"
    config = await owner.get(
        setup_path, headers=headers, params={**params, **_target(installed)}
    )
    assert config.status_code == 200, config.text
    assert config.json()["eligible_profiles"] == [
        {"key": PROFILE, "name": "Synthetic guide"}
    ]
    revision = config.json()["profile_revision"]
    assigned = _row(
        await owner.put(
            setup_path,
            **options,
            json={
                **_target(installed),
                "profile_key": PROFILE,
                "expected_profile_revision": revision,
            },
        )
    )
    assert assigned["route_epoch"] == "2" and not assigned["can_enable"]
    assert assigned["state"] == "inactive"
    provision = ExternalChatProvision(workspace_id, SLUG, assigned["provision_id"])

    def share_query(row):
        return {
            **params,
            **_target(row),
            "profile_key": PROFILE,
            "published_version": 3,
        }

    assert not await assembled.reconcile(provision)
    unapproved = await owner.put(
        enabled_path, **options, json={**_target(assigned), "enabled": True}
    )
    assert unapproved.status_code == 409, unapproved.text
    platform.approved.add(ManagedWebChatHostRoute(provision, "2", PROFILE, 3, False))
    assert await assembled.reconcile(provision)
    assert _row(await owner.get(MANAGE, **options))["can_enable"]
    before_enable = await owner.get(
        share_path, headers=headers, params=share_query(assigned)
    )
    assert before_enable.status_code == 404
    enabled = _row(
        await owner.put(
            enabled_path, **options, json={**_target(assigned), "enabled": True}
        )
    )
    assert enabled["route_epoch"] == "3" and enabled["state"] != "active"
    before_approval = await owner.get(
        share_path, headers=headers, params=share_query(enabled)
    )
    assert before_approval.status_code == 404
    platform.approved.add(ManagedWebChatHostRoute(provision, "3", PROFILE, 3, True))
    assert await assembled.reconcile(provision)
    assert _row(await owner.get(MANAGE, **options))["state"] == "active"

    refused = await second_user_client.get(
        share_path, headers=headers, params=share_query(enabled)
    )
    assert refused.status_code == 403, refused.text
    share = await owner.get(share_path, headers=headers, params=share_query(enabled))
    assert share.status_code == 200, share.text
    assert share.headers["cache-control"] == "no-store"
    assert share.json()["kind"] == "approved-full-page-share-v1"
    assert share.json()["route_epoch"] == "3"
    url = urlsplit(share.json()["full_page_url"])
    assert (
        f"{url.scheme}://{url.netloc}" == ORIGIN and not url.query and not url.fragment
    )
    page, key = url.path.rsplit("/", 1)
    assert page == "/chat" and key
    page_context = await assembled.page_context(key, ORIGIN)
    assert page_context is not None and page_context.route == key
    assert page_context.title == "Web Chat"
    assert await assembled.page_context(key, "https://other.example.invalid") is None
    if with_document:
        document = await visitor.get(share.json()["full_page_url"])
        assert document.status_code == 200
        assert document.headers["cache-control"] == "no-store"
        assert 'id="public-web-chat-root"' in document.text
        assert f'data-web-chat-route="{key}"' in document.text
        assert 'data-web-chat-title="Web Chat"' in document.text
        assert workspace_id not in document.text and PERSONA not in document.text
        assert assets is not None
        for file in (assets.script, assets.stylesheet):
            path = f"/web-chat-assets/assets/{file.filename}"
            assert path in document.text and file.integrity in document.text
            body = await visitor.get(path)
            assert body.status_code == 200 and body.content == file.content
        other = await visitor.get(f"https://other.example.invalid{url.path}")
        assert other.status_code == 404 and key not in other.text
    session = f"/api/public/web-chat/{key}/session"
    messages = f"/api/public/web-chat/{key}/messages"
    start = await visitor.post(session, json={"mode": "new"})
    assert start.json() == {"kind": "ready", "messages": []}, start.text
    assert "__Secure-integral_web_chat" in start.cookies
    first = {"id": "first", "text": "First visitor turn"}
    second = {"id": "second", "text": "Follow-up visitor turn"}
    sent = await visitor.post(messages, json=first)
    assert sent.json()["kind"] == "ready", sent.text
    assert (await visitor.post(messages, json=first)).json() == sent.json()
    followup = await visitor.post(messages, json=second)
    assert followup.json()["kind"] == "ready", followup.text
    assert [item["text"] for item in followup.json()["messages"]] == [
        first["text"],
        "Synthetic reply 1",
        second["text"],
        "Synthetic reply 2",
    ]
    assert calls == [first["text"], second["text"]]

    if with_document:
        platform.host_up = False
        assert (await visitor.get(share.json()["full_page_url"])).status_code == 404
        assert (await visitor.post(session, json={"mode": "resume"})).status_code == 404
        assert (await visitor.post(messages, json=second)).status_code == 404
        assert (
            await owner.get(share_path, headers=headers, params=share_query(enabled))
        ).status_code == 404
        assert len(calls) == 2
        platform.host_up = True

    disabled = _row(
        await owner.put(
            enabled_path, **options, json={**_target(enabled), "enabled": False}
        )
    )
    assert disabled["route_epoch"] == "4" and disabled["state"] != "active"
    stale_share = await owner.get(
        share_path, headers=headers, params=share_query(enabled)
    )
    assert stale_share.status_code == 404, stale_share.text
    assert await assembled.page_context(key, ORIGIN) is None
    if with_document:
        stale_document = await visitor.get(share.json()["full_page_url"])
        assert stale_document.status_code == 404 and key not in stale_document.text
    for stale in (
        await visitor.post(session, json={"mode": "resume"}),
        await visitor.post(messages, json=second),
    ):
        assert stale.status_code == 404, stale.text
        assert stale.json() == {"kind": "unavailable"}
    assert len(calls) == 2
