"""WP-01 contracts for the explicit Core module composition root."""

from __future__ import annotations

from unittest.mock import patch

from app.modules import core_modules
from app.modules.intelligence import IntelligenceRuntimeStatus
from app.modules.policy import policy_module


def test_core_modules_exposes_the_single_policy_interface() -> None:
    """All consumers receive the Core-owned policy module instance."""
    assert core_modules() is core_modules()
    assert core_modules().policy is policy_module


def test_core_modules_reads_optional_intelligence_without_boot_coupling() -> None:
    """Composition exposes status, while bootstrap remains an infrastructure job."""
    expected = IntelligenceRuntimeStatus(available=False, reason="not_started")
    with patch(
        "app.modules.composition.intelligence_runtime_status", return_value=expected
    ):
        assert core_modules().intelligence_status() is expected
