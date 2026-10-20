"""Reviewed collection effects and recovery use canonical Entry journals."""

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.agentive import staging
from app.agentive.services import staging_apply, staging_command_batch
from app.agentive.staging import create_staged_change
from app.agentive.tooling import bindings
from app.models.nodes import Entry
from app.services.agent_scope import current_scope_workspace_id
from tests.contract.test_staging_command_apply_postgres import proposal

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def prepared_collection():
    auth, workspace, track, _ = await proposal()
    from app.services.entry_create import create_entry_in_track

    entries = [
        await create_entry_in_track(
            track=track, user_id=auth.id, workspace_id=workspace.id, title=name
        )
        for name in ("First", "Second")
    ]
    p = bindings._propose_principal.set(auth.id)
    w = current_scope_workspace_id.set(workspace.id)
    try:
        staged = await bindings._stage_bulk_update_entries(
            {
                "entry_ids": [e.id for e in entries],
                "updates": {"body": "Reviewed collection change"},
            }
        )
        sc = await create_staged_change(user_id=auth.id, session_id=None, **staged)
    finally:
        bindings._propose_principal.reset(p)
        current_scope_workspace_id.reset(w)
    return auth, workspace, track, entries, sc


async def test_bulk_update_commits_each_record(postgres_raw_db, monkeypatch):
    from app.services.entry_update import update_entry_in_track

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "42" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, _, entries, sc = await prepared_collection()
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert result["consumed"], result
        assert result["execute_result"]["completed"] == 2
        for entry in entries:
            current = await Entry.get(entry.id)
            assert current.body == "Reviewed collection change"
            assert current.record_revision == entry.record_revision + 1


async def test_stale_selection_refuses_before_first_write(postgres_raw_db, monkeypatch):
    from app.services.entry_update import update_entry_in_track

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "42" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, entries, sc = await prepared_collection()
        await update_entry_in_track(
            entry_id=entries[1].id,
            user_id=auth.id,
            workspace_id=workspace.id,
            title="Changed after review",
        )
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert not result["consumed"], result
        assert result["execute_result"]["completed"] == 0
        assert result["execute_result"]["status_code"] == 409
        current = await Entry.get(entries[0].id)
        assert current.record_revision == entries[0].record_revision
        assert current.body != "Reviewed collection change"


async def test_retry_does_not_rewrite_completed_records(postgres_raw_db, monkeypatch):
    from app.services.entry_update import update_entry_in_track

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "42" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, entries, sc = await prepared_collection()
        original = staging_command_batch.apply_staged_command_once
        calls = 0

        async def lost_worker(**kwargs):
            nonlocal calls
            result = await original(**kwargs)
            calls += 1
            if calls == 1:
                raise RuntimeError("Lost worker after committed record")
            return result

        monkeypatch.setattr(
            staging_command_batch, "apply_staged_command_once", lost_worker
        )
        with pytest.raises(RuntimeError, match="Lost worker"):
            await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        first = await update_entry_in_track(
            entry_id=entries[0].id,
            user_id=auth.id,
            workspace_id=workspace.id,
            title="Later human edit",
        )
        revision = first.record_revision
        staging._tokens.clear()
        monkeypatch.setattr(
            staging_command_batch, "apply_staged_command_once", original
        )
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert result["consumed"], result
        first = await Entry.get(entries[0].id)
        assert first.title == "Later human edit" and first.record_revision == revision
        assert (await Entry.get(entries[1].id)).body == "Reviewed collection change"


