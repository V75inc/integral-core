"""Creation receipts include graph setup and outbox, before HTTP readback."""

import uuid

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.agentive import staging
from app.agentive.services import staging_apply
from app.agentive.services.staging_execution import execution_record_id
from app.agentive.staging import StagingError, create_staged_change
from app.models.nodes import App, EntryType, Tag, Track
from app.services.agent_scope import current_scope_workspace_id
from tests.contract.test_staging_command_apply_postgres import proposal

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def mint(kind):
    auth, workspace, _, _ = await proposal()
    suffix = uuid.uuid4().hex
    payload = (
        {"name": "Receipt " + suffix}
        if kind == "create_app"
        else {
            "title": "Receipt " + suffix,
            "entry_types": [
                {
                    "name": "Item",
                    "fields": [{"key": "amount", "name": "Amount", "type": "number"}],
                }
            ],
            "taxonomy": {
                "tag_groups": [
                    {
                        "key": "status",
                        "tags": [
                            {"name": "Open"},
                            {"name": "Review", "parent": "Open"},
                        ],
                    }
                ]
            },
        }
    )
    reset = current_scope_workspace_id.set(workspace.id)
    try:
        sc = await create_staged_change(
            user_id=auth.id,
            session_id=None,
            kind=kind,
            summary="Create resource",
            diff_human="Create resource",
            diff_machine={},
            payload=payload,
        )
    finally:
        current_scope_workspace_id.reset(reset)
    return auth, workspace, sc


@pytest.mark.parametrize("kind", ["create_app", "create_track"])
async def test_creation_reconciles_after_completion_failure(
    postgres_raw_db, monkeypatch, kind
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "55" * 32)
    original = staging_apply.consume_token

    async def fail(**kwargs):
        raise StagingError("completion_unavailable", "Lost completion")

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, sc = await mint(kind)
        monkeypatch.setattr(staging_apply, "consume_token", fail)
        first = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert first["application_state"] == "reconciliation_required", first
        key = "app" if kind == "create_app" else "track"
        resource_id = first["execute_result"][key]["id"]
        receipt = await postgres_raw_db.get("object", execution_record_id(sc.token))
        assert receipt["context"]["state"] == "applied"
        if kind == "create_track":
            types = await EntryType.find({"track_id": resource_id})
            assert [t.name for t in types] == ["Item"]
            assert types[0].form_schema["fields"][0]["key"] == "amount"
            tags = await Tag.find({"track_id": resource_id})
            assert {t.name for t in tags} == {"Open", "Review"}
            assert next(t for t in tags if t.name == "Review").parent_tag_id == next(
                t.id for t in tags if t.name == "Open"
            )
        staging._tokens.clear()
        monkeypatch.setattr(staging_apply, "consume_token", original)
        second = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert second["consumed"] is True, second
        assert second["execute_result"][key]["id"] == resource_id
        cls = App if key == "app" else Track
        field = "name" if key == "app" else "title"
        assert [n.id for n in await cls.find({field: sc.payload[field]})] == [
            resource_id
        ]


async def test_inline_setup_failure_rolls_back_creation_and_events(
    postgres_raw_db, monkeypatch
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "55" * 32)
    from app.services import staged_creation_setup

    original = staged_creation_setup.create_tag_for_scope
    calls = 0

    async def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("Taxonomy failure after first tag")
        return await original(*args, **kwargs)

    monkeypatch.setattr(staged_creation_setup, "create_tag_for_scope", fail_second)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, sc = await mint("create_track")
        before = await postgres_raw_db.find(
            "object", {"entity": "OperationEventOutbox"}
        )
        failed = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert failed["consumed"] is False
        assert failed["execute_result"]["error"] is True
        assert "Taxonomy failure" in failed["execute_result"]["message"]
        assert calls == 2
        assert await Track.find({"title": sc.payload["title"]}) == []
        assert (
            await postgres_raw_db.find("object", {"entity": "OperationEventOutbox"})
            == before
        )
        receipt = await postgres_raw_db.get("object", execution_record_id(sc.token))
        assert receipt["context"]["state"] == "ready"
        assert receipt["context"]["result_ciphertext"] == ""


