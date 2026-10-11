"""Model producer and handler admission on actual rooted stores."""

import pytest
from starlette.requests import Request

from app.api.errors import ResourceNotFoundError
from app.api.operational_models import (
    _resolve_cp_edit_permission,
    get_library_operational_model,
)
from app.models.edges import (
    COLLABORATES_ON,
    CONTAINS,
    HAS_LIBRARY_MODEL,
    HAS_OPERATIONAL_MODEL,
    IS_MEMBER_OF,
)
from app.models.nodes import Track
from app.services.app_graph import catalog_track
from app.services.library_model_creation import create_private_library_model
from app.services.model_authority import get_current_model, resolve_model_authority
from app.services.operational_model_authoring import _user_can_edit_cp
from tests.test_resource_location import graph

pytestmark = pytest.mark.smoke


def request_for(user_id, workspace_id):
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/operational-models",
            "headers": [(b"x-integral-scope", f"ws:{workspace_id}".encode())],
        }
    )
    request.state.user = {"id": user_id}
    return request


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["published", "draft"])
async def test_private_creation_has_named_ownership_and_exact_configuration(status):
    owner, workspace, _, _, _ = await graph()
    member, _, _, _, _ = await graph()
    await member.connect(
        workspace, edge=IS_MEMBER_OF, role="admin", can_create_apps=True
    )
    model = await create_private_library_model(
        user_id=owner.id,
        workspace_id=workspace.id,
        name="Private created model",
        scope="track",
        status=status,
    )
    authority = await resolve_model_authority(model.id)
    assert (
        authority and authority.workspace_id == workspace.id and not authority.platform
    )
    assert authority.unpublished is (status == "draft")
    context = await model.get_context()
    assert (
        len(
            await context.find_edges_between(
                workspace.id, model.id, edge_class=HAS_LIBRARY_MODEL
            )
        )
        == 1
    )
    assert await _resolve_cp_edit_permission(
        user_id=owner.id, cp=model, request=request_for(owner.id, workspace.id)
    )
    assert not await _resolve_cp_edit_permission(
        user_id=member.id, cp=model, request=request_for(member.id, workspace.id)
    )


@pytest.mark.asyncio
async def test_both_edit_helpers_require_all_actual_shared_parents():
    caller, _, _, _, _ = await graph()
    _, workspace, app, track, _ = await graph()
    await caller.connect(workspace, edge=IS_MEMBER_OF, role="guest")
    await caller.connect(track, edge=COLLABORATES_ON, role="admin")
    model = await get_current_model(track.attached_operational_model_id)
    request = request_for(caller.id, workspace.id)
    assert await _resolve_cp_edit_permission(
        user_id=caller.id, cp=model, request=request
    )
    assert await _user_can_edit_cp(user_id=caller.id, cp=model)
    sibling = await Track.create(
        title="Unmanaged affected sibling",
        workspace_id=workspace.id,
        attached_operational_model_id=model.id,
    )
    await sibling.connect(model, edge=HAS_OPERATIONAL_MODEL)
    await catalog_track(sibling)
    await app.connect(sibling, edge=CONTAINS)
    assert not await _resolve_cp_edit_permission(
        user_id=caller.id, cp=model, request=request
    )
    assert not await _user_can_edit_cp(user_id=caller.id, cp=model)


@pytest.mark.asyncio
async def test_library_point_handler_uses_current_body_and_designated_audience():
    owner, workspace, _, _, _ = await graph()
    guest, _, _, _, _ = await graph()
    membership = await guest.connect(workspace, edge=IS_MEMBER_OF, role="guest")
    model = await create_private_library_model(
        user_id=owner.id,
        workspace_id=workspace.id,
        name="Before warmed capture",
        scope="track",
    )
    request = request_for(guest.id, workspace.id)
    with pytest.raises(ResourceNotFoundError):
        await get_library_operational_model(
            request=request, operational_model_id=model.id
        )
    await guest.connect(model, edge=COLLABORATES_ON, role="viewer")
    context = await model.get_context()
    await context.database.find_one_and_update(
        "node", {"id": model.id}, {"$set": {"context.name": "Fresh primary body"}}
    )
    result = await get_library_operational_model(
        request=request, operational_model_id=model.id
    )
    assert result["operational_model"]["name"] == "Fresh primary body"
    await membership.delete()
    with pytest.raises(ResourceNotFoundError):
        await get_library_operational_model(
            request=request, operational_model_id=model.id
        )


