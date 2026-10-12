"""Live rooted grants, flat groups and current definition boundaries."""

import pytest

from app.models.edges import (
    AUTHORED_BY,
    COLLABORATES_ON,
    HAS_APPLICATION_DEFINITION,
    IS_MEMBER_OF,
)
from app.models.nodes import ApplicationDefinition, User
from app.services.access_delegation import (
    action_decision,
    create_binding,
    create_group,
    revoke_binding,
    set_group_member,
)
from app.services.app_graph import catalog_user
from app.services.application_definitions import definition_fingerprint
from tests.test_resource_location import graph

pytestmark = [pytest.mark.asyncio, pytest.mark.smoke]


async def fixture():
    owner, workspace, app, track, entry = await graph()
    await entry.connect(owner, edge=AUTHORED_BY)
    reader = await User.create(display_name="Reviewer")
    await catalog_user(reader)
    await reader.connect(workspace, edge=IS_MEMBER_OF, role="member")
    await reader.connect(app, edge=COLLABORATES_ON, role="viewer")
    manifest = {
        "app": {
            "operations": [
                {
                    "key": "approve",
                    "authorization": {
                        "roles": ["reviewer"],
                        "targets": [{"input": "entry_id", "kind": "entry"}],
                        "conditions": [
                            {
                                "attribute": "entry.author_id",
                                "operator": "ne",
                                "principal": True,
                            }
                        ],
                        "mutations": [
                            {"entry_type": "request", "fields": ["approval"]}
                        ],
                    },
                }
            ]
        }
    }
    definition = await ApplicationDefinition.create(
        app_id=app.id,
        workspace_id=workspace.id,
        revision=1,
        status="active",
        canonical_manifest=manifest,
        manifest_fingerprint=definition_fingerprint(manifest),
    )
    await app.connect(definition, edge=HAS_APPLICATION_DEFINITION)
    app.active_definition_id = definition.id
    app.active_definition_revision = 1
    await app.save()
    return owner, reader, workspace, app, track, entry, definition


async def test_group_action_grant_revocation_and_owner_no_implicit_action():
    owner, reader, workspace, app, _, entry, _ = await fixture()
    arguments = dict(
        principal_id=reader.id,
        app_id=app.id,
        operation_key="approve",
        payload={"entry_id": entry.id},
    )
    assert not (await action_decision(**arguments)).allowed
    group = await create_group(
        actor_id=owner.id, workspace_id=workspace.id, name="Reviewers"
    )
    await set_group_member(
        actor_id=owner.id, group_id=group.id, user_id=reader.id, member=True
    )
    binding = await create_binding(
        actor_id=owner.id,
        app_id=app.id,
        subject_kind="group",
        subject_id=group.id,
        role="reviewer",
        conditions=[],
    )
    assert (await action_decision(**arguments)).allowed
    assert not (
        await action_decision(
            principal_id=owner.id,
            app_id=app.id,
            operation_key="approve",
            payload={"entry_id": entry.id},
        )
    ).allowed
    await set_group_member(
        actor_id=owner.id, group_id=group.id, user_id=reader.id, member=False
    )
    assert not (await action_decision(**arguments)).allowed
    await set_group_member(
        actor_id=owner.id, group_id=group.id, user_id=reader.id, member=True
    )
    assert (await action_decision(**arguments)).allowed
    await revoke_binding(actor_id=owner.id, binding_id=binding.id)
    assert not (await action_decision(**arguments)).allowed


async def test_human_binding_expires_when_membership_lifetime_changes():
    owner, reader, workspace, app, _, entry, _ = await fixture()
    await create_binding(
        actor_id=owner.id,
        app_id=app.id,
        subject_kind="human",
        subject_id=reader.id,
        role="reviewer",
        conditions=[],
    )
    arguments = dict(
        principal_id=reader.id,
        app_id=app.id,
        operation_key="approve",
        payload={"entry_id": entry.id},
    )
    assert (await action_decision(**arguments)).allowed
    context = await reader.get_context()
    for edge in await context.find_edges_between(
        reader.id, workspace.id, edge_class=IS_MEMBER_OF
    ):
        await edge.delete()
    await reader.connect(workspace, edge=IS_MEMBER_OF, role="member")
    assert not (await action_decision(**arguments)).allowed


