"""Human revisions keep immutable approval, scope, rollback and restart guarantees."""

import asyncio

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.agentive import staging, staging_store
from app.agentive.services import entry_proposal_revision as revisions
from app.agentive.services.staging_apply import bless_and_execute
from app.agentive.staging import StagingError
from app.models.nodes import Entry
from app.schemas.agentive.staging import ReviseEntryProposalRequest
from app.services.app_graph import ensure_track_attached_operational_model
from tests.contract.test_staging_command_apply_postgres import proposal

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def test_revision_endpoint_accepts_the_browser_json_body(postgres_raw_db):
    from app.agentive.api.staging import revise_entry_proposal_endpoint
    from app.agentive.staging_executors import _call_endpoint_with_json
    from app.services.agent_scope import current_scope_workspace_id

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, sc, request = await fixture()
        scope = current_scope_workspace_id.set(workspace.id)
        try:
            result = await _call_endpoint_with_json(
                revise_entry_proposal_endpoint,
                auth.id,
                request.model_dump(),
                token=sc.token,
            )
        finally:
            current_scope_workspace_id.reset(scope)
        assert result["ok"], result
        assert result["staged_change"]["state"] == "pending"


async def test_batch_revision_preserves_other_steps_and_current_update_preconditions(
    postgres_raw_db, monkeypatch
):
    import copy

    from app.agentive.tooling import bindings
    from app.services.agent_scope import current_scope_workspace_id

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "88" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, track, sc, _ = await fixture()
        created = await bless_and_execute(token=sc.token, user_id=auth.id)
        entry_id = created["execute_result"]["entry"]["id"]
        scope = current_scope_workspace_id.set(workspace.id)
        principal = bindings._propose_principal.set(auth.id)
        try:
            update = await bindings._stage_update_entry(
                {"entry_id": entry_id, "updates": {"body": "Proposed update"}}
            )
            untouched = {
                "kind": "create_entry",
                "summary": "Keep unchanged",
                "diff_human": "Keep unchanged",
                "diff_machine": {},
                "payload": {
                    "track_id": track.id,
                    "title": "Unchanged second operation",
                },
            }
            operations = [update, copy.deepcopy(untouched)]
            batch = await staging.create_staged_change(
                user_id=auth.id,
                session_id=None,
                workspace_id=workspace.id,
                kind="batch",
                summary="Two changes",
                diff_human="Two changes",
                diff_machine={"operations": operations},
                payload={"operations": operations},
            )
        finally:
            bindings._propose_principal.reset(principal)
            current_scope_workspace_id.reset(scope)
        form = await revisions.entry_proposal_form(
            token=batch.token, user_id=auth.id, workspace_id=workspace.id
        )
        request = ReviseEntryProposalRequest(
            operation_index=0,
            revision=form["entries"][0]["revision"],
            draft={"title": "Human-edited existing record", "body": "Reviewed update"},
        )
        new = await revisions.revise_entry_proposal(
            token=batch.token,
            user_id=auth.id,
            workspace_id=workspace.id,
            request=request,
        )
        assert new.payload["operations"][1] == untouched
        assert new.payload["operations"][0]["payload"]["entry_id"] == entry_id
        assert (
            new.payload["operations"][0]["payload"]["expected_record_revision"]
            == update["payload"]["expected_record_revision"]
        )
        assert (
            await Entry.find_one({"id": entry_id})
        ).title != "Human-edited existing record"
        result = await bless_and_execute(token=new.token, user_id=auth.id)
        assert result["consumed"], result
        assert (
            await Entry.find_one({"id": entry_id})
        ).title == "Human-edited existing record"
        assert len(await Entry.find({"track_id": track.id})) == 2


