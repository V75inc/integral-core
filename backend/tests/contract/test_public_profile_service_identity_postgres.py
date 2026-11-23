"""Explicit authoring binds one private zero-grant identity, never a public route."""

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
    legacy = _result(await _publish(client, headers, 0))
    assert legacy["profiles"]["public"]["service_identity_ready"] is False
    assert "service_identity_ready" not in legacy["profiles"]["staff"]
    before = await profile_management.record(workspace)
    assert before.public_profile_service_identities == {}
    assert before.public_web_chat == {}

    member = await Workspace.find_one({"id": workspace})
    await second_user.connect(member, edge=IS_MEMBER_OF, role="member")
    denied = await _publish(second_user_client, headers, legacy["revision"], **OPT_IN)
    assert denied.status_code == 403
    spoof = await _publish(
        client, headers, legacy["revision"], enable_public_service_identity="true"
    )
    assert spoof.status_code == 400

    # Two submissions at one revision cannot create two principals.
    attempts = await asyncio.gather(
        *(_publish(client, headers, legacy["revision"], **OPT_IN) for _ in range(2))
    )
    assert sorted(response.status_code for response in attempts) == [200, 400]
    accepted = next(response for response in attempts if response.status_code == 200)
    adopted = accepted.json()
    projected = adopted["profiles"]["public"]
    assert projected["service_identity_ready"] is True
    assert (
        projected["published"]["version"]
        > legacy["profiles"]["public"]["published"]["version"]
    )

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
        profile_management.current_public_profile_service_identity(
            saved, "public", legacy["profiles"]["public"]["published"]["version"]
        )
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
    created = _result(await _publish(client, headers, 0))
    initial_version = created["profiles"]["public"]["published"]["version"]

    async def select_legacy(_profiles, _bindings, _native, route):
        route.profile_key = "public"
        route.enabled = True
        route.provision_id = "synthetic-provision"
        route.generation = 3

    await profile_management.mutate_config(
        test_user.id, workspace, created["revision"], select_legacy
    )
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
    assert changed["profiles"]["public"]["service_identity_ready"] is False
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
        is None
    )

    second = _result(await _publish(client, headers, changed["revision"], **OPT_IN))
    rebound = profile_management.current_public_profile_service_identity(
        await profile_management.record(workspace),
        "public",
        second["profiles"]["public"]["published"]["version"],
    )
    assert rebound is not None and rebound.generation == initial.generation + 1
    assert rebound.identity_id == initial.identity_id
    assert rebound.published_version > published["version"]
    staff = await _publish(client, headers, second["revision"], key="staff", **OPT_IN)
    assert staff.status_code == 400 and "public profile without tools" in staff.text
    assert (await profile_management.record(workspace)).revision == second["revision"]
