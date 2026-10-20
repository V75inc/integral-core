"""Installing Apps stay private while exact installer seed relations validate."""

import asyncio

import pytest

from app.models.edges import (
    CONTAINS,
    EXCLUDED_FROM,
    IS_MEMBER_OF,
    IS_OF_TYPE,
    REFERENCES,
)
from app.models.nodes import App, Entry, Track
from app.schemas.policy import Decision, Resource, Subject
from app.services.app_graph import catalog_app, catalog_track, wire_app_owner
from app.services.install_seed_authority import (
    install_seed_read_scope,
    may_read_install_seed_relation,
)
from app.services.policy_engine import evaluate
from app.utils.time import utc_now_iso
from tests.test_apps_batch_install import _make_workspace, _seed_library

pytestmark = pytest.mark.smoke


async def resources(user):
    workspace = await _make_workspace("Seed authority")
    await user.connect(
        workspace, edge=IS_MEMBER_OF, role="owner", added_at=utc_now_iso()
    )
    app = await App.create(
        name="Seed bundle", workspace_id=workspace.id, lifecycle_state="installing"
    )
    await wire_app_owner(app, user.id, workspace_id=workspace.id)
    await catalog_app(app)
    source = await Track.create(
        title="Source", workspace_id=workspace.id, app_id=app.id
    )
    target = await Track.create(
        title="Target", workspace_id=workspace.id, app_id=app.id
    )
    await app.connect(source, edge=CONTAINS)
    await app.connect(target, edge=CONTAINS)
    await catalog_track(source)
    await catalog_track(target)
    entry = await Entry.create(title="Related seed", track_id=target.id)
    await target.connect(entry, edge=CONTAINS)
    return workspace, app, source, target, entry


async def allowed(
    user, source, target, *, kind="entry", reason="default_human_policy_denied"
):
    return await may_read_install_seed_relation(
        Decision(allowed=False, reason=reason),
        Subject(kind="human", id=user.id),
        source_track_id=source.id,
        target_kind=kind,
        target_id=target.id,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["installing", "awaiting_settings"])
async def test_only_installer_effect_reads_its_own_installing_app(test_user, state):
    _, app, source, target, entry = await resources(test_user)
    app.lifecycle_state = state
    await app.save()
    subject = Subject(kind="human", id=test_user.id)
    resource = Resource(kind="entry", id=entry.id, scope=f"track:{target.id}")
    assert not (
        await evaluate(subject=subject, action="entry.read", resource=resource)
    ).allowed
    assert not await allowed(test_user, source, entry)
    with install_seed_read_scope(app.id, test_user.id):
        assert await allowed(test_user, source, entry)
        assert await allowed(test_user, source, target, kind="track")
        # No global permission bypass, even in the same task.
        assert not (
            await evaluate(subject=subject, action="entry.read", resource=resource)
        ).allowed
    assert not await allowed(test_user, source, entry)


@pytest.mark.asyncio
async def test_context_does_not_transfer_to_child_tasks(test_user):
    _, app, source, _, entry = await resources(test_user)
    with install_seed_read_scope(app.id, test_user.id):
        assert not await asyncio.create_task(allowed(test_user, source, entry))
        assert await allowed(test_user, source, entry)


@pytest.mark.asyncio
async def test_scope_never_crosses_app_or_workspace(test_user):
    _, app, source, _, entry = await resources(test_user)
    _, other, other_source, _, other_entry = await resources(test_user)
    with install_seed_read_scope(app.id, test_user.id):
        assert not await allowed(test_user, source, other_entry)
        assert not await allowed(test_user, other_source, entry)
    with install_seed_read_scope(other.id, test_user.id):
        assert not await allowed(test_user, source, entry)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["active", "paused", "failed", "uninstalled"])
async def test_current_lifecycle_is_required(test_user, state):
    _, app, source, _, entry = await resources(test_user)
    with install_seed_read_scope(app.id, test_user.id):
        app.lifecycle_state = state
        await app.save()
        assert not await allowed(test_user, source, entry)


@pytest.mark.asyncio
async def test_foreign_subject_and_nondefault_denials_are_not_admitted(test_user):
    _, app, source, _, entry = await resources(test_user)
    with install_seed_read_scope(app.id, "another-human"):
        assert not await allowed(test_user, source, entry)
    with install_seed_read_scope(app.id, test_user.id):
        assert not await allowed(
            test_user, source, entry, reason="canonical_execution_scope_mismatch"
        )
        decision = Decision(allowed=False, reason="default_human_policy_denied")
        assert not await may_read_install_seed_relation(
            decision,
            Subject(kind="agent", id=test_user.id),
            source_track_id=source.id,
            target_kind="entry",
            target_id=entry.id,
        )


