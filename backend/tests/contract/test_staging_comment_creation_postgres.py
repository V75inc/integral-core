"""Real comment effects, immutable inputs and restart-safe staged recovery."""

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.agentive import staging
from app.agentive.services import staging_apply, staging_execution
from app.agentive.staging import StagingError, create_staged_change
from app.models.edges import CONTAINS
from app.models.nodes import Comment, Entry
from app.services.agent_scope import current_scope_workspace_id
from app.services.comment_creation import create_comment_record
from tests.contract.test_staging_command_apply_postgres import proposal

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def comment_proposal():
    auth, workspace, track, _ = await proposal()
    entry = await Entry.create(title="Comment target", track_id=track.id)
    await track.connect(entry, edge=CONTAINS)
    reset = current_scope_workspace_id.set(workspace.id)
    try:
        sc = await create_staged_change(
            user_id=auth.id,
            session_id=None,
            kind="add_comment",
            summary="Comment",
            diff_human="Comment",
            diff_machine={},
            payload={"entry_id": entry.id, "body": "Approved comment"},
        )
    finally:
        current_scope_workspace_id.reset(reset)
    return auth, workspace, track, entry, sc


async def test_comment_completion_failure_reconciles_without_duplicates(
    postgres_raw_db, monkeypatch
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, _, _, sc = await comment_proposal()
        consume = staging_apply.consume_token

        async def unavailable(**kwargs):
            raise StagingError("completion_unavailable", "Injected completion outage")

        monkeypatch.setattr(staging_apply, "consume_token", unavailable)
        first = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert first.get("application_state") == "reconciliation_required", first
        comments = await Comment.find()
        assert len(comments) == 1 and comments[0].text == "Approved comment"
        staging._tokens.clear()
        monkeypatch.setattr(staging_apply, "consume_token", consume)
        second = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert second["consumed"] is True, second
        assert second["execute_result"]["comment"]["id"] == comments[0].id
        assert second["execute_result"]["execution_receipt"]["replayed"] is True
        assert len(await Comment.find()) == 1


async def test_receipt_failure_rolls_back_comment_and_links(
    postgres_raw_db, monkeypatch
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, _, _, sc = await comment_proposal()

        async def unavailable(**kwargs):
            raise RuntimeError("Injected receipt failure")

        monkeypatch.setattr(
            staging_execution, "record_staging_execution_result", unavailable
        )
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert not result.get("consumed"), result
        assert await Comment.find() == []
        assert await postgres_raw_db.find("edge", {"entity": "AUTHORED_BY"}) == []


async def test_reply_parent_cannot_cross_entries(postgres_raw_db):
    from app.api.errors import ResourceNotFoundError

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, track, entry, _ = await comment_proposal()
        reset = current_scope_workspace_id.set(workspace.id)
        try:
            parent = await create_comment_record(
                user_id=auth.id, entry_id=entry.id, text="Parent"
            )
            other = await Entry.create(title="Other target", track_id=track.id)
            await track.connect(other, edge=CONTAINS)
            with pytest.raises(ResourceNotFoundError):
                await create_comment_record(
                    user_id=auth.id,
                    entry_id=other.id,
                    text="Forbidden reply",
                    parent_id=parent.comment.id,
                )
            assert [row.text for row in await Comment.find()] == ["Parent"]
        finally:
            current_scope_workspace_id.reset(reset)


async def test_actual_staged_body_must_match_approved_input(postgres_raw_db):
    from app.agentive.services.staging_command_apply import apply_staged_command_once
    from app.agentive.staging import bless_token

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, entry, sc = await comment_proposal()
        await bless_token(user_id=auth.id, token=sc.token)

        async def altered():
            await create_comment_record(
                user_id=auth.id, entry_id=entry.id, text="Altered body"
            )
            return {}

        reset = current_scope_workspace_id.set(workspace.id)
        try:
            with pytest.raises(StagingError):
                await apply_staged_command_once(sc=sc, request=None, execute=altered)
            assert await Comment.find() == []
        finally:
            current_scope_workspace_id.reset(reset)


async def test_reply_event_retains_authorized_content_and_parent_reference(
    postgres_raw_db,
):
    from app.services.change_event_logger import ChangeEventEnvelope
    from app.services.event_access import prepare_event_payload
    from app.services.policy_engine import evaluate

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, track, entry, _ = await comment_proposal()
        parent = await create_comment_record(
            user_id=auth.id, entry_id=entry.id, text="Parent"
        )
        reply = await create_comment_record(
            user_id=auth.id,
            entry_id=entry.id,
            text="A literal n.Entry.example in the authorized text",
            parent_id=parent.comment.id,
        )
        event = ChangeEventEnvelope(
            id="reply-proof",
            ts="2026-10-14T00:00:00Z",
            actor_kind="human",
            actor_id=auth.id,
            action="comment.create",
            resource_type="Comment",
            resource_id=reply.comment.id,
            scope=f"track:{track.id}",
            before=None,
            after={
                "id": reply.comment.id,
                "author_id": auth.id,
                "text": reply.comment.text,
                "parent_id": parent.comment.id,
                "_entry_id": entry.id,
            },
        )
        payload = await prepare_event_payload(
            auth.id, event, action="audit_log.read", evaluate=evaluate
        )
        assert payload["after"] == event.after


async def test_comment_receipt_survives_revoked_workspace_access(postgres_raw_db):
    from app.agentive.services.staging_command_apply import _readback_comment
    from tests.contract.test_release_admission_postgres import remove_membership

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, entry, sc = await comment_proposal()
        first = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        comment_id = first["execute_result"]["comment"]["id"]
        await remove_membership(auth, workspace)
        staging._tokens.clear()
        result = await _readback_comment(
            sc, workspace.id, {"entry_id": entry.id, "comment_id": comment_id}
        )
        assert result["applied"] is True
        assert result["result_access"] == "withheld"
        assert result["execution_receipt"]["status"] == "applied"
        assert comment_id not in str(result)
        assert entry.id not in str(result)
        assert len(await Comment.find()) == 1
        assert current_scope_workspace_id.get() is None


async def test_comment_readback_does_not_hide_authorization_outage(
    postgres_raw_db, monkeypatch
):
    from contextlib import asynccontextmanager

    from app.agentive.services.staging_command_apply import _readback_comment
    from app.exceptions import AuthorizationUnavailableError
    from app.services import release_admission

    @asynccontextmanager
    async def unavailable(*args):
        raise AuthorizationUnavailableError()
        yield

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        _, workspace, _, entry, sc = await comment_proposal()
        monkeypatch.setattr(release_admission, "release_admission", unavailable)
        with pytest.raises(AuthorizationUnavailableError):
            await _readback_comment(
                sc, workspace.id, {"entry_id": entry.id, "comment_id": "missing"}
            )
        assert current_scope_workspace_id.get() is None


@pytest.mark.parametrize("kind", ["edit_comment", "delete_comment"])
async def test_comment_mutation_completion_recovers_once(
    postgres_raw_db, monkeypatch, kind
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, entry, _ = await comment_proposal()
        original = await create_comment_record(
            user_id=auth.id, entry_id=entry.id, text="Original"
        )
        payload = {"comment_id": original.comment.id}
        if kind == "edit_comment":
            payload["body"] = "Revised"
        reset = current_scope_workspace_id.set(workspace.id)
        try:
            sc = await create_staged_change(
                user_id=auth.id,
                session_id=None,
                kind=kind,
                summary="Mutation",
                diff_human="Mutation",
                diff_machine={},
                payload=payload,
            )
        finally:
            current_scope_workspace_id.reset(reset)
        consume = staging_apply.consume_token

        async def unavailable(**kwargs):
            raise StagingError("completion_unavailable", "Injected completion outage")

        monkeypatch.setattr(staging_apply, "consume_token", unavailable)
        first = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert first["application_state"] == "reconciliation_required", first
        rows = await Comment.find()
        if kind == "edit_comment":
            assert len(rows) == 1 and rows[0].text == "Revised"
            updated_at = rows[0].updated_at
        else:
            assert rows == []
        staging._tokens.clear()
        monkeypatch.setattr(staging_apply, "consume_token", consume)
        second = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert second["consumed"] is True, second
        result = second["execute_result"]
        assert result["execution_receipt"]["replayed"] is True
        if kind == "edit_comment":
            assert result["comment"]["text"] == "Revised"
            assert (await Comment.find())[0].updated_at == updated_at
        else:
            assert result["deleted"] is True
            assert original.comment.id not in str(result)
            assert await Comment.find() == []


@pytest.mark.parametrize("delete", [False, True])
async def test_comment_mutation_receipt_failure_rolls_back(
    postgres_raw_db, monkeypatch, delete
):
    from app.agentive.services.staging_command_apply import apply_staged_command_once
    from app.agentive.staging import bless_token
    from app.services.comment_creation import mutate_comment_record

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, entry, _ = await comment_proposal()
        created = await create_comment_record(
            user_id=auth.id, entry_id=entry.id, text="Original"
        )
        payload = {"comment_id": created.comment.id}
        kind = "delete_comment" if delete else "edit_comment"
        if not delete:
            payload["text"] = "Changed"
        reset = current_scope_workspace_id.set(workspace.id)
        try:
            sc = await create_staged_change(
                user_id=auth.id,
                session_id=None,
                kind=kind,
                summary="Mutation",
                diff_human="Mutation",
                diff_machine={},
                payload=payload,
            )
            await bless_token(user_id=auth.id, token=sc.token)

            async def unavailable(**kwargs):
                raise RuntimeError("Injected receipt outage")

            async def execute():
                return await mutate_comment_record(
                    user_id=auth.id,
                    comment_id=created.comment.id,
                    text="Changed",
                    delete=delete,
                )

            monkeypatch.setattr(
                staging_execution, "record_staging_execution_result", unavailable
            )
            with pytest.raises(RuntimeError, match="Injected receipt outage"):
                await apply_staged_command_once(sc=sc, request=None, execute=execute)
            # Read primary storage; an earlier hydrated node is not rollback proof.
            row = await postgres_raw_db.get("node", created.comment.id)
            assert row["context"]["text"] == "Original"
            rows = await Comment.find()
            assert len(rows) == 1
            assert (
                await rows[0].nodes(
                    edge=["HAS_COMMENT"], direction="in", node=["Entry"]
                )
            )[0].id == entry.id
        finally:
            current_scope_workspace_id.reset(reset)