async def test_mixed_creation_batch_recovers_without_replaying_setup(
    postgres_raw_db, monkeypatch
):
    from app.agentive.services import staging_command_batch

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "55" * 32)
    original = staging_command_batch.apply_staged_command_once
    calls = 0

    async def lose_after_track(**kwargs):
        nonlocal calls
        result = await original(**kwargs)
        calls += 1
        if calls == 2:
            raise RuntimeError("Worker loss after Track setup commit")
        return result

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _ = await mint("create_track")
        label = "Mixed " + uuid.uuid4().hex
        reset = current_scope_workspace_id.set(workspace.id)
        try:
            sc = await create_staged_change(
                user_id=auth.id,
                session_id=None,
                kind="batch",
                summary="Create App, Track and Entry",
                diff_human="Three commands",
                diff_machine={},
                payload={
                    "operations": [
                        {"kind": "create_app", "payload": {"name": label}},
                        {
                            "kind": "create_track",
                            "payload": {
                                "title": "Records",
                                "app_id": "{{app.id}}",
                                "entry_types": [{"name": "Item", "fields": []}],
                                "taxonomy": {
                                    "tag_groups": [
                                        {"key": "status", "tags": [{"name": "Open"}]}
                                    ]
                                },
                            },
                        },
                        {
                            "kind": "create_entry",
                            "payload": {
                                "track_id": "{{track.id:Records}}",
                                "title": "First item",
                            },
                        },
                    ]
                },
            )
        finally:
            current_scope_workspace_id.reset(reset)
        monkeypatch.setattr(
            staging_command_batch, "apply_staged_command_once", lose_after_track
        )
        with pytest.raises(RuntimeError, match="Worker loss"):
            await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        apps = await App.find({"name": label})
        assert len(apps) == 1
        tracks = await Track.find({"title": "Records", "workspace_id": workspace.id})
        assert len(tracks) == 1
        original_track = tracks[0].id
        type_ids = [n.id for n in await EntryType.find({"track_id": original_track})]
        tag_ids = [n.id for n in await Tag.find({"track_id": original_track})]
        assert len(type_ids) == len(tag_ids) == 1
        staging._tokens.clear()
        monkeypatch.setattr(
            staging_command_batch, "apply_staged_command_once", original
        )
        recovered = await staging_apply.bless_and_execute(
            user_id=auth.id, token=sc.token
        )
        assert recovered["consumed"] is True, recovered
        assert [n.id for n in await App.find({"name": label})] == [apps[0].id]
        assert [
            n.id
            for n in await Track.find(
                {"title": "Records", "workspace_id": workspace.id}
            )
        ] == [original_track]
        assert [
            n.id for n in await EntryType.find({"track_id": original_track})
        ] == type_ids
        assert [n.id for n in await Tag.find({"track_id": original_track})] == tag_ids
        from app.models.nodes import Entry

        assert len(await Entry.find({"track_id": original_track})) == 1


async def test_competing_creation_recovery_reports_one_shared_effect(
    postgres_raw_db, monkeypatch
):
    import asyncio

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "55" * 32)
    from app.services import app_service

    entered, unblock = asyncio.Event(), asyncio.Event()
    original = app_service.create_app_response_payload

    async def pause_projection(*args, **kwargs):
        entered.set()
        await unblock.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(app_service, "create_app_response_payload", pause_projection)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, sc = await mint("create_app")
        applying = asyncio.create_task(
            staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        )
        await asyncio.wait_for(entered.wait(), timeout=10)
        try:
            staging._tokens.clear()  # Model an independent worker cache.
            recovered = await staging_apply.bless_and_execute(
                user_id=auth.id, token=sc.token
            )
            assert recovered["consumed"] is True
            assert recovered["execute_result"]["execution_receipt"]["replayed"] is True
        finally:
            unblock.set()
        first = await applying
        assert first["consumed"] is True
        assert first["staged_change"]["state"] == "consumed"
        assert "consume_warning" not in first
        assert (
            first["execute_result"]["app"]["id"]
            == recovered["execute_result"]["app"]["id"]
        )
        assert len(await App.find({"name": sc.payload["name"]})) == 1
