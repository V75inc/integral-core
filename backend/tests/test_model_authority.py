"""Current canonical Model ownership and complete shared mutation authority."""

import pytest

from app.contracts.runtime import ExecutionScope
from app.models.edges import (
    COLLABORATES_ON,
    CONTAINS,
    HAS_OPERATIONAL_MODEL,
    IS_MEMBER_OF,
)
from app.models.nodes import OperationalModel, Track
from app.modules.policy import PolicyModule
from app.schemas.policy import Resource
from app.services.app_graph import catalog_track, create_track_view
from tests.test_resource_location import graph

pytestmark = pytest.mark.smoke


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["model", "attachment", "parent"])
async def test_model_source_outage_never_reuses_warmed_authority(monkeypatch, source):
    from app.exceptions import AuthorizationUnavailableError
    from app.services.model_authority import resolve_model_authority

    _, workspace, _, track, _ = await graph()
    model = await OperationalModel.get(track.attached_operational_model_id)
    assert model is not None
    admitted = await resolve_model_authority(model.id)
    assert admitted and admitted.workspace_id == workspace.id
    context = await model.get_context()
    original_find = context.database.find

    async def unavailable(collection, query, **kwargs):
        if (
            (source == "model" and collection == "node" and query.get("id") == model.id)
            or (
                source == "attachment"
                and collection == "edge"
                and query.get("entity") == HAS_OPERATIONAL_MODEL._entity_name()
                and query.get("target") == model.id
            )
            or (
                source == "parent"
                and collection == "node"
                and query.get("id") == track.id
            )
        ):
            raise RuntimeError("Injected primary authority source outage")
        return await original_find(collection, query, **kwargs)

    monkeypatch.setattr(context.database, "find", unavailable)
    with pytest.raises(AuthorizationUnavailableError):
        await resolve_model_authority(model.id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corruption",
    [
        "wrong_execution_workspace",
        "unrelated_parent",
        "missing_attachment",
        "conflicting_cache",
    ],
)
async def test_model_bound_to_actual_parent_and_workspace(corruption):
    caller, home, _, _, _ = await graph()
    _, workspace, app, track, _ = await graph()
    await create_track_view(track, name="Model authority baseline")
    model = await OperationalModel.get(track.attached_operational_model_id)
    assert model is not None
    await caller.connect(workspace, edge=IS_MEMBER_OF, role="guest")
    await caller.connect(track, edge=COLLABORATES_ON, role="viewer")
    resource = Resource(
        kind="operational_model", id=model.id, scope=f"track:{track.id}"
    )
    scope = ExecutionScope.create(principal_id=caller.id, workspace_id=workspace.id)
    module = PolicyModule()
    assert (
        await module.evaluate(
            scope=scope, action="operational_model.read", resource=resource
        )
    ).allowed
    if corruption == "wrong_execution_workspace":
        scope = ExecutionScope.create(principal_id=caller.id, workspace_id=home.id)
    elif corruption == "unrelated_parent":
        sibling = await Track.create(
            title="Unrelated model parent", workspace_id=workspace.id
        )
        await catalog_track(sibling)
        await app.connect(sibling, edge=CONTAINS)
        await caller.connect(sibling, edge=COLLABORATES_ON, role="viewer")
        resource = Resource(
            kind="operational_model", id=model.id, scope=f"track:{sibling.id}"
        )
    elif corruption == "missing_attachment":
        context = await track.get_context()
        edges = await context.find_edges_between(
            track.id, model.id, edge_class=HAS_OPERATIONAL_MODEL
        )
        assert len(edges) == 1
        await edges[0].delete()
    else:
        track.attached_operational_model_id = "n.OperationalModel.foreign-cache"
        await track.save()
    assert not (
        await module.evaluate(
            scope=scope, action="operational_model.read", resource=resource
        )
    ).allowed


