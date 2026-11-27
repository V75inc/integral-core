"""Auth, exact scope, and lifecycle over HTTP with a non-serving in-process adapter."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from app.agentive.api.managed_channels import install_managed_channel_registry
from app.api.errors import ResourceConflictError
from app.connectors.channel_packages import (
    ChannelDefinition,
    ChannelInstallation,
    ChannelLifecycle,
    ChannelPackage,
    ChannelPackageRegistry,
    ChannelProfileConfiguration,
    ChannelProfileOption,
    ChannelProfileSetup,
    ChannelServing,
    ChannelServingStatus,
)

PATH = "/api/agentive/managed-channels"
SLUG = "standalone_fixture"


@pytest.fixture
async def endpoint_fixture(authenticated_client, monkeypatch):
    response = await authenticated_client.post(
        "/api/workspaces",
        json={"name": "Managed channels test"},
    )
    assert response.status_code in {200, 201}, response.text
    workspace = response.json()["workspace"]["id"]
    scope = {"X-Integral-Scope": f"ws:{workspace}"}
    registry = ChannelPackageRegistry()
    state = {
        "installation": None,
        "ready": True,
        "epoch": 0,
        "profile": None,
        "revision": 4,
        "share_reads": 0,
    }
    calls = []
    lock = asyncio.Lock()

    async def observe(ctx):
        assert ctx.workspace_id == workspace
        return state["installation"]

    async def probe(ctx, current):
        status = "accepting" if current and current.enabled else "ready"
        return ChannelServingStatus(
            workspace,
            SLUG,
            current.provision_id if current else None,
            current.route_epoch if current else None,
            status if state["ready"] else "unavailable",
        )

    async def install(ctx):
        async with lock:
            if state["installation"] is not None or not state["ready"]:
                raise ResourceConflictError("Fixture state changed")
            state["epoch"] += 1
            current = ChannelInstallation(
                workspace, SLUG, "provision", str(state["epoch"]), False
            )
            state["installation"] = current
            calls.append(("install", ctx.actor_id))
            return current

    async def set_enabled(ctx, current, enabled):
        async with lock:
            if state["installation"] != current or (enabled and not state["ready"]):
                raise ResourceConflictError("Fixture state changed")
            state["epoch"] += 1
            state["installation"] = replace(
                current, enabled=enabled, route_epoch=str(state["epoch"])
            )
            calls.append(("enable" if enabled else "disable", ctx.actor_id))

    async def remove(ctx, current):
        async with lock:
            if state["installation"] != current:
                raise ResourceConflictError("Fixture state changed")
            state["installation"] = None
            calls.append(("remove", ctx.actor_id))

    async def describe_profile(ctx, current):
        return ChannelProfileConfiguration(
            state["revision"],
            state["profile"],
            (ChannelProfileOption("public", "Visitor guide"),),
            not current.enabled,
        )

    async def assign_profile(ctx, current, revision, profile):
        async with lock:
            if state["installation"] != current or state["revision"] != revision:
                raise ResourceConflictError("Fixture state changed")
            state["epoch"] += 1
            state["installation"] = replace(current, route_epoch=str(state["epoch"]))
            state["revision"] += 1
            state["profile"] = profile
            calls.append(("assign", ctx.actor_id))

    async def never(*args):
        raise AssertionError("Transport is never called by management")

    async def full_page_link(ctx, current):
        state["share_reads"] += 1
        return "https://synthetic-visitor.example.invalid/chat/opaque-fixture"

    registry.register(
        ChannelPackage(
            ChannelDefinition(
                SLUG,
                "Fixture",
                "Standalone connector",
                "Not a served service",
                "managed-text-v1",
            ),
            ChannelLifecycle(observe, install, set_enabled, remove),
            ChannelServing(probe, never, full_page_link),
            ChannelProfileSetup(describe_profile, assign_profile),
        )
    )
    from app.main import app

    state_key = "integral_managed_channel_registry"
    # Restore app state after each HTTP test, with no runtime registration.
    monkeypatch.setattr(app.state, state_key, None, raising=False)
    delattr(app.state, state_key)
    install_managed_channel_registry(app, registry)
    query = {"workspace_id": workspace}
    return authenticated_client, scope, query, state, calls


@pytest.mark.asyncio
async def test_managed_lifecycle_versioning_and_removal_with_transport_outage(
    endpoint_fixture,
):
    client, headers, params, state, calls = endpoint_fixture
    listing = await client.get(PATH, headers=headers, params=params)
    assert listing.status_code == 200, listing.text
    [row] = listing.json()["channels"]
    assert (row["state"], row["action"], row["can_install"], row["connected"]) == (
        "registered",
        "add",
        True,
        False,
    )
    assert "full_page_url" not in row
    posted = await client.post(f"{PATH}/{SLUG}/install", headers=headers, params=params)
    assert posted.status_code == 200, posted.text
    [row] = posted.json()["channels"]
    assert (row["state"], row["action"], row["connected"], row["can_enable"]) == (
        "inactive",
        "configure",
        True,
        True,
    )
    assert row["enabled"] is False
    before = {"provision_id": row["provision_id"], "route_epoch": row["route_epoch"]}
    outcomes = await asyncio.gather(
        *(
            client.put(
                f"{PATH}/{SLUG}/enabled",
                headers=headers,
                params=params,
                json={**before, "enabled": True},
            )
            for _ in range(2)
        )
    )
    assert sorted(result.status_code for result in outcomes) == [200, 409], [
        r.text for r in outcomes
    ]
    [active] = next(result for result in outcomes if result.status_code == 200).json()[
        "channels"
    ]
    assert active["state"] == "active"
    assert active["route_epoch"] != before["route_epoch"]
    state["ready"] = False
    read = await client.get(PATH, headers=headers, params=params)
    [unavailable] = read.json()["channels"]
    assert unavailable["state"] == "unavailable" and unavailable["connected"]
    assert unavailable["enabled"] is True
    ids = {key: active[key] for key in before}
    stale = await client.delete(
        f"{PATH}/{SLUG}", headers=headers, params={**params, **before}
    )
    assert stale.status_code == 409
    disabled = await client.put(
        f"{PATH}/{SLUG}/enabled",
        headers=headers,
        params=params,
        json={**ids, "enabled": False},
    )
    assert disabled.status_code == 200, disabled.text
    [off] = disabled.json()["channels"]
    assert off["enabled"] is False
    removed = await client.delete(
        f"{PATH}/{SLUG}",
        headers=headers,
        params={**params, **{key: off[key] for key in before}},
    )
    assert removed.status_code == 200, removed.text
    [blocked] = removed.json()["channels"]
    assert blocked["state"] == "registered" and not blocked["can_install"]
    assert [verb for verb, _ in calls] == ["install", "enable", "disable", "remove"]


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"", b"{}", b'{"connection_mode":"shared"}'])
async def test_workspace_scope_is_explicit_but_old_clients_still_install(
    endpoint_fixture, body
):
    client, headers, params, state, calls = endpoint_fixture
    [entry] = (await client.get(PATH, headers=headers, params=params)).json()[
        "channels"
    ]
    assert entry["definition"]["install_scope"] == "shared"
    assert not entry["connected"]
    response = await client.post(
        f"{PATH}/{SLUG}/install", headers=headers, params=params, content=body
    )
    assert response.status_code == 200, response.text
    [installed] = response.json()["channels"]
    assert installed["definition"]["install_scope"] == "shared"
    assert installed["connected"] and installed["state"] == "inactive"
    assert not state["installation"].enabled
    assert [verb for verb, _ in calls] == ["install"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body, query",
    [
        (b'{"connection_mode":"per_user"}', {}),
        (b'{"connection_mode":"another"}', {}),
        (b'{"connection_mode":null}', {}),
        (b'{"connection_mode":true}', {}),
        (b'{"install_scope":"per_user"}', {}),
        (b'{"connection_mode":"shared","workspace_id":"other"}', {}),
        (b'{"connection_mode":"per_user","connection_mode":"shared"}', {}),
        (b'{"connection_mode":"shared"', {}),
        (b"null", {}),
        (b"[]", {}),
        (b" " * 257, {}),
        (b"", {"connection_mode": "per_user"}),
        (b"", {"install_scope": "personal"}),
    ],
)
async def test_wrong_or_unknown_install_scope_is_denied_before_adapter(
    endpoint_fixture, body, query, monkeypatch
):
    client, headers, params, state, calls = endpoint_fixture
    describe = AsyncMock(side_effect=AssertionError("Wrong scope reached the adapter"))
    monkeypatch.setattr(ChannelPackageRegistry, "describe_one", describe)
    response = await client.post(
        f"{PATH}/{SLUG}/install",
        headers=headers,
        params={**params, **query},
        content=body,
    )
    assert response.status_code == 400, response.text
    assert "Workspace connection scope" in response.text
    describe.assert_not_awaited()
    assert state["installation"] is None
    assert calls == []


@pytest.mark.asyncio
async def test_unready_or_unsupported_never_installs(endpoint_fixture):
    client, headers, params, state, calls = endpoint_fixture
    state["ready"] = False
    unavailable = await client.post(
        f"{PATH}/{SLUG}/install", headers=headers, params=params
    )
    unknown = await client.post(
        f"{PATH}/unknown_channel/install", headers=headers, params=params
    )
    mismatched = await client.post(
        f"{PATH}/{SLUG}/install", headers=headers, params={"workspace_id": "elsewhere"}
    )
    assert (unavailable.status_code, unknown.status_code, mismatched.status_code) == (
        409,
        404,
        400,
    )
    assert calls == []


@pytest.mark.asyncio
async def test_non_manager_cannot_observe_or_operate(
    endpoint_fixture, second_user_client
):
    _, headers, params, _, calls = endpoint_fixture
    attempts = (
        await second_user_client.get(PATH, headers=headers, params=params),
        await second_user_client.post(
            f"{PATH}/{SLUG}/install", headers=headers, params=params
        ),
        await second_user_client.put(
            f"{PATH}/{SLUG}/enabled",
            headers=headers,
            params=params,
            json={"enabled": True, "provision_id": "p", "route_epoch": "1"},
        ),
        await second_user_client.delete(
            f"{PATH}/{SLUG}",
            headers=headers,
            params={**params, "provision_id": "p", "route_epoch": "1"},
        ),
    )
    assert [response.status_code for response in attempts] == [403] * 4
    assert calls == []


@pytest.mark.asyncio
async def test_optional_configuration_uses_exact_scope_provision_and_profile_revision(
    endpoint_fixture,
):
    client, headers, params, state, calls = endpoint_fixture
    target = f"{PATH}/{SLUG}/configuration"
    response = await client.post(
        f"{PATH}/{SLUG}/install", headers=headers, params=params
    )
    [row] = response.json()["channels"]
    assert row["configuration_kind"] == "published-public-profile-v1"
    ids = {key: row[key] for key in ("provision_id", "route_epoch")}
    described = await client.get(target, headers=headers, params={**params, **ids})
    assert described.status_code == 200, described.text
    assert described.json() == {
        **ids,
        "kind": "published-public-profile-v1",
        "profile_revision": 4,
        "profile_key": None,
        "eligible_profiles": [{"key": "public", "name": "Visitor guide"}],
        "editable": True,
    }
    body = {**ids, "expected_profile_revision": 4, "profile_key": "public"}
    for invalid in (
        {**body, "expected_profile_revision": 3},
        {**body, "profile_key": "staff"},
        {**body, "route_epoch": "stale"},
    ):
        response = await client.put(
            target, headers=headers, params=params, json=invalid
        )
        assert response.status_code == 409, response.text
    assert [verb for verb, _ in calls] == ["install"]

    response = await client.put(target, headers=headers, params=params, json=body)
    assert response.status_code == 200, response.text
    [new] = response.json()["channels"]
    assert new["route_epoch"] != ids["route_epoch"] and state["profile"] == "public"
    assert [verb for verb, _ in calls] == ["install", "assign"]
    assert not state["installation"].enabled
    assert (
        await client.get(target, headers=headers, params={**params, **ids})
    ).status_code == 409


@pytest.mark.asyncio
async def test_management_reads_never_project_public_share_or_mutate(
    endpoint_fixture, second_user_client
):
    client, headers, params, state, calls = endpoint_fixture
    workspace = params["workspace_id"]
    target = f"{PATH}/{SLUG}/configuration"
    ids = {"provision_id": "synthetic-provision", "route_epoch": "2"}
    for enabled, ready, status in (
        (False, True, "inactive"),
        (True, True, "active"),
        (True, False, "unavailable"),
    ):
        # Synthetic in-process state only; never install, enable, or bind a route.
        state.update(
            installation=ChannelInstallation(
                workspace, SLUG, ids["provision_id"], ids["route_epoch"], enabled
            ),
            ready=ready,
            profile="public",
        )
        before = dict(state)
        listed = await client.get(PATH, headers=headers, params=params)
        configured = await client.get(target, headers=headers, params={**params, **ids})
        assert listed.status_code == configured.status_code == 200
        [row] = listed.json()["channels"]
        assert row["state"] == status
        for response in (listed, configured):
            assert "full_page_url" not in response.text
            assert "approved-full-page-share-v1" not in response.text
        assert state == before
    denied = await second_user_client.get(
        target, headers=headers, params={**params, **ids}
    )
    assert denied.status_code == 403
    assert "full_page_url" not in denied.text
    assert state["share_reads"] == 0 and calls == []
