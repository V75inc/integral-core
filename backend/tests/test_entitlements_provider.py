"""Provider/manual entitlement race and respect_manual coverage."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context

from app.models.entitlement import Entitlement
from app.services.entitlements import (
    find_entitlement,
    grant_entitlement,
    revoke_entitlement,
    revoke_provider_entitlement,
)
from app.services.personal_workspace import ensure_personal_workspace


@pytest.mark.asyncio
async def test_revoke_provider_skips_manual_row(test_user):
    workspace = await ensure_personal_workspace(test_user)
    row = await grant_entitlement(
        workspace_id=workspace.id,
        entitlement_key="crm",
        package_slug="crm",
        actor_id=test_user.id,
        source="manual",
    )
    assert row.source == "manual"

    result = await revoke_provider_entitlement(
        workspace_id=workspace.id,
        entitlement_key="crm",
        actor_id="system:billing",
    )
    assert result["skipped"] is True
    assert result["reason"] == "manual"
    fresh = await Entitlement.get(row.id)
    assert fresh is not None
    assert fresh.status == "active"
    assert (fresh.source or "") == "manual"


@pytest.mark.asyncio
async def test_manual_revoke_preserves_storage_source_across_contexts(test_user):
    """An older worker cache must not erase another worker's manual grant."""
    workspace = await ensure_personal_workspace(test_user)
    key = "manual-revoke-cache"
    provider = await grant_entitlement(
        workspace_id=workspace.id,
        entitlement_key=key,
        package_slug=key,
        actor_id=test_user.id,
        source="provider",
        on_loss="disable",
    )
    context = await provider.get_context()
    cached = await Entitlement.get(provider.id)
    assert cached is not None and cached.source == "provider"

    # Both contexts use real storage, but retain independent identity caches.
    with scoped_default_context(GraphContext(database=context.database)):
        await grant_entitlement(
            workspace_id=workspace.id,
            entitlement_key=key,
            package_slug=key,
            actor_id=test_user.id,
            source="manual",
            on_loss="pause",
        )
    assert cached.source == "provider"
    before = await find_entitlement(workspace_id=workspace.id, entitlement_key=key)
    assert before is not None and before.source == "manual"

    result = await revoke_entitlement(
        workspace_id=workspace.id,
        entitlement_key=key,
        actor_id=test_user.id,
        pause_installed=False,
    )
    assert result["status"] == "revoked"
    revoked = await find_entitlement(workspace_id=workspace.id, entitlement_key=key)
    assert revoked is not None
    assert revoked.source == "manual"
    assert revoked.status == "revoked"
    assert revoked.on_loss == "pause"

    await grant_entitlement(
        workspace_id=workspace.id,
        entitlement_key=key,
        package_slug=key,
        actor_id="system:provider",
        source="provider",
        respect_manual=True,
    )
    reconciled = await find_entitlement(workspace_id=workspace.id, entitlement_key=key)
    assert reconciled is not None
    assert reconciled.source == "manual"
    assert reconciled.status == "revoked"


@pytest.mark.asyncio
async def test_revoke_provider_revokes_provider_row(test_user):
    workspace = await ensure_personal_workspace(test_user)
    row = await grant_entitlement(
        workspace_id=workspace.id,
        entitlement_key="crm-provider",
        package_slug="crm",
        actor_id=test_user.id,
        source="stripe",
    )
    assert row.source == "stripe"

    result = await revoke_provider_entitlement(
        workspace_id=workspace.id,
        entitlement_key="crm-provider",
        actor_id="system:billing",
    )
    assert result["skipped"] is False
    assert result["status"] == "revoked"
    fresh = await Entitlement.get(row.id)
    assert fresh is not None
    assert fresh.status == "revoked"