@pytest.mark.asyncio
async def test_model_update_requires_authority_over_every_affected_track():
    caller, _, _, _, _ = await graph()
    owner, workspace, app, track, _ = await graph()
    model = await OperationalModel.get(track.attached_operational_model_id)
    assert model is not None
    await caller.connect(workspace, edge=IS_MEMBER_OF, role="guest")
    await caller.connect(track, edge=COLLABORATES_ON, role="owner")
    resource = Resource(
        kind="operational_model", id=model.id, scope=f"track:{track.id}"
    )
    scope = ExecutionScope.create(principal_id=caller.id, workspace_id=workspace.id)
    module = PolicyModule()
    assert (
        await module.evaluate(
            scope=scope, action="operational_model.update", resource=resource
        )
    ).allowed
    sibling = await Track.create(
        title="Shared model sibling",
        workspace_id=workspace.id,
        attached_operational_model_id=model.id,
    )
    await sibling.connect(model, edge=HAS_OPERATIONAL_MODEL)
    await catalog_track(sibling)
    await app.connect(sibling, edge=CONTAINS)
    await owner.connect(sibling, edge=COLLABORATES_ON, role="owner")
    # Sharing preserves legitimate reads through the admitted Track, but the
    # caller has no configuration rights over the sibling affected by mutation.
    assert (
        await module.evaluate(
            scope=scope, action="operational_model.read", resource=resource
        )
    ).allowed
    assert not (
        await module.evaluate(
            scope=scope, action="operational_model.update", resource=resource
        )
    ).allowed


@pytest.mark.asyncio
@pytest.mark.parametrize("family", ["app", "draft", "template", "template_draft"])
@pytest.mark.parametrize("corruption", ["none", "missing_link", "wrong_cache"])
async def test_model_family_requires_its_actual_named_provenance(family, corruption):
    from app.models.edges import DEFINES_TRACK_PROFILE, HAS_DRAFT_PROFILE
    from app.services.operational_model_atomic_swap import fork_draft

    caller, workspace, app, track, _ = await graph()
    parent_kind, parent = ("app", app) if family != "draft" else ("track", track)
    published = await OperationalModel.get(parent.attached_operational_model_id)
    assert published is not None
    model = published
    link_source = parent
    link_kind = HAS_OPERATIONAL_MODEL
    if family.startswith("template"):
        model = await OperationalModel.create(
            name="Named model template", scope="track", app_id=app.id
        )
        await published.connect(model, edge=DEFINES_TRACK_PROFILE)
        link_source, link_kind = published, DEFINES_TRACK_PROFILE
    if family in {"draft", "template_draft"}:
        link_source = model
        model = await fork_draft(published=model, actor_id=caller.id)
        link_kind = HAS_DRAFT_PROFILE
    resource = Resource(
        kind="operational_model", id=model.id, scope=f"{parent_kind}:{parent.id}"
    )
    scope = ExecutionScope.create(principal_id=caller.id, workspace_id=workspace.id)
    module = PolicyModule()
    assert (
        await module.evaluate(
            scope=scope, action="operational_model.read", resource=resource
        )
    ).allowed
    assert (
        await module.evaluate(
            scope=scope, action="operational_model.update", resource=resource
        )
    ).allowed
    if corruption == "missing_link":
        context = await model.get_context()
        links = await context.find_edges_between(
            link_source.id, model.id, edge_class=link_kind
        )
        assert len(links) == 1
        await links[0].delete()
    elif corruption == "wrong_cache":
        if family in {"draft", "template_draft"}:
            model.draft_of_id = "n.OperationalModel.forged-parent"
        else:
            model.app_id = "n.App.forged-parent"
        await model.save()
    decision = await module.evaluate(
        scope=scope, action="operational_model.read", resource=resource
    )
    assert decision.allowed is (corruption == "none")


@pytest.mark.asyncio
async def test_consumed_model_history_uses_current_parent_and_is_read_only():
    from app.services.operational_model_atomic_swap import fork_draft

    caller, workspace, _, track, _ = await graph()
    published = await OperationalModel.get(track.attached_operational_model_id)
    history = await fork_draft(published=published, actor_id=caller.id)
    # Persist the actual consumed-row shape; publication/effect atomicity is a
    # separate acceptance lane. This test qualifies current history admission.
    history.status = "published"
    history.published_id = published.id
    await history.save()
    module = PolicyModule()
    resource = Resource(
        kind="operational_model", id=history.id, scope=f"track:{track.id}"
    )
    scope = ExecutionScope.create(principal_id=caller.id, workspace_id=workspace.id)
    assert (
        await module.evaluate(
            scope=scope, action="operational_model.read", resource=resource
        )
    ).allowed
    assert not (
        await module.evaluate(
            scope=scope, action="operational_model.update", resource=resource
        )
    ).allowed
    history.published_id = "n.OperationalModel.wrong-publication-parent"
    await history.save()
    assert not (
        await module.evaluate(
            scope=scope, action="operational_model.read", resource=resource
        )
    ).allowed


