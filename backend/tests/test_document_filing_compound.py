"""Explicit document filing creates one review without ambient batch state."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agentive.tooling import bindings
from app.agentive.tooling.stagers_document_filing import stage_file_uploaded_document


def plan(kind, payload):
    return {
        "kind": kind,
        "payload": payload,
        "summary": kind,
        "diff_human": kind,
        "diff_machine": payload,
    }


@pytest.fixture
def prepared(monkeypatch):
    from app.agentive import staging

    staging._reset_for_tests()
    monkeypatch.setattr(bindings, "_bound_propose_principal", lambda: "user")
    monkeypatch.setattr(bindings, "_bound_propose_session_id", lambda: "thread")
    monkeypatch.setattr(
        "app.services.agent_scope.active_workspace_id", lambda: "workspace"
    )
    monkeypatch.setattr(
        "app.services.chat_threads.get_thread_by_session",
        AsyncMock(return_value=SimpleNamespace(id="thread")),
    )
    source = AsyncMock(return_value=(SimpleNamespace(), None, None))
    read = AsyncMock(
        return_value={"text": "Source facts", "metadata_status": "complete"}
    )
    monkeypatch.setattr(
        "app.services.attachment_agent.validate_chat_attachment_source", source
    )
    monkeypatch.setattr("app.services.attachment_agent.get_attachment_text", read)
    create = AsyncMock(
        return_value=plan(
            "create_entry", {"track_id": "track", "title": "Project", "body": "Facts"}
        )
    )
    update = AsyncMock(
        return_value=plan(
            "update_entry",
            {"entry_id": "entry", "body": "Facts", "expected_record_revision": 2},
        )
    )

    async def attachment(args, **kwargs):
        return plan(
            "attach_uploaded_file",
            {
                **args,
                "expected_sha256": "a" * 64,
                "expected_size": 42,
                "source_thread_id": "thread",
            },
        )

    attach = AsyncMock(side_effect=attachment)
    monkeypatch.setattr(bindings, "_stage_create_entry", create)
    monkeypatch.setattr(bindings, "_stage_update_entry", update)
    monkeypatch.setattr(bindings, "_stage_attach_uploaded_file", attach)
    return SimpleNamespace(
        source=source, read=read, create=create, update=update, attach=attach
    )


def args(mode):
    base = {"mode": mode, "attachment_id": "source"}
    if mode == "create":
        return {**base, "track_id": "track", "title": "Project", "text": "Facts"}
    if mode == "update":
        return {**base, "entry_id": "entry", "updates": {"body": "Facts"}}
    return {**base, "entry_id": "entry"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,kinds,target",
    [
        ("create", ["create_entry", "attach_uploaded_file"], "{{step_1.id}}"),
        ("update", ["update_entry", "attach_uploaded_file"], "entry"),
        ("attach", ["attach_uploaded_file"], "entry"),
    ],
)
async def test_compound_is_one_plan_with_explicit_source(prepared, mode, kinds, target):
    from app.agentive import staging

    result = await stage_file_uploaded_document(args(mode))
    assert result["kind"] == "batch"
    operations = result["payload"]["operations"]
    assert [op["kind"] for op in operations] == kinds
    assert operations[-1]["payload"]["entry_id"] == target
    assert operations[-1]["payload"]["attachment_id"] == "source"
    assert operations[-1]["payload"]["expected_sha256"] == "a" * 64
    assert not staging.is_batch_open("user", "thread")
    if mode != "create":
        prepared.create.assert_not_awaited()
    if mode == "attach":
        prepared.update.assert_not_awaited()
        prepared.read.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["foreign_source", "unread", "create", "attach"])
async def test_failed_plan_never_leaves_a_partial_review(prepared, fault):
    from app.agentive import staging

    if fault == "foreign_source":
        prepared.source.return_value = (None, None, {"message": "Source unavailable"})
    elif fault == "unread":
        prepared.read.return_value = {"text": "", "metadata_status": "pending"}
    else:
        getattr(prepared, fault).side_effect = ValueError("Validation failed")
    with pytest.raises(ValueError):
        await stage_file_uploaded_document(args("create"))
    assert not staging.is_batch_open("user", "thread")
    assert await staging.get_pending_for_user("user") == []
    if fault in {"foreign_source", "unread"}:
        prepared.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_batch_is_preserved_and_not_composed(prepared):
    from app.agentive import staging

    await staging.open_batch(user_id="user", session_id="thread", label="Other work")
    with pytest.raises(ValueError, match="existing batch"):
        await stage_file_uploaded_document(args("create"))
    assert staging.is_batch_open("user", "thread")
    prepared.source.assert_not_awaited()
    await staging.cancel_batch(user_id="user", session_id="thread")


@pytest.mark.asyncio
@pytest.mark.parametrize("session", ["n.ChatThread.test", "legacy-session"])
async def test_dispatch_mints_exactly_one_batch_review(prepared, monkeypatch, session):
    from app.agentive import staging
    from app.agentive.tooling.dispatch import _dispatch_propose
    from app.agentive.tooling.manifest import load_manifest

    monkeypatch.setattr(
        "app.services.chat_threads.design_proposed_pending",
        AsyncMock(return_value=True),
    )
    minted = []

    async def mint(**kwargs):
        minted.append(kwargs)
        return SimpleNamespace(
            origin_proof={},
            to_dict=lambda: {
                "_kind": "staged_change",
                "kind": kwargs["kind"],
                "state": "pending",
            },
        )

    monkeypatch.setattr(staging, "create_staged_change", mint)
    monkeypatch.setattr("app.services.prompt_queue.enqueue_staged_write", AsyncMock())
    result = await _dispatch_propose(
        load_manifest()["integral_file_uploaded_document"],
        bindings.TOOL_BINDINGS["integral_file_uploaded_document"],
        args("create"),
        principal_id="user",
        scope="workspace",
        session_id=session,
    )
    assert not result.is_error
    assert len(minted) == 1
    assert not staging.is_batch_open("user", session)
    assert [op["kind"] for op in minted[0]["payload"]["operations"]] == [
        "create_entry",
        "attach_uploaded_file",
    ]
    assert result.data["state"] == "pending"


@pytest.mark.asyncio
async def test_partial_attach_failure_exposes_real_created_entry(prepared, monkeypatch):
    from app.agentive import staging_executors

    staged = await stage_file_uploaded_document(args("create"))
    seen = []

    async def execute(*, user_id, kind, payload):
        seen.append((kind, payload))
        if kind == "create_entry":
            return {"entry": {"id": "n.Entry.real", "title": "Project"}}
        return {
            "error": True,
            "error_code": "source_changed",
            "message": "Source changed",
        }

    monkeypatch.setattr(staging_executors, "dispatch", execute)
    result = await staging_executors._x_batch("user", staged["payload"])
    assert result["error_code"] == "batch_partial_failure"
    assert result["completed"] == 1
    assert seen[1][1]["entry_id"] == "n.Entry.real"
    assert seen[1][1]["expected_sha256"] == "a" * 64


@pytest.mark.asyncio
async def test_partial_retry_uses_saved_created_id_without_creating_twice(
    prepared, monkeypatch
):
    from app.agentive import staging_executors

    staged = await stage_file_uploaded_document(args("create"))
    progress = {}
    calls = []
    failed = True

    async def load(_kind):
        return "token", dict(progress)

    async def save(_token, value):
        progress.clear()
        progress.update(value or {})

    async def execute(*, user_id, kind, payload):
        nonlocal failed
        calls.append((kind, payload))
        if kind == "create_entry":
            return {"entry": {"id": "n.Entry.only-one", "title": "Project"}}
        if failed:
            failed = False
            return {"error": True, "message": "Source temporarily unavailable"}
        return {"attachment_id": "source", "entry_id": payload["entry_id"]}

    monkeypatch.setattr(staging_executors, "_load_execute_progress", load)
    monkeypatch.setattr(staging_executors, "_save_execute_progress", save)
    monkeypatch.setattr(staging_executors, "dispatch", execute)
    first = await staging_executors._x_batch("user", staged["payload"])
    assert first["completed"] == 1
    second = await staging_executors._x_batch("user", staged["payload"])
    assert not second.get("error")
    assert [kind for kind, _ in calls] == [
        "create_entry",
        "attach_uploaded_file",
        "attach_uploaded_file",
    ]
    assert calls[-1][1]["entry_id"] == "n.Entry.only-one"


@pytest.mark.asyncio
async def test_update_failure_and_already_filed_source_do_not_create(prepared):
    prepared.update.side_effect = ValueError("Target revision changed")
    with pytest.raises(ValueError, match="revision changed"):
        await stage_file_uploaded_document(args("update"))
    prepared.create.assert_not_awaited()
    prepared.attach.assert_not_awaited()
    prepared.source.return_value = (
        None,
        None,
        {"message": "Source is already filed", "error_code": "not_chat_owned"},
    )
    with pytest.raises(ValueError, match="already been filed"):
        await stage_file_uploaded_document(args("attach"))
    prepared.create.assert_not_awaited()