@pytest.mark.asyncio
async def test_describe_model_does_not_expose_pending_draft_to_published_viewer():
    from app.services.operational_model_atomic_swap import fork_draft
    from app.services.operational_model_authoring import describe_operational_model

    owner, workspace, _, track, _ = await graph()
    viewer, _, _, _, _ = await graph()
    await viewer.connect(workspace, edge=IS_MEMBER_OF, role="guest")
    await viewer.connect(track, edge=COLLABORATES_ON, role="viewer")
    published = await get_current_model(track.attached_operational_model_id)
    draft = await fork_draft(published=published, actor_id=owner.id)
    draft.description = "Pending private review content"
    await draft.save()
    result = await describe_operational_model(
        user_id=viewer.id, track_id=track.id, detail="full"
    )
    assert result["published"]["id"] == published.id
    assert result["draft"] is None and result["has_draft"] is False
    result = await describe_operational_model(
        user_id=owner.id, track_id=track.id, detail="full"
    )
    assert result["draft"]["id"] == draft.id and result["has_draft"] is True
    await viewer.connect(draft, edge=COLLABORATES_ON, role="viewer")
    result = await describe_operational_model(
        user_id=viewer.id, track_id=track.id, detail="full"
    )
    assert result["draft"]["description"] == "Pending private review content"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["patch", "diff", "discard"])
async def test_corrupt_draft_parent_claim_cannot_authorize_service_operation(operation):
    from app.services import operational_model_authoring as author
    from app.services.operational_model_atomic_swap import fork_draft

    owner, _, _, track, _ = await graph()
    other_owner, other_workspace, _, other_track, _ = await graph()
    await owner.connect(other_workspace, edge=IS_MEMBER_OF, role="guest")
    await owner.connect(other_track, edge=COLLABORATES_ON, role="admin")
    published = await get_current_model(track.attached_operational_model_id)
    draft = await fork_draft(published=published, actor_id=owner.id)
    draft.draft_of_id = other_track.attached_operational_model_id
    await draft.save()
    if operation == "patch":
        result = await author.apply_patch_to_draft(
            user_id=owner.id, draft_id=draft.id, operations=[]
        )
    elif operation == "diff":
        result = await author.diff_draft(user_id=owner.id, draft_id=draft.id)
    else:
        result = await author.discard_draft_for_agent(
            user_id=owner.id, draft_id=draft.id
        )
    assert result["error"] == "forbidden"
    assert await get_current_model(draft.id) is not None


@pytest.mark.asyncio
async def test_ambiguous_named_pending_drafts_never_fork_a_replacement():
    from app.exceptions import AuthorizationUnavailableError
    from app.models.edges import HAS_DRAFT_PROFILE
    from app.models.nodes import OperationalModel
    from app.services.operational_model_atomic_swap import fork_draft
    from app.services.operational_model_authoring import get_or_create_draft

    owner, _, _, track, _ = await graph()
    published = await get_current_model(track.attached_operational_model_id)
    first = await fork_draft(published=published, actor_id=owner.id)
    second = await OperationalModel.create(
        name="Second pending draft",
        scope="track",
        status="draft",
        draft_of_id=published.id,
    )
    await published.connect(second, edge=HAS_DRAFT_PROFILE)
    with pytest.raises(AuthorizationUnavailableError):
        await get_or_create_draft(user_id=owner.id, operational_model_id=published.id)
    context = await published.get_context()
    rows = await context.database.find(
        "node",
        {
            "entity": OperationalModel._entity_name(),
            "context.draft_of_id": published.id,
            "context.status": "draft",
        },
        limit=3,
    )
    assert {row["id"] for row in rows} == {first.id, second.id}