async def test_binding_conditions_do_not_mix_across_overlapping_sources():
    owner, reader, _, app, _, entry, _ = await fixture()
    await create_binding(
        actor_id=owner.id,
        app_id=app.id,
        subject_kind="human",
        subject_id=reader.id,
        role="reviewer",
        conditions=[{"attribute": "entry.status", "value": "unattainable"}],
    )
    await create_binding(
        actor_id=owner.id,
        app_id=app.id,
        subject_kind="human",
        subject_id=reader.id,
        role="reviewer",
        conditions=[{"attribute": "entry.author_id", "value": "foreign-author"}],
    )
    arguments = dict(
        principal_id=reader.id,
        app_id=app.id,
        operation_key="approve",
        payload={
            "entry_id": entry.id,
            "status": "unattainable",
            "author_id": "foreign-author",
        },
    )
    assert not (await action_decision(**arguments)).allowed
    binding = await create_binding(
        actor_id=owner.id,
        app_id=app.id,
        subject_kind="human",
        subject_id=reader.id,
        role="reviewer",
        conditions=[],
    )
    assert (await action_decision(**arguments)).allowed
    await revoke_binding(actor_id=owner.id, binding_id=binding.id)
    assert not (await action_decision(**arguments)).allowed


async def test_foreign_group_and_unknown_function_cannot_grant():
    from app.api.errors import InsufficientPermissionsError

    owner, reader, _, app, _, entry, _ = await fixture()
    foreign_owner, foreign_workspace, _, _, _ = await graph()
    group = await create_group(
        actor_id=foreign_owner.id, workspace_id=foreign_workspace.id, name="Foreign"
    )
    with pytest.raises(InsufficientPermissionsError):
        await create_binding(
            actor_id=owner.id,
            app_id=app.id,
            subject_kind="group",
            subject_id=group.id,
            role="reviewer",
            conditions=[],
        )
    assert not (
        await action_decision(
            principal_id=reader.id,
            app_id=app.id,
            operation_key="undeclared",
            payload={"entry_id": entry.id},
        )
    ).allowed


async def test_definition_revision_invalidates_grants_without_silent_new_functions():
    owner, reader, workspace, app, _, entry, old = await fixture()
    await create_binding(
        actor_id=owner.id,
        app_id=app.id,
        subject_kind="human",
        subject_id=reader.id,
        role="reviewer",
        conditions=[],
    )
    arguments = dict(
        principal_id=reader.id,
        app_id=app.id,
        operation_key="approve",
        payload={"entry_id": entry.id},
    )
    assert (await action_decision(**arguments)).allowed
    old.status = "superseded"
    await old.save()
    manifest = old.canonical_manifest
    replacement = await ApplicationDefinition.create(
        app_id=app.id,
        workspace_id=workspace.id,
        revision=2,
        status="active",
        canonical_manifest=manifest,
        manifest_fingerprint=definition_fingerprint(manifest),
    )
    await app.connect(replacement, edge=HAS_APPLICATION_DEFINITION)
    app.active_definition_id = replacement.id
    app.active_definition_revision = 2
    await app.save()
    assert not (await action_decision(**arguments)).allowed


async def test_current_definition_guards_ignore_stale_worker_registry():
    from app.api.errors import BadRequestError
    from app.services.app_invariant_guards import (
        enforce_protected_field_write,
        register_protected_fields,
        unregister_protected_fields,
    )

    owner, reader, workspace, app, track, entry, definition = await fixture()
    definition.canonical_manifest["app"]["protected_state"] = {
        "request": {
            "fields": ["approval"],
            "top_level": ["status"],
            "deny_delete": True,
        }
    }
    definition.manifest_fingerprint = definition_fingerprint(
        definition.canonical_manifest
    )
    await definition.save()
    register_protected_fields(workspace.id, app.id, {})
    try:
        with pytest.raises(BadRequestError):
            await enforce_protected_field_write(
                workspace_id=workspace.id,
                entry_type_key="request",
                entry_id=entry.id,
                proposed_custom_fields={"approval": "yes"},
            )
    finally:
        unregister_protected_fields(workspace.id, app.id)