@pytest.mark.asyncio
async def test_shared_model_updates_allow_only_after_all_parent_grants():
    caller, _, _, _, _ = await graph()
    _, workspace, app, track, _ = await graph()
    model = await OperationalModel.get(track.attached_operational_model_id)
    sibling = await Track.create(
        title="Second affected Track",
        workspace_id=workspace.id,
        attached_operational_model_id=model.id,
    )
    await sibling.connect(model, edge=HAS_OPERATIONAL_MODEL)
    await catalog_track(sibling)
    await app.connect(sibling, edge=CONTAINS)
    await caller.connect(workspace, edge=IS_MEMBER_OF, role="guest")
    await caller.connect(track, edge=COLLABORATES_ON, role="admin")
    module = PolicyModule()
    resource = Resource(
        kind="operational_model", id=model.id, scope=f"track:{track.id}"
    )
    scope = ExecutionScope.create(principal_id=caller.id, workspace_id=workspace.id)
    assert not (
        await module.evaluate(
            scope=scope, action="operational_model.update", resource=resource
        )
    ).allowed
    grant = await caller.connect(sibling, edge=COLLABORATES_ON, role="admin")
    assert (
        await module.evaluate(
            scope=scope, action="operational_model.update", resource=resource
        )
    ).allowed
    await grant.delete()
    assert not (
        await module.evaluate(
            scope=scope, action="operational_model.update", resource=resource
        )
    ).allowed


@pytest.mark.asyncio
async def test_model_action_cannot_be_reinterpreted_as_an_entry_action():
    caller, workspace, _, track, _ = await graph()
    module = PolicyModule()
    resource = Resource(
        kind="operational_model",
        id=track.attached_operational_model_id,
        scope=f"track:{track.id}",
    )
    scope = ExecutionScope.create(principal_id=caller.id, workspace_id=workspace.id)
    assert not (
        await module.evaluate(scope=scope, action="entry.update", resource=resource)
    ).allowed


@pytest.mark.asyncio
@pytest.mark.parametrize("principal_form", ["graph", "auth"])
async def test_model_read_rechecks_current_linked_identity(principal_form):
    from jvspatial.api.auth.models import User as AuthUser

    from app.models.nodes import User
    from app.services.app_graph import catalog_user

    _, workspace, _, track, _ = await graph()
    identity = await AuthUser.create(
        email="model-authority@example.com", password_hash="fixture"
    )
    reader = await User.create(
        display_name="Model identity fixture", user_id=identity.id
    )
    await catalog_user(reader)
    await reader.connect(workspace, edge=IS_MEMBER_OF, role="guest")
    await reader.connect(track, edge=COLLABORATES_ON, role="viewer")
    principal = reader.id if principal_form == "graph" else identity.id
    scope = ExecutionScope.create(principal_id=principal, workspace_id=workspace.id)
    resource = Resource(
        kind="operational_model",
        id=track.attached_operational_model_id,
        scope=f"track:{track.id}",
    )
    module = PolicyModule()
    assert (
        await module.evaluate(
            scope=scope, action="operational_model.read", resource=resource
        )
    ).allowed
    identity.is_active = False
    await identity.save()
    assert not (
        await module.evaluate(
            scope=scope, action="operational_model.read", resource=resource
        )
    ).allowed


