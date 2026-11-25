"""Publishing binds one addressable ACL principal without activating visitor delivery."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.agentive.services import profile_management, profile_testing
from app.models.edges import IS_MEMBER_OF
from app.models.nodes import Workspace

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]
ROOT = "/api/workspace/agent-profiles"
OPT_IN = {"enable_public_service_identity": True}


async def _workspace(client, label="Synthetic profiles"):
    result = await client.post("/api/workspaces", json={"name": label})
    assert result.status_code in (200, 201), result.text
    return result.json()["workspace"]["id"]


def _headers(workspace):
    return {"X-Integral-Scope": f"ws:{workspace}"}


async def _publish(client, headers, revision, key="public", **fields):
    return await client.post(
        f"{ROOT}/publish",
        headers=headers,
        json={"key": key, "expected_revision": revision, **fields},
    )


def _result(response):
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture(autouse=True)
def inert_validation(monkeypatch):
    fence = AsyncMock(return_value={"real_actions": 0})
    monkeypatch.setattr(profile_testing, "test_profile_draft", fence)
    return fence


async def test_explicit_publish_adopts_private_identity_without_serving(
    authenticated_client, second_user, second_user_client
):
    client = authenticated_client
    workspace = await _workspace(client)
    headers = _headers(workspace)
    before = _result(await client.get(ROOT, headers=headers))
    assert "service_identity_ready" not in before["profiles"]["staff"]
    member = await Workspace.find_one({"id": workspace})
    await second_user.connect(member, edge=IS_MEMBER_OF, role="member")
    denied = await _publish(second_user_client, headers, before["revision"], **OPT_IN)
    assert denied.status_code == 403
    spoof = await _publish(
        client, headers, before["revision"], enable_public_service_identity="true"
    )
    assert spoof.status_code == 400

    # Two submissions at one revision cannot create two principals.
    attempts = await asyncio.gather(
        *(_publish(client, headers, before["revision"], **OPT_IN) for _ in range(2))
    )
    assert sorted(response.status_code for response in attempts) == [200, 400]
    accepted = next(response for response in attempts if response.status_code == 200)
    adopted = accepted.json()
    projected = adopted["profiles"]["public"]
    assert projected["service_identity_ready"] is True
    assert projected["published"]["version"] == adopted["revision"]

    saved = await profile_management.record(workspace)
    assert saved.public_web_chat == {}
    identity = profile_management.current_public_profile_service_identity(
        saved, "public", projected["published"]["version"]
    )
    assert identity is not None and identity.generation == 1
    assert identity.workspace_id == workspace
    assert identity.identity_id not in accepted.text
    assert "public_profile_service_identities" not in accepted.text
    assert "identity_id" not in accepted.text
    assert identity.identity_id not in repr(identity)
    assert (
        profile_management.current_public_profile_service_identity(saved, "public", 0)
        is None
    )
    assert (
        profile_management.current_public_profile_service_identity(
            saved, "staff", identity.published_version
        )
        is None
    )

    repeated = _result(await _publish(client, headers, adopted["revision"], **OPT_IN))
    assert (
        repeated["profiles"]["public"]["published"]["version"]
        == identity.published_version
    )
    assert (
        await profile_management.record(workspace)
    ).public_profile_service_identities == (saved.public_profile_service_identities)
    new_draft = _result(
        await client.post(
            ROOT,
            headers=headers,
            json={
                "name": "Second public profile",
                "audience": "public",
                "expected_revision": repeated["revision"],
            },
        )
    )
    second_key = new_draft["created_profile"]
    additional = _result(
        await _publish(client, headers, new_draft["revision"], key=second_key, **OPT_IN)
    )
    additional_version = additional["profiles"][second_key]["published"]["version"]
    private = await profile_management.record(workspace)
    same_workspace_other = profile_management.current_public_profile_service_identity(
        private, second_key, additional_version
    )
    assert (
        same_workspace_other is not None
        and same_workspace_other.identity_id != identity.identity_id
    )
    assert (
        profile_management.current_public_profile_service_identity(
            private, "public", additional_version
        )
        is None
    )
    assert len(private.public_profile_service_identities) == 2
    assert set(private.public_profile_service_identities[second_key]) == {
        "identity_id",
        "workspace_id",
        "profile_key",
        "published_version",
        "generation",
    }
    other = await _workspace(client, "Other synthetic profiles")
    second = _result(await _publish(client, _headers(other), 0, **OPT_IN))
    other_saved = await profile_management.record(other)
    separate = profile_management.current_public_profile_service_identity(
        other_saved, "public", second["profiles"]["public"]["published"]["version"]
    )
    assert separate is not None and separate.identity_id != identity.identity_id
    assert identity.identity_id not in (await client.get(ROOT, headers=headers)).text


async def test_enabled_legacy_route_needs_disable_then_new_publication(
    authenticated_client, test_user
):
    client = authenticated_client
    workspace = await _workspace(client)
    headers = _headers(workspace)
    initial_version = 1

    async def select_legacy(_profiles, _bindings, _native, route):
        draft = profile_management.starter("public")
        _profiles["public"] = {
            "draft": draft,
            "published": {**draft, "version": initial_version},
        }
        route.profile_key = "public"
        route.enabled = True
        route.provision_id = "synthetic-provision"
        route.generation = 3

    await profile_management.mutate_config(test_user.id, workspace, 0, select_legacy)
    before = await profile_management.record(workspace)
    denied = await _publish(client, headers, before.revision, **OPT_IN)
    assert denied.status_code == 400 and "Disable Web Chat" in denied.text
    unchanged = await profile_management.record(workspace)
    assert unchanged.revision == before.revision
    assert unchanged.profiles == before.profiles
    assert unchanged.public_web_chat == before.public_web_chat
    assert unchanged.public_profile_service_identities == {}

    async def disable(_profiles, _bindings, _native, route):
        route.enabled = False
        route.generation += 1

    disabled = await profile_management.mutate_config(
        test_user.id, workspace, unchanged.revision, disable
    )
    adopted = _result(await _publish(client, headers, disabled["revision"], **OPT_IN))
    assert adopted["profiles"]["public"]["service_identity_ready"] is True
    assert adopted["profiles"]["public"]["published"]["version"] > initial_version
    saved = await profile_management.record(workspace)
    assert saved.public_web_chat == {
        "profile_key": "public",
        "enabled": False,
        "generation": 4,
        "provision_id": "synthetic-provision",
    }


async def test_meaningful_republish_requires_fresh_binding_and_never_grants_tools(
    authenticated_client,
):
    client = authenticated_client
    workspace = await _workspace(client)
    headers = _headers(workspace)
    first = _result(await _publish(client, headers, 0, **OPT_IN))
    publication = first["profiles"]["public"]["published"]
    initial = profile_management.current_public_profile_service_identity(
        await profile_management.record(workspace), "public", publication["version"]
    )
    assert initial is not None

    draft = first["profiles"]["public"]["draft"]
    saved = _result(
        await client.put(
            f"{ROOT}/draft",
            headers=headers,
            json={
                "key": "public",
                "expected_revision": first["revision"],
                "draft": {**draft, "persona": "Changed public persona"},
            },
        )
    )
    assert saved["profiles"]["public"]["service_identity_ready"] is True
    changed = _result(await _publish(client, headers, saved["revision"]))
    published = changed["profiles"]["public"]["published"]
    assert changed["profiles"]["public"]["service_identity_ready"] is True
    node = await profile_management.record(workspace)
    assert (
        profile_management.current_public_profile_service_identity(
            node, "public", publication["version"]
        )
        is None
    )
    assert (
        profile_management.current_public_profile_service_identity(
            node, "public", published["version"]
        )
        is not None
    )

    second = _result(await _publish(client, headers, changed["revision"], **OPT_IN))
    rebound = profile_management.current_public_profile_service_identity(
        await profile_management.record(workspace),
        "public",
        second["profiles"]["public"]["published"]["version"],
    )
    assert rebound is not None and rebound.generation == initial.generation + 1
    assert rebound.identity_id == initial.identity_id
    assert rebound.published_version == published["version"]
    staff = await _publish(client, headers, second["revision"], key="staff", **OPT_IN)
    assert _result(staff)["profiles"]["staff"]["service_identity_ready"]


async def test_directory_acl_mentions_and_existing_publication_backfill(
    authenticated_client, test_user
):
    from app.agentive.services.profile_principals import current_principal
    from app.services.chat_entity_refs import _resolve_mention_token_to_ref
    from app.services.mentions import resolve_mention_token
    from app.services.workspace_permissions import can_access_workspace

    client = authenticated_client
    ws = await _workspace(client)
    other = await _workspace(client, "Other synthetic workspace")

    async def seed(profiles, bindings, native, route):
        draft = {**profile_management.starter("staff"), "name": "Pending changes"}
        profiles["staff"] = {
            "draft": draft,
            "published": {**draft, "name": "Directory Agent", "version": 1},
        }

    initial = await profile_management.mutate_config(test_user.id, ws, 0, seed)
    assert not initial["profiles"]["staff"]["service_identity_ready"]
    assert await profile_management.migrate_published_principals(test_user.id, ws) == {
        "pending": ["staff"],
        "changed": False,
    }
    assert (
        await profile_management.migrate_published_principals(
            test_user.id, ws, apply=True
        )
    )["changed"]
    assert not (
        await profile_management.migrate_published_principals(
            test_user.id, ws, apply=True
        )
    )["changed"]
    config = await profile_management.record(ws)
    item = config.profiles["staff"]
    agent = await current_principal(config, "staff", item)
    assert agent and not agent.user_id and agent.display_name == "Directory Agent"
    assert item["draft"]["name"] == "Pending changes"
    assert await can_access_workspace(agent.id, ws) == "guest"
    assert await can_access_workspace(agent.id, other) == "none"
    search = "/api/users?search=Directory%20Agent"
    assert [
        u["id"]
        for u in _result(await client.get(search, headers=_headers(ws)))["users"]
    ] == [agent.id]
    assert _result(await client.get(search, headers=_headers(other)))["users"] == []
    assert (
        await client.get(f"/api/users/{agent.id}", headers=_headers(other))
    ).status_code == 404
    assert (await resolve_mention_token(agent.id, workspace_id=ws)).id == agent.id
    assert await resolve_mention_token(agent.id, workspace_id=other) is None
    assert (
        await _resolve_mention_token_to_ref("Directory_Agent", ws)
    ).entity_id == agent.id
    assert await _resolve_mention_token_to_ref("Directory_Agent", other) is None
    from app.services.mentions import resolve_mentions
    from app.services.permissions import resolve_role

    track_response = await client.post(
        "/api/tracks",
        headers=_headers(ws),
        json={"title": "Synthetic profile access", "visibility": "private"},
    )
    assert track_response.status_code in (200, 201), track_response.text
    track_id = track_response.json()["track"]["id"]
    picker = f"/api/tracks/{track_id}/mention-candidates"
    assert await resolve_role(agent.id, "track", track_id) is None
    assert not await resolve_mentions("@" + agent.id, track_id=track_id)
    added = await client.post(
        f"/api/tracks/{track_id}/collaborators",
        headers=_headers(ws),
        json={"collaborator_user_id": agent.id, "role": "viewer"},
    )
    assert added.status_code in (200, 201), added.text
    assert await resolve_role(agent.id, "track", track_id) == "viewer"
    assert agent.id in [
        u["id"]
        for u in _result(await client.get(picker, headers=_headers(ws)))["users"]
    ]
    assert [
        u.id for u in await resolve_mentions("@" + agent.id, track_id=track_id)
    ] == [agent.id]
    from app.models.edges import MENTIONS
    from app.models.nodes import Comment

    entry = _result(
        await client.post(
            "/api/entries",
            headers=_headers(ws),
            json={"track_id": track_id, "title": "Synthetic post", "body": "Fixture"},
        )
    )["entry"]
    assert await resolve_role(agent.id, "entry", entry["id"]) == "viewer"
    assert [
        user.id
        for user in await resolve_mentions(
            "@" + agent.id, track_id=track_id, entry_id=entry["id"]
        )
    ] == [agent.id]
    comment = _result(
        await client.post(
            f"/api/entries/{entry['id']}/comments",
            headers=_headers(ws),
            json={"text": "Review requested @" + agent.id},
        )
    )["comment"]
    stored = await Comment.get(comment["id"])
    assert [
        u.id
        for u in await stored.nodes(
            edge=[MENTIONS], node=["User"], direction="out", limit=2
        )
    ] == [agent.id]
    updated = _result(
        await _publish(
            client,
            _headers(ws),
            config.revision,
            key="staff",
            enable_public_service_identity=False,
        )
    )
    refreshed = await profile_management.record(ws)
    same = await current_principal(refreshed, "staff", refreshed.profiles["staff"])
    assert same.id == agent.id and same.display_name == "Pending changes"
    assert updated["profiles"]["staff"]["service_identity_ready"]


async def test_membership_revocation_blocks_public_delivery_and_publish_does_not_regrant(
    authenticated_client,
):
    from app.agentive.services.profile_principals import current_principal

    ws = await _workspace(authenticated_client)
    result = _result(await _publish(authenticated_client, _headers(ws), 0))
    config = await profile_management.record(ws)
    version = result["profiles"]["public"]["published"]["version"]
    assert await profile_management.live_public_service_identity(
        config, "public", version
    )
    actor = await current_principal(config, "public", config.profiles["public"])
    edges = await (await config.get_context()).find_edges_between(
        actor.id, ws, edge_class=IS_MEMBER_OF
    )
    await edges[0].delete()
    assert not await profile_management.live_public_service_identity(
        config, "public", version
    )
    _result(await _publish(authenticated_client, _headers(ws), result["revision"]))
    assert not await profile_management.live_public_service_identity(
        await profile_management.record(ws), "public", version
    )


async def test_native_call_runs_as_profile_with_caller_audience_and_rechecks_grants(
    authenticated_client, test_user, monkeypatch
):
    from app.agentive.services.connection_profiles import ProfileDenied

    ws = await _workspace(authenticated_client)
    headers = _headers(ws)
    declaration = {
        "kind": "tool",
        "label": "Synthetic lookup",
        "source": "app",
        "input_schema": {"type": "object", "properties": {}},
    }
    denied = set()

    async def catalogue(principal, workspace, audience):
        assert workspace == ws and audience == "staff"
        return {} if principal in denied else {"lookup": declaration}

    monkeypatch.setattr(
        profile_management, "catalogue", AsyncMock(return_value={"lookup": declaration})
    )
    draft = {**profile_management.starter("staff"), "tools": ["lookup"]}
    _result(
        await authenticated_client.put(
            f"{ROOT}/draft",
            headers=headers,
            json={"key": "staff", "expected_revision": 0, "draft": draft},
        )
    )
    _result(
        await _publish(
            authenticated_client, headers, 1, key="staff", offer_in_workspace_chat=True
        )
    )
    config = await profile_management.record(ws)
    actor = config.profiles["staff"]["service_principal_id"]
    monkeypatch.setattr(profile_management, "catalogue", catalogue)
    invoked = AsyncMock(return_value={"items": []})
    monkeypatch.setattr(profile_management, "invoke_installed", invoked)
    binding, _ = await profile_management.open_published_profile(
        ws, "native-chat", test_user.id, native_profile_key="staff"
    )

    async def call():
        return await binding.executor.execute(
            "native-chat", test_user.id, binding.session, "lookup", {}
        )

    assert await call() == {"items": []}
    invoked.assert_awaited_once_with(
        ws, actor, "staff", "lookup", {}, audience_principal=test_user.id
    )
    for principal in (test_user.id, actor):
        denied.add(principal)
        with pytest.raises(ProfileDenied):
            await call()
        denied.remove(principal)
    assert invoked.await_count == 1
