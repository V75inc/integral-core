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
        SimpleNamespace(
            id="removed",
            library_package=True,
            metadata={"slug": "removed"},
            workspace_id=None,
        ),
        SimpleNamespace(
            id="present",
            library_package=True,
            metadata={"slug": "present"},
            workspace_id=None,
        ),
        SimpleNamespace(
            id="private", metadata={"slug": "workspace-owned"}, workspace_id="workspace"
        ),
        SimpleNamespace(id="unmarked", metadata={}, workspace_id=None),
    ]

    async def admitted_seed(model_id):
        return next((row for row in rows[:2] if row.id == model_id), None)

    monkeypatch.setattr(sync, "platform_seed_model", admitted_seed)
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

    async def admitted_seed(model_id):
        return row if model_id == row.id else None

    monkeypatch.setattr(sync, "platform_seed_model", admitted_seed)
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
    authenticated_client,
):
    from app.models.nodes import (
        OPERATIONAL_MODELS_REGISTRY_ID,
        OperationalModel,
        OperationalModels,
    )
    from app.services.app_graph import ensure_catalog_edge
    from app.services.model_authority import (
        platform_seed_model,
        resolve_model_authority,
    )

    registry = await OperationalModels.get(OPERATIONAL_MODELS_REGISTRY_ID)
    spec = LibraryProfileSpec(
        name="Restored",
        version="1",
        description="",
        slug="restored",
        manifest={
            "operational_model_schema_version": 2,
            "scope": "track",
            "package": {"name": "restored"},
            "track": {"entry_types": []},
        },
        bundle_dir=Path("/restored"),
        bundle_fingerprint="fp",
    )
    await seed.upsert_seeded_library_packages(
        catalog_registry=registry,
        specs=[spec],
        now_iso="before",
        ensure_catalog_edge=ensure_catalog_edge,
    )
    rows = await OperationalModel.find({"context.metadata.slug": "restored"})
    assert len(rows) == 1
    original_id = rows[0].id
    await sync.reconcile_removed_seeded_packages(
        removed_slugs=["restored"], now_iso="removed"
    )
    retired = await platform_seed_model(original_id)
    assert retired and retired.library_package is False
    assert await resolve_model_authority(original_id) is None
    await seed.upsert_seeded_library_packages(
        catalog_registry=registry,
        specs=[spec],
        now_iso="now",
        ensure_catalog_edge=ensure_catalog_edge,
    )
    current = await platform_seed_model(original_id)
    assert current and current.library_package is True
    assert current.metadata["seed_status"] == "active"
    assert "seed_removed_at" not in current.metadata
    assert "seed_removed_reason" not in current.metadata
    assert (await resolve_model_authority(original_id)).platform
    rows = await OperationalModel.find({"context.metadata.slug": "restored"})
    assert [row.id for row in rows] == [original_id]


@pytest.mark.asyncio
async def test_persisted_removal_and_restoration_round_trip(
    authenticated_client, monkeypatch
):
    from app.models.nodes import (
        OPERATIONAL_MODELS_REGISTRY_ID,
        OperationalModel,
        OperationalModels,
    )
    from app.services.app_graph import ensure_catalog_edge

    registry = await OperationalModels.get(OPERATIONAL_MODELS_REGISTRY_ID)
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
    edge = ensure_catalog_edge
    monkeypatch.setattr(
        sync, "load_library_operational_models_with_issues", lambda: ([spec], [])
    )
    await sync.sync_library_catalog_from_disk(
        catalog_registry=registry, ensure_catalog_edge=edge
    )
    rows = await OperationalModel.find({"context.metadata.slug": spec.slug})
    original_id = rows[0].id
    monkeypatch.setattr(
        sync, "load_library_operational_models_with_issues", lambda: ([], [])
    )
    report = await sync.sync_library_catalog_from_disk(
        catalog_registry=registry, ensure_catalog_edge=edge
    )
    assert spec.slug in report["removed"]
    removed = await OperationalModel.get(original_id)
    assert removed.library_package is False
    assert removed.metadata["seed_status"] == "inactive"
    monkeypatch.setattr(
        sync, "load_library_operational_models_with_issues", lambda: ([spec], [])
    )
    await sync.sync_library_catalog_from_disk(
        catalog_registry=registry, ensure_catalog_edge=edge
    )
    rows = await OperationalModel.find({"context.metadata.slug": spec.slug})
    assert len(rows) == 1
    assert rows[0].id == original_id
    assert rows[0].library_package is True
    assert rows[0].metadata["seed_status"] == "active"
    repeated = await sync.sync_library_catalog_from_disk(
        catalog_registry=registry,
        ensure_catalog_edge=edge,
        previous_index={spec.slug: {"bundle_fingerprint": "same-fingerprint"}},
    )
    assert repeated["removed"] == []
    assert repeated["updated"] == []