@pytest.mark.asyncio
async def test_model_module_preserves_ordinary_app_authority():
    from app.models.edges import OWNS
    from app.models.nodes import User
    from app.services.app_graph import catalog_user

    caller, workspace, app, track, _ = await graph()
    context = await track.get_context()
    for resource in [app, track]:
        owns = await context.find_edges_between(caller.id, resource.id, edge_class=OWNS)
        assert len(owns) == 1
        await owns[0].delete()
    curator = await User.create(display_name="Private model curator")
    await catalog_user(curator)
    await curator.connect(workspace, edge=IS_MEMBER_OF, role="member")
    await curator.connect(app, edge=OWNS)
    await curator.connect(track, edge=OWNS)
    resource = Resource(
        kind="operational_model",
        id=track.attached_operational_model_id,
        scope=f"track:{track.id}",
    )
    module = PolicyModule()
    core = ExecutionScope.create(principal_id=caller.id, workspace_id=workspace.id)
    ordinary = ExecutionScope.create(
        principal_id=caller.id, workspace_id=workspace.id, origin="app_operation"
    )
    assert (
        await module.evaluate(
            scope=core, action="operational_model.update", resource=resource
        )
    ).allowed
    assert not (
        await module.evaluate(
            scope=ordinary, action="operational_model.read", resource=resource
        )
    ).allowed
    await caller.connect(track, edge=COLLABORATES_ON, role="viewer")
    assert (
        await module.evaluate(
            scope=ordinary, action="operational_model.read", resource=resource
        )
    ).allowed
    assert not (
        await module.evaluate(
            scope=ordinary, action="operational_model.update", resource=resource
        )
    ).allowed


@pytest.mark.asyncio
@pytest.mark.parametrize("family", ["platform", "workspace"])
@pytest.mark.parametrize("corruption", ["none", "missing_scope", "conflicting_scope"])
async def test_library_model_requires_explicit_named_publication_or_ownership(
    family, corruption
):
    from app.models.edges import CATALOGS, HAS_LIBRARY_MODEL, PUBLISHES_LIBRARY_MODEL
    from app.models.nodes import (
        APP_NODE_ID,
        OPERATIONAL_MODELS_REGISTRY_ID,
        IntegralApp,
        OperationalModels,
    )

    caller, workspace, _, _, _ = await graph()
    registry = await OperationalModels.get(OPERATIONAL_MODELS_REGISTRY_ID)
    prime = await IntegralApp.get(APP_NODE_ID)
    assert registry is not None and prime is not None
    model = await OperationalModel.create(
        name="Declared library fixture",
        scope="platform" if family == "platform" else "organization",
        workspace_id=None if family == "platform" else workspace.id,
        library_package=True,
    )
    await registry.connect(model, edge=CATALOGS)
    if family == "platform":
        declaration = await prime.connect(model, edge=PUBLISHES_LIBRARY_MODEL)
    else:
        declaration = await workspace.connect(model, edge=HAS_LIBRARY_MODEL)
    resource = Resource(
        kind="operational_model", id=model.id, scope=f"workspace:{workspace.id}"
    )
    scope = ExecutionScope.create(principal_id=caller.id, workspace_id=workspace.id)
    module = PolicyModule()
    assert (
        await module.evaluate(
            scope=scope, action="operational_model.read", resource=resource
        )
    ).allowed
    update = await module.evaluate(
        scope=scope, action="operational_model.update", resource=resource
    )
    assert update.allowed is (family == "workspace")
    if corruption == "missing_scope":
        await declaration.delete()
    elif corruption == "conflicting_scope":
        if family == "platform":
            await workspace.connect(model, edge=HAS_LIBRARY_MODEL)
        else:
            # Erasing the scalar cannot publish private content.
            model.workspace_id = None
            await model.save()
    decision = await module.evaluate(
        scope=scope, action="operational_model.read", resource=resource
    )
    assert decision.allowed is (corruption == "none")


