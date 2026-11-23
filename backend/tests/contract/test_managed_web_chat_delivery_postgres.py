"""Detach the production Web Chat components for a whole lifecycle proof."""

import asyncio
import os
import re
import time
from pathlib import Path
from secrets import token_urlsafe
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from jvspatial.db.factory import create_database
from starlette.responses import Response

from app.agentive.api.managed_channels import install_managed_channel_registry
from app.agentive.connectors.external_chat import (
    ExternalChatAssignment,
    ExternalChatProvision,
)
from app.agentive.services import profile_management
from app.agentive.services.web_chat_package import SLUG
from app.agentive.services.web_chat_request_budget import WebChatBudgetPolicy
from app.connectors.channel_packages import ChannelPackageRegistry
from app.connectors.channel_runtime import (
    ChannelRuntimeContribution,
    mount_trusted_channel_runtime,
)
from app.connectors.channel_share_check import ApprovedEmbedDescriptor
from app.models.edges import IS_MEMBER_OF
from app.models.nodes import Workspace

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]

MANAGE = "/api/agentive/managed-channels"
CONNECTOR = f"{MANAGE}/{SLUG}"
ORIGIN = "https://conversation.example.invalid"
STAFF_ORIGIN = "https://staff.example.test"
EMBED_ORIGIN = "https://embed.example.invalid"
CLIENT_ORIGIN = "https://client.example.invalid"
EMBED_REF = "isolated-embed-reference"
EMBED_PATH = f"/api/public/web-chat-embed/{EMBED_REF}"
CLIENT_PATH = "/api/v1/web-chat"
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
        self.identity_denied = set()
        self.embed_policy = None
        self.model_entered = {}
        self.release_model = asyncio.Event()

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

    embed_directory = (
        os.environ.get("INTEGRAL_TEST_WEB_CHAT_EMBED_DIST") if with_document else None
    )
    if with_document and embed_directory is None:
        pytest.skip(
            "Set INTEGRAL_TEST_WEB_CHAT_EMBED_DIST to the reviewed standalone embed build"
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
        draft = {
            **profile_management.starter(PROFILE),
            "name": "Synthetic guide",
            "persona": PERSONA,
            "tools": [],
        }
        profiles[PROFILE] = {"draft": draft, "published": {**draft, "version": 3}}

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
        entered = platform.model_entered.get(prompt.visitor_text)
        if entered is not None:
            entered.set()
            await platform.release_model.wait()
            return "Stale response must stay private"
        return f"Synthetic reply {len(calls)}"

    platform = _FixturePlatform(workspace_id)
    database = create_database(db_type="postgres")
    try:
        await database.get("object", "o.missing")
        budget = WebChatBudgetPolicy(60, 80, 80, 60, 60, 30, 30, 16)
        assets = None
        extra = None
        if directory is not None:
            from app.web.web_chat_client_grants import PostgresWebChatClientGrants
            from app.web.web_chat_connector_runtime import (
                WebChatClientServingConfiguration,
                WebChatEmbedServingConfiguration,
                WebChatServingConfiguration,
                compose_web_chat_connector,
            )
            from app.web.web_chat_embed_page_shell import load_web_chat_embed_assets
            from app.web.web_chat_page_shell import StandaloneWebChatAssets

            async def reviewed(change):
                return (
                    change.actor_id == test_user.id
                    and change.grant.assignment.provision.workspace_id == workspace_id
                )

            async def fixture_grant_scope(assignment):
                return assignment.provision.workspace_id == workspace_id

            grants = PostgresWebChatClientGrants(
                database=database,
                review_authority=reviewed,
                current_authority=fixture_grant_scope,
            )

            async def embed_policy(reference):
                return platform.embed_policy if reference == EMBED_REF else None

            async def issued_embed(
                provision, epoch, publication, revision, parent_origin
            ):
                from app.web.web_chat_embed_share import IssuedWebChatEmbedShare

                policy = platform.embed_policy
                if policy is None or (
                    policy.provision,
                    policy.route_epoch,
                    (policy.profile_key, policy.published_version),
                    policy.policy_revision,
                    policy.parent_origin,
                ) != (provision, epoch, publication, revision, parent_origin):
                    return None
                return IssuedWebChatEmbedShare(
                    policy, f"{EMBED_ORIGIN}/embed/{EMBED_REF}"
                )

            async def approved_descriptors(provision, epoch, publication):
                policy = platform.embed_policy
                if policy is None or (
                    policy.provision,
                    policy.route_epoch,
                    (policy.profile_key, policy.published_version),
                ) != (provision, epoch, publication):
                    return ()
                return (
                    ApprovedEmbedDescriptor(
                        policy.parent_origin, policy.policy_revision
                    ),
                )

            async def permit_budget(*_args):
                return True

            async def embed_ready(origin):
                return origin == EMBED_ORIGIN

            async def client_ready(origin):
                return origin == CLIENT_ORIGIN

            async def profile_authority(transport, assignment):
                return (
                    transport not in platform.identity_denied
                    and assignment.provision.workspace_id == workspace_id
                    and assignment.profile_key == PROFILE
                    and assignment.publication_revision.isdecimal()
                )

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
                    profile_authority=profile_authority,
                    embed=WebChatEmbedServingConfiguration(
                        EMBED_ORIGIN,
                        database,
                        load_web_chat_embed_assets(Path(embed_directory).resolve()),
                        embed_ready,
                        embed_policy,
                        permit_budget,
                        complete_text,
                        read_issued_share=issued_embed,
                        read_approved_descriptors=approved_descriptors,
                    ),
                    client_api=WebChatClientServingConfiguration(
                        CLIENT_ORIGIN,
                        database,
                        grants,
                        client_ready,
                        permit_budget,
                        permit_budget,
                        complete_text,
                    ),
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
                visitor_origin=ORIGIN,
            )
        registry = ChannelPackageRegistry()
        visitor_app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
        if with_document:
            isolated = {
                origin: FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
                for origin in (EMBED_ORIGIN, CLIENT_ORIGIN)
            }
            extra = SimpleNamespace(
                grants=grants, apps=isolated, actor=test_user.id, database=database
            )
        else:
            isolated = {}
        monkeypatch.setattr(app.router, "routes", list(app.router.routes))
        # The shared API fixture may have inspected docs while setting up the synthetic
        # workspace. Model a fresh approved host before graph-specific admission.
        monkeypatch.setattr(app, "openapi_schema", None)
        mount_trusted_channel_runtime(
            registry,
            contribution,
            staff_app=app,
            staff_origin=STAFF_ORIGIN,
            visitor_apps={ORIGIN: visitor_app, **isolated},
        )
        state_key = "integral_managed_channel_registry"
        monkeypatch.setattr(app.state, state_key, None, raising=False)
        delattr(app.state, state_key)
        install_managed_channel_registry(app, registry)
        if extra is not None:
            # Test-only vetted host dispatch: no socket, DNS, proxy, or trust store.
            apps = {
                urlsplit(STAFF_ORIGIN).netloc.encode(): app,
                urlsplit(ORIGIN).netloc.encode(): visitor_app,
            }

            async def dispatch(scope, receive, send):
                hosts = [value for name, value in scope["headers"] if name == b"host"]
                target = apps.get(hosts[0]) if len(hosts) == 1 else None
                if scope["scheme"] != "https" or target is None:
                    await Response(status_code=421)(scope, receive, send)
                    return
                await target(scope, receive, send)

            extra.two_origin = ASGITransport(app=dispatch)
        if extra is not None:
            extra.visitor_app = visitor_app
        async with AsyncClient(
            transport=ASGITransport(app=visitor_app),
            base_url=ORIGIN,
            headers={"Origin": ORIGIN, "Sec-Fetch-Site": "same-origin"},
        ) as visitor:
            yield assembled, platform, calls, workspace_id, visitor, assets, extra
    finally:
        await database.close()


