"""Relation writes require current target authority and declared containment."""

import pytest

from app.exceptions import BadRequestError
from app.middleware.permissions_cache import reset_permissions_cache
from app.models.edges import COLLABORATES_ON, CONTAINS, IS_MEMBER_OF, OWNS, REFERENCES
from app.models.nodes import App, Entry, EntryType, Track, Workspace
from app.schemas.policy import Subject
from app.services.app_graph import catalog_app, catalog_track, catalog_workspace
from app.services.permissions import can_view_entry
from app.services.relation_runtime import validate_cross_app_relation_set

pytestmark = [pytest.mark.asyncio, pytest.mark.smoke]


async def _fixture(writer, owner):
    ws = await Workspace.create(name="Relation admission", kind="organization")
    await catalog_workspace(ws)
    await writer.connect(ws, edge=IS_MEMBER_OF, role="member")
    await owner.connect(ws, edge=IS_MEMBER_OF, role="owner")
    source = await Track.create(
        title="Source",
        owner_id=writer.user_id,
        workspace_id=ws.id,
        visibility="private",
    )
    await catalog_track(source)
    target_track = await Track.create(
        title="Targets",
        owner_id=owner.user_id,
        workspace_id=ws.id,
        visibility="private",
    )
    await catalog_track(target_track)
    await ws.connect(source, edge=CONTAINS)
    await ws.connect(target_track, edge=CONTAINS)
    await writer.connect(source, edge=OWNS)
    await owner.connect(target_track, edge=OWNS)
    source_type = await EntryType.create(
        name="Source Record",
        track_id=source.id,
        form_schema={
            "fields": [
                {
                    "key": "related",
                    "type": "relation",
                    "relation": {"target": "entry", "allow_cross_track": True},
                }
            ]
        },
    )
    await source.connect(source_type, edge=CONTAINS)
    target = await Entry.create(
        title="Private target", track_id=target_track.id, author_id=owner.user_id
    )
    await target_track.connect(target, edge=CONTAINS)
    reset_permissions_cache()
    return ws, source, source_type, target_track, target


@pytest.mark.parametrize("many", [False, True])
async def test_create_refuses_unreadable_same_workspace_target(
    many, test_user, test_user2
):
    from app.services.entry_create import create_entry_in_track

    ws, source, source_type, _, target = await _fixture(test_user, test_user2)
    source_type.form_schema["fields"][0]["relation"]["many"] = many
    await source_type.save()
    assert not await can_view_entry(test_user.user_id, target.id)
    before = await source.nodes(edge=[CONTAINS], node=["Entry"], limit=10)
    with pytest.raises(BadRequestError, match="read|access"):
        await create_entry_in_track(
            track=source,
            entry_type=source_type,
            user_id=test_user.user_id,
            workspace_id=ws.id,
            title="Refused",
            custom_fields={"related": [target.id] if many else target.id},
            skip_profanity=True,
        )
    after = await source.nodes(edge=[CONTAINS], node=["Entry"], limit=10)
    assert {e.id for e in before} == {e.id for e in after}


@pytest.mark.parametrize("many", [False, True])
async def test_update_rechecks_revoked_target_before_revision_or_edges(
    many, test_user, test_user2
):
    from app.services.entry_create import create_entry_in_track
    from app.services.entry_update import update_entry_in_track

    ws, source, source_type, _, target = await _fixture(test_user, test_user2)
    source_type.form_schema["fields"][0]["relation"]["many"] = many
    await source_type.save()
    grant = await test_user.connect(target, edge=COLLABORATES_ON, role="viewer")
    reset_permissions_cache()
    assert await can_view_entry(test_user.user_id, target.id)
    entry = await create_entry_in_track(
        track=source,
        entry_type=source_type,
        user_id=test_user.user_id,
        workspace_id=ws.id,
        title="Retained",
        custom_fields={"related": [target.id] if many else target.id},
        skip_profanity=True,
    )
    graph = await entry.get_context()
    old_edges = await graph.find_edges_between(entry.id, None, edge_class=REFERENCES)
    old_revision = entry.record_revision
    old_fields = dict(entry.custom_fields)
    await grant.delete()
    reset_permissions_cache()
    assert not await can_view_entry(test_user.user_id, target.id)
    with pytest.raises(BadRequestError, match="read|access"):
        await update_entry_in_track(
            entry_id=entry.id,
            user_id=test_user.user_id,
            workspace_id=ws.id,
            custom_fields={"related": [target.id] if many else target.id},
            expected_record_revision=old_revision,
        )
    await graph._evict_from_cache(entry.id)
    restored = await Entry.get(entry.id)
    assert restored.record_revision == old_revision
    assert restored.custom_fields == old_fields
    new_edges = await graph.find_edges_between(entry.id, None, edge_class=REFERENCES)
    assert {e.id for e in old_edges} == {e.id for e in new_edges}


