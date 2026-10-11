"""Creation designates one Model curator for the current membership lifetime."""

import pytest

from app.api.errors import InsufficientPermissionsError
from app.exceptions import AuthorizationUnavailableError
from app.models.edges import COLLABORATES_ON, IS_MEMBER_OF
from app.models.nodes import OperationalModel, User
from app.schemas.policy import Resource
from app.services import operational_model_authoring as author
from app.services.library_model_creation import create_private_library_model
from app.services.model_authority import model_action_authorized
from app.services.permissions import _direct_role_on_resource
from app.services.workspace_governance import ordinary_authority_only
from tests.test_resource_location import graph

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]


def resource(model_id):
    return Resource(
        kind="operational_model", id=model_id, scope=f"operational_model:{model_id}"
    )


async def fixture(*, role="member", flag="can_create_tracks"):
    owner, workspace, _, _, _ = await graph()
    creator, _, _, _, _ = await graph()
    member = await creator.connect(
        workspace, edge=IS_MEMBER_OF, role=role, **{flag: True}
    )
    created = await author.create_empty_library_draft(
        creator.id, workspace.id, "Creator curation"
    )
    assert "error" not in created
    model_id = created["draft_id"]
    ctx = await creator.get_context()
    rows = await ctx.database.find(
        "edge", {"source": creator.id, "target": model_id, "entity": "COLLABORATES_ON"}
    )
    assert len(rows) == 1
    return owner, workspace, creator, member, model_id, rows[0]


@pytest.mark.parametrize("role", ["member", "guest", "admin"])
@pytest.mark.parametrize("flag", ["can_create_apps", "can_create_tracks"])
async def test_creator_curates_only_new_model_and_can_publish(role, flag):
    owner, workspace, creator, member, model_id, grant = await fixture(
        role=role, flag=flag
    )
    assert grant["context"]["role"] == "admin"
    assert grant["context"]["grant_membership_id"] == member.id
    assert grant["context"]["grant_workspace_id"] == workspace.id
    assert grant["context"]["grant_issuer_id"] == creator.id
    assert await model_action_authorized(
        creator.id, "operational_model.read", resource(model_id)
    )
    patch = await author.apply_patch_to_draft(
        user_id=creator.id,
        draft_id=model_id,
        operations=[{"op": "add_entry_type", "spec": {"key": "note", "name": "Note"}}],
    )
    assert "error" not in patch
    other = await author.create_empty_library_draft(
        owner.id, workspace.id, "Other private draft"
    )
    assert not await model_action_authorized(
        creator.id, "operational_model.read", resource(other["draft_id"])
    )
    assert not await model_action_authorized(
        creator.id, "operational_model.update", resource(other["draft_id"])
    )
    published = await author.publish_draft_for_agent(
        user_id=creator.id, draft_id=model_id
    )
    assert published["published_id"] == model_id
    assert await model_action_authorized(
        creator.id, "operational_model.update", resource(model_id)
    )


async def test_creation_flag_removal_preserves_existing_exact_curation():
    _, workspace, creator, member, model_id, _ = await fixture()
    ctx = await creator.get_context()
    await ctx.database.find_one_and_update(
        "edge", {"id": member.id}, {"$set": {"context.can_create_tracks": False}}
    )
    assert await model_action_authorized(
        creator.id, "operational_model.update", resource(model_id)
    )
    denied = await author.create_empty_library_draft(
        creator.id, workspace.id, "Forbidden new draft"
    )
    assert denied["error"] == "forbidden"


async def test_raw_membership_removal_and_rejoin_never_revive_creator_grant():
    _, workspace, creator, member, model_id, _ = await fixture()
    assert await model_action_authorized(
        creator.id, "operational_model.update", resource(model_id)
    )
    await member.delete()
    assert not await model_action_authorized(
        creator.id, "operational_model.update", resource(model_id)
    )
    replacement = await creator.connect(
        workspace, edge=IS_MEMBER_OF, role="member", can_create_tracks=True
    )
    assert replacement.id != member.id
    assert not await model_action_authorized(
        creator.id, "operational_model.read", resource(model_id)
    )
    assert not await model_action_authorized(
        creator.id, "operational_model.update", resource(model_id)
    )


async def test_owner_creation_without_flags_does_not_mint_ordinary_app_curation():
    owner, workspace, _, _, _ = await graph()
    created = await author.create_empty_library_draft(
        owner.id, workspace.id, "Protected creation"
    )
    ctx = await owner.get_context()
    assert not await ctx.database.find(
        "edge",
        {
            "source": owner.id,
            "target": created["draft_id"],
            "entity": "COLLABORATES_ON",
        },
    )
    with ordinary_authority_only():
        assert not await model_action_authorized(
            owner.id, "operational_model.update", resource(created["draft_id"])
        )
        with pytest.raises(InsufficientPermissionsError):
            await create_private_library_model(
                user_id=owner.id,
                workspace_id=workspace.id,
                name="Forbidden ordinary creation",
                scope="track",
            )
    assert await model_action_authorized(
        owner.id, "operational_model.update", resource(created["draft_id"])
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("grant_source", "invite"),
        ("grant_workspace_id", "foreign-workspace"),
        ("grant_membership_id", None),
        ("grant_issuer_id", "foreign-user"),
        ("grant_membership_id", {"malformed": True}),
        ("role", "owner"),
    ],
)
async def test_partial_corrupt_or_amplified_binding_is_unavailable(field, value):
    _, _, creator, _, model_id, grant = await fixture()
    ctx = await creator.get_context()
    await ctx.database.find_one_and_update(
        "edge", {"id": grant["id"]}, {"$set": {f"context.{field}": value}}
    )
    with pytest.raises(AuthorizationUnavailableError):
        await model_action_authorized(
            creator.id, "operational_model.update", resource(model_id)
        )