@pytest.mark.asyncio
async def test_revoke_provider_preserves_manual_after_interleaved_swap(test_user):
    """TOCTOU: first read is provider, second read (inside CAS miss path) is manual."""
    workspace = await ensure_personal_workspace(test_user)
    provider_row = await grant_entitlement(
        workspace_id=workspace.id,
        entitlement_key="crm-race",
        package_slug="crm",
        actor_id=test_user.id,
        source="stripe",
    )
    manual_row = Entitlement(
        id=provider_row.id,
        workspace_id=workspace.id,
        entitlement_key="crm-race",
        package_slug="crm",
        status="active",
        source="manual",
        on_loss="pause",
        data_access="core_generic_read",
        retention="retain_until_uninstall",
    )

    calls = {"n": 0}

    async def _find_side_effect(**_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return provider_row
        return manual_row

    with (
        patch(
            "app.services.entitlements.find_entitlement",
            new=AsyncMock(side_effect=_find_side_effect),
        ),
        patch(
            "app.services.entitlements._cas_update_entitlement_if_source",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await revoke_provider_entitlement(
            workspace_id=workspace.id,
            entitlement_key="crm-race",
            actor_id="system:billing",
        )

    assert result["skipped"] is True
    assert result["reason"] == "manual"


@pytest.mark.asyncio
async def test_grant_respect_manual_preserves_existing_manual(test_user):
    workspace = await ensure_personal_workspace(test_user)
    manual = await grant_entitlement(
        workspace_id=workspace.id,
        entitlement_key="crm-manual",
        package_slug="crm",
        actor_id=test_user.id,
        source="manual",
    )
    out = await grant_entitlement(
        workspace_id=workspace.id,
        entitlement_key="crm-manual",
        package_slug="crm",
        actor_id="system:billing",
        source="stripe",
        respect_manual=True,
    )
    assert out.id == manual.id
    assert (out.source or "") == "manual"
    assert out.status == "active"


@pytest.mark.asyncio
async def test_grant_respect_manual_cas_miss_returns_manual(test_user):
    workspace = await ensure_personal_workspace(test_user)
    provider = await grant_entitlement(
        workspace_id=workspace.id,
        entitlement_key="crm-cas",
        package_slug="crm",
        actor_id=test_user.id,
        source="stripe",
    )
    manual = Entitlement(
        id=provider.id,
        workspace_id=workspace.id,
        entitlement_key="crm-cas",
        package_slug="crm",
        status="active",
        source="manual",
        on_loss="pause",
        data_access="core_generic_read",
        retention="retain_until_uninstall",
    )

    find_calls = {"n": 0}

    async def _find_side_effect(**_kwargs):
        find_calls["n"] += 1
        if find_calls["n"] == 1:
            return provider
        return manual

    with (
        patch(
            "app.services.entitlements.find_entitlement",
            new=AsyncMock(side_effect=_find_side_effect),
        ),
        patch(
            "app.services.entitlements._cas_update_entitlement_if_source",
            new=AsyncMock(return_value=None),
        ),
    ):
        out = await grant_entitlement(
            workspace_id=workspace.id,
            entitlement_key="crm-cas",
            package_slug="crm",
            actor_id="system:billing",
            source="stripe",
            respect_manual=True,
        )

    assert (out.source or "") == "manual"


@pytest.mark.asyncio
async def test_fallback_revoke_preserves_real_manual_grant_interleaving(test_user):
    """Real-storage JSON/memory race: manual grant during provider revoke get.

    Mirrors the merge-fitness reproduction: wrap ``Entitlement.get`` so a real
    ``grant_entitlement(source='manual')`` commits after the provider object is
    read inside the CAS path, then assert storage still holds the manual row.
    Does not mock ``_cas_update_entitlement_if_source``.
    """
    workspace = await ensure_personal_workspace(test_user)
    provider = await grant_entitlement(
        workspace_id=workspace.id,
        entitlement_key="crm-real-race",
        package_slug="crm",
        actor_id=test_user.id,
        source="stripe",
    )
    provider_id = provider.id
    injected = {"done": False}
    original_get = Entitlement.get

    async def _get_with_manual_inject(entity_id: str, *args, **kwargs):
        row = await original_get(entity_id, *args, **kwargs)
        if (
            not injected["done"]
            and row is not None
            and str(getattr(row, "id", "")) == str(provider_id)
            and (getattr(row, "source", None) or "") == "stripe"
        ):
            injected["done"] = True
            await grant_entitlement(
                workspace_id=workspace.id,
                entitlement_key="crm-real-race",
                package_slug="crm",
                actor_id=test_user.id,
                source="manual",
            )
        return row

    with patch.object(Entitlement, "get", new=staticmethod(_get_with_manual_inject)):
        result = await revoke_provider_entitlement(
            workspace_id=workspace.id,
            entitlement_key="crm-real-race",
            actor_id="system:billing",
        )

    assert injected["done"] is True
    assert result.get("skipped") is True
    assert result.get("reason") == "manual"
    fresh = await find_entitlement(
        workspace_id=workspace.id, entitlement_key="crm-real-race"
    )
    assert fresh is not None
    assert (fresh.source or "") == "manual"
    assert fresh.status == "active"
