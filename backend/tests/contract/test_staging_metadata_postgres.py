"""Metadata receipts preserve rename identity and post-commit recovery."""

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.agentive import staging
from app.agentive.services import staging_apply
from app.agentive.staging import StagingError, create_staged_change
from app.models.nodes import App, Track
from app.services.agent_scope import current_scope_workspace_id
from app.services.track_relation_identity import relation_track_type
from tests.contract.test_staging_command_apply_postgres import proposal

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def mint_update(kind):
    auth, workspace, track, _ = await proposal()
    app = (await App.find({"workspace_id": workspace.id}))[0]
    payload = (
        {"app_id": app.id, "name": "Renamed App"}
        if kind == "update_app"
        else {"track_id": track.id, "title": "Renamed Track"}
    )
    reset = current_scope_workspace_id.set(workspace.id)
    try:
        sc = await create_staged_change(
            user_id=auth.id,
            session_id=None,
            kind=kind,
            summary="Rename existing resource",
            diff_human="Rename",
            diff_machine={},
            payload=payload,
        )
    finally:
        current_scope_workspace_id.reset(reset)
    return auth, workspace, app, track, sc


@pytest.mark.parametrize("kind", ["update_app", "update_track"])
async def test_metadata_completion_recovery_keeps_existing_identity(
    postgres_raw_db, monkeypatch, kind
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "66" * 32)
    original = staging_apply.consume_token

    async def fail(**kwargs):
        raise StagingError("completion_unavailable", "Lost completion")

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, app, track, sc = await mint_update(kind)
        prior_type = await relation_track_type(track)
        monkeypatch.setattr(staging_apply, "consume_token", fail)
        first = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert first["application_state"] == "reconciliation_required", first
        cls, resource = (App, app) if kind == "update_app" else (Track, track)
        current = await cls.find_one({"id": resource.id})
        timestamp = current.updated_at
        staging._tokens.clear()
        monkeypatch.setattr(staging_apply, "consume_token", original)
        second = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert second["consumed"] is True, second
        assert second["execute_result"]["execution_receipt"]["replayed"] is True
        current = await cls.find_one({"id": resource.id})
        assert current.updated_at == timestamp
        assert len(await cls.find({"workspace_id": resource.workspace_id})) == 1
        if kind == "update_track":
            assert await relation_track_type(current) == prior_type
            assert current.title == "Renamed Track"
        else:
            assert current.name == "Renamed App"


@pytest.mark.parametrize("kind", ["update_app", "update_track"])
async def test_metadata_receipt_failure_rolls_back_existing_fields(
    postgres_raw_db, monkeypatch, kind
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "66" * 32)
    import app.agentive.services.staging_execution as execution

    async def fail(**kwargs):
        raise OSError("Injected metadata receipt failure")

    monkeypatch.setattr(execution, "record_staging_execution_result", fail)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, app, track, sc = await mint_update(kind)
        resource = app if kind == "update_app" else track
        cls = App if kind == "update_app" else Track
        original_label = resource.name if kind == "update_app" else resource.title
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert result["consumed"] is False
        assert result["execute_result"]["error"] is True
        current = await cls.find_one({"id": resource.id})
        assert (
            current.name if kind == "update_app" else current.title
        ) == original_label


