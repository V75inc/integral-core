"""F0 Core-only suite — library filter + import smoke under INTEGRAL_CORE_ONLY."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.operational_model_library_sync import (
    reset_library_operational_models_cache_for_testing,
)
from app.services.operational_model_loader import (
    load_library_operational_models_with_issues,
)
from app.services.package_paths import CORE_SEED_SLUGS

PROFILES = Path(__file__).resolve().parents[2] / "app" / "packages"


@pytest.fixture
def core_only_env(monkeypatch):
    monkeypatch.setenv("INTEGRAL_CORE_ONLY", "1")
    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(PROFILES))
    reset_library_operational_models_cache_for_testing()
    yield
    reset_library_operational_models_cache_for_testing()


@pytest.mark.core_only
def test_core_only_library_contains_only_core_packages(core_only_env):
    assert PROFILES.is_dir(), f"missing Operational Model packages root {PROFILES}"
    specs, _issues = load_library_operational_models_with_issues(
        package_paths=[PROFILES],
        core_only=True,
        verify_signatures=False,
    )
    slugs = {s.slug for s in specs}
    assert slugs, "expected at least one core_package in library"
    for s in specs:
        assert (
            s.package_class == "core_package" or s.slug in CORE_SEED_SLUGS
        ), f"non-core package leaked into core-only library: {s.slug}"
    assert "crm" not in slugs
    assert "payroll-app" not in slugs
    assert "sales" not in slugs


@pytest.mark.core_only
def test_core_only_import_smoke(core_only_env):
    """Core package-path helpers must resolve under CORE_ONLY."""
    from app.services import core_seed_installer, package_paths

    assert package_paths.is_core_only_mode() is True
    assert package_paths.CORE_SEED_SLUGS
    assert callable(core_seed_installer.install_core_package)
    assert callable(core_seed_installer.resolve_core_package_library)
