"""Phase B (B3) — bundle metadata persists on OperationalModel rows.

The library-seed upsert in
``backend/app/services/operational_model_library_seed.py`` MUST persist the
v3 ``LibraryProfileSpec`` bundle-derived fields onto
``OperationalModel.metadata`` so hot-load can detect file edits via
``bundle_fingerprint`` drift even when the canonical manifest is
equivalent.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_upsert_writes_bundle_fingerprint_to_metadata(
    authenticated_client: AsyncClient,
) -> None:
    """Ensure a OperationalModel row created via upsert carries bundle metadata.

    ``authenticated_client`` is used purely to trigger jvspatial bootstrap
    (Server, registries, DB) — the test exercises the seed upsert directly.
    """
    from app.models.nodes import OperationalModel, OperationalModels
    from app.services.operational_model_library_seed import (
        upsert_seeded_library_packages,
    )
    from app.services.operational_model_loader import LibraryProfileSpec

    cps_raw = await OperationalModels.find({})
    cps_r = cps_raw[0] if isinstance(cps_raw, list) else cps_raw

    async def noop_edge(reg, cp):  # type: ignore[no-untyped-def]
        return None

    spec = LibraryProfileSpec(
        name="Test B3 Metadata",
        slug="test-b3-metadata",
        version="1.0.0",
        description="",
        manifest={
            "operational_model_schema_version": 2,
            "scope": "track",
            "package": {"name": "test-b3-metadata"},
            "track": {"entry_types": []},
        },
        scope="platform",
        library_package=True,
        bundle_dir=Path("/fake/bundle"),
        skill_keys=[],
        ships_python=False,
        signature_path=None,
        signature_verified=True,
        signature_reason="dev_mode",
        manifest_fingerprint="abc",
        bundle_fingerprint="xyz",
    )
    await upsert_seeded_library_packages(
        catalog_registry=cps_r,
        specs=[spec],
        now_iso=datetime.now(timezone.utc).isoformat(),
        ensure_catalog_edge=noop_edge,
    )
    rows = await OperationalModel.find({"context.name": "Test B3 Metadata"})
    assert rows, "upsert did not persist a OperationalModel row for the test spec"
    row = rows[0] if isinstance(rows, list) else rows
    md = row.metadata or {}
    assert md.get("bundle_fingerprint") == "xyz"
    assert md.get("manifest_fingerprint") == "abc"
    assert md.get("signature_verified") is True
    assert md.get("signature_reason") == "dev_mode"
    assert md.get("ships_python") is False
    assert md.get("slug") == "test-b3-metadata"
    assert md.get("bundle_dir_path") == str(Path("/fake/bundle"))


@pytest.mark.asyncio
async def test_upsert_updates_when_bundle_fingerprint_changes(
    authenticated_client: AsyncClient,
) -> None:
    """Bundle-fingerprint drift triggers update even when manifest is equivalent.

    Covers the file-edit-without-manifest-change case hot-load needs to detect.
    """
    from app.models.nodes import OperationalModel, OperationalModels
    from app.services.operational_model_library_seed import (
        upsert_seeded_library_packages,
    )
    from app.services.operational_model_loader import LibraryProfileSpec

    cps_raw = await OperationalModels.find({})
    cps_r = cps_raw[0] if isinstance(cps_raw, list) else cps_raw

    async def noop_edge(reg, cp):  # type: ignore[no-untyped-def]
        return None

    manifest = {
        "operational_model_schema_version": 2,
        "scope": "track",
        "package": {"name": "test-b3-drift"},
        "track": {"entry_types": []},
    }
    spec_v1 = LibraryProfileSpec(
        name="Test B3 Drift",
        slug="test-b3-drift",
        version="1.0.0",
        description="",
        manifest=manifest,
        scope="platform",
        library_package=True,
        bundle_dir=Path("/fake/bundle"),
        bundle_fingerprint="fp-v1",
        manifest_fingerprint="mf",
    )
    await upsert_seeded_library_packages(
        catalog_registry=cps_r,
        specs=[spec_v1],
        now_iso=datetime.now(timezone.utc).isoformat(),
        ensure_catalog_edge=noop_edge,
    )

    # Same canonical manifest, new bundle fingerprint — must trigger update.
    spec_v2 = LibraryProfileSpec(
        name="Test B3 Drift",
        slug="test-b3-drift",
        version="1.0.0",
        description="",
        manifest=manifest,
        scope="platform",
        library_package=True,
        bundle_dir=Path("/fake/bundle"),
        bundle_fingerprint="fp-v2",
        manifest_fingerprint="mf",
    )
    await upsert_seeded_library_packages(
        catalog_registry=cps_r,
        specs=[spec_v2],
        now_iso=datetime.now(timezone.utc).isoformat(),
        ensure_catalog_edge=noop_edge,
    )

    rows = await OperationalModel.find({"context.name": "Test B3 Drift"})
    assert rows
    row = rows[0] if isinstance(rows, list) else rows
    assert (row.metadata or {}).get("bundle_fingerprint") == "fp-v2"


@pytest.mark.asyncio
async def test_upsert_matches_existing_row_by_slug_when_name_changes(
    authenticated_client: AsyncClient,
) -> None:
    from app.models.nodes import OperationalModel, OperationalModels
    from app.services.operational_model_library_seed import (
        upsert_seeded_library_packages,
    )
    from app.services.operational_model_loader import LibraryProfileSpec

    cps_raw = await OperationalModels.find({})
    cps_r = cps_raw[0] if isinstance(cps_raw, list) else cps_raw

    async def noop_edge(reg, cp):  # type: ignore[no-untyped-def]
        return None

    first = LibraryProfileSpec(
        name="Original Display Name",
        slug="slug-stable-id",
        version="1.0.0",
        description="v1",
        manifest={
            "operational_model_schema_version": 2,
            "scope": "track",
            "package": {"name": "slug-stable-id"},
            "track": {"entry_types": []},
        },
        scope="platform",
        library_package=True,
        bundle_dir=Path("/fake/slug-stable-id"),
        bundle_fingerprint="fp-a",
        manifest_fingerprint="mf-a",
    )
    await upsert_seeded_library_packages(
        catalog_registry=cps_r,
        specs=[first],
        now_iso=datetime.now(timezone.utc).isoformat(),
        ensure_catalog_edge=noop_edge,
    )

    second = LibraryProfileSpec(
        name="Renamed Display Name",
        slug="slug-stable-id",
        version="1.0.1",
        description="v2",
        manifest={
            "operational_model_schema_version": 2,
            "scope": "track",
            "package": {"name": "slug-stable-id"},
            "track": {"entry_types": []},
        },
        scope="platform",
        library_package=True,
        bundle_dir=Path("/fake/slug-stable-id"),
        bundle_fingerprint="fp-b",
        manifest_fingerprint="mf-b",
    )
    await upsert_seeded_library_packages(
        catalog_registry=cps_r,
        specs=[second],
        now_iso=datetime.now(timezone.utc).isoformat(),
        ensure_catalog_edge=noop_edge,
    )

    rows = await OperationalModel.find({"context.metadata.slug": "slug-stable-id"})
    if rows is None:
        found = []
    elif isinstance(rows, list):
        found = rows
    else:
        found = [rows]
    assert len(found) == 1
    row = found[0]
    assert row.name == "Renamed Display Name"
    assert row.version == "1.0.1"
    assert (row.metadata or {}).get("bundle_fingerprint") == "fp-b"


def test_canonical_manifests_differ_treats_uncompilable_stored_as_stale() -> None:
    """A STORED manifest referencing a since-removed/renamed view type (or
    wizard step kind) must not raise out of ``canonical_manifests_differ`` —
    that would abort ``upsert_seeded_library_packages``'s whole loop,
    silently wedging every OTHER package's sync too (the only caller,
    ``ensure_integral_app_graph``, just logs and swallows). It should be
    treated as "obviously stale", i.e. differs, so the row falls through to
    a normal overwrite on the next sync instead of getting stuck forever.
    """
    from app.services.operational_model_library_seed import canonical_manifests_differ

    stored = {
        "operational_model_schema_version": 2,
        "scope": "track",
        "package": {"name": "stale-pkg"},
        "track": {
            "entry_types": [],
            "views": [{"key": "v", "name": "V", "view_type": "no/such-view-type"}],
        },
    }
    desired = {
        "operational_model_schema_version": 2,
        "scope": "track",
        "package": {"name": "stale-pkg"},
        "track": {"entry_types": []},
    }
    assert canonical_manifests_differ(stored, desired) is True