@pytest.mark.asyncio
async def test_http_orphan_discard_cannot_delete_an_unadmitted_draft():
    from app.api.operational_models import discard_operational_model_draft
    from app.models.edges import HAS_DRAFT_PROFILE
    from app.services.operational_model_atomic_swap import fork_draft

    owner, workspace, _, track, _ = await graph()
    published = await get_current_model(track.attached_operational_model_id)
    draft = await fork_draft(published=published, actor_id=owner.id)
    request = request_for(owner.id, workspace.id)
    assert await _resolve_cp_edit_permission(
        user_id=owner.id, cp=draft, request=request
    )
    context = await draft.get_context()
    for edge in await context.find_edges_between(
        published.id, draft.id, edge_class=HAS_DRAFT_PROFILE
    ):
        await edge.delete()
    draft.draft_of_id = None
    await draft.save()
    with pytest.raises(ResourceNotFoundError):
        await discard_operational_model_draft(
            request=request, operational_model_id=draft.id
        )
    assert await get_current_model(draft.id) is not None


@pytest.mark.asyncio
async def test_diff_admits_reference_separately_and_rechecks_revocation():
    from app.api.operational_models import diff_operational_model
    from app.models.edges import OWNS

    owner, workspace, app, candidate_track, _ = await graph()
    caller, _, _, _, _ = await graph()
    await caller.connect(workspace, edge=IS_MEMBER_OF, role="guest")
    await caller.connect(candidate_track, edge=COLLABORATES_ON, role="admin")
    reference_track = await Track.create(
        title="Private reference Track", workspace_id=workspace.id
    )
    await catalog_track(reference_track)
    await app.connect(reference_track, edge=CONTAINS)
    await owner.connect(reference_track, edge=OWNS)
    request = request_for(caller.id, workspace.id)
    with pytest.raises(ResourceNotFoundError):
        await diff_operational_model(
            request=request,
            operational_model_id=candidate_track.attached_operational_model_id,
            against=reference_track.attached_operational_model_id,
            include_entry_impact=False,
        )
    grant = await caller.connect(reference_track, edge=COLLABORATES_ON, role="viewer")
    result = await diff_operational_model(
        request=request,
        operational_model_id=candidate_track.attached_operational_model_id,
        against=reference_track.attached_operational_model_id,
        include_entry_impact=False,
    )
    assert result["reference_id"] == reference_track.attached_operational_model_id
    await grant.delete()
    with pytest.raises(ResourceNotFoundError):
        await diff_operational_model(
            request=request,
            operational_model_id=candidate_track.attached_operational_model_id,
            against=reference_track.attached_operational_model_id,
            include_entry_impact=False,
        )


@pytest.mark.asyncio
async def test_diff_cannot_use_foreign_workspace_reference_despite_a_valid_grant():
    from app.api.errors import InsufficientPermissionsError
    from app.api.operational_models import diff_operational_model

    caller, workspace, _, candidate_track, _ = await graph()
    _, foreign_workspace, _, reference_track, _ = await graph()
    await caller.connect(foreign_workspace, edge=IS_MEMBER_OF, role="guest")
    await caller.connect(reference_track, edge=COLLABORATES_ON, role="admin")
    assert await _resolve_cp_edit_permission(
        user_id=caller.id,
        cp=await get_current_model(reference_track.attached_operational_model_id),
    )
    with pytest.raises(InsufficientPermissionsError):
        await diff_operational_model(
            request=request_for(caller.id, workspace.id),
            operational_model_id=candidate_track.attached_operational_model_id,
            against=reference_track.attached_operational_model_id,
            include_entry_impact=False,
        )
