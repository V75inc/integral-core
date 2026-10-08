"""Generic catalog removal across restart and restoration identity regressions."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import operational_model_library_seed as seed
from app.services import operational_model_library_sync as sync
from app.services.operational_model_loader import LibraryProfileSpec


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "previous", [None, {}, {"present": {"bundle_fingerprint": "fp"}}]
)
async def test_removed_persisted_seed_is_reconciled_without_process_history(
    monkeypatch, previous
):
    present = LibraryProfileSpec(
        name="Present",
        version="1",
        description="",
        manifest={},
        slug="present",
        bundle_fingerprint="fp",
    )
    rows = [
        SimpleNamespace(metadata={"slug": "removed"}, workspace_id=None),
        SimpleNamespace(metadata={"slug": "present"}, workspace_id=None),
        SimpleNamespace(metadata={"slug": "workspace-owned"}, workspace_id="workspace"),
        SimpleNamespace(metadata={}, workspace_id=None),
    ]
    find = AsyncMock(return_value=rows)
    monkeypatch.setattr(sync.OperationalModel, "find", find)
    monkeypatch.setattr(
        sync, "load_library_operational_models_with_issues", lambda: ([present], [])
    )
    monkeypatch.setattr(sync, "upsert_seeded_library_packages", AsyncMock())
    reconcile = AsyncMock(return_value=[{"slug": "removed", "status": "deactivated"}])
    monkeypatch.setattr(sync, "reconcile_removed_seeded_packages", reconcile)
    report = await sync.sync_library_catalog_from_disk(
        catalog_registry=object(),
        ensure_catalog_edge=AsyncMock(),
        previous_index=previous,
        now_iso="now",
    )
    assert report["removed"] == ["removed"]
    find.assert_awaited_once_with(
        {"context.library_package": True, "context.metadata.seed_status": "active"}
    )
    reconcile.assert_awaited_once_with(removed_slugs=["removed"], now_iso="now")


@pytest.mark.asyncio
async def test_removal_only_deactivates_platform_seeds_and_keeps_references(
    monkeypatch,
):
    row = SimpleNamespace(
        id="seed",
        metadata={"slug": "removed", "seed_status": "active"},
        workspace_id=None,
        library_package=True,
        save=AsyncMock(),
    )
    workspace = SimpleNamespace(
        id="workspace-model", workspace_id="workspace", save=AsyncMock()
    )
    find = AsyncMock(return_value=[row, workspace])
    monkeypatch.setattr(sync.OperationalModel, "find", find)
    monkeypatch.setattr(
        sync, "_is_library_operational_model_referenced", AsyncMock(return_value=True)
    )
    report = await sync.reconcile_removed_seeded_packages(
        removed_slugs=["removed"], now_iso="now"
    )
    assert row.library_package is False
    assert row.metadata["seed_status"] == "inactive"
    assert row.metadata["seed_removed_reason"] == "bundle_missing_referenced"
    assert report[0]["referenced"] is True
    row.save.assert_awaited_once()
    workspace.save.assert_not_awaited()
    assert find.await_args.args[0]["context.metadata.seed_status"] == "active"


@pytest.mark.asyncio
async def test_reintroduced_seed_reuses_identity_even_when_fingerprint_unchanged(
    monkeypatch,
):
    manifest = {
        "operational_model_schema_version": 2,
        "scope": "track",
        "package": {"name": "restored"},
        "track": {"entry_types": []},
    }
    row = SimpleNamespace(
        id="original-seed",
        workspace_id=None,
        name="Restored",
        manifest=manifest,
        library_package=False,
        metadata={
            "slug": "restored",
            "seed_status": "inactive",
            "bundle_fingerprint": "fp",
            "seed_removed_at": "before",
            "seed_removed_reason": "bundle_missing",
        },
        save=AsyncMock(),
    )
    workspace = SimpleNamespace(workspace_id="workspace")
    find = AsyncMock(side_effect=[[], [workspace, row]])
    create = AsyncMock()
    monkeypatch.setattr(seed.OperationalModel, "find", find)
    monkeypatch.setattr(seed.OperationalModel, "create", create)
    edge = AsyncMock()
    spec = LibraryProfileSpec(
        name="Restored",
        version="1",
        description="",
        slug="restored",
        manifest=manifest,
        bundle_dir=Path("/restored"),
        bundle_fingerprint="fp",
    )
    await seed.upsert_seeded_library_packages(
        catalog_registry="registry",
        specs=[spec],
        now_iso="now",
        ensure_catalog_edge=edge,
    )
    create.assert_not_awaited()
    row.save.assert_awaited_once()
    assert row.id == "original-seed"
    assert row.library_package is True
    assert row.metadata["seed_status"] == "active"
    assert "seed_removed_at" not in row.metadata
    assert "seed_removed_reason" not in row.metadata
    edge.assert_awaited_once_with("registry", row)


@pytest.mark.asyncio
async def test_persisted_removal_and_restoration_round_trip(
    authenticated_client, monkeypatch
):
    from app.models.nodes import OperationalModel

    spec = LibraryProfileSpec(
        name="Catalog round trip",
        version="1",
        description="",
        slug="catalog-round-trip",
        manifest={
            "operational_model_schema_version": 2,
            "scope": "track",
            "package": {"name": "catalog-round-trip"},
            "track": {"entry_types": []},
        },
        bundle_dir=Path("/catalog-round-trip"),
        bundle_fingerprint="same-fingerprint",
    )
    edge = AsyncMock()
    monkeypatch.setattr(
        sync, "load_library_operational_models_with_issues", lambda: ([spec], [])
    )
    await sync.sync_library_catalog_from_disk(
        catalog_registry=object(), ensure_catalog_edge=edge
    )
    rows = await OperationalModel.find({"context.metadata.slug": spec.slug})
    original_id = rows[0].id
    monkeypatch.setattr(
        sync, "load_library_operational_models_with_issues", lambda: ([], [])
    )
    report = await sync.sync_library_catalog_from_disk(
        catalog_registry=object(), ensure_catalog_edge=edge
    )
    assert spec.slug in report["removed"]
    removed = await OperationalModel.get(original_id)
    assert removed.library_package is False
    assert removed.metadata["seed_status"] == "inactive"
    monkeypatch.setattr(
        sync, "load_library_operational_models_with_issues", lambda: ([spec], [])
    )
    await sync.sync_library_catalog_from_disk(
        catalog_registry=object(), ensure_catalog_edge=edge
    )
    rows = await OperationalModel.find({"context.metadata.slug": spec.slug})
    assert len(rows) == 1
    assert rows[0].id == original_id
    assert rows[0].library_package is True
    assert rows[0].metadata["seed_status"] == "active"
    repeated = await sync.sync_library_catalog_from_disk(
        catalog_registry=object(),
        ensure_catalog_edge=edge,
        previous_index={spec.slug: {"bundle_fingerprint": "same-fingerprint"}},
    )
    assert repeated["removed"] == []
    assert repeated["updated"] == []
