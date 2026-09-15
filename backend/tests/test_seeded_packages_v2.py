"""Phase 10 Plan 10-03 — seeded-package v2 round-trip test.

After SCHEMA_VERSION bumps to 2 (Task 2), every seeded package under
``backend/app/profiles/`` must declare
``content_profile_schema_version: 2`` and compile cleanly through
``compile_canonical_manifest()`` (Pitfall 3 — server boot fails otherwise).

Test approach: iterate all YAML library profiles, pull each manifest dict,
pass it through the compiler, and assert no exception is raised.
"""

from __future__ import annotations

import pytest

from app.services.content_profile_loader import load_library_profiles
from app.services.content_profile_runtime import (
    compile_canonical_manifest,
    invalidate_manifest_cache,
)


@pytest.fixture(autouse=True)
def _clear_compile_cache():
    invalidate_manifest_cache()
    yield
    invalidate_manifest_cache()


def _all_seeded_specs():
    """Return the registered library spec list. Used by parametrize."""
    return list(load_library_profiles())


@pytest.mark.parametrize(
    "spec", _all_seeded_specs(), ids=lambda s: getattr(s, "name", "unknown")
)
def test_each_seeded_package_compiles_under_v2(spec):
    """Every seeded library package compiles without error under v2."""
    manifest = spec.manifest or {}
    # Should not raise.
    compile_canonical_manifest(manifest=dict(manifest))


@pytest.mark.parametrize(
    "spec", _all_seeded_specs(), ids=lambda s: getattr(s, "name", "unknown")
)
def test_each_seeded_package_declares_schema_version_2(spec):
    """Every seeded library package declares content_profile_schema_version: 2."""
    manifest = spec.manifest or {}
    assert manifest.get("content_profile_schema_version") == 2, (
        f"Seeded package {spec.name!r} still declares "
        f"content_profile_schema_version={manifest.get('content_profile_schema_version')}; "
        f"Plan 10-03 Task 2 mandates 2."
    )