@pytest.mark.asyncio
async def test_private_library_guest_requires_direct_model_grant_and_current_membership():
    from app.models.edges import CATALOGS, HAS_LIBRARY_MODEL
    from app.models.nodes import OPERATIONAL_MODELS_REGISTRY_ID, OperationalModels

    caller, _, _, _, _ = await graph()
    _, workspace, _, _, _ = await graph()
    registry = await OperationalModels.get(OPERATIONAL_MODELS_REGISTRY_ID)
    model = await OperationalModel.create(
        name="Private library",
        scope="organization",
        workspace_id=workspace.id,
        library_package=True,
    )
    await registry.connect(model, edge=CATALOGS)
    await workspace.connect(model, edge=HAS_LIBRARY_MODEL)
    membership = await caller.connect(workspace, edge=IS_MEMBER_OF, role="guest")
    scope = ExecutionScope.create(principal_id=caller.id, workspace_id=workspace.id)
    resource = Resource(
        kind="operational_model", id=model.id, scope=f"workspace:{workspace.id}"
    )
    module = PolicyModule()
    assert not (
        await module.evaluate(
            scope=scope, action="operational_model.read", resource=resource
        )
    ).allowed
    await caller.connect(model, edge=COLLABORATES_ON, role="viewer")
    assert (
        await module.evaluate(
            scope=scope, action="operational_model.read", resource=resource
        )
    ).allowed
    assert not (
        await module.evaluate(
            scope=scope, action="operational_model.update", resource=resource
        )
    ).allowed
    await membership.delete()
    assert not (
        await module.evaluate(
            scope=scope, action="operational_model.read", resource=resource
        )
    ).allowed


@pytest.mark.asyncio
@pytest.mark.parametrize("family", ["platform", "workspace", "attached"])
async def test_published_model_viewer_does_not_automatically_read_pending_drafts(
    family,
):
    from app.models.edges import CATALOGS, HAS_LIBRARY_MODEL, PUBLISHES_LIBRARY_MODEL
    from app.models.nodes import (
        APP_NODE_ID,
        OPERATIONAL_MODELS_REGISTRY_ID,
        IntegralApp,
        OperationalModels,
    )
    from app.services.operational_model_atomic_swap import fork_draft

    caller, _, _, _, _ = await graph()
    _, workspace, _, track, _ = await graph()
    membership = await caller.connect(workspace, edge=IS_MEMBER_OF, role="guest")
    if family == "attached":
        published = await OperationalModel.get(track.attached_operational_model_id)
        await caller.connect(track, edge=COLLABORATES_ON, role="viewer")
        scope_string = f"track:{track.id}"
    else:
        published = await OperationalModel.create(
            name="Pending publication parent",
            scope="platform" if family == "platform" else "organization",
            workspace_id=None if family == "platform" else workspace.id,
            library_package=True,
        )
        registry = await OperationalModels.get(OPERATIONAL_MODELS_REGISTRY_ID)
        await registry.connect(published, edge=CATALOGS)
        if family == "platform":
            prime = await IntegralApp.get(APP_NODE_ID)
            await prime.connect(published, edge=PUBLISHES_LIBRARY_MODEL)
        else:
            await workspace.connect(published, edge=HAS_LIBRARY_MODEL)
        await caller.connect(published, edge=COLLABORATES_ON, role="viewer")
        scope_string = f"workspace:{workspace.id}"
    scope = ExecutionScope.create(principal_id=caller.id, workspace_id=workspace.id)
    module = PolicyModule()
    resource = Resource(kind="operational_model", id=published.id, scope=scope_string)
    assert (
        await module.evaluate(
            scope=scope, action="operational_model.read", resource=resource
        )
    ).allowed
    draft = await fork_draft(published=published)
    resource = Resource(kind="operational_model", id=draft.id, scope=scope_string)
    assert not (
        await module.evaluate(
            scope=scope, action="operational_model.read", resource=resource
        )
    ).allowed
    await caller.connect(draft, edge=COLLABORATES_ON, role="viewer")
    assert (
        await module.evaluate(
            scope=scope, action="operational_model.read", resource=resource
        )
    ).allowed
    assert not (
        await module.evaluate(
            scope=scope, action="operational_model.update", resource=resource
        )
    ).allowed
    await membership.delete()
    assert not (
        await module.evaluate(
            scope=scope, action="operational_model.read", resource=resource
        )
    ).allowed