@pytest.mark.parametrize("rollback", [False, True])
async def test_linked_work_keeps_original_authority_and_rolls_back_with_revision(
    postgres_raw_db, monkeypatch, rollback
):
    from app.agentive.work_models import WorkApproval, WorkItem

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, sc, request = await fixture()
        work = await WorkItem.create(
            id="o.WorkItem.revision-fixture",
            work_item_id="revision-fixture",
            status="waiting_for_human",
            principal_id=auth.id,
            workspace_id=workspace.id,
        )
        approval = await WorkApproval.create(
            id="o.WorkApproval.revision-fixture",
            work_approval_id="revision-fixture",
            work_item_id=work.work_item_id,
            staging_token=sc.token,
            authority_digest="original-capability-proof",
        )
        if rollback:

            async def fail(*_):
                raise RuntimeError("Rollback after linked decision")

            monkeypatch.setattr(revisions, "_replace_prompt", fail)
            with pytest.raises(RuntimeError):
                await revisions.revise_entry_proposal(
                    token=sc.token,
                    user_id=auth.id,
                    workspace_id=workspace.id,
                    request=request,
                )
        else:
            new = await revisions.revise_entry_proposal(
                token=sc.token,
                user_id=auth.id,
                workspace_id=workspace.id,
                request=request,
            )
            assert new.origin_proof is None
            assert new.state == "pending"
        held = await postgres_raw_db.get("object", approval.id)
        item = await postgres_raw_db.get("object", work.id)
        assert held["context"]["authority_digest"] == "original-capability-proof"
        assert held["context"]["staging_token"] == sc.token
        assert held["context"]["status"] == ("pending" if rollback else "rejected")
        assert item["context"]["status"] == (
            "waiting_for_human" if rollback else "failed"
        )
        assert (await WorkApproval.get(approval.id)).status == (
            "pending" if rollback else "rejected"
        )
        assert (await WorkItem.get(work.id)).status == (
            "waiting_for_human" if rollback else "failed"
        )


async def test_revoked_workspace_access_cannot_create_a_revision(postgres_raw_db):
    from app.models.edges import IS_MEMBER_OF
    from app.models.nodes import User

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, sc, request = await fixture()
        user = await User.find_one({"user_id": auth.id})
        context = await user.get_context()
        for edge in await context.find_edges_between(
            user.id, workspace.id, edge_class=IS_MEMBER_OF
        ):
            await edge.delete()
        with pytest.raises(StagingError, match="no longer"):
            await revisions.revise_entry_proposal(
                token=sc.token,
                user_id=auth.id,
                workspace_id=workspace.id,
                request=request,
            )
        assert (await staging_store.load_current(sc.token)).state == "pending"


async def test_native_thread_queue_and_human_edit_message_commit_together(
    postgres_raw_db,
):
    from app.services.chat_threads import create_thread, list_messages
    from app.services.prompt_queue import enqueue_staged_write, get_queue

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, sc, _ = await fixture()
        thread = await create_thread(
            user_id=auth.id, provider_id="integral_native", workspace_id=workspace.id
        )
        # Native checkpoint IDs are intentionally different from review identity.
        thread.provider_session_id = "rotating-checkpoint-session"
        await thread.save()
        await staging.revoke_token(user_id=auth.id, token=sc.token)
        sc = await staging.create_staged_change(
            user_id=auth.id,
            session_id=thread.id,
            workspace_id=workspace.id,
            kind=sc.kind,
            summary=sc.summary,
            diff_human=sc.diff_human,
            diff_machine=sc.diff_machine,
            payload=sc.payload,
        )
        await enqueue_staged_write(
            user_id=auth.id, session_id=thread.id, staged=sc.to_dict()
        )
        form = await revisions.entry_proposal_form(
            token=sc.token, user_id=auth.id, workspace_id=workspace.id
        )
        request = ReviseEntryProposalRequest(
            revision=form["entries"][0]["revision"],
            draft={"title": "Edited in native chat"},
        )
        new = await revisions.revise_entry_proposal(
            token=sc.token, user_id=auth.id, workspace_id=workspace.id, request=request
        )
        from app.models.nodes import ChatThread

        fresh = await ChatThread.find_one({"id": thread.id})
        assert [item["token"] for item in get_queue(fresh)["items"]] == [new.token]
        messages = await list_messages(fresh)
        assert messages[-1].parts[1]["result"]["token"] == new.token
        assert messages[-1].provider_metadata["source"] == "human_proposal_revision"


async def fixture():
    auth, workspace, track, sc = await proposal()
    await ensure_track_attached_operational_model(track)
    form = await revisions.entry_proposal_form(
        token=sc.token, user_id=auth.id, workspace_id=workspace.id
    )
    assert len(form["entries"]) == 1
    request = ReviseEntryProposalRequest(
        revision=form["entries"][0]["revision"],
        draft={
            "title": "Human-reviewed title",
            "body": "Human-reviewed content",
            "fields": {},
            "tags": [],
        },
    )
    return auth, workspace, track, sc, request