@pytest.mark.asyncio
async def test_explicit_exclusion_and_membership_revocation_remain_denials(test_user):
    workspace, app, source, _, entry = await resources(test_user)
    from app.services.permissions import get_user_node

    principal = await get_user_node(test_user.id)
    await principal.connect(entry, edge=EXCLUDED_FROM)
    with install_seed_read_scope(app.id, test_user.id):
        assert not await allowed(test_user, source, entry)
    # A fabricated actor/context never establishes workspace authority.
    with install_seed_read_scope(app.id, "missing-actor"):
        decision = Decision(allowed=False, reason="default_human_policy_denied")
        assert not await may_read_install_seed_relation(
            decision,
            Subject(kind="human", id="missing-actor"),
            source_track_id=source.id,
            target_kind="entry",
            target_id=entry.id,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("deferred", [False, True])
async def test_full_install_validates_manifest_and_required_hook_relations(
    test_user, monkeypatch, deferred
):
    """Actual lifecycle + canonical relation validation, not mocked materialization."""
    from app.models.nodes import EntryType
    from app.services.app_batch_install import batch_install
    from app.services.operational_model_entry_fields import (
        validate_and_materialize_entry_custom_fields,
    )
    from app.services.operational_model_runtime import resolve_track_runtime_profile

    workspace = await _make_workspace("Full seeded install")
    await test_user.connect(
        workspace, edge=IS_MEMBER_OF, role="owner", added_at=utc_now_iso()
    )
    bundle = await _seed_library("seed_relations")
    manifest = bundle.manifest
    manifest["app"]["tracks"] = [
        {
            "key": "records",
            "name": "Records",
            "entry_types": [
                {
                    "key": "record",
                    "name": "Record",
                    "fields": [
                        {
                            "key": "related",
                            "name": "Related",
                            "type": "relation",
                            "relation": {
                                "target": "entry",
                                "target_entry_types": ["record"],
                                "target_track_types": ["records"],
                                "many": False,
                            },
                        }
                    ],
                }
            ],
        }
    ]
    manifest["app"]["seeds"] = [
        {
            "track": "records",
            "entries": [
                {
                    "entry_type": "record",
                    "id": "base",
                    "title": "Base seed",
                    "custom_fields": {},
                }
            ],
        }
    ]
    manifest["app"]["settings_schema"] = {
        "type": "object",
        "properties": {"label": {"type": "string", "default": "ready"}},
    }
    bundle.manifest = manifest
    await bundle.save()
    observed = []

    async def required_hook(app, actor_id, **kwargs):
        from app.services.entry_type_resolver import resolve_entry_type_id_by_key

        tracks = await app.nodes(edge=[CONTAINS], node=["Track"])
        track = tracks[0]
        base = await Entry.find_one({"context.track_id": track.id})
        if base is None:
            return 0
        et = await EntryType.get(await resolve_entry_type_id_by_key(track.id, "record"))
        _, tier, _ = await resolve_track_runtime_profile(track)
        fields, refs = await validate_and_materialize_entry_custom_fields(
            track=track,
            entry_type=et,
            custom_fields={"related": base.id},
            runtime_tier=tier,
            entry=None,
            actor_user_id=actor_id,
            actor_kind="human",
            source_entry_title="Hook seed",
        )
        assert fields["related"] == base.id and refs
        observed.append(app.lifecycle_state)
        row = await Entry.create(
            title="Hook seed", track_id=track.id, type_id=et.id, custom_fields=fields
        )
        await track.connect(row, edge=CONTAINS)
        await row.connect(et, edge=IS_OF_TYPE)
        from app.services.operational_model_graph import sync_relation_edges

        await sync_relation_edges(source_entry=row, relation_refs=refs)
        return 1

    monkeypatch.setattr(
        "app.services.bundle_post_seed.run_bundle_post_seed", required_hook
    )
    if deferred:
        from app.services.app_lifecycle import finalize_install, install_app

        pending = await install_app(
            workspace_id=workspace.id,
            library_cp_id=bundle.id,
            actor_id=test_user.id,
            include_seed_data=True,
        )
        assert pending["status"] == "awaiting_settings" and not observed
        await finalize_install(
            app_id=pending["app_id"],
            install_token=pending["install_token"],
            settings={},
            actor_id=test_user.id,
        )
        app_id = pending["app_id"]
    else:
        result = await batch_install(
            workspace_id=workspace.id,
            items=[{"library_cp_id": bundle.id, "include_seed_data": True}],
            actor_id=test_user.id,
            use_default_settings=True,
        )
        assert result["failed"] == []
        app_id = result["installed"][0]["app_id"]
    assert observed == ["awaiting_settings" if deferred else "installing"]
    app = await App.find_one({"id": app_id})
    assert app.lifecycle_state == "active"

    tracks = await app.nodes(edge=[CONTAINS], node=["Track"])
    hook = await Entry.find_one(
        {"context.track_id": tracks[0].id, "context.title": "Hook seed"}
    )
    assert hook is not None
    assert len(await hook.nodes(edge=[REFERENCES], node=["Entry"])) == 1
    repeated = await batch_install(
        workspace_id=workspace.id,
        items=[{"library_cp_id": bundle.id, "include_seed_data": True}],
        actor_id=test_user.id,
        use_default_settings=True,
    )
    assert not repeated["failed"] and repeated["skipped"]
    assert (
        await Entry.count(
            {"context.track_id": tracks[0].id, "context.title": "Hook seed"}
        )
        == 1
    )


@pytest.mark.asyncio
async def test_current_membership_must_survive_until_each_seed_read(test_user):
    workspace, app, source, _, entry = await resources(test_user)
    with install_seed_read_scope(app.id, test_user.id):
        assert await allowed(test_user, source, entry)
        context = await workspace.get_context()
        from app.services.permissions import get_user_node

        principal = await get_user_node(test_user.id)
        for actor_id in {test_user.id, principal.id}:
            for edge in await context.find_edges_between(
                actor_id, workspace.id, edge_class=IS_MEMBER_OF
            ):
                await edge.delete()
        assert not await allowed(test_user, source, entry)


@pytest.mark.asyncio
async def test_exception_restores_outer_scope_and_no_scope_escapes(test_user):
    _, app, source, _, entry = await resources(test_user)
    with install_seed_read_scope(app.id, test_user.id):
        with pytest.raises(RuntimeError):
            with install_seed_read_scope("different-app", test_user.id):
                raise RuntimeError("fixture")
        assert await allowed(test_user, source, entry)
    assert not await allowed(test_user, source, entry)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["app", "source", "target", "entry"])
