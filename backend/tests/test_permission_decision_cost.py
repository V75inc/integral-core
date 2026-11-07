"""Role decisions bound identity reads and recheck identity before releasing a role."""

import uuid

import pytest
from jvspatial.api.auth.models import User as AuthUser

from app.models.edges import COLLABORATES_ON, EXCLUDED_FROM, IS_MEMBER_OF
from app.models.nodes import User
from app.services import permissions
from app.services.app_graph import catalog_user
from tests.test_resource_location import graph

pytestmark = [pytest.mark.asyncio, pytest.mark.smoke]


@pytest.mark.parametrize("principal_form", ["graph", "auth"])
async def test_parent_cascade_bounds_identity_reads_and_observes_next_decision_changes(
    monkeypatch, principal_form
):
    _, workspace, app, _, entry = await graph()
    app.visibility = "private"
    await app.save()
    identity = await AuthUser.create(
        email=f"decision-{uuid.uuid4().hex}@example.com", password_hash="fixture"
    )
    reader = await User.create(user_id=identity.id, display_name="Decision QA")
    await catalog_user(reader)
    await reader.connect(workspace, edge=IS_MEMBER_OF, role="member")
    await reader.connect(app, edge=COLLABORATES_ON, role="editor")
    principal = reader.id if principal_form == "graph" else identity.id
    reads = []
    original = permissions.primary_auth_record

    async def observed(database, auth_id):
        reads.append(auth_id)
        return await original(database, auth_id)

    monkeypatch.setattr(permissions, "primary_auth_record", observed)
    assert await permissions.resolve_role(principal, "entry", entry.id) == "editor"
    # Admit once and recheck before returning an allow, rather than repeating
    # linked auth/profile validation at every helper and parent hop.
    assert reads == [identity.id] * 2
    await reader.connect(entry, edge=EXCLUDED_FROM)
    assert await permissions.resolve_role(principal, "entry", entry.id) is None
    assert reads == [identity.id] * 3
    await reader.connect(entry, edge=COLLABORATES_ON, role="viewer")
    assert await permissions.resolve_role(principal, "entry", entry.id) == "viewer"
    identity.is_active = False
    await identity.save()
    assert await permissions.resolve_role(principal, "entry", entry.id) is None
    assert reads == [identity.id] * 6


async def test_identity_disabled_during_cascade_cannot_release_a_role(monkeypatch):
    _, workspace, app, _, entry = await graph()
    identity = await AuthUser.create(
        email=f"concurrent-decision-{uuid.uuid4().hex}@example.com",
        password_hash="fixture",
    )
    reader = await User.create(user_id=identity.id, display_name="Decision QA")
    await catalog_user(reader)
    await reader.connect(workspace, edge=IS_MEMBER_OF, role="member")
    await reader.connect(app, edge=COLLABORATES_ON, role="editor")
    original = permissions._direct_role_on_resource

    async def disable_on_app(user, resource_id):
        role = await original(user, resource_id)
        if resource_id == app.id:
            identity.is_active = False
            await identity.save()
        return role

    monkeypatch.setattr(permissions, "_direct_role_on_resource", disable_on_app)
    assert await permissions.resolve_role(reader.id, "entry", entry.id) is None
