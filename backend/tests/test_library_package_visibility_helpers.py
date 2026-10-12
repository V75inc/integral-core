"""Library visibility requires current named provenance and execution access."""

import pytest

from app.api.errors import ResourceNotFoundError
from app.models.edges import IS_MEMBER_OF
from app.services.library_model_creation import create_private_library_model
from app.services.library_package_visibility import (
    assert_library_package_installable_in_workspace,
    visible_library_model,
)
from tests.test_library_dependency_authority import manifest
from tests.test_resource_location import graph

pytestmark = pytest.mark.smoke


@pytest.mark.asyncio
async def test_private_package_is_scoped_and_install_returns_current_body():
    owner, workspace, _, _, _ = await graph()
    _, foreign_workspace, _, _, _ = await graph()
    package = await create_private_library_model(
        user_id=owner.id,
        workspace_id=workspace.id,
        name="Private package",
        scope="track",
        manifest=manifest("private-package"),
    )
    assert await visible_library_model(package.id, workspace.id, user_id=owner.id)
    assert (
        await visible_library_model(package.id, foreign_workspace.id, user_id=owner.id)
        is None
    )
    assert await visible_library_model(package.id, None, user_id=owner.id) is None
    assert await visible_library_model(package.id, workspace.id) is None
    stale = package.model_copy()
    package.description = "Current description"
    await package.save()
    admitted = await assert_library_package_installable_in_workspace(
        stale, workspace.id, user_id=owner.id
    )
    assert admitted.description == "Current description"
    with pytest.raises(ResourceNotFoundError):
        await assert_library_package_installable_in_workspace(
            stale, foreign_workspace.id, user_id=owner.id
        )
    package.workspace_id = None
    await package.save()
    assert (
        await visible_library_model(package.id, workspace.id, user_id=owner.id) is None
    )


@pytest.mark.asyncio
async def test_warmed_private_visibility_does_not_survive_membership_removal():
    owner, workspace, _, _, _ = await graph()
    reader, _, _, _, _ = await graph()
    package = await create_private_library_model(
        user_id=owner.id,
        workspace_id=workspace.id,
        name="Private source",
        scope="track",
        manifest=manifest("private-source"),
    )
    membership = await reader.connect(workspace, edge=IS_MEMBER_OF, role="member")
    assert await visible_library_model(package.id, workspace.id, user_id=reader.id)
    await membership.delete()
    assert (
        await visible_library_model(package.id, workspace.id, user_id=reader.id) is None
    )


@pytest.mark.asyncio
async def test_authoring_library_list_admits_current_private_audience():
    from app.services.operational_model_authoring import list_library_operational_models
    from tests.test_library_dependency_authority import seed_public_package

    owner, workspace, _, _, _ = await graph()
    foreign_owner, foreign_workspace, _, _, _ = await graph()
    private = await create_private_library_model(
        user_id=owner.id,
        workspace_id=workspace.id,
        name="Own library",
        scope="track",
        manifest=manifest("own-library"),
    )
    foreign = await create_private_library_model(
        user_id=foreign_owner.id,
        workspace_id=foreign_workspace.id,
        name="Foreign library",
        scope="track",
        manifest=manifest("foreign-library"),
    )
    public = await seed_public_package("visible-platform")
    anonymous = await list_library_operational_models()
    assert {row["id"] for row in anonymous["operational_models"]} == {public.id}
    result = await list_library_operational_models(
        user_id=owner.id, workspace_id=workspace.id
    )
    assert {row["id"] for row in result["operational_models"]} == {
        private.id,
        public.id,
    }
    assert foreign.id not in {row["id"] for row in result["operational_models"]}


@pytest.mark.asyncio
async def test_runtime_package_summary_admits_current_audience_and_manifest_scope():
    from app.services.operational_model_runtime import list_library_packages
    from tests.test_library_dependency_authority import seed_public_package

    owner, workspace, _, _, _ = await graph()
    foreign_owner, foreign_workspace, _, _, _ = await graph()
    own = await create_private_library_model(
        user_id=owner.id,
        workspace_id=workspace.id,
        name="Own summary",
        scope="track",
        manifest=manifest("own-summary"),
    )
    foreign = await create_private_library_model(
        user_id=foreign_owner.id,
        workspace_id=foreign_workspace.id,
        name="Foreign summary",
        scope="track",
        manifest=manifest("foreign-summary"),
    )
    public = await seed_public_package("public-summary")
    summaries = await list_library_packages()
    assert {item.id for item in summaries} == {public.id}
    assert summaries[0].scope == "track"
    summaries = await list_library_packages(user_id=owner.id, workspace_id=workspace.id)
    assert {item.id for item in summaries} == {own.id, public.id}
    assert foreign.id not in {item.id for item in summaries}


@pytest.mark.asyncio
async def test_runtime_package_summary_rejects_removed_workspace_membership():
    from app.api.errors import InsufficientPermissionsError
    from app.services.operational_model_runtime import list_library_packages
    from tests.test_library_dependency_authority import seed_public_package

    _, workspace, _, _, _ = await graph()
    reader, _, _, _, _ = await graph()
    membership = await reader.connect(workspace, edge=IS_MEMBER_OF, role="member")
    public = await seed_public_package("summary-revocation")
    assert [
        item.id
        for item in await list_library_packages(
            user_id=reader.id, workspace_id=workspace.id
        )
    ] == [public.id]
    await membership.delete()
    with pytest.raises(InsufficientPermissionsError):
        await list_library_packages(user_id=reader.id, workspace_id=workspace.id)
    with pytest.raises(InsufficientPermissionsError):
        await list_library_packages(user_id="missing-actor", workspace_id=workspace.id)
