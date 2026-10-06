"""Boundary checks: Core stays offer-free without a host extension module."""

from __future__ import annotations

import os

import pytest

from app.api.errors import EntitlementRequiredError
from app.services.entitlements import require_active_entitlement


def test_entitlement_required_error_is_stable_and_provider_neutral():
    err = EntitlementRequiredError(
        message="entitlement required",
        details={"entitlement_key": "crm", "package_slug": "crm"},
    )
    assert err.status_code == 403
    assert err.error_code == "entitlement.required"
    assert err.details["entitlement_key"] == "crm"


@pytest.mark.asyncio
async def test_missing_entitlement_raises_generic_denial(monkeypatch):
    async def _no_row(**_kwargs):
        return None

    monkeypatch.setattr(
        "app.services.entitlements.find_entitlement",
        _no_row,
    )
    with pytest.raises(EntitlementRequiredError) as caught:
        await require_active_entitlement(
            workspace_id="n.Workspace.test",
            package_meta={"slug": "crm", "class": "commercial_app"},
        )
    assert caught.value.error_code == "entitlement.required"
    assert caught.value.details["entitlement_key"] == "crm"


def test_host_extension_module_defaults_empty():
    from app.config import settings

    assert (settings.INTEGRAL_HOST_EXTENSION_MODULE or "").strip() == ""
    # Compatibility alias may also be empty in Core-only boots.
    assert (os.environ.get("INTEGRAL_HOST_EXTENSION_MODULE") or "").strip() == ""
