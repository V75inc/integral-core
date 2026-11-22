"""Auth, exact scope, and lifecycle over HTTP with a non-serving in-process adapter."""

import asyncio
from dataclasses import replace

import pytest

from app.agentive.api.managed_channels import install_managed_channel_registry
from app.api.errors import ResourceConflictError
from app.connectors.channel_packages import (
    ChannelDefinition,
    ChannelInstallation,
    ChannelLifecycle,
    ChannelPackage,
    ChannelPackageRegistry,
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
    state = {"installation": None, "ready": True, "epoch": 0}
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

    async def never(*args):
        raise AssertionError("Transport or sharing is never called by management")

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
            ChannelServing(probe, never, never),
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