async def _apps(ws, source, target_track, user):
    source_app = await App.create(
        name="consumer", workspace_id=ws.id, owner_user_id=user.user_id
    )
    await catalog_app(source_app)
    provider = await App.create(
        name="provider", workspace_id=ws.id, owner_user_id=user.user_id
    )
    await catalog_app(provider)
    rogue = await App.create(
        name="rogue", workspace_id=ws.id, owner_user_id=user.user_id
    )
    await catalog_app(rogue)
    for app in (source_app, provider, rogue):
        await ws.connect(app, edge=CONTAINS)
        await user.connect(app, edge=OWNS)
    await source_app.connect(source, edge=CONTAINS)
    await rogue.connect(target_track, edge=CONTAINS)
    return source_app, provider, rogue


async def test_cross_app_validator_refuses_readable_target_in_wrong_app(
    test_user, test_user2
):
    ws, source, _, target_track, target = await _fixture(test_user, test_user2)
    source_app, _, _ = await _apps(ws, source, target_track, test_user)
    with pytest.raises(BadRequestError, match="contain|belong|App"):
        await validate_cross_app_relation_set(
            source_app=source_app,
            relation_spec={"target_app": "provider", "allow_cross_app": True},
            target_entry_id=target.id,
            viewer_subject=Subject(kind="system", id="test-job"),
        )


async def test_shared_create_enforces_declared_app_not_just_type_or_workspace(
    test_user, test_user2
):
    from app.services.entry_create import create_entry_in_track

    ws, source, source_type, target_track, target = await _fixture(
        test_user, test_user2
    )
    await _apps(ws, source, target_track, test_user)
    await test_user.connect(target, edge=COLLABORATES_ON, role="viewer")
    source_type.form_schema["fields"][0]["relation"].update(
        target_app="provider", allow_cross_app=True
    )
    await source_type.save()
    reset_permissions_cache()
    assert await can_view_entry(test_user.user_id, target.id)
    with pytest.raises(BadRequestError, match="contain|belong|App"):
        await create_entry_in_track(
            track=source,
            entry_type=source_type,
            user_id=test_user.user_id,
            workspace_id=ws.id,
            title="Wrong provider",
            custom_fields={"related": target.id},
            skip_profanity=True,
        )


async def test_authorized_cross_app_write_stamps_canonical_target_app(
    test_user, test_user2
):
    from app.services.entry_create import create_entry_in_track

    ws, source, source_type, target_track, target = await _fixture(
        test_user, test_user2
    )
    _, provider, rogue = await _apps(ws, source, target_track, test_user)
    graph = await target_track.get_context()
    for edge in await graph.find_edges_between(
        rogue.id, target_track.id, edge_class=CONTAINS
    ):
        await edge.delete()
    await provider.connect(target_track, edge=CONTAINS)
    await test_user.connect(target, edge=COLLABORATES_ON, role="viewer")
    source_type.form_schema["fields"][0]["relation"].update(
        target_app="provider", allow_cross_app=True
    )
    await source_type.save()
    reset_permissions_cache()
    entry = await create_entry_in_track(
        track=source,
        entry_type=source_type,
        user_id=test_user.user_id,
        workspace_id=ws.id,
        title="Authorized",
        custom_fields={"related": target.id},
        skip_profanity=True,
        actor_kind="agent",
        actor_id="delegated-provenance",
        authorization_subject=Subject(kind="human", id=test_user.user_id),
    )
    edges = await graph.find_edges_between(entry.id, target.id, edge_class=REFERENCES)
    assert len(edges) == 1
    assert edges[0].target_app_id == provider.id