async def test_revoked_record_access_refuses_the_selection_before_writing(
    postgres_raw_db, monkeypatch
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "42" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, entries, sc = await prepared_collection()
        from app.models.edges import EXCLUDED_FROM, IS_MEMBER_OF
        from app.models.nodes import User

        user = (await User.find({"context.user_id": auth.id}))[0]
        context = await user.get_context()
        edges = await context.find_edges_between(
            user.id, workspace.id, edge_class=IS_MEMBER_OF
        )
        edge = edges[0]
        edge.role = "member"
        await edge.save()
        await user.connect(entries[1], edge=EXCLUDED_FROM)
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert not result["consumed"], result
        assert result["execute_result"]["completed"] == 0
        assert result["execute_result"]["status_code"] == 403
        for entry in entries:
            current = await Entry.get(entry.id)
            assert current.record_revision == entry.record_revision
            assert current.body != "Reviewed collection change"


async def test_revocation_during_recovery_reports_completed_records(
    postgres_raw_db, monkeypatch
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "42" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, entries, sc = await prepared_collection()
        original = staging_command_batch.apply_staged_command_once

        async def lost_after_commit(**kwargs):
            await original(**kwargs)
            raise RuntimeError("Lost after one committed record")

        monkeypatch.setattr(
            staging_command_batch, "apply_staged_command_once", lost_after_commit
        )
        with pytest.raises(RuntimeError, match="Lost after"):
            await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        monkeypatch.setattr(
            staging_command_batch, "apply_staged_command_once", original
        )
        from app.models.edges import EXCLUDED_FROM, IS_MEMBER_OF
        from app.models.nodes import User

        user = (await User.find({"context.user_id": auth.id}))[0]
        context = await user.get_context()
        membership = (
            await context.find_edges_between(
                user.id, workspace.id, edge_class=IS_MEMBER_OF
            )
        )[0]
        membership.role = "member"
        await membership.save()
        await user.connect(entries[1], edge=EXCLUDED_FROM)
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert not result["consumed"], result
        assert result["execute_result"]["completed"] == 1
        assert result["execute_result"]["application_state"] == "partially_applied"
        assert (await Entry.get(entries[0].id)).record_revision == entries[
            0
        ].record_revision + 1
        assert (await Entry.get(entries[1].id)).record_revision == entries[
            1
        ].record_revision


async def test_authorized_editor_can_update_a_collection_without_ownership(
    postgres_raw_db, monkeypatch
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "42" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, workspace, track, entries, _ = await prepared_collection()
        from jvspatial.api.auth.models import User as AuthUser

        from app.models.edges import COLLABORATES_ON, IS_MEMBER_OF
        from app.models.nodes import User
        from app.services.app_graph import catalog_user

        editor_auth = await AuthUser.create(
            email="editor-" + owner.email, password_hash="fixture"
        )
        editor = await User.create(
            user_id=editor_auth.id, display_name="Collection editor"
        )
        await catalog_user(editor)
        await editor.connect(workspace, edge=IS_MEMBER_OF, role="member")
        await editor.connect(track, edge=COLLABORATES_ON, role="editor")
        p = bindings._propose_principal.set(editor_auth.id)
        w = current_scope_workspace_id.set(workspace.id)
        try:
            staged = await bindings._stage_bulk_update_entries(
                {
                    "entry_ids": [entry.id for entry in entries],
                    "updates": {"body": "Editor collection change"},
                }
            )
            sc = await create_staged_change(
                user_id=editor_auth.id, session_id=None, **staged
            )
        finally:
            bindings._propose_principal.reset(p)
            current_scope_workspace_id.reset(w)
        result = await staging_apply.bless_and_execute(
            user_id=editor_auth.id, token=sc.token
        )
        assert result["consumed"], result
        assert result["execute_result"]["completed"] == 2
        for entry in entries:
            assert (await Entry.get(entry.id)).body == "Editor collection change"


async def test_app_owner_uses_agent_tool_to_change_another_owners_track(
    postgres_raw_db, monkeypatch
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "42" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        original, workspace, track, _ = await proposal()
        from jvspatial.api.auth.models import User as AuthUser

        from app.agentive.tooling.dispatch import dispatch_tool
        from app.models.edges import IS_MEMBER_OF, OWNS
        from app.models.nodes import EntryType, User
        from app.services.app_graph import catalog_user
        from app.services.resource_location import locate_resource

        location = await locate_resource("track", track.id)
        from app.models.nodes import App

        app = await App.get(location.app_id)
        owner_auth = await AuthUser.create(
            email="app-owner-" + original.email, password_hash="fixture"
        )
        owner = await User.create(user_id=owner_auth.id, display_name="App owner")
        await catalog_user(owner)
        await owner.connect(workspace, edge=IS_MEMBER_OF, role="member")
        await owner.connect(app, edge=OWNS)
        result = await dispatch_tool(
            "integral_modify_model",
            {
                "action": "add_entry_type",
                "track_id": track.id,
                "name": "Additional type",
                "fields": [{"key": "note", "name": "Note", "type": "text"}],
            },
            principal_id=owner_auth.id,
            scope=workspace.id,
        )
        assert not result.is_error, result
        applied = await staging_apply.bless_and_execute(
            user_id=owner_auth.id, token=result.data["token"]
        )
        assert applied["consumed"], applied
        rows = await EntryType.find(
            {"context.track_id": track.id, "context.name": "Additional type"}
        )
        assert len(rows) == 1
        assert rows[0].form_schema["fields"][0]["key"] == "note"
