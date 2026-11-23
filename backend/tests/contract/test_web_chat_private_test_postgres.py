"""Prove authenticated stateless Web Chat Test in detached management-only mode."""

import asyncio
import os

import pytest

from app.agentive.services import profile_management
from app.api.errors import InsufficientPermissionsError
from app.connectors.channel_packages import ChannelPackageRegistry
from app.connectors.channel_runtime import mount_trusted_channel_runtime
from app.models.edges import IS_MEMBER_OF
from app.models.nodes import Workspace
from app.web import web_chat_share
from app.web.web_chat_connector_runtime import compose_web_chat_connector
from app.web.web_chat_private_test import WebChatPrivateTestConfiguration

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]
BASE = "/api/agentive/managed-channels/public_web_chat"
PUBLISH = "/api/workspace/agent-profiles/publish"
DRAFT = "/api/workspace/agent-profiles/draft"
PERSONA = "Use a short synthetic greeting."


@pytest.mark.parametrize(
    "change", ["no-change", "republish", "manager-lost", "timeout", "budget-denied"]
)
async def test_private_management_only_test(
    authenticated_client,
    second_user_client,
    test_user,
    second_user,
    monkeypatch,
    change,
):
    if os.getenv("INTEGRAL_TEST_DB") not in ("postgres", "postgresql"):
        pytest.skip("Private Test contract requires disposable PostgreSQL")

    from app.agentive.api.managed_channels import install_managed_channel_registry
    from app.main import app

    owner = authenticated_client
    workspace_response = await owner.post(
        "/api/workspaces", json={"name": "Private test fixture"}
    )
    assert workspace_response.status_code == 200
    workspace_id = workspace_response.json()["workspace"]["id"]
    workspace = await Workspace.get(workspace_id)
    await second_user.connect(workspace, edge=IS_MEMBER_OF, role="member")
    headers = {"X-Integral-Scope": f"ws:{workspace_id}"}
    params = {"workspace_id": workspace_id}

    async def seed(profiles, _bindings, _native, _route):
        profiles["public"] = {
            "draft": {
                **profile_management.starter("public"),
                "name": "Synthetic guide",
                "persona": PERSONA,
            }
        }

    await profile_management.mutate_config(test_user.id, workspace_id, 0, seed)
    published = await owner.post(
        PUBLISH,
        headers=headers,
        json={
            "key": "public",
            "expected_revision": 1,
            "enable_public_service_identity": True,
        },
    )
    assert published.status_code == 200, published.text
    entered = asyncio.Event()
    release = asyncio.Event()
    calls, quotas = [], []
    allowed = True
    test_ready = True

    async def ready(scope):
        return (
            test_ready
            and scope.provision.workspace_id == workspace_id
            and scope.profile_key == "public"
        )

    async def budget(scope):
        quotas.append(scope)
        return change != "budget-denied"

    async def complete(message):
        assert vars(message) == {
            "persona": PERSONA,
            "text": "Are you the Test assistant?",
        }
        calls.append(message)
        entered.set()
        await release.wait()
        return "Yes, this is a private test."

    if change == "timeout":
        from app.web import web_chat_private_test

        monkeypatch.setattr(web_chat_private_test, "_TIMEOUT_SECONDS", 0.30)

    original_manager = web_chat_share.require_profile_manager

    async def check_manager(actor, selected):
        await original_manager(actor, selected)
        if not allowed:
            raise InsufficientPermissionsError("fixture role removed")

    monkeypatch.setattr(web_chat_share, "require_profile_manager", check_manager)
    runtime = compose_web_chat_connector(
        private_test=WebChatPrivateTestConfiguration(ready, budget, complete)
    )
    assert (
        runtime.contribution.visitor_routes
        == runtime.contribution.isolated_routes
        == ()
    )
    registry = ChannelPackageRegistry()
    monkeypatch.setattr(app.router, "routes", list(app.router.routes))
    monkeypatch.setattr(app, "openapi_schema", None)
    mount_trusted_channel_runtime(
        registry,
        runtime.contribution,
        staff_app=app,
        staff_origin="https://staff.example.invalid",
        visitor_apps={},
    )
    state_key = "integral_managed_channel_registry"
    monkeypatch.setattr(app.state, state_key, None, raising=False)
    delattr(app.state, state_key)
    install_managed_channel_registry(app, registry)

    install = await owner.post(f"{BASE}/install", params=params, headers=headers)
    assert install.status_code == 200, install.text
    installed = install.json()["channels"][0]
    assert installed["state"] == "inactive" and not installed["can_enable"]
    setup = f"{BASE}/configuration"
    exact = {k: installed[k] for k in ("provision_id", "route_epoch")}
    config = await owner.get(setup, params={**params, **exact}, headers=headers)
    assert "private_test_available" not in config.json()
    assigned = await owner.put(
        setup,
        params=params,
        headers=headers,
        json={
            **exact,
            "profile_key": "public",
            "expected_profile_revision": config.json()["profile_revision"],
        },
    )
    assert assigned.status_code == 200, assigned.text
    row = assigned.json()["channels"][0]
    exact = {k: row[k] for k in ("provision_id", "route_epoch")}
    config = await owner.get(setup, params={**params, **exact}, headers=headers)
    data = config.json()
    assert data["private_test_available"] is True
    assert "full_page_share_check_available" not in data
    assert config.headers["cache-control"] == "no-store" and not calls and not quotas
    body = {
        **exact,
        "profile_key": "public",
        "published_version": data["published_version"],
        "text": "Are you the Test assistant?",
    }
    path = f"{BASE}/test"
    if change == "no-change":
        test_ready = False
        not_ready = await owner.get(setup, params={**params, **exact}, headers=headers)
        assert "private_test_available" not in not_ready.json()
        denied = await owner.post(path, params=params, headers=headers, json=body)
        assert denied.status_code == 404 and not calls and not quotas
        test_ready = True
    unauthorized = await second_user_client.post(
        path, params=params, headers=headers, json=body
    )
    assert unauthorized.status_code == 403 and not calls
    assert unauthorized.headers["cache-control"] == "no-store"
    if change != "timeout":
        invalid = await owner.post(
            path, params=params, headers=headers, json={**body, "published_version": 1}
        )
        assert invalid.status_code == 404 and not calls
    if change == "budget-denied":
        response = await owner.post(path, params=params, headers=headers, json=body)
        assert response.status_code == 429 and not calls
        return

    sending = asyncio.create_task(
        owner.post(path, params=params, headers=headers, json=body)
    )
    try:
        await asyncio.wait_for(entered.wait(), 2)
        if change == "republish":
            record = await profile_management.record(workspace_id)
            draft = {
                **record.profiles["public"]["draft"],
                "persona": "Superseded persona.",
            }
            saved = await owner.put(
                DRAFT,
                headers=headers,
                json={
                    "key": "public",
                    "expected_revision": record.revision,
                    "draft": draft,
                },
            )
            assert saved.status_code == 200, saved.text
            updated = await profile_management.record(workspace_id)
            result = await owner.post(
                PUBLISH,
                headers=headers,
                json={
                    "key": "public",
                    "expected_revision": updated.revision,
                },
            )
            assert result.status_code == 200, result.text
        elif change == "manager-lost":
            allowed = False
        if change != "timeout":
            release.set()
        reply = await sending
    finally:
        release.set()
        sending.cancel()
        await asyncio.gather(sending, return_exceptions=True)
    expected = {
        "no-change": 200,
        "republish": 404,
        "manager-lost": 403,
        "timeout": 503,
    }[change]
    assert reply.status_code == expected, reply.text
    assert reply.headers["cache-control"] == "no-store"
    assert "set-cookie" not in reply.headers
    assert len(calls) == len(quotas) == 1
    if change == "no-change":
        assert reply.json() == {
            **{k: v for k, v in body.items() if k != "text"},
            "workspace_id": workspace_id,
            "slug": "public_web_chat",
            "kind": "private-web-chat-test-v1",
            "reply": "Yes, this is a private test.",
        }
    else:
        assert reply.json() == {"kind": "unavailable"}

    from jvspatial.core.context import get_default_context

    database = get_default_context().database
    for entity in (
        "WebChatVisitorSession",
        "WebChatTurnReceipt",
        "WebChatEmbedCredential",
    ):
        assert not await database.find("object", {"entity": entity}, limit=1)
