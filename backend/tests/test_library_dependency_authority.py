"""Dependency admission uses named source scope before any target write."""

import pytest

from app.api.errors import BadRequestError, ResourceNotFoundError
from app.models.edges import IS_MEMBER_OF
from app.services.library_dependencies import admitted_library_dependency_chain
from app.services.library_model_creation import create_private_library_model
from app.services.model_authority import get_current_model
from app.services.operational_model_merge import (
    merge_library_manifest_into_operational_model,
)
from tests.test_resource_location import graph

pytestmark = pytest.mark.smoke


def manifest(name, dependencies=()):
    return {
        "operational_model_schema_version": 2,
        "scope": "track",
        "package": {
            "name": name,
            "dependencies": [
                {"id": dependency, "version": "1.0.0"} for dependency in dependencies
            ],
        },
        "track": {"entry_types": []},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("lookup", ["point", "alias"])
async def test_private_dependency_chain_has_stable_order_and_current_membership(lookup):
    owner, workspace, _, _, _ = await graph()
    dependency = await create_private_library_model(
        user_id=owner.id,
        workspace_id=workspace.id,
        name="Dependency",
        scope="track",
        manifest=manifest("base-package"),
    )
    root = await create_private_library_model(
        user_id=owner.id,
        workspace_id=workspace.id,
        name="Root",
        scope="track",
        manifest=manifest(
            "root-package", [dependency.id if lookup == "point" else "BASE-PACKAGE"]
        ),
    )
    chain = await admitted_library_dependency_chain(
        root.id, workspace.id, user_id=owner.id
    )
    assert [model.id for model in chain] == [dependency.id, root.id]
    reader, _, _, _, _ = await graph()
    membership = await reader.connect(workspace, edge=IS_MEMBER_OF, role="member")
    assert (
        len(
            await admitted_library_dependency_chain(
                root.id, workspace.id, user_id=reader.id
            )
        )
        == 2
    )
    await membership.delete()
    with pytest.raises(ResourceNotFoundError):
        await admitted_library_dependency_chain(
            root.id, workspace.id, user_id=reader.id
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["foreign", "cleared_cache", "draft"])
async def test_foreign_or_unpublished_dependency_rejects_before_merge_write(corruption):
    owner, workspace, _, track, _ = await graph()
    foreign_owner, foreign_workspace, _, _, _ = await graph()
    dependency = await create_private_library_model(
        user_id=foreign_owner.id,
        workspace_id=foreign_workspace.id,
        name="Private foreign dependency",
        scope="track",
        manifest=manifest("foreign-base"),
        status="draft" if corruption == "draft" else "published",
    )
    if corruption == "cleared_cache":
        dependency.workspace_id = None
        await dependency.save()
    root = await create_private_library_model(
        user_id=owner.id,
        workspace_id=workspace.id,
        name="Owned root",
        scope="track",
        manifest=manifest("root-with-foreign", [dependency.id]),
    )
    target = await get_current_model(track.attached_operational_model_id)
    before = target.model_dump()
    with pytest.raises(ResourceNotFoundError):
        await merge_library_manifest_into_operational_model(
            root, target, track, user_id=owner.id
        )
    assert (await get_current_model(target.id)).model_dump() == before


@pytest.mark.asyncio
async def test_duplicate_visible_dependency_alias_and_cycle_are_withheld():
    owner, workspace, _, _, _ = await graph()
    first = await create_private_library_model(
        user_id=owner.id,
        workspace_id=workspace.id,
        name="First alias",
        scope="track",
        manifest=manifest("duplicate-alias"),
    )
    await create_private_library_model(
        user_id=owner.id,
        workspace_id=workspace.id,
        name="Second alias",
        scope="track",
        manifest=manifest("duplicate-alias"),
    )
    root = await create_private_library_model(
        user_id=owner.id,
        workspace_id=workspace.id,
        name="Ambiguous root",
        scope="track",
        manifest=manifest("root-ambiguous", ["duplicate-alias"]),
    )
    with pytest.raises(BadRequestError, match="Ambiguous"):
        await admitted_library_dependency_chain(root.id, workspace.id, user_id=owner.id)
    root.manifest = manifest("root-cycle", [first.id])
    await root.save()
    first.manifest = manifest("first-cycle", [root.id])
    await first.save()
    with pytest.raises(BadRequestError, match="Circular"):
        await admitted_library_dependency_chain(root.id, workspace.id, user_id=owner.id)


@pytest.mark.asyncio
async def test_actorless_internal_call_cannot_consume_private_dependency():
    owner, workspace, _, _, _ = await graph()
    model = await create_private_library_model(
        user_id=owner.id,
        workspace_id=workspace.id,
        name="Private source",
        scope="track",
        manifest=manifest("private-source"),
    )
    with pytest.raises(ResourceNotFoundError):
        await admitted_library_dependency_chain(model.id, workspace.id)


async def seed_public_package(name, dependencies=()):
    from app.models.nodes import (
        OPERATIONAL_MODELS_REGISTRY_ID,
        OperationalModel,
        OperationalModels,
    )
    from app.services.app_graph import ensure_catalog_edge
    from app.services.operational_model_library_seed import (
        upsert_seeded_library_packages,
    )
    from app.services.operational_model_loader import LibraryProfileSpec

    registry = await OperationalModels.get(OPERATIONAL_MODELS_REGISTRY_ID)
    await upsert_seeded_library_packages(
        catalog_registry=registry,
        specs=[
            LibraryProfileSpec(
                name=name,
                slug=name,
                version="1.0.0",
                description="Public source",
                scope="platform",
                library_package=True,
                manifest=manifest(name, dependencies),
            )
        ],
        now_iso="now",
        ensure_catalog_edge=ensure_catalog_edge,
    )
    return (await OperationalModel.find({"context.metadata.slug": name}))[0]


@pytest.mark.asyncio
async def test_public_source_private_dependency_needs_actor_and_current_workspace():
    owner, workspace, _, _, _ = await graph()
    private = await create_private_library_model(
        user_id=owner.id,
        workspace_id=workspace.id,
        name="Private dependency",
        scope="track",
        manifest=manifest("private-dependency"),
    )
    public = await seed_public_package("public-root", [private.id])
    assert [
        item.id
        for item in await admitted_library_dependency_chain(
            public.id, workspace.id, user_id=owner.id
        )
    ] == [private.id, public.id]
    with pytest.raises(ResourceNotFoundError):
        await admitted_library_dependency_chain(public.id, workspace.id)
    reader, _, _, _, _ = await graph()
    membership = await reader.connect(workspace, edge=IS_MEMBER_OF, role="member")
    assert await admitted_library_dependency_chain(
        public.id, workspace.id, user_id=reader.id
    )
    await membership.delete()
    with pytest.raises(ResourceNotFoundError):
        await admitted_library_dependency_chain(
            public.id, workspace.id, user_id=reader.id
        )


@pytest.mark.asyncio
async def test_inaccessible_existing_id_cannot_substitute_visible_alias():
    owner, workspace, _, _, _ = await graph()
    foreign_owner, foreign_workspace, _, _, _ = await graph()
    private = await create_private_library_model(
        user_id=foreign_owner.id,
        workspace_id=foreign_workspace.id,
        name="Inaccessible exact ID",
        scope="track",
        manifest=manifest("foreign-exact"),
    )
    await seed_public_package(private.id)
    root = await seed_public_package("public-point-root", [private.id])
    with pytest.raises(ResourceNotFoundError):
        await admitted_library_dependency_chain(root.id, workspace.id, user_id=owner.id)


@pytest.mark.asyncio
async def test_public_only_chain_is_available_to_internal_reader():
    _, workspace, _, _, _ = await graph()
    base = await seed_public_package("public-base")
    root = await seed_public_package("public-chain", [base.id])
    assert [
        item.id
        for item in await admitted_library_dependency_chain(root.id, workspace.id)
    ] == [base.id, root.id]


@pytest.mark.asyncio
async def test_batch_preflight_withholds_foreign_source_before_any_install(monkeypatch):
    from unittest.mock import AsyncMock

    from app.services.app_batch_install import batch_install

    owner, workspace, _, _, _ = await graph()
    foreign_owner, foreign_workspace, _, _, _ = await graph()
    foreign = await create_private_library_model(
        user_id=foreign_owner.id,
        workspace_id=foreign_workspace.id,
        name="Foreign batch source",
        scope="track",
        manifest=manifest("foreign-batch"),
    )
    public = await seed_public_package("batch-public")
    install = AsyncMock()
    monkeypatch.setattr("app.services.app_batch_install.install_app", install)
    with pytest.raises(ResourceNotFoundError):
        await batch_install(
            workspace_id=workspace.id,
            actor_id=owner.id,
            items=[{"library_cp_id": public.id}, {"library_cp_id": foreign.id}],
        )
    install.assert_not_awaited()