@pytest.mark.parametrize("with_document", [False, True], ids=["api", "built-document"])
async def test_configure_share_chat_retry_and_revoke_on_detached_apps(
    with_document, detached_delivery, authenticated_client, second_user_client
):
    """Exercise authenticated management and anonymous visitor I/O together."""
    from app.web.web_chat_host_assembly import ManagedWebChatHostRoute

    assembled, platform, calls, workspace_id, visitor, assets, extra = detached_delivery
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
    assert config.headers["cache-control"] == "no-store"
    assert "full_page_share_check_available" not in config.json()
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

    publication = 3
    record = await profile_management.record(workspace_id)
    if with_document:
        # Even explicit fixture host approval is insufficient without a service identity.
        platform.approved.add(
            ManagedWebChatHostRoute(
                provision,
                assigned["route_epoch"],
                PROFILE,
                publication,
                False,
            )
        )
        assert not await assembled.reconcile(provision)
        no_identity = await owner.put(
            enabled_path, **options, json={**_target(assigned), "enabled": True}
        )
        assert no_identity.status_code == 409
        opted_in = await owner.post(
            "/api/workspace/agent-profiles/publish",
            headers=headers,
            json={
                "key": PROFILE,
                "expected_revision": record.revision,
                "enable_public_service_identity": True,
            },
        )
        assert opted_in.status_code == 200, opted_in.text
        record = await profile_management.record(workspace_id)
        publication = record.profiles[PROFILE]["published"]["version"]
        identity = profile_management.current_public_profile_service_identity(
            record,
            PROFILE,
            publication,
        )
        assert identity is not None and identity.workspace_id == workspace_id
        assert identity.identity_id not in opted_in.text
        assigned = _row(await owner.get(MANAGE, **options))
        assert publication != 3 and not assigned["can_enable"]

    def share_query(row):
        return {
            **params,
            **_target(row),
            "profile_key": PROFILE,
            "published_version": publication,
        }

    assert not await assembled.reconcile(provision)
    unapproved = await owner.put(
        enabled_path, **options, json={**_target(assigned), "enabled": True}
    )
    assert unapproved.status_code == 409, unapproved.text
    platform.approved.add(
        ManagedWebChatHostRoute(
            provision, assigned["route_epoch"], PROFILE, publication, False
        )
    )
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
    assert int(enabled["route_epoch"]) == int(assigned["route_epoch"]) + 1
    assert enabled["state"] != "active"
    before_approval = await owner.get(
        share_path, headers=headers, params=share_query(enabled)
    )
    assert before_approval.status_code == 404
    platform.approved.add(
        ManagedWebChatHostRoute(
            provision, enabled["route_epoch"], PROFILE, publication, True
        )
    )
    assert await assembled.reconcile(provision)
    assert _row(await owner.get(MANAGE, **options))["state"] == "active"

    active_config_params = {**params, **_target(enabled)}
    active_config = await owner.get(
        setup_path, headers=headers, params=active_config_params
    )
    assert active_config.status_code == 200, active_config.text
    assert active_config.headers["cache-control"] == "no-store"
    assert active_config.json()["full_page_share_check_available"] is True
    assert "full_page_url" not in active_config.json()
    assert "approved_embed_policies" not in active_config.json()
    assert (
        await second_user_client.get(
            setup_path, headers=headers, params=active_config_params
        )
    ).status_code == 403
    wrong_publication = await owner.get(
        share_path,
        headers=headers,
        params={**share_query(enabled), "published_version": publication + 1},
    )
    assert wrong_publication.status_code == 404

    refused = await second_user_client.get(
        share_path, headers=headers, params=share_query(enabled)
    )
    assert refused.status_code == 403, refused.text
    share = await owner.get(share_path, headers=headers, params=share_query(enabled))
    assert share.status_code == 200, share.text
    assert share.headers["cache-control"] == "no-store"
    assert share.json()["kind"] == "approved-full-page-share-v1"
    assert share.json()["route_epoch"] == enabled["route_epoch"]
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
        unready_config = await owner.get(
            setup_path, headers=headers, params=active_config_params
        )
        assert "full_page_share_check_available" not in unready_config.json()
        assert len(calls) == 2
        platform.host_up = True

    if with_document:
        from app.web.web_chat_client_grants import (
            WebChatClientGrant,
            WebChatGrantReview,
        )
        from app.web.web_chat_embed_policy import ManagedWebChatEmbedPolicy

        platform.embed_policy = ManagedWebChatEmbedPolicy(
            provision,
            enabled["route_epoch"],
            PROFILE,
            publication,
            1,
            "https://parent.example.invalid",
            EMBED_ORIGIN,
            EMBED_REF,
        )
        embedded_config = await owner.get(
            setup_path, headers=headers, params=active_config_params
        )
        assert embedded_config.status_code == 200
        assert embedded_config.json()["approved_embed_policies"] == [
            {
                "parent_origin": "https://parent.example.invalid",
                "embed_policy_revision": 1,
            }
        ]
        assert embedded_config.json()["full_page_share_check_available"] is True
        assert EMBED_REF not in embedded_config.text
        assert "embed_page_url" not in embedded_config.json()
        check = await owner.get(
            f"{CONNECTOR}/embed-share",
            headers=headers,
            params={
                **share_query(enabled),
                "parent_origin": "https://parent.example.invalid",
                "embed_policy_revision": 1,
            },
        )
        assert (
            check.status_code == 200
            and check.json()["embed_page_url"] == f"{EMBED_ORIGIN}/embed/{EMBED_REF}"
        )
        assignment = ExternalChatAssignment(
            provision, PROFILE, str(publication), enabled["route_epoch"]
        )
        review = WebChatGrantReview(
            extra.actor,
            "issue",
            WebChatClientGrant(
                token_urlsafe(32),
                assignment,
                int(time.time()) + 500,
            ),
        )
        client_key = await extra.grants.issue(review)
        assert client_key is not None
        embed_headers = {"Origin": EMBED_ORIGIN, "Sec-Fetch-Site": "same-origin"}
        async with (
            AsyncClient(
                transport=ASGITransport(app=extra.apps[EMBED_ORIGIN]),
                base_url=EMBED_ORIGIN,
            ) as embed,
            AsyncClient(
                transport=ASGITransport(app=extra.apps[CLIENT_ORIGIN]),
                base_url=CLIENT_ORIGIN,
            ) as client,
        ):
            iframe_path = f"/embed/{EMBED_REF}"
            iframe_headers = {"Sec-Fetch-Dest": "iframe"}
            assert (
                await visitor.get(iframe_path, headers=iframe_headers)
            ).status_code == 404
            page = await embed.get(iframe_path, headers=iframe_headers)
            assert page.status_code == 200
            [seed] = re.findall(
                r'data-web-chat-embed-bootstrap-seed="(iweb_[A-Za-z0-9_-]{86})"',
                page.text,
            )
            embed_session = await embed.post(
                f"{EMBED_PATH}/session",
                headers=embed_headers,
                json={"mode": "resume", "bootstrap_seed": seed},
            )
            assert embed_session.status_code == 200, embed_session.text
            visitor_headers = {
                **embed_headers,
                "Authorization": "Bearer " + embed_session.json()["session_token"],
            }
            send = await embed.post(
                f"{EMBED_PATH}/messages",
                headers=visitor_headers,
                json={"id": "embed", "text": "Embed visitor"},
            )
            assert send.json()["messages"][-1]["text"] == "Synthetic reply 3"
            service_start = await client.post(
                f"{CLIENT_PATH}/sessions",
                headers={"Authorization": "Bearer " + client_key.secret},
            )
            assert service_start.status_code == 201, service_start.text
            service_headers = {
                "Authorization": "Bearer " + service_start.json()["session_token"]
            }
            response = await client.post(
                f"{CLIENT_PATH}/session/messages",
                headers=service_headers,
                json={"id": "service", "text": "Service client"},
            )
            assert response.json()["messages"][-1]["text"] == "Synthetic reply 4"
            platform.identity_denied.add("client-api")
            assert (
                await client.get(f"{CLIENT_PATH}/session", headers=service_headers)
            ).status_code == 401
            assert (
                await embed.get(iframe_path, headers=iframe_headers)
            ).status_code == 200
            platform.identity_denied.add("embed")
            unavailable_embed = await owner.get(
                setup_path, headers=headers, params=active_config_params
            )
            assert "approved_embed_policies" not in unavailable_embed.json()
            assert unavailable_embed.json()["full_page_share_check_available"] is True
            assert (
                await embed.get(iframe_path, headers=iframe_headers)
            ).status_code == 404
            assert (await visitor.get(share.json()["full_page_url"])).status_code == 200

            platform.identity_denied.clear()
            batch = (
                (visitor, messages, {}, "Stale full-page turn"),
                (embed, f"{EMBED_PATH}/messages", visitor_headers, "Stale embed turn"),
                (
                    client,
                    f"{CLIENT_PATH}/session/messages",
                    service_headers,
                    "Stale service turn",
                ),
            )
            platform.model_entered = {text: asyncio.Event() for _, _, _, text in batch}

            async def send_held(browser, path, request_headers, text):
                return await browser.post(
                    path, headers=request_headers, json={"id": "held", "text": text}
                )

            before_ids = {
                row["id"]
                for row in await extra.database.find(
                    "object",
                    {"entity": "WebChatTurnReceipt"},
                    limit=30,
                )
            }
            pending = [asyncio.create_task(send_held(*item)) for item in batch]
            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        *(entered.wait() for entered in platform.model_entered.values())
                    ),
                    timeout=8,
                )
                snapshot = await profile_management.record(workspace_id)
                draft = {
                    **snapshot.profiles[PROFILE]["draft"],
                    "persona": "A new synthetic persona.",
                }
                saved = await owner.put(
                    "/api/workspace/agent-profiles/draft",
                    headers=headers,
                    json={
                        "key": PROFILE,
                        "expected_revision": snapshot.revision,
                        "draft": draft,
                    },
                )
                assert saved.status_code == 200, saved.text
                changed = await profile_management.record(workspace_id)
                published = await owner.post(
                    "/api/workspace/agent-profiles/publish",
                    headers=headers,
                    json={
                        "key": PROFILE,
                        "expected_revision": changed.revision,
                        "enable_public_service_identity": False,
                    },
                )
                assert published.status_code == 200, published.text
                updated = await profile_management.record(workspace_id)
                new_publication = updated.profiles[PROFILE]["published"]["version"]
                new_identity = (
                    profile_management.current_public_profile_service_identity(
                        updated,
                        PROFILE,
                        new_publication,
                    )
                )
                assert new_publication != publication and new_identity is None
            finally:
                platform.release_model.set()
            rejected = await asyncio.gather(*pending)

            def assert_denied(response):
                assert response.status_code in {401, 404} or (
                    response.status_code == 200
                    and response.json() == {"kind": "expired"}
                ), response.text
                assert "Stale response" not in response.text

            for response in rejected:
                assert_denied(response)
            for item in batch:
                assert_denied(await send_held(*item))
            held_rows = [
                row["context"]
                for row in await extra.database.find(
                    "object",
                    {"entity": "WebChatTurnReceipt"},
                    limit=30,
                )
                if row["id"] not in before_ids
            ]
            assert len(held_rows) == 3
            assert all(
                not row.get("reply_ciphertext") and not row.get("history_completed_at")
                for row in held_rows
            )
            assert len(calls) == 7

    current_enabled = _row(await owner.get(MANAGE, **options))
    disabled = _row(
        await owner.put(
            enabled_path, **options, json={**_target(current_enabled), "enabled": False}
        )
    )
    assert int(disabled["route_epoch"]) == int(current_enabled["route_epoch"]) + 1
    assert disabled["state"] != "active"
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
    assert len(calls) == (7 if with_document else 2)