@pytest.mark.asyncio
@pytest.mark.parametrize("workspace_role", ["member", "admin"])
async def test_initial_private_library_draft_requires_configuration_or_explicit_review(
    workspace_role,
):
    from app.models.edges import CATALOGS, HAS_LIBRARY_MODEL
    from app.models.nodes import OPERATIONAL_MODELS_REGISTRY_ID, OperationalModels

    owner, workspace, _, _, _ = await graph()
    reader, _, _, _, _ = await graph()
    await reader.connect(
        workspace, edge=IS_MEMBER_OF, role=workspace_role, can_create_apps=True
    )
    registry = await OperationalModels.get(OPERATIONAL_MODELS_REGISTRY_ID)
    draft = await OperationalModel.create(
        name="Initial unpublished private model",
        scope="organization",
        workspace_id=workspace.id,
        library_package=True,
        status="draft",
    )
    await registry.connect(draft, edge=CATALOGS)
    await workspace.connect(draft, edge=HAS_LIBRARY_MODEL)
    resource = Resource(
        kind="operational_model", id=draft.id, scope=f"workspace:{workspace.id}"
    )
    owner_scope = ExecutionScope.create(
        principal_id=owner.id, workspace_id=workspace.id
    )
    reader_scope = ExecutionScope.create(
        principal_id=reader.id, workspace_id=workspace.id
    )
    module = PolicyModule()
    assert (
        await module.evaluate(
            scope=owner_scope, action="operational_model.read", resource=resource
        )
    ).allowed
    assert (
        await module.evaluate(
            scope=owner_scope, action="operational_model.update", resource=resource
        )
    ).allowed
    assert not (
        await module.evaluate(
            scope=reader_scope, action="operational_model.read", resource=resource
        )
    ).allowed
    assert not (
        await module.evaluate(
            scope=reader_scope, action="operational_model.update", resource=resource
        )
    ).allowed

    await reader.connect(draft, edge=COLLABORATES_ON, role="viewer")
    assert (
        await module.evaluate(
            scope=reader_scope, action="operational_model.read", resource=resource
        )
    ).allowed
    assert not (
        await module.evaluate(
            scope=reader_scope, action="operational_model.update", resource=resource
        )
    ).allowed


@pytest.mark.asyncio
@pytest.mark.parametrize("family", ["platform", "workspace"])
@pytest.mark.parametrize("corruption", ["none", "missing_template", "extra_owner"])
async def test_library_template_requires_complete_named_library_provenance(
    family,
    corruption,
):
    from app.models.edges import (
        CATALOGS,
        DEFINES_TRACK_PROFILE,
        HAS_LIBRARY_MODEL,
        PUBLISHES_LIBRARY_MODEL,
    )
    from app.models.nodes import (
        APP_NODE_ID,
        OPERATIONAL_MODELS_REGISTRY_ID,
        IntegralApp,
        OperationalModels,
    )

    owner, workspace, _, _, _ = await graph()
    registry = await OperationalModels.get(OPERATIONAL_MODELS_REGISTRY_ID)
    library = await OperationalModel.create(
        name="Library template authority",
        scope="platform" if family == "platform" else "organization",
        workspace_id=None if family == "platform" else workspace.id,
        library_package=True,
    )
    await registry.connect(library, edge=CATALOGS)
    if family == "platform":
        prime = await IntegralApp.get(APP_NODE_ID)
        await prime.connect(library, edge=PUBLISHES_LIBRARY_MODEL)
    else:
        await workspace.connect(library, edge=HAS_LIBRARY_MODEL)
    template = await OperationalModel.create(
        name="Library Track template", scope="track"
    )
    link = await library.connect(template, edge=DEFINES_TRACK_PROFILE)
    resource = Resource(
        kind="operational_model", id=template.id, scope=f"workspace:{workspace.id}"
    )
    scope = ExecutionScope.create(principal_id=owner.id, workspace_id=workspace.id)
    module = PolicyModule()
    assert (
        await module.evaluate(
            scope=scope, action="operational_model.read", resource=resource
        )
    ).allowed
    assert (
        await module.evaluate(
            scope=scope, action="operational_model.update", resource=resource
        )
    ).allowed is (family == "workspace")
    if corruption == "missing_template":
        await link.delete()
    elif corruption == "extra_owner":
        _, other_workspace, _, _, _ = await graph()
        await other_workspace.connect(library, edge=HAS_LIBRARY_MODEL)
    assert (
        await module.evaluate(
            scope=scope, action="operational_model.read", resource=resource
        )
    ).allowed is (corruption == "none")