@pytest.mark.parametrize("kind", ["agent", "connector"])
async def test_independent_actor_does_not_borrow_human_target_grant(
    kind, test_user, test_user2
):
    from app.services.entry_create import create_entry_in_track

    ws, source, source_type, _, target = await _fixture(test_user, test_user2)
    await test_user.connect(target, edge=COLLABORATES_ON, role="viewer")
    reset_permissions_cache()
    assert await can_view_entry(test_user.user_id, target.id)
    from app.agentive.edges import HAS_ORG_AGENT
    from app.agentive.edges import OWNS as OWNS_CONNECTOR
    from app.agentive.nodes import AgentConfig, Connector
    from app.models.edges import HAS_CONNECTOR, IS_CONNECTED_TO
    from app.services.policy_registry import create_policy

    if kind == "agent":
        actor = await AgentConfig.create(
            scope="org_facing", facet="org_facing", workspace_id=ws.id, is_active=True
        )
        await ws.connect(actor, edge=HAS_ORG_AGENT)
    else:
        actor = await Connector.create(owner=test_user.id, workspace_id=ws.id)
        await test_user.connect(actor, edge=OWNS_CONNECTOR)
        await ws.connect(actor, edge=HAS_CONNECTOR)
        await actor.connect(source, edge=IS_CONNECTED_TO)
    await create_policy(
        subject_kind=kind,
        subject_id=actor.id,
        scope=f"track:{source.id}",
        actions=["entry.create"],
        created_by=test_user.id,
    )
    with pytest.raises(BadRequestError, match="read|access"):
        await create_entry_in_track(
            track=source,
            entry_type=source_type,
            user_id=test_user.user_id,
            workspace_id=ws.id,
            title="Independent denied",
            custom_fields={"related": target.id},
            skip_profanity=True,
            actor_kind=kind,
            authorization_subject=Subject(kind=kind, id=actor.id),
        )


async def test_explicit_system_subject_still_requires_canonical_scope(
    test_user, test_user2
):
    from app.services.entry_create import create_entry_in_track
    from app.services.entry_update import update_entry_in_track

    ws, source, source_type, _, target = await _fixture(test_user, test_user2)
    entry = await create_entry_in_track(
        track=source,
        entry_type=source_type,
        user_id=test_user.user_id,
        workspace_id=ws.id,
        title="System job",
        custom_fields={"related": target.id},
        skip_profanity=True,
        actor_kind="system",
        authorization_subject=Subject(kind="system", id="qualified-job"),
    )
    assert entry.custom_fields["related"] == target.id
    target.track_id = source.id
    await target.save()
    with pytest.raises(BadRequestError, match="containment"):
        await update_entry_in_track(
            entry_id=entry.id,
            user_id=test_user.user_id,
            workspace_id=ws.id,
            custom_fields={"related": target.id},
            authorization_subject=Subject(kind="system", id="qualified-job"),
        )


