"""Private same-slug library must survive trusted platform seed sync."""

import pytest

pytestmark = pytest.mark.smoke
from app.models.edges import CATALOGS, HAS_LIBRARY_MODEL
from app.models.nodes import (
    OPERATIONAL_MODELS_REGISTRY_ID,
    OperationalModel,
    OperationalModels,
)
from app.services.app_graph import ensure_catalog_edge
from app.services.operational_model_library_seed import upsert_seeded_library_packages
from app.services.operational_model_loader import LibraryProfileSpec
from tests.test_resource_location import graph


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "match", ["slug", "name", "cleared_workspace_cache", "unmarked_global"]
)
async def test_seed_does_not_overwrite_workspace_library(match):
    _, workspace, _, _, _ = await graph()
    registry = await OperationalModels.get(OPERATIONAL_MODELS_REGISTRY_ID)
    private_manifest = {
        "operational_model_schema_version": 2,
        "scope": "track",
        "track": {"entry_types": []},
        "package": {"name": "private-must-survive"},
    }
    private = await OperationalModel.create(
        name="Same display name",
        scope="track",
        library_package=True,
        workspace_id=workspace.id,
        manifest=private_manifest,
        metadata={"slug": "same-slug" if match == "slug" else "private-slug"},
    )
    await registry.connect(private, edge=CATALOGS)
    await workspace.connect(private, edge=HAS_LIBRARY_MODEL)
    if match == "cleared_workspace_cache":
        private.workspace_id = None
        await private.save()
    elif match == "unmarked_global":
        context = await private.get_context()
        for edge in await context.find_edges_between(
            workspace.id, private.id, edge_class=HAS_LIBRARY_MODEL
        ):
            await edge.delete()
        private.workspace_id = None
        await private.save()
    spec = LibraryProfileSpec(
        name="Same display name",
        slug="same-slug",
        version="2",
        description="Platform version",
        manifest={
            "operational_model_schema_version": 2,
            "scope": "track",
            "track": {"entry_types": []},
            "package": {"name": "same-slug"},
        },
        scope="platform",
        library_package=True,
    )
    await upsert_seeded_library_packages(
        catalog_registry=registry,
        specs=[spec],
        now_iso="now",
        ensure_catalog_edge=ensure_catalog_edge,
    )
    context = await private.get_context()
    stored = await context.database.find("node", {"id": private.id}, limit=2)
    assert len(stored) == 1
    assert stored[0]["context"]["manifest"] == private_manifest
    assert stored[0]["context"]["metadata"] == private.metadata
    from app.services.model_authority import (
        platform_seed_model,
        resolve_model_authority,
    )

    candidates = await OperationalModel.find({"context.metadata.slug": "same-slug"})
    seeded = [
        candidate for candidate in candidates if await platform_seed_model(candidate.id)
    ]
    assert len(seeded) == 1 and seeded[0].id != private.id
    assert (await resolve_model_authority(seeded[0].id)).platform
    await upsert_seeded_library_packages(
        catalog_registry=registry,
        specs=[spec],
        now_iso="later",
        ensure_catalog_edge=ensure_catalog_edge,
    )
    again = await platform_seed_model(seeded[0].id)
    assert again and again.id == seeded[0].id
    assert (await context.database.find("node", {"id": private.id}, limit=2))[0][
        "context"
    ]["manifest"] == private_manifest


@pytest.mark.asyncio
async def test_platform_packages_with_same_display_name_keep_separate_seed_identity():
    from dataclasses import replace

    from app.services.model_authority import platform_seed_model

    await graph()
    registry = await OperationalModels.get(OPERATIONAL_MODELS_REGISTRY_ID)
    spec = LibraryProfileSpec(
        name="Shared platform display name",
        slug="first-package",
        version="1",
        description="First package",
        scope="platform",
        library_package=True,
        manifest={
            "operational_model_schema_version": 2,
            "scope": "track",
            "track": {"entry_types": []},
            "package": {"name": "first-package"},
        },
    )
    await upsert_seeded_library_packages(
        catalog_registry=registry,
        specs=[spec],
        now_iso="first",
        ensure_catalog_edge=ensure_catalog_edge,
    )
    first = (await OperationalModel.find({"context.metadata.slug": "first-package"}))[0]
    second_spec = replace(
        spec,
        slug="second-package",
        description="Second package",
        manifest={**spec.manifest, "package": {"name": "second-package"}},
    )
    await upsert_seeded_library_packages(
        catalog_registry=registry,
        specs=[second_spec],
        now_iso="second",
        ensure_catalog_edge=ensure_catalog_edge,
    )
    preserved = await platform_seed_model(first.id)
    assert preserved and preserved.metadata["slug"] == "first-package"
    assert preserved.manifest == spec.manifest
    second = (await OperationalModel.find({"context.metadata.slug": "second-package"}))[
        0
    ]
    assert second.id != first.id
    await upsert_seeded_library_packages(
        catalog_registry=registry,
        specs=[spec, second_spec],
        now_iso="again",
        ensure_catalog_edge=ensure_catalog_edge,
    )
    assert (await platform_seed_model(first.id)).manifest == spec.manifest
    assert (await platform_seed_model(second.id)).manifest == second_spec.manifest


@pytest.mark.asyncio
async def test_failed_publication_creation_leaves_no_catalog_or_model():
    await graph()
    registry = await OperationalModels.get(OPERATIONAL_MODELS_REGISTRY_ID)
    spec = LibraryProfileSpec(
        name="Failed publication",
        slug="failed-publication",
        version="1",
        description="",
        scope="platform",
        library_package=True,
        manifest={
            "operational_model_schema_version": 2,
            "scope": "track",
            "track": {"entry_types": []},
            "package": {"name": "failed-publication"},
        },
    )

    async def failed_catalog(registry, model):
        await ensure_catalog_edge(registry, model)
        raise RuntimeError("Injected failure after catalog attachment")

    with pytest.raises(RuntimeError, match="Injected failure"):
        await upsert_seeded_library_packages(
            catalog_registry=registry,
            specs=[spec],
            now_iso="now",
            ensure_catalog_edge=failed_catalog,
        )
    context = await registry.get_context()
    assert (
        await context.database.find(
            "node",
            {
                "entity": OperationalModel._entity_name(),
                "context.metadata.slug": "failed-publication",
            },
            limit=2,
        )
        == []
    )
    assert await OperationalModel.find({"context.name": "Failed publication"}) == []
