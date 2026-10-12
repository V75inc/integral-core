"""Missing, conflicting and hydration-skipped facts cannot invent authority."""

import copy
import os
import uuid

import pytest
from jvspatial.api.auth.models import User as AuthUser

from app.exceptions import AuthorizationUnavailableError
from app.middleware.permissions_cache import isolated_permissions_cache
from app.models.edges import COLLABORATES_ON, IS_MEMBER_OF, OWNS
from app.models.nodes import User
from app.services.app_graph import catalog_user
from app.services.permissions import (
    can_create_app_under_workspace,
    can_create_track_under_workspace,
    can_publish_operational_models_under_workspace,
    get_user_node,
    resolve_role,
)
from app.services.workspace_permissions import (
    can_access_workspace,
    member_creation_flags,
)
from tests.test_resource_location import graph

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]


async def member_fixture():
    _, workspace, app, track, entry = await graph()
    member = await User.create(display_name="Primary facts member")
    await catalog_user(member)
    edge = await member.connect(workspace, edge=IS_MEMBER_OF, role="member")
    context = await member.get_context()
    return member, workspace, app, track, entry, edge, context


@pytest.mark.parametrize("literal", [None, "", False, "viewer", "OWNER"])
async def test_malformed_canonical_membership_never_activates_legacy_owner(literal):
    member, workspace, _, _, _, edge, context = await member_fixture()
    await context.database.find_one_and_update(
        "edge",
        {"id": edge.id},
        {"$set": {"context.role": literal, "role": "owner"}},
    )
    with pytest.raises(AuthorizationUnavailableError):
        await can_access_workspace(member.id, workspace.id)


async def test_conflicting_membership_is_not_a_weaker_canonical_allow():
    member, workspace, _, _, _, edge, context = await member_fixture()
    await context.database.find_one_and_update(
        "edge", {"id": edge.id}, {"$set": {"role": "owner"}}
    )
    with pytest.raises(AuthorizationUnavailableError):
        await can_access_workspace(member.id, workspace.id)


@pytest.mark.parametrize("value", ["false", "true", 1, 0, None, {"enabled": True}])
async def test_creation_grants_require_stored_booleans(value):
    member, workspace, _, _, _, edge, context = await member_fixture()
    await context.database.find_one_and_update(
        "edge", {"id": edge.id}, {"$set": {"context.can_create_apps": value}}
    )
    for check in (
        member_creation_flags,
        can_create_app_under_workspace,
        can_publish_operational_models_under_workspace,
    ):
        with pytest.raises(AuthorizationUnavailableError):
            await check(
                member if check is member_creation_flags else member.id, workspace.id
            )


async def test_explicit_creation_grants_and_absent_defaults():
    member, workspace, _, _, _, edge, context = await member_fixture()
    assert await member_creation_flags(member, workspace.id) == (False, False)
    assert not await can_create_app_under_workspace(member.id, workspace.id)
    await context.database.find_one_and_update(
        "edge",
        {"id": edge.id},
        {"$set": {"context.can_create_apps": True, "context.can_create_tracks": True}},
    )
    assert await can_create_app_under_workspace(member.id, workspace.id)
    assert await can_create_track_under_workspace(member.id, workspace.id)
    assert await can_publish_operational_models_under_workspace(member.id, workspace.id)
    await context.database.find_one_and_update(
        "edge",
        {"id": edge.id},
        {"$unset": {"context.can_create_apps": "", "context.can_create_tracks": ""}},
    )
    assert await member_creation_flags(member, workspace.id) == (False, False)


@pytest.mark.parametrize("fault", ["conflict", "duplicate"])
async def test_creation_never_selects_an_ambiguous_membership(fault):
    member, workspace, _, _, _, edge, context = await member_fixture()
    if fault == "conflict":
        await context.database.find_one_and_update(
            "edge", {"id": edge.id}, {"$set": {"can_create_apps": True}}
        )
    else:
        duplicate = copy.deepcopy(
            await context.database.find_one("edge", {"id": edge.id})
        )
        duplicate["id"] = f"e.IS_MEMBER_OF.{uuid.uuid4()}"
        duplicate["context"]["can_create_apps"] = True
        if os.environ.get("INTEGRAL_TEST_DB") == "postgres":
            from asyncpg import UniqueViolationError

            with pytest.raises(UniqueViolationError):
                await context.database.save("edge", duplicate)
            assert await member_creation_flags(member, workspace.id) == (False, False)
            assert not await can_create_app_under_workspace(member.id, workspace.id)
            return
        await context.database.save("edge", duplicate)
    with pytest.raises(AuthorizationUnavailableError):
        await can_create_app_under_workspace(member.id, workspace.id)


@pytest.mark.parametrize("fault", ["missing", "unknown", "conflict"])
async def test_ownership_role_never_uses_a_model_default(fault):
    member, _, app, _, _, _, context = await member_fixture()
    grant = await member.connect(app, edge=OWNS)
    update = (
        {"$unset": {"context.role": ""}}
        if fault == "missing"
        else {
            "$set": (
                {"context.role": "invalid-owner"}
                if fault == "unknown"
                else {"role": "viewer"}
            )
        }
    )
    await context.database.find_one_and_update("edge", {"id": grant.id}, update)
    with isolated_permissions_cache(), pytest.raises(AuthorizationUnavailableError):
        await resolve_role(member.id, "app", app.id)