async def test_retained_blocks_are_rechecked_inside_install_scope(test_user, kind):
    from app.models.access_lifecycle import RESOURCE_ACCESS_BLOCK
    from app.services.permissions import get_user_node

    workspace, app, source, target, entry = await resources(test_user)
    principal = await get_user_node(test_user.id)
    node = {"app": app, "source": source, "target": target, "entry": entry}[kind]
    resource_type = {"source": "track", "target": "track"}.get(kind, kind)
    with install_seed_read_scope(app.id, test_user.id):
        assert await allowed(test_user, source, entry)
        await principal.connect(
            node,
            edge=RESOURCE_ACCESS_BLOCK,
            workspace_id=workspace.id,
            resource_type=resource_type,
            state="blocked",
            actor_id=principal.id,
            reason="fixture",
            changed_at=utc_now_iso(),
            revision=1,
        )
        assert not await allowed(test_user, source, entry)


@pytest.mark.asyncio
async def test_suspension_revokes_install_read_without_removing_membership(test_user):
    from app.models.access_lifecycle import WORKSPACE_SUSPENSION
    from app.services.permissions import get_user_node

    workspace, app, source, _, entry = await resources(test_user)
    principal = await get_user_node(test_user.id)
    with install_seed_read_scope(app.id, test_user.id):
        assert await allowed(test_user, source, entry)
        await principal.connect(
            workspace,
            edge=WORKSPACE_SUSPENSION,
            state="suspended",
            actor_id=principal.id,
            reason="fixture",
            changed_at=utc_now_iso(),
            revision=1,
        )
        assert not await allowed(test_user, source, entry)


@pytest.mark.asyncio
async def test_same_workspace_foreign_app_is_outside_install_scope(test_user):
    workspace, app, source, _, _ = await resources(test_user)
    other = await App.create(
        name="Other bundle", workspace_id=workspace.id, lifecycle_state="installing"
    )
    await wire_app_owner(other, test_user.id, workspace_id=workspace.id)
    await catalog_app(other)
    track = await Track.create(
        title="Other", workspace_id=workspace.id, app_id=other.id
    )
    await other.connect(track, edge=CONTAINS)
    await catalog_track(track)
    entry = await Entry.create(title="Foreign seed", track_id=track.id)
    await track.connect(entry, edge=CONTAINS)
    with install_seed_read_scope(app.id, test_user.id):
        assert not await allowed(test_user, source, entry)
