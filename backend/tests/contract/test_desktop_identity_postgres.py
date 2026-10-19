"""Atomic desktop identity and real signed-session admission on PostgreSQL."""

from __future__ import annotations

import asyncio

import pytest
from jvspatial.api.auth.models import User as AuthUser
from jvspatial.core.context import get_default_context

from app.config import settings
from app.models.nodes import User, Workspace
from app.services.app_graph import ensure_integral_app_graph
from app.services.authentication import get_authentication_service
from app.services.deployment import owner_ids
from app.services.desktop_identity import bootstrap_desktop_owner, native_session

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


@pytest.fixture(params=[None, "local"])
def desktop_profile(monkeypatch, request):
    """Select an isolated synthetic desktop identity."""
    from app.runtime import local_identity

    # Each parameter models a fresh application process with its own event loop.
    monkeypatch.setattr(local_identity, "_bootstrap_lock", asyncio.Lock())
    monkeypatch.setattr(settings, "INTEGRAL_DEPLOYMENT_PROFILE", "desktop_single_user")
    monkeypatch.setattr(settings, "INTEGRAL_RUNTIME_PRESET", request.param)
    monkeypatch.setattr(settings, "INTEGRAL_DESKTOP_INSTALLATION_ID", "d" * 32)
    monkeypatch.setattr(settings, "INTEGRAL_DESKTOP_CONTROL_TOKEN", "c" * 64)


async def test_concurrent_bootstrap_has_one_rooted_owner_and_real_session(
    postgres_raw_db, desktop_profile
):
    """Concurrent bootstrap produces one identity, and reopening retains it."""
    await ensure_integral_app_graph()
    await asyncio.gather(*(bootstrap_desktop_owner() for _ in range(6)))
    for collection, model in (
        ("object", AuthUser),
        ("node", User),
        ("node", Workspace),
    ):
        assert (
            len(
                await postgres_raw_db.find(collection, {"entity": model._entity_name()})
            )
            == 1
        )
    facts = await postgres_raw_db.find(
        "object",
        {
            "entity": "OperationEventOutbox",
            "context.operation_key": {"$in": ["user.create", "workspace.create"]},
        },
    )
    assert len(facts) == 2
    assert {fact["context"]["status"] for fact in facts} == {"pending"}
    assert {fact["context"]["event"]["action"] for fact in facts} == {
        "user.create",
        "workspace.create",
    }
    for fact in facts:
        event = fact["context"]["event"]
        expected_scope = (
            f"user:{owner_ids()[1]}"
            if event["action"] == "user.create"
            else f"ws:{event['resource_id']}"
        )
        assert event["scope"] == expected_scope
    service = get_authentication_service()
    session = await native_session()
    principal = await service.validate_token(session["access_token"])
    assert principal.id == owner_ids()[0]
    renewed = await service.refresh_access_token(session["refresh_token"])
    assert (await service.validate_token(renewed.access_token)).id == principal.id
    await bootstrap_desktop_owner()
    assert (
        await service.validate_token((await native_session())["access_token"])
    ).id == principal.id
    from app.services.graph_reachability import run_audit

    report = await run_audit(raise_on_violation=False)
    assert report.reachable_counts["User"] == 1
    assert report.reachable_counts["Workspace"] == 1


async def test_failed_workspace_catalog_rolls_back_auth_owner_and_marker(
    postgres_raw_db, desktop_profile, monkeypatch
):
    """An interrupted first launch cannot leave a partial owner or workspace."""
    from app.runtime import local_identity as desktop_identity

    await ensure_integral_app_graph()
    original = desktop_identity.catalog_workspace

    async def fail(workspace):
        raise RuntimeError("simulated catalog failure")

    monkeypatch.setattr(desktop_identity, "catalog_workspace", fail)
    with pytest.raises(RuntimeError, match="simulated catalog failure"):
        await bootstrap_desktop_owner()
    for collection, model in (
        ("object", AuthUser),
        ("node", User),
        ("node", Workspace),
        ("object", desktop_identity.DesktopIdentity),
    ):
        assert not await postgres_raw_db.find(
            collection, {"entity": model._entity_name()}
        )
    monkeypatch.setattr(desktop_identity, "catalog_workspace", original)
    await bootstrap_desktop_owner()
    assert await native_session()


