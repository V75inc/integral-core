"""Plan 07-01 — seeded library package coverage (LIB-01).

Four parametrized contracts assert the core load + validate + listability +
merge-into-fresh-track invariants for every spec returned by
``load_library_operational_models()``. The 6 Phase 7 packages + the
``CRM`` / ``Projects`` reference packages all gain ``package.tags`` for MCP-04
keyword resolution; the two non-Phase-7 specs (``Agent Scratch`` and
``Personal CRM``) are exempt from the tag-presence assertion (they
predate Plan 07-01 and are not refined here).

See also:
- ``docs/INVARIANTS.md`` I-LIB-01 (every refined spec carries
  ``package.tags`` with len >= 3)
- ``docs/INVARIANTS.md`` I-LIB-02 (semantics — backend-only, never crosses
  the ``extra=forbid`` Pydantic boundary)
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest
from httpx import AsyncClient

from app.services.operational_model_loader import (
    LibraryProfileSpec as SeededLibraryPackageSpec,
)
from app.services.operational_model_loader import (
    load_library_operational_models,
)
from app.services.operational_model_runtime import compile_canonical_manifest
from app.services.personal_workspace import ensure_personal_workspace

# Spec display names that Plan 07-01 does NOT refine (no ``package.tags``).
_UNREFINED_SPEC_NAMES = frozenset(
    {
        # Phase 4 MEM-01 — per-user scratch space.
        "Agent Scratch",
        # Phase 3.1 — Personal CRM space (alias-style, not in 07-01 scope).
        "Personal CRM",
        # Phase 5 CON-02 / CON-04 — GitHub Issues connector package.
        "GitHub Issues",
    }
)


def _all_specs() -> List[SeededLibraryPackageSpec]:
    return list(load_library_operational_models())


def _refined_specs() -> List[SeededLibraryPackageSpec]:
    return [s for s in _all_specs() if s.name not in _UNREFINED_SPEC_NAMES]


@pytest.mark.parametrize("spec", _all_specs(), ids=lambda s: s.name)
def test_seeded_package_compiles_canonically(spec: SeededLibraryPackageSpec) -> None:
    """Every spec's manifest survives ``compile_canonical_manifest`` round-trip.

    Permissive on ``package`` subkeys, so Plan 07-01's additive ``tags``
    field MUST NOT trigger a schema rejection.
    """
    canonical = compile_canonical_manifest(manifest=dict(spec.manifest))
    assert canonical["operational_model_schema_version"] == 2
    # ``workspace`` scope was added as a top-level option alongside
    # ``track``/``app`` (see operational_model_runtime.py — manifest scope
    # validator). Seeded library packages may declare any of the three.
    assert canonical["scope"] in ("track", "app", "workspace")
    assert isinstance(canonical.get("package"), dict)
    assert (canonical["package"].get("name") or "").strip()


@pytest.mark.parametrize("spec", _refined_specs(), ids=lambda s: s.name)
def test_refined_package_has_tags(spec: SeededLibraryPackageSpec) -> None:
    """Refined Plan 07-01 packages MUST carry ``package.tags: List[str]`` len >= 3.

    This is the I-LIB-01 enforcement point. ``_UNREFINED_SPEC_NAMES``
    enumerates the carve-outs (specs predating Plan 07-01).
    """
    pkg = spec.manifest.get("package") or {}
    tags = pkg.get("tags")
    assert isinstance(
        tags, list
    ), f"spec {spec.name!r}: package.tags must be a list (got {type(tags).__name__})"
    assert (
        len(tags) >= 3
    ), f"spec {spec.name!r}: package.tags must have >= 3 entries (got {len(tags)})"
    for tag in tags:
        assert (
            isinstance(tag, str) and tag.strip()
        ), f"spec {spec.name!r}: every tag must be a non-empty str (got {tag!r})"


@pytest.fixture
async def _seeded_lib_user_ready(test_user):
    """Personal workspace + graph User node — required for track OWNS wiring."""
    if test_user is not None:
        await ensure_personal_workspace(test_user)
    yield test_user


@pytest.mark.asyncio
async def test_seeded_packages_load_at_startup(
    authenticated_client: AsyncClient,
    _seeded_lib_user_ready,
    library_catalog_seeded,
) -> None:
    """``GET /api/operational-models`` exposes every seeded library spec.

    Confirms Plan 07-01's LIB-01 load+validate+listable proof — previously
    unasserted in the test suite.
    """
    resp = await authenticated_client.get("/api/operational-models")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    items = body.get("operational_models") or []
    listed_names = {(p.get("name") or "") for p in items}
    expected_names = {spec.name for spec in _all_specs()}
    missing = expected_names - listed_names
    assert not missing, (
        f"GET /api/operational-models missing seeded specs: {sorted(missing)} "
        f"(listed: {sorted(listed_names)})"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "spec",
    [s for s in _refined_specs() if s.manifest.get("scope") == "track"],
    ids=lambda s: s.name,
)
async def test_track_scope_package_merges_into_fresh_track(
    spec: SeededLibraryPackageSpec,
    authenticated_client: AsyncClient,
    _seeded_lib_user_ready,
    library_catalog_seeded,
) -> None:
    """Every refined track-scope spec merges into a fresh Track without error.

    Asserts the (load → list → resolve-by-name → merge) chain holds for
    every refined spec. The fresh Track's resulting attached CP must
    materialize >= 1 EntryType (otherwise the merge silently no-op'd).
    """
    listed = await authenticated_client.get("/api/operational-models")
    assert listed.status_code == 200, listed.text
    rows: List[Dict[str, Any]] = listed.json().get("operational_models") or []
    match = next((r for r in rows if (r.get("name") or "") == spec.name), None)
    assert match, f"spec {spec.name!r} not found in /api/operational-models listing"
    lib_id = match.get("id")
    assert lib_id

    created = await authenticated_client.post(
        "/api/tracks",
        json={"title": f"07-01 merge test: {spec.name}", "visibility": "private"},
    )
    assert created.status_code == 200, created.text
    track_id = created.json()["track"]["id"]

    merged = await authenticated_client.post(
        f"/api/tracks/{track_id}/operational-model/merge-library",
        json={"library_operational_model_id": lib_id},
    )
    assert merged.status_code == 200, merged.text

    cp_resp = await authenticated_client.get(
        f"/api/tracks/{track_id}/operational-model"
    )
    assert cp_resp.status_code == 200, cp_resp.text
    cp = cp_resp.json().get("operational_model") or {}
    manifest = cp.get("manifest") or {}
    assert manifest.get("scope") == "track"
    track_tier = manifest.get("track") or {}
    entry_types = track_tier.get("entry_types") or []
    assert (
        isinstance(entry_types, list) and len(entry_types) > 0
    ), f"spec {spec.name!r}: merge produced 0 entry types under track tier"