async def test_mixed_many_target_denial_preserves_existing_record(
    test_user, test_user2
):
    from app.services.entry_create import create_entry_in_track
    from app.services.entry_update import update_entry_in_track

    ws, source, source_type, target_track, private = await _fixture(
        test_user, test_user2
    )
    allowed = await Entry.create(
        title="Allowed", track_id=target_track.id, author_id=test_user2.user_id
    )
    await target_track.connect(allowed, edge=CONTAINS)
    await test_user.connect(allowed, edge=COLLABORATES_ON, role="viewer")
    source_type.form_schema["fields"][0]["relation"]["many"] = True
    await source_type.save()
    entry = await create_entry_in_track(
        track=source,
        entry_type=source_type,
        user_id=test_user.user_id,
        workspace_id=ws.id,
        title="Retained many",
        custom_fields={"related": [allowed.id]},
        skip_profanity=True,
    )
    old_fields, old_revision = dict(entry.custom_fields), entry.record_revision
    graph = await entry.get_context()
    old_edges = await graph.find_edges_between(entry.id, None, edge_class=REFERENCES)
    with pytest.raises(BadRequestError, match="read|access"):
        await update_entry_in_track(
            entry_id=entry.id,
            user_id=test_user.user_id,
            workspace_id=ws.id,
            custom_fields={"related": [allowed.id, private.id]},
        )
    await graph._evict_from_cache(entry.id)
    restored = await Entry.get(entry.id)
    assert (restored.custom_fields, restored.record_revision) == (
        old_fields,
        old_revision,
    )
    new_edges = await graph.find_edges_between(entry.id, None, edge_class=REFERENCES)
    assert {e.id for e in old_edges} == {e.id for e in new_edges}


@pytest.mark.parametrize(
    "method", ["create_entry", "update_entry_fields", "conditional_update_entry_fields"]
)
async def test_operation_facade_cannot_link_an_unreadable_target(
    method, test_user, test_user2
):
    from app.services.app_operations.context import OperationContext
    from app.services.entry_create import create_entry_in_track

    ws, source, source_type, _, target = await _fixture(test_user, test_user2)
    app = await App.create(
        name="Writer app", workspace_id=ws.id, owner_user_id=test_user.user_id
    )
    await catalog_app(app)
    await ws.connect(app, edge=CONTAINS)
    await app.connect(source, edge=CONTAINS)
    await test_user.connect(app, edge=OWNS)
    entry = await create_entry_in_track(
        track=source,
        entry_type=source_type,
        user_id=test_user.user_id,
        workspace_id=ws.id,
        title="Operation record",
        custom_fields={},
        skip_profanity=True,
    )
    fields, revision = dict(entry.custom_fields), entry.record_revision
    context = OperationContext(
        user_id=test_user.user_id,
        workspace_id=ws.id,
        app_id=app.id,
        scope=f"app:{app.id}",
    )
    if method == "create_entry":
        assert (
            await context.create_entry(
                track_id=source.id,
                entry_type_key="source_record",
                title="Refused operation",
                custom_fields={"related": target.id},
            )
            is None
        )
    else:
        with pytest.raises(BadRequestError, match="read|access"):
            if method == "update_entry_fields":
                await context.update_entry_fields(entry.id, {"related": target.id})
            else:
                await context.conditional_update_entry_fields(
                    entry.id,
                    state_field="related",
                    expected_state=None,
                    updates={"related": target.id},
                )
    graph = await entry.get_context()
    await graph._evict_from_cache(entry.id)
    restored = await Entry.get(entry.id)
    assert (restored.custom_fields, restored.record_revision) == (fields, revision)
    assert (
        await graph.find_edges_between(entry.id, target.id, edge_class=REFERENCES) == []
    )