@pytest.mark.parametrize("with_document", [False], ids=["api"])
@pytest.mark.parametrize("failure", ["none", "approval", "timeout", "disable-race"])
async def test_configure_reconciles_exact_committed_transitions(
    failure, detached_delivery, authenticated_client, monkeypatch
):
    from app.agentive.services import web_chat_provision_store
    from app.web.web_chat_host_assembly import ManagedWebChatHostRoute

    assembled, platform, _, workspace_id, _, _, _ = detached_delivery
    owner = authenticated_client
    headers = {"X-Integral-Scope": f"ws:{workspace_id}"}
    options = {"headers": headers, "params": {"workspace_id": workspace_id}}
    setup = f"{CONNECTOR}/configuration"
    enabled_path = f"{CONNECTOR}/enabled"
    share = f"{CONNECTOR}/share"
    installed = _row(await owner.post(f"{CONNECTOR}/install", **options))
    provision = ExternalChatProvision(workspace_id, SLUG, installed["provision_id"])
    record = await profile_management.record(workspace_id)

    prep = ManagedWebChatHostRoute(provision, "2", PROFILE, 3, False)
    active = ManagedWebChatHostRoute(provision, "3", PROFILE, 3, True)
    platform.approved.add(prep)
    assigned = _row(
        await owner.put(
            setup,
            **options,
            json={
                **_target(installed),
                "profile_key": PROFILE,
                "expected_profile_revision": record.revision,
            },
        )
    )
    assert assigned["route_epoch"] == "2" and assigned["can_enable"]
    for epoch in ("2", "3"):
        before = await owner.get(
            share,
            headers=headers,
            params={
                "workspace_id": workspace_id,
                "provision_id": provision.provision_id,
                "route_epoch": epoch,
                "profile_key": PROFILE,
                "published_version": 3,
            },
        )
        assert before.status_code == 404

    if failure != "approval":
        platform.approved.add(active)
    if failure == "timeout":
        monkeypatch.setattr(web_chat_provision_store, "_POST_COMMIT_SECONDS", 0.05)
        approved_origin = platform.approved_ready_origin

        async def stall_exact_new_epoch(route):
            if route == active:
                await asyncio.Event().wait()
            return await approved_origin(route)

        monkeypatch.setattr(platform, "approved_ready_origin", stall_exact_new_epoch)
    if failure == "disable-race":
        entered, release = asyncio.Event(), asyncio.Event()
        approved_origin = platform.approved_ready_origin

        async def hold_exact_new_epoch(route):
            if route == active:
                entered.set()
                await release.wait()
            return await approved_origin(route)

        monkeypatch.setattr(platform, "approved_ready_origin", hold_exact_new_epoch)
        pending = asyncio.create_task(
            owner.put(
                enabled_path, **options, json={**_target(assigned), "enabled": True}
            )
        )
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            new_epoch = {**_target(assigned), "route_epoch": "3"}
            disabled = _row(
                await owner.put(
                    enabled_path, **options, json={**new_epoch, "enabled": False}
                )
            )
            assert disabled["route_epoch"] == "4" and not disabled["can_enable"]
            assert disabled["state"] != "active"
        finally:
            release.set()
            result = _row(await pending)
        assert result["route_epoch"] == "4" and result["state"] != "active"
        assert (
            await owner.get(
                share,
                headers=headers,
                params={
                    "workspace_id": workspace_id,
                    **new_epoch,
                    "profile_key": PROFILE,
                    "published_version": 3,
                },
            )
        ).status_code == 404
        return

    enabled = _row(
        await owner.put(
            enabled_path, **options, json={**_target(assigned), "enabled": True}
        )
    )
    query = {
        "workspace_id": workspace_id,
        **_target(enabled),
        "profile_key": PROFILE,
        "published_version": 3,
    }
    assert enabled["route_epoch"] == "3"
    if failure != "none":
        assert enabled["state"] != "active"
        current = await profile_management.record(workspace_id)
        assert current.public_web_chat["enabled"] is True
        platform.approved.add(active)
        if failure == "timeout":
            monkeypatch.setattr(platform, "approved_ready_origin", approved_origin)
        assert _row(await owner.get(MANAGE, **options))["state"] != "active"
        assert (
            await owner.get(share, headers=headers, params=query)
        ).status_code == 404
        assert await assembled.reconcile(provision)
    else:
        assert enabled["state"] == "active"

    proof = await owner.get(share, headers=headers, params=query)
    assert proof.status_code == 200
    key = urlsplit(proof.json()["full_page_url"]).path.rsplit("/", 1)[1]
    # Even pre-approval for the new disabled epoch must not mint on Disable.
    platform.approved.add(ManagedWebChatHostRoute(provision, "4", PROFILE, 3, False))
    disabled = _row(
        await owner.put(
            enabled_path, **options, json={**_target(enabled), "enabled": False}
        )
    )
    assert disabled["route_epoch"] == "4" and not disabled["can_enable"]
    assert await assembled.page_context(key, ORIGIN) is None
    assert _row(await owner.get(MANAGE, **options))["can_enable"] is False