@pytest.mark.parametrize("fault", ["absent", "unknown", "conflict", "duplicate"])
async def test_raw_direct_grant_cannot_default_or_hide_a_candidate(fault):
    member, _, app, _, _, _, context = await member_fixture()
    grant = await member.connect(app, edge=COLLABORATES_ON, role="viewer")
    with isolated_permissions_cache():
        assert await resolve_role(member.id, "app", app.id) == "viewer"
    if fault == "duplicate":
        row = await context.database.find_one("edge", {"id": grant.id})
        duplicate = copy.deepcopy(row)
        duplicate["id"] = f"e.COLLABORATES_ON.{uuid.uuid4()}"
        duplicate["context"]["invited_at"] = {"invalid": True}
        if os.environ.get("INTEGRAL_TEST_DB") == "postgres":
            from asyncpg import UniqueViolationError

            with pytest.raises(UniqueViolationError):
                await context.database.save("edge", duplicate)
            with isolated_permissions_cache():
                assert await resolve_role(member.id, "app", app.id) == "viewer"
            return
        await context.database.save("edge", duplicate)
    else:
        update = (
            {"$unset": {"context.role": ""}}
            if fault == "absent"
            else {
                "$set": (
                    {"context.role": "not-a-role"}
                    if fault == "unknown"
                    else {"role": "owner"}
                )
            }
        )
        await context.database.find_one_and_update("edge", {"id": grant.id}, update)
    with isolated_permissions_cache(), pytest.raises(AuthorizationUnavailableError):
        await resolve_role(member.id, "app", app.id)


@pytest.mark.parametrize("kind", ["owner", "viewer"])
async def test_optional_audit_metadata_cannot_hide_complete_direct_authority(kind):
    member, _, app, _, _, _, context = await member_fixture()
    edge_type = OWNS if kind == "owner" else COLLABORATES_ON
    grant = await member.connect(app, edge=edge_type, role=kind)
    field = "granted_at" if kind == "owner" else "invited_at"
    await context.database.find_one_and_update(
        "edge", {"id": grant.id}, {"$set": {f"context.{field}": {"invalid": True}}}
    )
    with isolated_permissions_cache():
        assert await resolve_role(member.id, "app", app.id) == kind


async def test_unambiguous_legacy_role_is_supported_without_canonical_default():
    member, workspace, app, _, _, edge, context = await member_fixture()
    grant = await member.connect(app, edge=COLLABORATES_ON, role="viewer")
    for relation, role in ((edge, "guest"), (grant, "commenter")):
        await context.database.find_one_and_update(
            "edge",
            {"id": relation.id},
            {"$unset": {"context.role": ""}, "$set": {"role": role}},
        )
    assert await can_access_workspace(member.id, workspace.id) == "guest"
    with isolated_permissions_cache():
        assert await resolve_role(member.id, "app", app.id) == "commenter"


async def test_personal_workspace_guest_is_not_advertised_creation_rights():
    from app.api.workspaces import _caller_member_creation_flags

    member, workspace, _, _, _, edge, context = await member_fixture()
    workspace.kind = "personal"
    await workspace.save()
    await context.database.find_one_and_update(
        "edge", {"id": edge.id}, {"$set": {"context.role": "guest"}}
    )
    assert await _caller_member_creation_flags(member.id, workspace) == (False, False)
    assert not await can_create_app_under_workspace(member.id, workspace.id)


@pytest.mark.parametrize("principal_form", ["auth", "graph"])
@pytest.mark.parametrize(
    "fault", ["missing_active", "coerced_active", "duplicate_profile"]
)
async def test_identity_defaults_and_ambiguous_profile_aliases_deny(
    principal_form, fault
):
    member, workspace, app, _, _, _, context = await member_fixture()
    identity = await AuthUser.create(
        email="raw-facts@example.com", password_hash="fixture"
    )
    member.user_id = identity.id
    await member.save()
    await member.connect(app, edge=COLLABORATES_ON, role="editor")
    principal = identity.id if principal_form == "auth" else member.id
    assert (await get_user_node(principal)).id == member.id
    if fault == "duplicate_profile":
        duplicate = await User.create(
            display_name="Corrupt duplicate", user_id=identity.id
        )
        await catalog_user(duplicate)
        await context.database.find_one_and_update(
            "node",
            {"id": duplicate.id},
            {"$set": {"context.display_name": {"invalid": True}}},
        )
        with pytest.raises(AuthorizationUnavailableError):
            await get_user_node(principal)
    else:
        update = (
            {"$unset": {"context.is_active": ""}}
            if fault == "missing_active"
            else {"$set": {"context.is_active": "true"}}
        )
        await context.database.find_one_and_update(
            "object", {"id": identity.id}, update
        )
        assert await get_user_node(principal) is None
        assert await can_access_workspace(principal, workspace.id) == "none"
        with isolated_permissions_cache():
            assert await resolve_role(principal, "app", app.id) is None
