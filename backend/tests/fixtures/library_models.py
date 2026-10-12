"""Rooted public library fixtures use the actual trusted seed producer."""

from app.models.nodes import (
    OPERATIONAL_MODELS_REGISTRY_ID,
    OperationalModel,
    OperationalModels,
)
from app.services.app_graph import ensure_catalog_edge, ensure_integral_app_graph
from app.services.model_authority import platform_seed_model
from app.services.operational_model_library_seed import upsert_seeded_library_packages
from app.services.operational_model_loader import LibraryProfileSpec
from app.utils.time import utc_now_iso


async def publish_library_spec(spec: LibraryProfileSpec) -> OperationalModel:
    """Publish one valid fixture spec; catalog membership alone is not enough."""
    assert spec.slug and spec.library_package
    await ensure_integral_app_graph(include_library=False)
    registry = await OperationalModels.get(OPERATIONAL_MODELS_REGISTRY_ID)
    assert (
        registry is not None
    ), "Fixture graph must have its canonical library registry"
    await upsert_seeded_library_packages(
        catalog_registry=registry,
        specs=[spec],
        now_iso=utc_now_iso(),
        ensure_catalog_edge=ensure_catalog_edge,
    )
    rows = await OperationalModel.find({"context.metadata.slug": spec.slug})
    admitted = [
        current
        for row in rows
        if (current := await platform_seed_model(row.id)) is not None
    ]
    assert len(admitted) == 1
    return admitted[0]


async def publish_library_manifest(
    *, manifest: dict, name: str, version: str = "1.0.0", description: str = ""
) -> OperationalModel:
    """A synthetic positive library fixture still has named publication."""
    package = manifest.get("package") or {}
    slug = (
        str(package.get("slug") or package.get("key") or package.get("name") or name)
        .lower()
        .replace(" ", "-")
    )
    return await publish_library_spec(
        LibraryProfileSpec(
            name=name,
            slug=slug,
            version=version,
            description=description,
            manifest=manifest,
            scope=str(manifest.get("scope") or "app"),
            library_package=True,
            package_class=str(package.get("class") or "community_app"),
        )
    )