@pytest.fixture
def optional_live_web_chat_origin(monkeypatch):
    """Configure only the opt-in fixture, never the Integral server."""
    origin = os.getenv("INTEGRAL_FIXTURE_WEB_CHAT_ORIGIN")
    if origin is not None:
        from app.web.full_page_chat import is_canonical_web_chat_origin

        assert is_canonical_web_chat_origin(origin)
        parsed = urlsplit(origin)
        assert parsed.port and 1024 <= parsed.port <= 65535
        assert parsed.hostname != "localhost" and not parsed.hostname.endswith(
            ".localhost"
        )
        assert not parsed.hostname.startswith("127.")
        monkeypatch.setitem(globals(), "ORIGIN", origin)
    return origin


async def _run_manual_tls_preview(app, page_url):
    """Serve only disposable visitor routes, with no trusted proxy headers."""
    import uvicorn

    origin = urlsplit(page_url)
    duration = int(os.getenv("INTEGRAL_FIXTURE_WEB_CHAT_SECONDS", "900"))
    assert 1 <= duration <= 3600
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=origin.port,
        ssl_certfile=os.environ["INTEGRAL_FIXTURE_WEB_CHAT_CERT"],
        ssl_keyfile=os.environ["INTEGRAL_FIXTURE_WEB_CHAT_KEY"],
        lifespan="off",
        proxy_headers=False,
        access_log=False,
        log_level="warning",
        timeout_graceful_shutdown=3,
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("Could not start the loopback preview")
                await asyncio.sleep(0.05)
        print(f"Disposable Web Chat ready for {duration}s: {page_url}", flush=True)
        await asyncio.wait({task}, timeout=duration)
    finally:
        server.should_exit = True
        await task