async def test_conflicting_flat_and_canonical_binding_is_unavailable():
    _, _, creator, _, model_id, grant = await fixture()
    ctx = await creator.get_context()
    await ctx.database.find_one_and_update(
        "edge", {"id": grant["id"]}, {"$set": {"grant_membership_id": "conflict"}}
    )
    with pytest.raises(AuthorizationUnavailableError):
        await model_action_authorized(
            creator.id, "operational_model.update", resource(model_id)
        )


async def test_creator_binding_cannot_be_transplanted_to_track():
    _, _, creator, _, _, grant = await fixture()
    _, _, _, track, _ = await graph()
    await creator.connect(track, edge=COLLABORATES_ON, **grant["context"])
    with pytest.raises(AuthorizationUnavailableError):
        await _direct_role_on_resource(creator, track.id)


async def test_membership_source_outage_withholds_bound_grant(monkeypatch):
    _, workspace, creator, _, model_id, _ = await fixture()
    ctx = await creator.get_context()
    original = ctx.database.find

    async def failing(collection, query, **kwargs):
        if (
            collection == "edge"
            and query.get("entity") == "IS_MEMBER_OF"
            and query.get("target") == workspace.id
        ):
            raise RuntimeError("membership unavailable")
        return await original(collection, query, **kwargs)

    monkeypatch.setattr(ctx.database, "find", failing)
    with pytest.raises(AuthorizationUnavailableError):
        await _direct_role_on_resource(creator, model_id)


async def test_grant_creation_failure_leaves_no_model_or_bound_edges(monkeypatch):
    _, workspace, _, _, _ = await graph()
    creator, _, _, _, _ = await graph()
    await creator.connect(
        workspace, edge=IS_MEMBER_OF, role="member", can_create_apps=True
    )
    original = User.connect

    async def failing(self, target, edge=None, **kwargs):
        if edge is COLLABORATES_ON:
            raise RuntimeError("grant write unavailable")
        return await original(self, target, edge=edge, **kwargs)

    monkeypatch.setattr(User, "connect", failing)
    with pytest.raises(RuntimeError, match="grant write unavailable"):
        await create_private_library_model(
            user_id=creator.id,
            workspace_id=workspace.id,
            name="Failed creator grant",
            scope="track",
        )
    assert await OperationalModel.find({"context.name": "Failed creator grant"}) == []


async def test_creator_flags_revoked_after_initial_gate_do_not_create_model(
    monkeypatch,
):
    _, workspace, _, _, _ = await graph()
    creator, _, _, _, _ = await graph()
    member = await creator.connect(
        workspace, edge=IS_MEMBER_OF, role="member", can_create_tracks=True
    )
    from app.services import permissions

    original = permissions.can_publish_operational_models_under_workspace

    async def revoked_after_gate(user_id, workspace_id):
        allowed = await original(user_id, workspace_id)
        ctx = await creator.get_context()
        await ctx.database.find_one_and_update(
            "edge", {"id": member.id}, {"$set": {"context.can_create_tracks": False}}
        )
        return allowed

    monkeypatch.setattr(
        permissions,
        "can_publish_operational_models_under_workspace",
        revoked_after_gate,
    )
    with pytest.raises(InsufficientPermissionsError):
        await create_private_library_model(
            user_id=creator.id,
            workspace_id=workspace.id,
            name="Revoked creation",
            scope="track",
        )
    assert await OperationalModel.find({"context.name": "Revoked creation"}) == []


@pytest.mark.parametrize(
    "field",
    [
        "id",
        "entity",
        "app_id",
        "draft_of_id",
        "published_id",
        "parent_version_id",
        "signature",
        "metadata",
        "library_package",
    ],
)
async def test_creation_rejects_identity_and_provenance_input_without_effects(field):
    owner, workspace, _, _, _ = await graph()
    existing = await create_private_library_model(
        user_id=owner.id,
        workspace_id=workspace.id,
        name="Owner private Model",
        scope="track",
        status="draft",
    )
    creator, _, _, _, _ = await graph()
    await creator.connect(
        workspace, edge=IS_MEMBER_OF, role="member", can_create_tracks=True
    )
    from app.services.model_authority import get_current_model

    before = (await get_current_model(existing.id)).model_dump()
    context = await creator.get_context()
    model_rows = await context.database.find(
        "node", {"entity": OperationalModel._entity_name()}
    )
    with pytest.raises(ValueError, match="server-owned identity"):
        await create_private_library_model(
            user_id=creator.id,
            workspace_id=workspace.id,
            name="Creator replacement",
            scope="track",
            status="draft",
            **{field: existing.id},
        )
    assert (await get_current_model(existing.id)).model_dump() == before
    assert (
        await context.database.find("node", {"entity": OperationalModel._entity_name()})
        == model_rows
    )
    assert not await context.database.find(
        "edge",
        {
            "source": creator.id,
            "target": existing.id,
            "entity": COLLABORATES_ON._entity_name(),
        },
    )