async def test_group_api_scope_and_strict_body(authenticated_client):
    from app.services.permissions import get_user_node

    response = await authenticated_client.get("/api/auth/me")
    user_body = response.json().get("user", response.json())
    test_user = await get_user_node(user_body["id"])
    assert test_user is not None, response.text
    from app.models.edges import OWNS
    from app.models.nodes import Workspace
    from app.services.app_graph import catalog_workspace

    first = await Workspace.create(name="Scoped grants first", kind="organization")
    second = await Workspace.create(name="Scoped grants second", kind="organization")
    for workspace in (first, second):
        await catalog_workspace(workspace)
        await test_user.connect(workspace, edge=OWNS)
        await test_user.connect(workspace, edge=IS_MEMBER_OF, role="owner")
    path = f"/api/workspaces/{first.id}/access-groups"
    correct = {"X-Integral-Scope": f"ws:{first.id}"}
    foreign = {"X-Integral-Scope": f"ws:{second.id}"}
    response = await authenticated_client.post(
        path, json={"name": "Reviewers"}, headers=correct
    )
    assert response.status_code in (200, 201), response.text
    group_id = response.json()["id"]
    assert (await authenticated_client.get(path, headers=correct)).status_code == 200
    assert (await authenticated_client.get(path, headers=foreign)).status_code == 403
    assert (
        await authenticated_client.delete(
            f"/api/access-groups/{group_id}", headers=foreign
        )
    ).status_code == 403
    assert (
        await authenticated_client.get(path, headers={"X-Integral-Scope": first.id})
    ).status_code == 400
    assert (
        await authenticated_client.post(
            path, json={"name": "Reviewers", "workspace_id": second.id}, headers=correct
        )
    ).status_code == 400
    assert (
        await authenticated_client.request(
            "DELETE",
            f"/api/access-groups/{group_id}",
            json={"subject_id": test_user.id},
            headers=correct,
        )
    ).status_code == 400


async def test_review_fingerprint_changes_and_revoked_binding_cleanup():
    from app.models.access_delegation import AppActionBinding
    from app.services.access_delegation import (
        delete_app_delegation_roots,
        delete_group,
        subject_delegation_fingerprint,
        subject_delegation_review,
    )

    owner, reader, workspace, app, track, entry, definition = await fixture()
    before = await subject_delegation_fingerprint(reader.id, workspace.id)
    group = await create_group(
        actor_id=owner.id, workspace_id=workspace.id, name="Reviewers"
    )
    await set_group_member(
        actor_id=owner.id, group_id=group.id, user_id=reader.id, member=True
    )
    grouped = await subject_delegation_fingerprint(reader.id, workspace.id)
    assert grouped != before
    binding = await create_binding(
        actor_id=owner.id,
        app_id=app.id,
        subject_kind="group",
        subject_id=group.id,
        role="reviewer",
        conditions=[],
    )
    bound = await subject_delegation_fingerprint(reader.id, workspace.id)
    assert bound != grouped
    review = await subject_delegation_review(reader.id, workspace.id)
    assert review["groups"] == [{"group_id": group.id, "name": "Reviewers"}]
    assert review["action_bindings"][0]["binding_id"] == binding.id
    await revoke_binding(actor_id=owner.id, binding_id=binding.id)
    assert await subject_delegation_fingerprint(reader.id, workspace.id) != bound
    await delete_group(actor_id=owner.id, group_id=group.id)
    assert not (
        await action_decision(
            principal_id=reader.id,
            app_id=app.id,
            operation_key="approve",
            payload={"entry_id": entry.id},
        )
    ).allowed
    await delete_app_delegation_roots(app.id)
    assert await AppActionBinding.get(binding.id) is None