async def test_six_updates_keep_relation_identity_and_do_not_duplicate(
    postgres_raw_db, monkeypatch
):
    from app.agentive.staging_executors import _x_create_entry
    from app.models.nodes import Entry
    from app.services.operational_model_authoring import apply_entry_types_to_track
    from app.services.track_service import create_track_in_space

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "66" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, app, businesses, _ = await mint_update("update_app")
        # Give the original fixture a stable declared business identity before
        # building its typed expense reference. The rename must preserve it.
        businesses.title = "Businesses"
        businesses.relation_type_key = "Businesses"
        await businesses.save()
        reset = current_scope_workspace_id.set(workspace.id)
        try:
            expenses = await create_track_in_space(
                auth.id, title="Expenses", app_id=app.id
            )
            contacts = await create_track_in_space(
                auth.id, title="Contacts", app_id=app.id
            )
            await apply_entry_types_to_track(
                user_id=auth.id,
                track_id=expenses.id,
                entry_types=[
                    {
                        "name": "Expense",
                        "fields": [
                            {"key": "amount", "name": "Amount", "type": "number"},
                            {
                                "key": "business",
                                "name": "Business",
                                "type": "relation",
                                "relation": {
                                    "target": "entry",
                                    "many": False,
                                    "allow_cross_track": True,
                                    "target_track_types": ["Businesses"],
                                },
                            },
                        ],
                    }
                ],
            )
            walmart = await _x_create_entry(
                auth.id, {"track_id": businesses.id, "title": "Walmart"}
            )
            assert not walmart.get("error"), walmart
            walmart_id = walmart["entry"]["id"]
            expense = await _x_create_entry(
                auth.id,
                {
                    "track_id": expenses.id,
                    "title": "Groceries",
                    "entry_type": "Expense",
                    "fields": {"amount": 120, "business": walmart_id},
                },
            )
            assert not expense.get("error"), expense
            john = await _x_create_entry(
                auth.id, {"track_id": contacts.id, "title": "John Smith"}
            )
            assert not john.get("error"), john
            operations = [
                {
                    "kind": "update_app",
                    "payload": {
                        "app_id": app.id,
                        "name": "Personal & Business Manager",
                    },
                },
                {
                    "kind": "update_track",
                    "payload": {
                        "track_id": businesses.id,
                        "title": "Business Directory",
                    },
                },
                {
                    "kind": "update_track",
                    "payload": {
                        "track_id": contacts.id,
                        "title": "People & Organizations",
                    },
                },
                {
                    "kind": "update_entry",
                    "payload": {"entry_id": walmart_id, "title": "Walmart Supercenter"},
                },
                {
                    "kind": "update_entry",
                    "payload": {
                        "entry_id": expense["entry"]["id"],
                        "fields": {"amount": 150},
                    },
                },
                {
                    "kind": "update_entry",
                    "payload": {
                        "entry_id": john["entry"]["id"],
                        "title": "John Smith — Project Manager",
                    },
                },
            ]
            sc = await create_staged_change(
                user_id=auth.id,
                session_id=None,
                kind="batch",
                summary="Six existing-record updates",
                diff_human="Rename and update",
                diff_machine={},
                payload={"operations": operations},
            )
        finally:
            current_scope_workspace_id.reset(reset)
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert result["consumed"] is True, result
        assert result["execute_result"]["completed"] == 6
        assert (
            await App.find_one({"id": app.id})
        ).name == "Personal & Business Manager"
        current_business = await Track.find_one({"id": businesses.id})
        assert current_business.title == "Business Directory"
        assert await relation_track_type(current_business) == "Businesses"
        assert (await Track.find_one({"id": expenses.id})).title == "Expenses"
        assert (
            await Track.find_one({"id": contacts.id})
        ).title == "People & Organizations"
        assert (await Entry.find_one({"id": walmart_id})).title == "Walmart Supercenter"
        current_expense = await Entry.find_one({"id": expense["entry"]["id"]})
        assert current_expense.custom_fields["amount"] == 150
        assert current_expense.custom_fields["business"] == walmart_id
        assert (
            await Entry.find_one({"id": john["entry"]["id"]})
        ).title == "John Smith — Project Manager"
        assert len(await App.find({"workspace_id": workspace.id})) == 1
        assert len(await Track.find({"workspace_id": workspace.id})) == 3
        assert (
            len(
                await Entry.find(
                    {"track_id": {"$in": [businesses.id, expenses.id, contacts.id]}}
                )
            )
            == 3
        )


@pytest.mark.parametrize("kind", ["update_app", "update_track"])
async def test_metadata_recovery_withholds_current_revoked_record(
    postgres_raw_db, monkeypatch, kind
):
    import uuid

    from jvspatial.api.auth.models import User as AuthUser

    from app.models.edges import COLLABORATES_ON, EXCLUDED_FROM, IS_MEMBER_OF
    from app.models.nodes import User
    from app.services.app_graph import catalog_user

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "66" * 32)
    original = staging_apply.consume_token

    async def fail(**kwargs):
        raise StagingError("completion_unavailable", "Lost completion")

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        _, workspace, app, track, owner_sc = await mint_update(kind)
        auth = await AuthUser.create(
            email=f"metadata-reader-{uuid.uuid4().hex}@example.com",
            password_hash="fixture",
        )
        reader = await User.create(user_id=auth.id, display_name="Metadata editor")
        await catalog_user(reader)
        await reader.connect(workspace, edge=IS_MEMBER_OF, role="member")
        resource = app if kind == "update_app" else track
        await reader.connect(resource, edge=COLLABORATES_ON, role="admin")
        reset = current_scope_workspace_id.set(workspace.id)
        try:
            sc = await create_staged_change(
                user_id=auth.id,
                session_id=None,
                kind=kind,
                summary="Rename",
                diff_human="Rename",
                diff_machine={},
                payload=owner_sc.payload,
            )
        finally:
            current_scope_workspace_id.reset(reset)
        monkeypatch.setattr(staging_apply, "consume_token", fail)
        first = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert first["application_state"] == "reconciliation_required", first
        # Exclusion removes inheritance; it cannot revoke a direct admin
        # grant. Remove that grant as well to exercise actual loss of access.
        context = await reader.get_context()
        for edge in await context.find_edges_between(
            reader.id, resource.id, edge_class=COLLABORATES_ON
        ):
            await edge.delete()
        await reader.connect(resource, edge=EXCLUDED_FROM)
        staging._tokens.clear()
        monkeypatch.setattr(staging_apply, "consume_token", original)
        recovered = await staging_apply.bless_and_execute(
            user_id=auth.id, token=sc.token
        )
        assert recovered["consumed"] is True
        assert recovered["execute_result"]["result_access"] == "withheld"
        assert resource.id not in str(recovered["execute_result"])