@pytest.mark.parametrize("mismatch", ["app", "entry_type"])
async def test_labeled_relation_does_not_create_before_declared_target_admission(
    mismatch, test_user, test_user2
):
    from app.services.entry_create import create_entry_in_track

    ws, source, source_type, target_track, _ = await _fixture(test_user, test_user2)
    source_app, provider, rogue = await _apps(ws, source, target_track, test_user)
    graph = await target_track.get_context()
    for edge in await graph.find_edges_between(
        rogue.id, target_track.id, edge_class=CONTAINS
    ):
        await edge.delete()
    await (source_app if mismatch == "app" else provider).connect(
        target_track, edge=CONTAINS
    )
    target_track.template_id = "targets"
    await target_track.save()
    target_type = await EntryType.create(
        name="Target Record",
        track_id=target_track.id,
        form_schema={"fields": [{"key": "company_name", "type": "text"}]},
    )
    await target_track.connect(target_type, edge=CONTAINS)
    field = source_type.form_schema["fields"][0]
    field.update(
        required=True, validation={"create_from_label": True, "write_label_to": "name"}
    )
    field["relation"].update(
        target_app="provider",
        allow_cross_app=True,
        target_track_types=["targets"],
        target_entry_types=["target_record"],
    )
    if mismatch == "entry_type":
        field["relation"]["target_entry_types"] = ["absent_type"]
    source_type.form_schema["fields"].append({"key": "name", "type": "text"})
    await source_type.save()
    reset_permissions_cache()
    before = await target_track.nodes(edge=[CONTAINS], node=["Entry"], limit=10)
    with pytest.raises(BadRequestError):
        await create_entry_in_track(
            track=source,
            entry_type=source_type,
            user_id=test_user.user_id,
            workspace_id=ws.id,
            title="Refused inferred link",
            custom_fields={"name": "New bound record"},
            skip_profanity=True,
        )
    after = await target_track.nodes(edge=[CONTAINS], node=["Entry"], limit=10)
    assert {e.id for e in after} == {e.id for e in before}


@pytest.mark.postgres
@pytest.mark.parametrize("failure", ["later_field", "source_hook"])
async def test_failed_source_create_rolls_back_inferred_target_and_retry(
    failure, test_user, monkeypatch
):
    """Nested label creation commits only with its complete source command."""
    from app.services import entry_create

    ws, source, source_type, target_track, target = await _fixture(test_user, test_user)
    _, provider, rogue = await _apps(ws, source, target_track, test_user)
    graph = await target_track.get_context()
    for edge in await graph.find_edges_between(
        rogue.id, target_track.id, edge_class=CONTAINS
    ):
        await edge.delete()
    await provider.connect(target_track, edge=CONTAINS)
    target_track.template_id = "targets"
    await target_track.save()
    target_type = await EntryType.create(
        name="Target Record",
        track_id=target_track.id,
        form_schema={"fields": [{"key": "company_name", "type": "text"}]},
    )
    await target_track.connect(target_type, edge=CONTAINS)
    field = source_type.form_schema["fields"][0]
    field.update(
        required=True, validation={"create_from_label": True, "write_label_to": "name"}
    )
    field["relation"].update(
        target_app="provider",
        allow_cross_app=True,
        target_track_types=["targets"],
        target_entry_types=["target_record"],
    )
    source_type.form_schema["fields"].extend(
        [
            {"key": "name", "type": "text"},
            {"key": "state", "type": "select", "enum": ["ready"]},
        ]
    )
    await source_type.save()
    reset_permissions_cache()
    before_target = await target.export(flat=True)
    before_outbox = await graph.database.find(
        "object", {"entity": "OperationEventOutbox"}
    )
    inferred_ids = []
    original_create = entry_create.create_entry_in_track
    original_hooks = entry_create.run_entry_save_hooks

    async def observe_create(**kwargs):
        created = await original_create(**kwargs)
        if kwargs["track"].id == target_track.id:
            inferred_ids.append(created.id)
        return created

    async def fail_source_hook(**kwargs):
        if kwargs["entry"].track_id == source.id:
            raise RuntimeError("source hook rejected after related creation")
        return await original_hooks(**kwargs)

    monkeypatch.setattr(entry_create, "create_entry_in_track", observe_create)
    if failure == "source_hook":
        monkeypatch.setattr(entry_create, "run_entry_save_hooks", fail_source_hook)
    payload = {
        "name": "Transactional related record",
        "state": "invalid" if failure == "later_field" else "ready",
    }
    error_type = BadRequestError if failure == "later_field" else RuntimeError
    error_message = "state" if failure == "later_field" else "source hook rejected"
    with pytest.raises(error_type, match=error_message):
        await entry_create.create_entry_in_track(
            track=source,
            entry_type=source_type,
            user_id=test_user.user_id,
            workspace_id=ws.id,
            title="Transactional source record",
            custom_fields=payload,
            skip_profanity=True,
        )
    assert len(inferred_ids) == 1, "the test must reach real nested target creation"
    assert await Entry.get(inferred_ids[0]) is None
    assert await Entry.find({"context.track_id": source.id}) == []
    targets = await Entry.find({"context.track_id": target_track.id})
    assert {entry.id for entry in targets} == {target.id}
    assert await (await Entry.get(target.id)).export(flat=True) == before_target
    assert (
        await graph.database.find("object", {"entity": "OperationEventOutbox"})
        == before_outbox
    ), "failed creation must not leave a deliverable event fact"
    assert (
        await graph.find_edges_between(
            target_track.id, inferred_ids[0], edge_class=CONTAINS
        )
        == []
    )

    monkeypatch.setattr(entry_create, "run_entry_save_hooks", original_hooks)
    payload["state"] = "ready"
    created = await entry_create.create_entry_in_track(
        track=source,
        entry_type=source_type,
        user_id=test_user.user_id,
        workspace_id=ws.id,
        title="Transactional source record",
        custom_fields=payload,
        skip_profanity=True,
    )
    inferred = await Entry.get(created.custom_fields["related"])
    assert inferred is not None and inferred.id != inferred_ids[0]
    assert inferred.title == payload["name"]
    assert inferred.author_id == test_user.user_id
    assert {
        entry.id for entry in await Entry.find({"context.track_id": source.id})
    } == {created.id}
    assert {
        entry.id for entry in await Entry.find({"context.track_id": target_track.id})
    } == {target.id, inferred.id}
    edges = await graph.find_edges_between(
        created.id, inferred.id, edge_class=REFERENCES
    )
    assert len(edges) == 1 and edges[0].target_app_id == provider.id


