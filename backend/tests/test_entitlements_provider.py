"""Provider/manual entitlement race and respect_manual coverage."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.models.entitlement import Entitlement
from app.services.entitlements import (
    grant_entitlement,
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