@pytest.mark.parametrize("with_document", [True], ids=["built-document"])
async def test_staff_and_public_web_chat_are_distinct_reviewed_hosts(
    optional_live_web_chat_origin, detached_delivery, authenticated_client
):
    """Drive real review, serving and cookies through an isolated two-host edge."""
    from app.web.web_chat_host_assembly import ManagedWebChatHostRoute

    _, platform, calls, workspace, _, assets, extra = detached_delivery
    manager = {
        "Authorization": authenticated_client.headers["Authorization"],
        "X-Integral-Scope": f"ws:{workspace}",
    }
    params = {"workspace_id": workspace}
    async with (
        AsyncClient(transport=extra.two_origin, base_url=STAFF_ORIGIN) as staff,
        AsyncClient(transport=extra.two_origin, base_url=ORIGIN) as visitor,
    ):
        assert (await staff.get("/api/auth/me", headers=manager)).status_code == 200
        installed = _row(
            await staff.post(f"{CONNECTOR}/install", params=params, headers=manager)
        )
        provision = ExternalChatProvision(workspace, SLUG, installed["provision_id"])
        record = await profile_management.record(workspace)
        published = await staff.post(
            "/api/workspace/agent-profiles/publish",
            headers=manager,
            json={
                "key": PROFILE,
                "expected_revision": record.revision,
                "enable_public_service_identity": True,
            },
        )
        assert published.status_code == 200, published.text
        record = await profile_management.record(workspace)
        version = record.profiles[PROFILE]["published"]["version"]
        epoch = int(installed["route_epoch"])
        platform.approved.update(
            {
                ManagedWebChatHostRoute(
                    provision, str(epoch + 1), PROFILE, version, False
                ),
                ManagedWebChatHostRoute(
                    provision, str(epoch + 2), PROFILE, version, True
                ),
            }
        )
        assigned = _row(
            await staff.put(
                f"{CONNECTOR}/configuration",
                params=params,
                headers=manager,
                json={
                    **_target(installed),
                    "profile_key": PROFILE,
                    "expected_profile_revision": record.revision,
                },
            )
        )
        assert assigned["can_enable"]
        enabled = _row(
            await staff.put(
                f"{CONNECTOR}/enabled",
                params=params,
                headers=manager,
                json={**_target(assigned), "enabled": True},
            )
        )
        assert enabled["state"] == "active"
        proof = await staff.get(
            f"{CONNECTOR}/share",
            headers=manager,
            params={
                **params,
                **_target(enabled),
                "profile_key": PROFILE,
                "published_version": version,
            },
        )
        assert proof.status_code == 200
        url = urlsplit(proof.json()["full_page_url"])
        assert f"{url.scheme}://{url.netloc}" == ORIGIN
        key = url.path.rsplit("/", 1)[1]
        session = f"/api/public/web-chat/{key}/session"
        messages = f"/api/public/web-chat/{key}/messages"
        document = await visitor.get(url.path)
        assert document.status_code == 200
        assert f'data-web-chat-route="{key}"' in document.text
        assert "frame-ancestors 'none'" in document.headers["Content-Security-Policy"]
        for asset in (assets.stylesheet, assets.script):
            path = f"/web-chat-assets/assets/{asset.filename}"
            assert (await visitor.get(path)).content == asset.content
            assert (await staff.get(path, headers=manager)).content != asset.content
        for staff_path in (
            "/api/auth/me",
            MANAGE,
            f"{CONNECTOR}/share",
            "/docs",
            "/openapi.json",
        ):
            assert (await visitor.get(staff_path, headers=manager)).status_code == 404
        assert (await visitor.post("/api/auth/login", json={})).status_code == 404
        staff_page = await staff.get(url.path, headers=manager)
        assert "data-web-chat-route" not in staff_page.text
        staff_session = await staff.post(
            session,
            json={"mode": "new"},
            headers={
                **manager,
                "Origin": STAFF_ORIGIN,
                "Sec-Fetch-Site": "same-origin",
            },
        )
        assert staff_session.status_code == 404
        assert "set-cookie" not in staff_session.headers

        preflight = await visitor.options(
            session,
            headers={
                "Origin": STAFF_ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert "access-control-allow-origin" not in preflight.headers
        assert "access-control-allow-credentials" not in preflight.headers
        cross = await visitor.post(
            session,
            json={"mode": "new"},
            headers={
                "Origin": STAFF_ORIGIN,
                "Sec-Fetch-Site": "cross-site",
            },
        )
        assert cross.status_code == 403 and "set-cookie" not in cross.headers
        assert "access-control-allow-origin" not in cross.headers

        same_origin = {"Origin": ORIGIN, "Sec-Fetch-Site": "same-origin"}
        started = await visitor.post(session, json={"mode": "new"}, headers=same_origin)
        assert started.status_code == 200 and started.json() == {
            "kind": "ready",
            "messages": [],
        }
        attrs = started.headers["set-cookie"].lower()
        assert "__secure-integral_web_chat=" in attrs and "domain=" not in attrs
        assert all(
            item in attrs
            for item in (
                "secure",
                "httponly",
                "samesite=strict",
                f"path=/api/public/web-chat/{key.lower()}",
            )
        )
        assert (
            "cookie"
            not in visitor.build_request("POST", STAFF_ORIGIN + session).headers
        )
        assert "cookie" not in visitor.build_request("GET", url.path).headers
        replied = await visitor.post(
            messages,
            headers=same_origin,
            json={
                "id": "two-host-turn",
                "text": "Reviewed visitor test",
            },
        )
        assert replied.status_code == 200
        assert replied.json()["messages"][-1]["text"] == "Synthetic reply 1"
        assert calls == ["Reviewed visitor test"]
        assert (
            await visitor.get("https://unknown.example.invalid" + url.path)
        ).status_code == 421

    if optional_live_web_chat_origin is not None:
        await _run_manual_tls_preview(extra.visitor_app, proof.json()["full_page_url"])
