"""Package defaults and explicit Track identities serialize across connections."""

import asyncio
from contextlib import asynccontextmanager
from contextvars import Context

import pytest
from jvspatial.core.context import (
    GraphContext,
    get_default_context,
    scoped_default_context_async,
)
from jvspatial.db.factory import create_database

from app.models.edges import CONTAINS
from app.models.nodes import EntryType, Track
from app.services import operational_model_merge as merge
from app.services.app_graph import get_app_attached_operational_model
from app.services.authority_facts import primary_node_record
from app.services.membership_write_scope import membership_write_scope
from app.services.resource_metadata_update import update_track_metadata
from tests.test_resource_location import graph

pytestmark = [pytest.mark.postgres, pytest.mark.contract, pytest.mark.asyncio]


async def fixture_graph():
    owner, workspace, app, track, _ = await graph()
    track.template_id = "records"
    track.icon = "briefcase"
    await track.save()
    spec = {
        "key": "records",
        "name": "Records",
        "description": "Records for review.",
        "icon": "briefcase",
    }
    spec["entry_types"] = [{"key": "record", "name": "Record"}]
    spec["views"] = [
        {"key": "records_table", "name": "Records table", "view_type": "table"}
    ]
    model = await get_app_attached_operational_model(app)
    model.manifest = {
        "operational_model_schema_version": 2,
        "scope": "app",
        "app": {
            "tracks": [spec],
            "track_templates": [spec],
            "defaults": {"provision_prescribed_tracks": True},
        },
    }
    await model.save()
    return owner, workspace, app, track


@pytest.mark.parametrize("refresh_kind", ["template", "prescribed"])
@pytest.mark.parametrize("edit_first", [True, False])
async def test_explicit_dot_and_package_refresh_serialize(
    postgres_raw_db, refresh_kind, edit_first, monkeypatch
):
    from jvspatial.core.context import get_default_context

    from app.services import membership_write_scope as scopes
    from app.services import resource_metadata_update as metadata

    second = create_database(db_type="postgres", min_size=1, max_size=1)
    tasks = []
    first_admitted, release = asyncio.Event(), asyncio.Event()
    follower_started, follower_admitted = asyncio.Event(), asyncio.Event()
    admitted_names = set()
    original_scope = scopes.membership_write_scope

    def observe(actual_scope):
        @asynccontextmanager
        async def observed_scope(*args, **kwargs):
            async with actual_scope(*args, **kwargs):
                name = asyncio.current_task().get_name()
                admitted_names.add(name)
                if name == "icon-first":
                    first_admitted.set()
                    await release.wait()
                elif name == "icon-follower":
                    follower_admitted.set()
                    row = await primary_node_record(
                        get_default_context().database, Track, track.id
                    )
                    assert row["context"]["icon"] == ("" if edit_first else "briefcase")
                    assert row["context"]["icon_overridden"] is edit_first
                yield

        return observed_scope

    try:
        async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
            owner, workspace, app, track = await fixture_graph()
            if not edit_first:
                track.icon = ""
                await track.save()
            for collection in ("node", "edge", "object"):
                await second.find(collection, {"id": ""}, limit=1)
            monkeypatch.setattr(
                scopes, "membership_write_scope", observe(original_scope)
            )
            monkeypatch.setattr(
                metadata, "membership_write_scope", observe(original_scope)
            )
            monkeypatch.setattr(
                merge, "_template_refresh_scope", observe(merge._template_refresh_scope)
            )

            async def edit():
                await update_track_metadata(
                    user_id=owner.id, track_id=track.id, icon=""
                )

            async def refresh():
                if refresh_kind == "template":
                    result = await merge.refresh_app_track_template_materialization(
                        app, "records"
                    )
                    assert result is not None
                    await result.nodes(edge=[CONTAINS], node=["EntryType"], limit=1)
                else:
                    await merge.provision_prescribed_tracks_from_app_manifest(
                        app, owner.id
                    )

            async def run(database, first):
                async with scoped_default_context_async(
                    GraphContext(database=database)
                ):
                    if not first:
                        follower_started.set()
                    await (edit() if (first == edit_first) else refresh())

            tasks.append(
                asyncio.create_task(
                    run(postgres_raw_db, True), name="icon-first", context=Context()
                )
            )
            await asyncio.wait_for(first_admitted.wait(), timeout=15)
            tasks.append(
                asyncio.create_task(
                    run(second, False), name="icon-follower", context=Context()
                )
            )
            await asyncio.wait_for(follower_started.wait(), timeout=15)
            # The first real service owns the fence. No test-owned outer fence
            # can hide a missing refresh/provision admission boundary.
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(follower_admitted.wait(), timeout=0.25)
            release.set()
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=30)
            assert admitted_names == {"icon-first", "icon-follower"}
            row = await primary_node_record(postgres_raw_db, Track, track.id)
            assert row["context"]["icon"] == ""
            assert row["context"]["icon_overridden"] is True
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await second.close()


