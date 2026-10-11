"""First private library publication preserves provenance and exact authority."""

import pytest

from app.models.edges import IS_MEMBER_OF
from app.services import operational_model_authoring as author
from app.services.model_authority import get_current_model, resolve_model_authority
from tests.test_model_api_authority import request_for
from tests.test_resource_location import graph

pytestmark = pytest.mark.smoke


async def draft_fixture():
    owner, workspace, _, _, _ = await graph()
    created = await author.create_empty_library_draft(
        owner.id, workspace.id, "First private package"
    )
    assert "error" not in created
    return owner, workspace, created["draft_id"]


@pytest.mark.asyncio
async def test_initial_draft_patch_review_and_publish_preserve_private_identity():
    owner, workspace, model_id = await draft_fixture()
    patched = await author.apply_patch_to_draft(
        user_id=owner.id,
        draft_id=model_id,
        operations=[{"op": "add_entry_type", "spec": {"key": "note", "name": "Note"}}],
    )
    assert "error" not in patched
    review = await author.diff_draft(user_id=owner.id, draft_id=model_id)
    assert review["initial_publication"] and review["entry_impact"] == []
    result = await author.publish_draft_for_agent(
        user_id=owner.id,
        draft_id=model_id,
        expected_review_fingerprint=review["review_fingerprint"],
    )
    assert result["initial_publication"] and result["published_id"] == model_id
    model = await get_current_model(model_id)
    assert model.status == "published" and model.workspace_id == workspace.id
    authority = await resolve_model_authority(model_id)
    assert authority.library_model_id == model_id and not authority.platform
    assert not authority.unpublished
    assert model.manifest["track"]["entry_types"][0]["key"] == "note"


@pytest.mark.asyncio
async def test_initial_draft_admin_creation_flags_do_not_authorize_patch_or_publication():
    owner, workspace, model_id = await draft_fixture()
    admin, _, _, _, _ = await graph()
    await admin.connect(
        workspace, edge=IS_MEMBER_OF, role="admin", can_create_apps=True
    )
    before = (await get_current_model(model_id)).model_dump()
    assert (
        await author.apply_patch_to_draft(
            user_id=admin.id, draft_id=model_id, operations=[]
        )
    )["error"] == "forbidden"
    assert (await author.publish_draft_for_agent(user_id=admin.id, draft_id=model_id))[
        "error"
    ] == "forbidden"
    assert (await get_current_model(model_id)).model_dump() == before


@pytest.mark.asyncio
async def test_initial_draft_stale_review_does_not_publish():
    owner, _, model_id = await draft_fixture()
    review = await author.diff_draft(user_id=owner.id, draft_id=model_id)
    await author.apply_patch_to_draft(
        user_id=owner.id,
        draft_id=model_id,
        operations=[
            {"op": "add_entry_type", "spec": {"key": "changed", "name": "Changed"}}
        ],
    )
    result = await author.publish_draft_for_agent(
        user_id=owner.id,
        draft_id=model_id,
        expected_review_fingerprint=review["review_fingerprint"],
    )
    assert result["error"] == "bad_request"
    assert (await get_current_model(model_id)).status == "draft"


@pytest.mark.asyncio
async def test_http_initial_publication_binds_execution_workspace():
    from app.api.errors import InsufficientPermissionsError
    from app.api.operational_models import publish_operational_model_draft

    owner, workspace, model_id = await draft_fixture()
    other_owner, other_workspace, _, _, _ = await graph()
    with pytest.raises(InsufficientPermissionsError):
        await publish_operational_model_draft(
            request_for(other_owner.id, other_workspace.id), model_id
        )
    assert (await get_current_model(model_id)).status == "draft"
    result = await publish_operational_model_draft(
        request_for(owner.id, workspace.id), model_id
    )
    assert result["initial_publication"] and result["published_id"] == model_id
