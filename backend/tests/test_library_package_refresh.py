"""Update from library sees a package's current files, not only the boot-time copy."""

from __future__ import annotations

import pytest

from app.models.nodes import (
    OPERATIONAL_MODELS_REGISTRY_ID,
    OperationalModel,
    OperationalModels,
)
from app.services import operational_model_library_sync as sync
from app.utils.time import utc_now_iso

pytestmark = pytest.mark.asyncio


def _manifest(slug: str, description: str) -> dict:
    return {
        "operational_model_schema_version": 2,
        "scope": "app",
        "package": {
            "name": slug.title(),
            "slug": slug,
            "key": slug,
            "version": "1.0.0",
            "description": description,
        },
        "app": {"description": description, "tracks": []},
    }


async def _seed(slug: str, description: str) -> OperationalModel:
    from app.services.app_graph import ensure_catalog_edge
    from app.services.operational_model_library_seed import (
        upsert_seeded_library_packages,
    )
    from app.services.operational_model_loader import LibraryProfileSpec

    manifest = _manifest(slug, description)
    await upsert_seeded_library_packages(
        catalog_registry=await OperationalModels.get(OPERATIONAL_MODELS_REGISTRY_ID),
        specs=[
            LibraryProfileSpec(
                name=slug.title(),
                slug=slug,
                version="1.0.0",
                description=description,
                manifest=manifest,
                scope="platform",
                library_package=True,
            )
        ],
        now_iso=utc_now_iso(),
        ensure_catalog_edge=ensure_catalog_edge,
    )
    return (await OperationalModel.find({"context.metadata.slug": slug}))[0]


def _disk(monkeypatch, slug: str, description: str) -> None:
    from app.services.operational_model_loader import LibraryProfileSpec

    spec = LibraryProfileSpec(
        name=slug.title(),
        slug=slug,
        version="1.0.0",
        description=description,
        manifest=_manifest(slug, description),
        scope="platform",
        library_package=True,
    )
    monkeypatch.setattr(
        sync, "load_library_operational_models_with_issues_cached", lambda: ([spec], [])
    )


async def test_changed_package_files_refresh_the_catalog_copy_in_place(
    test_user, monkeypatch
):
    before = await _seed("refreshme", "old text")
    _disk(monkeypatch, "refreshme", "new text")
    assert await sync.refresh_library_package_from_disk("refreshme") is True
    after = await OperationalModel.get(before.id)
    assert after.id == before.id  # same row, so installed Apps keep pointing at it
    assert after.manifest["package"]["description"] == "new text"


async def test_unchanged_package_files_change_nothing(test_user, monkeypatch):
    await _seed("steady", "same")
    _disk(monkeypatch, "steady", "same")
    assert await sync.refresh_library_package_from_disk("steady") is False


async def test_package_missing_from_disk_is_left_as_it_was(test_user, monkeypatch):
    before = await _seed("gone", "kept")
    monkeypatch.setattr(
        sync, "load_library_operational_models_with_issues_cached", lambda: ([], [])
    )
    assert await sync.refresh_library_package_from_disk("gone") is False
    assert (await OperationalModel.get(before.id)).manifest["package"][
        "description"
    ] == "kept"


async def test_blank_slug_is_ignored(test_user):
    assert await sync.refresh_library_package_from_disk("  ") is False