async def test_revision_requires_new_approval_and_survives_restart(
    postgres_raw_db, monkeypatch
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "88" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, track, sc, request = await fixture()
        original = dict(sc.payload)
        new = await revisions.revise_entry_proposal(
            token=sc.token, user_id=auth.id, workspace_id=workspace.id, request=request
        )
        assert sc.payload == original
        assert new.state == "pending" and new.origin_proof is None
        assert new.supersedes_token == sc.token
        assert new.token != sc.token
        assert await Entry.find({"track_id": track.id}) == []
        with pytest.raises(StagingError):
            await staging.bless_token(token=sc.token, user_id=auth.id)
        staging._tokens.clear()
        restored = await staging.get_token(new.token)
        assert restored.payload["title"] == "Human-reviewed title"
        assert restored.entry_edit_fences == new.entry_edit_fences
        result = await bless_and_execute(token=new.token, user_id=auth.id)
        assert result["consumed"], result
        entries = await Entry.find({"track_id": track.id})
        assert [(e.title, e.body) for e in entries] == [
            ("Human-reviewed title", "Human-reviewed content")
        ]
        marker = await staging_store.load_decision_state(sc.token, auth.id)
        assert marker["superseded_by"] == new.token


async def test_human_replacement_drops_legacy_authority_and_decision_metadata(
    postgres_raw_db,
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, sc, request = await fixture()
        rows = await postgres_raw_db.find(
            "object",
            {"entity": "StagedChangeRecord", "context.token": sc.token},
            limit=1,
        )
        await postgres_raw_db.find_one_and_update(
            "object",
            {"id": rows[0]["id"]},
            {
                "$set": {
                    "context.origin_proof": {"grant": "old-agent-only-authority"},
                    "context.autonomy_grant_used": True,
                    "context.blessed_at": sc.created_at.isoformat(),
                    "context.resolved_at": sc.created_at.isoformat(),
                    "context.last_error": {"message": "Old decision failure"},
                }
            },
        )
        staging._tokens.clear()
        new = await revisions.revise_entry_proposal(
            token=sc.token, user_id=auth.id, workspace_id=workspace.id, request=request
        )
        assert new.state == "pending" and new.origin_proof is None
        assert not new.autonomy_grant_used
        assert new.blessed_at is None and new.resolved_at is None
        assert new.last_error is None and new.progress is None
        assert not new.executing and new.execute_started_at is None


@pytest.mark.parametrize("kind", ["readonly", "file", "computed", "undeclared"])
async def test_form_cannot_rewrite_protected_fields(postgres_raw_db, kind):
    from app.models.nodes import EntryType

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, track, sc, _ = await fixture()
        context = (await revisions._contexts(sc))[0]
        entry_type = await EntryType.get(context["fence"]["type_id"])
        entry_type.form_schema = {
            **entry_type.form_schema,
            "fields": (
                []
                if kind == "undeclared"
                else [
                    {
                        "key": "protected",
                        "name": "Protected",
                        "type": "text" if kind == "readonly" else kind,
                        "readonly": kind == "readonly",
                        **({"expression": "1 + 1"} if kind == "computed" else {}),
                    }
                ]
            ),
        }
        await entry_type.save()
        form = await revisions.entry_proposal_form(
            token=sc.token, user_id=auth.id, workspace_id=workspace.id
        )
        request = ReviseEntryProposalRequest(
            revision=form["entries"][0]["revision"],
            draft={"title": "Edited", "fields": {"protected": "changed"}},
        )
        with pytest.raises(StagingError) as error:
            await revisions.revise_entry_proposal(
                token=sc.token,
                user_id=auth.id,
                workspace_id=workspace.id,
                request=request,
            )
        assert error.value.code == "invalid_entry_draft"
        assert (await staging_store.load_current(sc.token)).state == "pending"
        assert await Entry.find({"track_id": track.id}) == []


@pytest.mark.parametrize("kind", ["readonly", "file"])
async def test_omitting_a_locked_field_preserves_its_proposed_value(
    postgres_raw_db, kind
):
    from app.models.nodes import EntryType

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, sc, _ = await fixture()
        context = (await revisions._contexts(sc))[0]
        entry_type = await EntryType.get(context["fence"]["type_id"])
        entry_type.form_schema = {
            **entry_type.form_schema,
            "fields": [
                {
                    "key": "protected",
                    "type": "text" if kind == "readonly" else kind,
                    "readonly": kind == "readonly",
                }
            ],
        }
        await entry_type.save()
        await staging.revoke_token(user_id=auth.id, token=sc.token)
        original = await staging.create_staged_change(
            user_id=auth.id,
            workspace_id=workspace.id,
            session_id=None,
            kind=sc.kind,
            summary=sc.summary,
            diff_human=sc.diff_human,
            diff_machine=sc.diff_machine,
            payload={**sc.payload, "fields": {"protected": "original-binding"}},
        )
        form = await revisions.entry_proposal_form(
            token=original.token, user_id=auth.id, workspace_id=workspace.id
        )
        request = ReviseEntryProposalRequest(
            revision=form["entries"][0]["revision"],
            draft={"title": "Edited without resending the locked field"},
        )
        new = await revisions.revise_entry_proposal(
            token=original.token,
            user_id=auth.id,
            workspace_id=workspace.id,
            request=request,
        )
        assert new.payload["fields"]["protected"] == "original-binding"


@pytest.mark.parametrize("wrong", ["user", "workspace", "revision"])
async def test_revision_rejects_wrong_scope_or_stale_form(postgres_raw_db, wrong):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, sc, request = await fixture()
        if wrong == "revision":
            request.revision = "0" * 64
        with pytest.raises(StagingError):
            await revisions.revise_entry_proposal(
                token=sc.token,
                user_id="another-user" if wrong == "user" else auth.id,
                workspace_id=(
                    "another-workspace" if wrong == "workspace" else workspace.id
                ),
                request=request,
            )
        assert (await staging_store.load_current(sc.token)).state == "pending"


async def test_rollback_leaves_only_original_proposal(postgres_raw_db, monkeypatch):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, sc, request = await fixture()

        async def fail(*_):
            raise RuntimeError("Injected queue failure")

        monkeypatch.setattr(revisions, "_replace_prompt", fail)
        before = await postgres_raw_db.find("object", {"entity": "StagedChangeRecord"})
        with pytest.raises(RuntimeError, match="Injected"):
            await revisions.revise_entry_proposal(
                token=sc.token,
                user_id=auth.id,
                workspace_id=workspace.id,
                request=request,
            )
        assert (
            await postgres_raw_db.find("object", {"entity": "StagedChangeRecord"})
            == before
        )
        assert (await staging.get_token(sc.token)).state == "pending"
        assert await staging_store.load_decision_state(sc.token, auth.id) is None


@pytest.mark.parametrize("competitor", ["approve", "edit"])
async def test_one_winner_between_edit_and_concurrent_decision(
    postgres_raw_db, competitor
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, sc, request = await fixture()

        async def edit():
            return await revisions.revise_entry_proposal(
                token=sc.token,
                user_id=auth.id,
                workspace_id=workspace.id,
                request=request,
            )

        other = (
            staging.bless_token(token=sc.token, user_id=auth.id)
            if competitor == "approve"
            else edit()
        )
        results = await asyncio.gather(edit(), other, return_exceptions=True)
        assert (
            sum(isinstance(value, staging.StagedChange) for value in results) == 1
        ), results
        assert sum(isinstance(value, StagingError) for value in results) == 1, results


async def test_changed_schema_cannot_apply_edited_proposal(
    postgres_raw_db, monkeypatch
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "88" * 32)
    from app.models.nodes import EntryType

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, track, sc, request = await fixture()
        new = await revisions.revise_entry_proposal(
            token=sc.token, user_id=auth.id, workspace_id=workspace.id, request=request
        )
        entry_type = await EntryType.get(new.entry_edit_fences["0"]["type_id"])
        entry_type.form_schema = {
            **entry_type.form_schema,
            "fields": [{"key": "team", "name": "Team", "type": "text"}],
        }
        await entry_type.save()
        result = await bless_and_execute(token=new.token, user_id=auth.id)
        assert not result["consumed"], result
        assert await Entry.find({"track_id": track.id}) == []