@pytest.mark.parametrize("path", ["facade", "direct", "unreadable", "wrong_app"])
async def test_readonly_relation_app_write_keeps_target_admission(
    path, test_user, test_user2
):
    """App-owned computed links use the facade; ordinary edits stay readonly."""
    from app.services.app_operations.context import OperationContext
    from app.services.entry_create import create_entry_in_track
    from app.services.entry_update import update_entry_in_track

    ws, source, source_type, target_track, target = await _fixture(
        test_user, test_user2
    )
    app = await App.create(
        name="Computed relation owner",
        workspace_id=ws.id,
        owner_user_id=test_user.user_id,
    )
    await catalog_app(app)
    await ws.connect(app, edge=CONTAINS)
    await app.connect(source, edge=CONTAINS)
    if path != "unreadable":
        await app.connect(target_track, edge=CONTAINS)
    await test_user.connect(app, edge=OWNS)
    source_type.form_schema["fields"][0]["readonly"] = True
    await source_type.save()
    if path != "unreadable":
        await test_user.connect(target, edge=COLLABORATES_ON, role="viewer")
    reset_permissions_cache()
    entry = await create_entry_in_track(
        track=source,
        entry_type=source_type,
        user_id=test_user.user_id,
        workspace_id=ws.id,
        title="Computed link",
        custom_fields={},
        skip_profanity=True,
    )
    context = OperationContext(
        user_id=test_user.user_id,
        workspace_id=ws.id,
        app_id=app.id,
        scope=f"app:{app.id}",
    )
    if path == "wrong_app":
        context.app_id = source.id
        assert not await context.update_entry_fields(entry.id, {"related": target.id})
    elif path == "direct":
        with pytest.raises(BadRequestError, match="readonly"):
            await update_entry_in_track(
                entry_id=entry.id,
                user_id=test_user.user_id,
                workspace_id=ws.id,
                custom_fields={"related": target.id},
                expected_record_revision=entry.record_revision,
            )
    elif path == "unreadable":
        with pytest.raises(BadRequestError, match="read|access"):
            await context.update_entry_fields(entry.id, {"related": target.id})
    else:
        assert await context.update_entry_fields(entry.id, {"related": target.id})
    graph = await entry.get_context()
    await graph._evict_from_cache(entry.id)
    saved = await Entry.get(entry.id)
    assert saved.custom_fields.get("related") == (
        target.id if path == "facade" else None
    )