async def test_existing_identity_is_refused_without_deletion(
    postgres_raw_db, desktop_profile
):
    """An incompatible store is never claimed or converted by desktop boot."""
    await ensure_integral_app_graph()
    await postgres_raw_db.save(
        "object",
        {
            "id": "o.User.other",
            "entity": AuthUser._entity_name(),
            "context": {"email": "existing@example.test"},
        },
    )
    with pytest.raises(RuntimeError, match="not an eligible single-owner"):
        await bootstrap_desktop_owner()
    rows = await postgres_raw_db.find("object", {"entity": AuthUser._entity_name()})
    assert [row["id"] for row in rows] == ["o.User.other"]
    assert not await get_default_context().find(User, {})


async def test_desktop_retains_commercial_extension_entitlement_and_export_contract(
    postgres_raw_db, desktop_profile, monkeypatch
):
    """Local ownership neither bypasses App licensing nor loses exported data."""
    from app.api.errors import InsufficientPermissionsError
    from app.models.edges import CONTAINS
    from app.models.nodes import App, Entry
    from app.services.app_export import export_app_bundle
    from app.services.app_lifecycle import install_app, resume_app
    from app.services.entitlements import grant_entitlement, revoke_entitlement
    from tests.contract.test_f3_entitlement_kill_switch import (
        COMMERCIAL,
        ENT_KEY,
        SLUG,
        _seed_commercial_library,
    )

    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(COMMERCIAL.parent))
    monkeypatch.setenv("INTEGRAL_CORE_ONLY", "0")
    await ensure_integral_app_graph()
    await bootstrap_desktop_owner()
    actor = owner_ids()[1]
    workspace = (await get_default_context().find(Workspace, {}, limit=1))[0]
    library = await _seed_commercial_library()
    with pytest.raises(InsufficientPermissionsError):
        await install_app(
            workspace_id=workspace.id,
            library_cp_id=library.id,
            actor_id=actor,
            include_seed_data=False,
        )
    await grant_entitlement(
        workspace_id=workspace.id,
        entitlement_key=ENT_KEY,
        package_slug=SLUG,
        actor_id=actor,
    )
    installed = await install_app(
        workspace_id=workspace.id,
        library_cp_id=library.id,
        actor_id=actor,
        include_seed_data=False,
    )
    app = await App.get(installed["app_id"])
    track = (await app.nodes(edge=[CONTAINS], node=["Track"], limit=1))[0]
    entry = await Entry.create(
        title="Local paid-App data", body="Retained after revocation", track_id=track.id
    )
    await track.connect(entry, edge=CONTAINS)
    result = await revoke_entitlement(
        workspace_id=workspace.id, entitlement_key=ENT_KEY, actor_id=actor
    )
    assert app.id in result["paused_app_ids"]
    bundle = await export_app_bundle(app_id=app.id)
    assert bundle["policy"]["data_access"] == "core_generic_read"
    assert entry.title in {row.get("title") for row in bundle["entries"]}
    with pytest.raises(InsufficientPermissionsError):
        await resume_app(app_id=app.id, actor_id=actor)
    # Restart admission remains valid with installed and paused commercial Apps.
    await bootstrap_desktop_owner()


async def test_default_desktop_workspace_remains_recoverable(
    postgres_raw_db, desktop_profile
):
    """Deleting the default must not strand the installation marker on restart."""
    from app.api.errors import ResourceConflictError
    from app.services.workspace_lifecycle import delete_workspace_cascade

    await ensure_integral_app_graph()
    await bootstrap_desktop_owner()
    workspace = (await get_default_context().find(Workspace, {}, limit=1))[0]
    with pytest.raises(ResourceConflictError):
        await delete_workspace_cascade(workspace, cascade=True, actor_id=owner_ids()[1])
    await bootstrap_desktop_owner()
    assert await native_session()
