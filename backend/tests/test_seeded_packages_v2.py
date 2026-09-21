"""Phase 10 Plan 10-03 — seeded-package v2 round-trip test.

After SCHEMA_VERSION bumps to 2 (Task 2), every seeded package under
``backend/app/packages/`` must declare
``operational_model_schema_version: 2`` and compile cleanly through
``compile_canonical_manifest()`` (Pitfall 3 — server boot fails otherwise).

Test approach: iterate all YAML library Operational Models, pull each manifest dict,
pass it through the compiler, and assert no exception is raised.
"""

from __future__ import annotations

import pytest

from app.services.operational_model_loader import load_library_operational_models
from app.services.operational_model_runtime import (
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
    return list(load_library_operational_models())


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
    """Every seeded library package declares operational_model_schema_version: 2."""
    manifest = spec.manifest or {}
    assert manifest.get("operational_model_schema_version") == 2, (
        f"Seeded package {spec.name!r} still declares "
        f"operational_model_schema_version={manifest.get('operational_model_schema_version')}; "
        f"Plan 10-03 Task 2 mandates 2."
    )
