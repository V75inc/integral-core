"""Contract: untrusted bundles cannot register executable operations (WP-04)."""

from __future__ import annotations

import pytest

from app.services.hooks.errors import ToolTrustTierDeniedError
from app.services.hooks.trust import check_operations_permitted


@pytest.mark.contract
def test_untrusted_bundle_operations_rejected():
    with pytest.raises(ToolTrustTierDeniedError):
        check_operations_permitted("community", 2, "evil-app")


@pytest.mark.contract
def test_trusted_bundle_operations_allowed():
    check_operations_permitted("trusted", 3, "asset-register")