async def test_provisioning_failure_rolls_back_defaults_and_attached_schema(
    postgres_raw_db, monkeypatch
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, _, app, track = await fixture_graph()
        track.icon = ""
        await track.save()

        async def snapshot():
            return {
                collection: sorted(
                    await get_default_context().database.find(collection, {}),
                    key=lambda row: row["id"],
                )
                for collection in ("node", "edge")
            }

        before = await snapshot()
        apply_spec = merge.apply_space_track_spec_to_track

        async def fail_after_materialization(*args, **kwargs):
            await apply_spec(*args, **kwargs)
            changed_track = args[0]
            assert changed_track.icon == "briefcase"
            assert changed_track.attached_operational_model_id
            after = await snapshot()
            before_ids = {row["id"] for row in before["node"]}
            assert any(row["id"] not in before_ids for row in after["node"])
            assert any(
                row["id"] not in {item["id"] for item in before["edge"]}
                for row in after["edge"]
            )
            raise RuntimeError("fixture failure after nested schema writes")

        monkeypatch.setattr(
            merge, "apply_space_track_spec_to_track", fail_after_materialization
        )
        with pytest.raises(RuntimeError, match="fixture failure"):
            await merge.provision_prescribed_tracks_from_app_manifest(app, owner.id)
        assert await snapshot() == before


@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize("operation", ["create", "update"])
async def test_anchor_materialization_preserves_admitted_mode(
    postgres_raw_db, monkeypatch, shared, operation
):
    from copy import deepcopy

    from app.config import settings
    from app.exceptions import AuthorizationUnavailableError
    from app.services.authorization_scope import _held, authorization_scope
    from app.services.entry_create import create_entry_in_track
    from app.services.entry_update import update_entry_in_track
    from app.services.operational_model_entry_fields import CREATE_ANCHOR_SENTINEL

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, workspace, app, track = await fixture_graph()
        app_model = await get_app_attached_operational_model(app)
        manifest = deepcopy(app_model.manifest)
        source = manifest["app"]["tracks"][0]
        source["entry_types"][0]["fields"] = [
            {
                "key": "details",
                "type": "relation",
                "relation": {
                    "target": "track",
                    "target_track_template": "details",
                    "auto_provision": operation == "create",
                },
            }
        ]
        manifest["app"]["track_templates"] = [
            {
                "key": "details",
                "name": "Details",
                "description": "Supporting records.",
                "icon": "folder",
                "entry_types": [{"key": "task", "name": "Task"}],
            }
        ]
        app_model.manifest = manifest
        await app_model.save()
        await merge.apply_space_track_spec_to_track(track, source)
        record_type = await EntryType.find_one(
            {"context.track_id": track.id, "context.name": "Record"}
        )
        assert record_type is not None
        # First-use state creation is exclusive. Prime it so the opt-in case
        # genuinely starts under an established shared admission.
        async with authorization_scope(workspace.id):
            pass
        monkeypatch.setattr(settings, "ENTRY_SHARED_ADMISSION_ENABLED", shared)
        original = merge._refresh_app_track_template_materialization
        observed_modes = []

        async def observed_refresh(app_node, key):
            if key == "details":
                held = (_held.get() or {}).get(workspace.id)
                assert held is not None
                observed_modes.append(held[2])
            return await original(app_node, key)

        monkeypatch.setattr(
            merge, "_refresh_app_track_template_materialization", observed_refresh
        )
        entry = await create_entry_in_track(
            track=track,
            user_id=owner.id,
            workspace_id=workspace.id,
            title="Parent fixture",
            entry_type=record_type,
            custom_fields={},
        )
        if operation == "update":
            entry = await update_entry_in_track(
                entry_id=entry.id,
                user_id=owner.id,
                workspace_id=workspace.id,
                custom_fields={"details": CREATE_ANCHOR_SENTINEL},
            )
        assert observed_modes and all(mode is shared for mode in observed_modes)
        anchor_id = entry.custom_fields["details"]
        assert isinstance(anchor_id, str) and anchor_id.startswith("n.Track.")
        anchor = await Track.find_one({"id": anchor_id})
        assert anchor is not None and anchor.icon == "folder"
        # The ACL guard itself remains strict: no shared-to-exclusive upgrade.
        with pytest.raises(AuthorizationUnavailableError):
            async with authorization_scope(workspace.id, shared=True):
                async with authorization_scope(workspace.id, authority_mutation=True):
                    pytest.fail("Authority mutation upgraded a shared holder")
