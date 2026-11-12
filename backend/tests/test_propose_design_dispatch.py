"""integral_propose_design records the thread marker + blueprint artifact (no card)."""

from __future__ import annotations

import pytest

from app.agentive.tooling.dispatch import dispatch_tool
from app.models.edges import CONTAINS
from app.models.nodes import ChatMessage, ChatThread

_BLUEPRINT = {
    "app": {"id": "app", "name": "Demo"},
    "tracks": [
        {
            "id": "track.contacts",
            "name": "Contacts",
            "entry_types": [
                {
                    "name": "Contact",
                    "fields": [{"key": "email", "name": "Email", "type": "text"}],
                }
            ],
        }
    ],
}

_PROPOSAL = (
    "**Demo** app (fresh).\n\n"
    "- **Contacts** — name, email, company\n"
    "- **Deals** — amount, stage, contact relation\n"
    "- Views: Contacts table, Deals kanban\n"
)


@pytest.fixture(autouse=True)
def _reset_staging():
    from app.agentive.staging import _reset_for_tests

    _reset_for_tests()
    yield
    _reset_for_tests()


async def _thread(session_id: str, n_user: int, user_id: str = "u1") -> ChatThread:
    t = await ChatThread.create(user_id=user_id, provider_session_id=session_id)
    for _ in range(n_user):
        m = await ChatMessage.create(role="user", thread_id=t.id)
        await t.connect(m, edge=CONTAINS)
    return t


async def _approve_design(session_id: str, user_id: str = "u1") -> None:
    """Stamp chat-affirm on the thread marker (no staged design card)."""
    from app.services import chat_threads

    thread = await chat_threads.get_thread_by_session(session_id)
    assert thread is not None
    assert await chat_threads.stamp_design_approved(
        thread=thread, utterance="Looks good, please proceed"
    )


@pytest.mark.asyncio
async def test_dispatch_propose_design_records_marker(
    bind_fresh_graph_context_for_async_tests,
):
    """Dispatching with a live session_id records the design_proposed marker."""
    thread = await _thread("sess-PD", 1, user_id="u1")
    res = await dispatch_tool(
        "integral_propose_design",
        {
            "summary": "App X: tracks A, B",
            "proposal": _PROPOSAL,
            "blueprint": _BLUEPRINT,
        },
        principal_id="u1",
        scope="ws1",
        session_id="sess-PD",
    )
    assert not res.is_error, res
    assert res.data.get("_kind") == "design_outline"
    assert res.data.get("token") is None
    assert res.data.get("proposal")
    assert res.data.get("artifact_key") == "app_design_blueprint"
    reloaded = await ChatThread.get(thread.id)
    assert reloaded.design_proposed is not None
    assert reloaded.design_proposed["proposed_at_user_turn"] == 1
    assert reloaded.design_proposed.get("artifact_key") == "app_design_blueprint"
    arts = reloaded.artifacts or {}
    assert "app_design_blueprint" in arts
    assert arts["app_design_blueprint"]["body"].startswith("**Demo**")
    assert "Contacts" in arts["app_design_blueprint"]["body"]
    # A resumed model needs the typed revision, not only the readable preview.
    # Even older or overwritten scratch metadata is not design authority.
    arts["app_design_blueprint"]["metadata"]["blueprint"] = {"app": {"name": "Stale"}}
    arts["app_design_blueprint"]["body"] = "Stale scratch preview"
    reloaded.artifacts = arts
    await reloaded.save()
    from app.agentive.artifacts import get_artifact

    got = await get_artifact(
        user_id="u1", session_id="sess-PD", key="app_design_blueprint"
    )
    assert got["blueprint"] == reloaded.design_proposed["blueprint"]
    assert "Stale" not in str(got)
    assert got["blueprint_revision"] == reloaded.design_proposed["blueprint_revision"]
    assert got["blueprint_digest"] == reloaded.design_proposed["blueprint_digest"]
    assert got["body"] == reloaded.design_proposed["proposal"]

    denied = await get_artifact(
        user_id="other", session_id="sess-PD", key="app_design_blueprint"
    )
    assert denied["error"] == "not_found"
    assert "blueprint" not in denied
    # No Prompt Sheet / staged design card.
    from app.services.prompt_queue import get_queue, queue_is_open

    assert queue_is_open(reloaded) is False
    queue = get_queue(reloaded)
    writes = [
        i
        for i in (queue.get("items") or [])
        if i.get("kind") == "staged_write" and i.get("write_kind") == "design_proposal"
    ]
    assert writes == []


@pytest.mark.asyncio
async def test_dispatch_propose_design_replace_updates_artifact(
    bind_fresh_graph_context_for_async_tests,
):
    await _thread("sess-PD-idem", 1, user_id="u1")
    first = await dispatch_tool(
        "integral_propose_design",
        {
            "summary": "App X: tracks A, B",
            "proposal": _PROPOSAL,
            "blueprint": _BLUEPRINT,
        },
        principal_id="u1",
        scope="ws1",
        session_id="sess-PD-idem",
    )
    amended = _PROPOSAL + "\n- **Notes** — free text\n"
    second = await dispatch_tool(
        "integral_propose_design",
        {
            "summary": "App X: tracks A, B, Notes",
            "proposal": amended,
            "blueprint": _BLUEPRINT,
        },
        principal_id="u1",
        scope="ws1",
        session_id="sess-PD-idem",
    )
    assert not first.is_error, first
    assert not second.is_error, second
    assert second.data.get("replaced") is True
    assert second.data.get("artifact_version", 0) >= 2


@pytest.mark.asyncio
async def test_dispatch_create_without_batch_auto_opens_after_user_confirms(
    bind_fresh_graph_context_for_async_tests,
):
    """Confirm turn: create_app without begin_batch auto-opens the scaffold batch."""
    thread = await _thread("sess-batch-req", 1, user_id="u1")
    propose = await dispatch_tool(
        "integral_propose_design",
        {"summary": "Car Rental", "proposal": _PROPOSAL, "blueprint": _BLUEPRINT},
        principal_id="u1",
        scope="ws1",
        session_id="sess-batch-req",
    )
    assert not propose.is_error, propose
    await _approve_design("sess-batch-req")

    m = await ChatMessage.create(role="user", thread_id=thread.id)
    await thread.connect(m, edge=CONTAINS)

    first = await dispatch_tool(
        "integral_create_app",
        {
            "name": "Car Rental Management",
            "description": "Fleet, renters, and rentals",
        },
        principal_id="u1",
        scope="ws1",
        session_id="sess-batch-req",
    )
    assert not first.is_error, first
    assert first.data.get("batched") is True
    assert first.data.get("batch_auto_opened") is True
    assert first.data.get("kind") == "create_app"

    orphan = await dispatch_tool(
        "integral_create_track",
        {"name": "Cars", "description": "Fleet of cars available for rent"},
        principal_id="u1",
        scope="ws1",
        session_id="sess-batch-req",
    )
    assert orphan.is_error
    assert orphan.error_code == "scaffold_track_requires_app"


@pytest.mark.asyncio
async def test_dispatch_create_app_requires_recorded_design_in_chat(
    bind_fresh_graph_context_for_async_tests,
):
    """A prose-only plan cannot turn into a second approval card."""
    await _thread("sess-design-required", 1, user_id="u1")

    result = await dispatch_tool(
        "integral_create_app",
        {"name": "Vehicle Maintenance"},
        principal_id="u1",
        scope="ws1",
        session_id="sess-design-required",
    )

    assert result.is_error
    assert result.error_code == "design_required"
    assert "integral_propose_design" in result.message


@pytest.mark.asyncio
async def test_dispatch_create_app_recovers_an_affirmed_visible_design(
    bind_fresh_graph_context_for_async_tests,
):
    """A model's prose design does not force the user to repeat confirmation."""
    from app.services import chat_threads

    thread = await _thread("sess-visible-design", 0, user_id="u1")
    await chat_threads.append_message(
        thread=thread,
        role="user",
        parts=[{"type": "text", "text": "Build a facilities inspection app."}],
    )
    await chat_threads.append_message(
        thread=thread,
        role="assistant",
        parts=[
            {
                "type": "text",
                "text": (
                    "Facilities Inspection app design:\n"
                    "- Track: Inspections with Asset Name, Inspection Date, and Result fields.\n"
                    "- Views: a table for all inspections and a calendar by Inspection Date."
                ),
            }
        ],
    )
    await chat_threads.append_message(
        thread=thread,
        role="user",
        parts=[{"type": "text", "text": "Confirmed. Build it now."}],
    )

    result = await dispatch_tool(
        "integral_create_app",
        {"name": "Facilities Inspection"},
        principal_id="u1",
        scope="ws1",
        session_id="sess-visible-design",
    )

    assert not result.is_error, result
    assert result.data.get("batched") is True
    reloaded = await ChatThread.get(thread.id)
    assert reloaded.design_proposed is not None
    assert reloaded.design_proposed["approved_via"] == "visible_chat_design_affirm"
    assert (reloaded.artifacts or {})["app_design_blueprint"]["metadata"]["source"] == (
        "visible_chat_design_affirm"
    )


@pytest.mark.asyncio
async def test_dispatch_scaffold_batch_refuses_track_without_app(
    bind_fresh_graph_context_for_async_tests,
):
    """Inside an open scaffold batch, create_track still needs app_id."""
    thread = await _thread("sess-track-app", 1, user_id="u1")
    propose = await dispatch_tool(
        "integral_propose_design",
        {"summary": "Car Rental", "proposal": _PROPOSAL, "blueprint": _BLUEPRINT},
        principal_id="u1",
        scope="ws1",
        session_id="sess-track-app",
    )
    assert not propose.is_error, propose
    await _approve_design("sess-track-app")

    m = await ChatMessage.create(role="user", thread_id=thread.id)
    await thread.connect(m, edge=CONTAINS)

    fresh_manual = await dispatch_tool(
        "integral_begin_batch",
        {"label": "Car Rental"},
        principal_id="u1",
        scope="ws1",
        session_id="sess-track-app",
    )
    assert fresh_manual.error_code == "use_approved_build_tool"

    opened = await dispatch_tool(
        "integral_begin_batch",
        {"label": "Car Rental", "manual_recovery": True},
        principal_id="u1",
        scope="ws1",
        session_id="sess-track-app",
    )
    assert not opened.is_error, opened

    orphan = await dispatch_tool(
        "integral_create_track",
        {"name": "Cars", "description": "Fleet of cars available for rent"},
        principal_id="u1",
        scope="ws1",
        session_id="sess-track-app",
    )
    assert orphan.is_error
    assert orphan.error_code == "scaffold_track_requires_app"


@pytest.mark.asyncio
async def test_artifact_upsert_get_list(
    bind_fresh_graph_context_for_async_tests, monkeypatch
):
    thread = await _thread("sess-art", 1, user_id="u1")
    thread.workspace_id = "ws1"
    await thread.save()
    from unittest.mock import AsyncMock

    from app.services import workspace_permissions

    monkeypatch.setattr(
        workspace_permissions, "can_access_workspace", AsyncMock(return_value="member")
    )
    up = await dispatch_tool(
        "integral_upsert_artifact",
        {
            "key": "checklist",
            "kind": "checklist",
            "title": "Build checks",
            "body": "- [ ] tracks\n- [ ] views\n",
        },
        principal_id="u1",
        scope="ws1",
        session_id="sess-art",
    )
    assert not up.is_error, up
    assert up.data.get("version") == 1
    stale = await dispatch_tool(
        "integral_upsert_artifact",
        {
            "key": "checklist",
            "kind": "checklist",
            "body": "stale",
            "expected_version": 0,
        },
        principal_id="u1",
        scope="ws1",
        session_id="sess-art",
    )
    assert stale.is_error and stale.error_code == "version_conflict"

    got = await dispatch_tool(
        "integral_get_artifact",
        {"key": "checklist"},
        principal_id="u1",
        scope="ws1",
        session_id="sess-art",
    )
    assert not got.is_error, got
    assert "tracks" in (got.data.get("body") or "")

    listed = await dispatch_tool(
        "integral_list_artifacts",
        {"kind": "checklist"},
        principal_id="u1",
        scope="ws1",
        session_id="sess-art",
    )
    assert not listed.is_error, listed
    assert listed.data.get("count") == 1


@pytest.mark.asyncio
async def test_artifact_tools_refuse_foreign_scope_and_revoked_membership(monkeypatch):
    """A private draft cannot cross the active workspace or survive revocation."""
    from unittest.mock import AsyncMock

    from app.agentive import artifacts
    from app.services import workspace_permissions

    thread = await _thread("sess-scoped-artifact", 1, user_id="u1")
    thread.workspace_id = "ws1"
    await thread.save()
    allow = AsyncMock(return_value="member")
    monkeypatch.setattr(workspace_permissions, "can_access_workspace", allow)
    saved = await artifacts.upsert_artifact(
        user_id="u1",
        session_id="sess-scoped-artifact",
        workspace_id="ws1",
        key="draft",
        kind="note",
        body="private draft",
    )
    assert saved["ok"]
    for tool, args in (
        ("integral_get_artifact", {"key": "draft"}),
        ("integral_list_artifacts", {}),
        (
            "integral_upsert_artifact",
            {"key": "draft", "kind": "note", "body": "overwrite"},
        ),
    ):
        result = await dispatch_tool(
            tool,
            args,
            principal_id="u1",
            scope="ws2",
            session_id="sess-scoped-artifact",
        )
        assert result.is_error and result.error_code == "session_unavailable"
        assert "private draft" not in str(result)
    allow.return_value = "none"
    denied = await dispatch_tool(
        "integral_get_artifact",
        {"key": "draft"},
        principal_id="u1",
        scope="ws1",
        session_id="sess-scoped-artifact",
    )
    assert denied.is_error and denied.error_code == "session_unavailable"
    assert (await ChatThread.get(thread.id)).artifacts["draft"][
        "body"
    ] == "private draft"


@pytest.mark.asyncio
async def test_artifact_tool_refuses_ambiguous_provider_session(monkeypatch):
    """Two matching sessions are not resolved by arbitrary query order."""
    from unittest.mock import AsyncMock

    from app.services import workspace_permissions

    monkeypatch.setattr(
        workspace_permissions, "can_access_workspace", AsyncMock(return_value="member")
    )
    for _ in range(2):
        thread = await _thread("ambiguous-artifact-session", 1, user_id="u1")
        thread.workspace_id = "ws1"
        thread.artifacts = {"draft": {"body": "private", "kind": "note"}}
        await thread.save()
    result = await dispatch_tool(
        "integral_get_artifact",
        {"key": "draft"},
        principal_id="u1",
        scope="ws1",
        session_id="ambiguous-artifact-session",
    )
    assert result.is_error and result.error_code == "session_unavailable"
    assert "private" not in str(result)


@pytest.mark.asyncio
async def test_native_artifact_scope_uses_live_workspace_membership():
    """Scoped draft access follows actual workspace membership and revocation."""
    from jvspatial.api.auth.models import User as AuthUser

    from app.models.edges import IS_MEMBER_OF
    from app.models.nodes import User
    from app.services import chat_threads
    from app.services.app_graph import catalog_user, ensure_integral_app_graph
    from tests.fixtures.workspaces import make_org_workspace

    await ensure_integral_app_graph(include_library=False)
    user_id = "o.User.artifact-scope"
    await AuthUser.create(
        id=user_id, email="artifact-scope@example.com", password_hash="fixture"
    )
    user = await User.create(user_id=user_id, display_name="Tester")
    await catalog_user(user)
    first = await make_org_workspace("Drafts one", owner=user)
    second = await make_org_workspace("Drafts two", owner=user)
    thread = await chat_threads.create_thread(
        user_id=user_id, provider_id="integral_native", workspace_id=first.id
    )
    saved = await dispatch_tool(
        "integral_upsert_artifact",
        {"key": "draft", "kind": "note", "body": "private"},
        principal_id=user_id,
        scope=first.id,
        session_id=thread.id,
    )
    assert not saved.is_error
    wrong = await dispatch_tool(
        "integral_get_artifact",
        {"key": "draft"},
        principal_id=user_id,
        scope=second.id,
        session_id=thread.id,
    )
    assert wrong.is_error and wrong.error_code == "session_unavailable"
    graph = await user.get_context()
    memberships = await graph.find_edges_between(
        user.id, first.id, edge_class=IS_MEMBER_OF
    )
    assert memberships
    for membership in memberships:
        await membership.delete()
    revoked = await dispatch_tool(
        "integral_get_artifact",
        {"key": "draft"},
        principal_id=user_id,
        scope=first.id,
        session_id=thread.id,
    )
    assert revoked.is_error and revoked.error_code == "session_unavailable"
    assert "private" not in str(revoked)


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_artifact_revisions_serialize_and_reject_stale_writes():
    """Concurrent revisions cannot clobber other drafts or a newer version."""
    import asyncio

    from jvspatial.api.auth.models import User as AuthUser

    from app.agentive import artifacts
    from app.models.nodes import User
    from app.services import chat_threads
    from app.services.app_graph import catalog_user, ensure_integral_app_graph
    from tests.fixtures.workspaces import make_org_workspace

    await ensure_integral_app_graph(include_library=False)
    user_id = "o.User.artifact-concurrency"
    await AuthUser.create(
        id=user_id, email="artifact-concurrency@example.com", password_hash="fixture"
    )
    user = await User.create(user_id=user_id, display_name="Tester")
    await catalog_user(user)
    workspace = await make_org_workspace("Draft concurrency", owner=user)
    thread = await chat_threads.create_thread(
        user_id=user_id, provider_id="integral_native", workspace_id=workspace.id
    )
    scope = {"user_id": user_id, "workspace_id": workspace.id, "session_id": thread.id}
    first = await artifacts.upsert_artifact(
        **scope, key="draft", kind="note", body="initial", expected_version=0
    )
    assert first["version"] == 1
    results = await asyncio.gather(
        artifacts.upsert_artifact(
            **scope, key="draft", kind="note", body="left", expected_version=1
        ),
        artifacts.upsert_artifact(
            **scope, key="draft", kind="note", body="right", expected_version=1
        ),
        artifacts.upsert_artifact(
            **scope, key="other", kind="note", body="independent", expected_version=0
        ),
    )
    assert sum(item.get("error") == "version_conflict" for item in results) == 1
    assert sum(item.get("ok") is True for item in results) == 2
    draft = await artifacts.get_artifact(**scope, key="draft")
    other = await artifacts.get_artifact(**scope, key="other")
    assert draft["version"] == 2 and draft["body"] in {"left", "right"}
    assert other["body"] == "independent" and other["version"] == 1
    stale = await artifacts.upsert_artifact(
        **scope, key="draft", kind="note", body="stale", expected_version=1
    )
    assert stale["error"] == "version_conflict" and stale["current_version"] == 2
    assert (await artifacts.get_artifact(**scope, key="draft"))["body"] == draft["body"]

    initial_document = await artifacts.upsert_artifact(
        **scope,
        key="report",
        kind="document_draft",
        title="Report",
        body="Initial draft",
        metadata={"format": "markdown"},
        expected_version=0,
    )
    assert initial_document["version"] == 1
    revised, unfenced = await asyncio.gather(
        artifacts.upsert_artifact(
            **scope,
            key="report",
            kind="document_draft",
            title="Report",
            body="Reviewed revision",
            metadata={"format": "markdown"},
            expected_version=1,
        ),
        artifacts.upsert_artifact(
            **scope, key="report", kind="note", body="Unfenced replacement"
        ),
    )
    assert revised.get("ok") is True and revised["version"] == 2
    assert unfenced["error"] == "version_required"
    saved_document = await artifacts.get_artifact(**scope, key="report")
    assert saved_document["kind"] == "document_draft"
    assert saved_document["body"] == "Reviewed revision"


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [False, True])
async def test_missing_design_scratch_recovers_canonical_read_without_mutation(
    bind_fresh_graph_context_for_async_tests,
    approved,
):
    from copy import deepcopy

    from app.agentive.artifacts import get_artifact
    from app.services.design_blueprint import blueprint_digest

    thread = await _thread("recovery-design", 1)
    marker = {
        "artifact_key": "app_design_blueprint",
        "design_id": "saved-design",
        "blueprint": deepcopy(_BLUEPRINT),
        "blueprint_revision": 3,
        "blueprint_digest": blueprint_digest(_BLUEPRINT),
        "proposal": _PROPOSAL,
        "summary": "Demo",
        "approved": approved,
    }
    thread.design_proposed = deepcopy(marker)
    thread.artifacts = None
    await thread.save()
    result = await get_artifact(
        user_id="u1", session_id="recovery-design", key="app_design_blueprint"
    )
    assert result["ok"]
    assert result["blueprint"] == _BLUEPRINT
    assert result["blueprint_revision"] == 3
    assert result["version"] == 0
    assert result["metadata"] == {"source": "saved_design"}
    assert result["body"] == _PROPOSAL
    result["blueprint"]["app"]["name"] = "Changed client copy"
    reloaded = await ChatThread.get(thread.id)
    assert reloaded.design_proposed == marker
    assert reloaded.artifacts is None
    assert (
        await get_artifact(
            user_id="other", session_id="recovery-design", key="app_design_blueprint"
        )
    )["error"] == "not_found"
    assert (
        await get_artifact(user_id="u1", session_id="recovery-design", key="unrelated")
    )["error"] == "not_found"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [
        {"blueprint_digest": "sha256:invalid"},
        {"blueprint_revision": True},
        {"blueprint_revision": 0},
        {"design_id": ""},
        {"artifact_key": "other"},
    ],
)
async def test_missing_design_scratch_refuses_incomplete_authority(
    bind_fresh_graph_context_for_async_tests,
    invalid,
):
    from app.agentive.artifacts import get_artifact
    from app.services.design_blueprint import blueprint_digest

    thread = await _thread("invalid-recovery", 1)
    thread.design_proposed = {
        "artifact_key": "app_design_blueprint",
        "design_id": "saved-design",
        "blueprint": _BLUEPRINT,
        "blueprint_revision": 1,
        "blueprint_digest": blueprint_digest(_BLUEPRINT),
        **invalid,
    }
    await thread.save()
    result = await get_artifact(
        user_id="u1", session_id="invalid-recovery", key="app_design_blueprint"
    )
    assert result["error"] == "not_found"


@pytest.mark.asyncio
async def test_unresolved_proposal_reaches_native_final_clarification(monkeypatch):
    """Exercise real proposal dispatch, not a mock with a richer response."""
    from types import SimpleNamespace

    from app.agentive.harness.broker_tools import build_brokered_tools
    from app.agentive.harness.contracts import HarnessExecutionScope
    from app.schemas.capability_broker import CapabilityResult
    from app.services.chat_providers.pydantic_ai_provider import (
        _scaffold_completion_validator,
    )

    thread = await _thread("unresolved-dispatch", 1)
    question = "Should project updates also change the main project status?"
    blueprint = dict(
        _BLUEPRINT, open_decisions=[{"id": "q.filing", "question": question}]
    )
    state = {"capability_search_completed": True}
    dispatched = []

    async def invoke(**kwargs):
        result = await dispatch_tool(
            kwargs["capability_key"],
            kwargs["arguments"],
            principal_id="u1",
            scope="ws1",
            session_id="unresolved-dispatch",
        )
        dispatched.append(result)
        return CapabilityResult(
            ok=not result.is_error,
            data=result.data,
            error_code=result.error_code,
            message=result.message,
        )

    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability", invoke
    )
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.infer_source_and_op_class",
        lambda _: ("core", "propose"),
    )
    scope = HarnessExecutionScope(
        tenant_id="ws1",
        workspace_id="ws1",
        principal_id="u1",
        thread_id=thread.id,
        session_id="unresolved-dispatch",
        run_id="run1",
        permission_revision="p1",
        capability_version="c1",
    )
    tool = build_brokered_tools(
        scope=scope,
        run_state=state,
        catalogue=[
            {
                "name": "integral_propose_design",
                "description": "Propose",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string"},
                        "proposal": {"type": "string"},
                        "blueprint": {"type": "object"},
                    },
                },
            }
        ],
    )[0]
    result = await tool.function_schema.call(
        {"summary": "Demo", "proposal": _PROPOSAL, "blueprint": blueprint},
        SimpleNamespace(
            tool_call_id="proposal", active_capability_ids={"integral-scaffold"}
        ),
    )
    assert len(dispatched) == 1
    assert not dispatched[0].is_error
    assert result["coverage"]["status"] == "needs_decision"
    assert question in result["decision_required"]
    output = await _scaffold_completion_validator(state)(None, "Confirm this setup.")
    assert output == result["decision_required"]
    assert question in output
    assert "Confirm" not in output


@pytest.mark.asyncio
async def test_misnested_blueprint_sections_refused_without_losing_open_questions(
    bind_fresh_graph_context_for_async_tests,
):
    import copy

    thread = await _thread("sess-misnested", 1)
    question = "Should updates change the main project status too?"
    sections = {
        "tracks": _BLUEPRINT["tracks"],
        "views": [],
        "platform_defaults": [],
        "open_decisions": [{"id": "q.filing", "question": question}],
    }
    arguments = {
        "summary": "Contacts app",
        "proposal": _PROPOSAL,
        "blueprint": {"schema_version": 1, "app": _BLUEPRINT["app"]},
        **copy.deepcopy(sections),
    }
    original = copy.deepcopy(arguments)
    result = await dispatch_tool(
        "integral_propose_design",
        arguments,
        principal_id="u1",
        scope="ws1",
        session_id="sess-misnested",
    )
    assert result.error_code == "invalid_arguments"
    assert "inside `blueprint`" in result.message
    assert "open_decisions" in result.message
    assert "NOT recorded" in result.message
    assert question not in result.message
    assert arguments == original
    saved = await ChatThread.get(thread.id)
    assert not saved.design_proposed and not saved.artifacts

    corrected = {key: value for key, value in original.items() if key not in sections}
    corrected["blueprint"] = {**original["blueprint"], **sections}
    result = await dispatch_tool(
        "integral_propose_design",
        corrected,
        principal_id="u1",
        scope="ws1",
        session_id="sess-misnested",
    )
    assert not result.is_error, result
    saved = await ChatThread.get(thread.id)
    assert (
        saved.design_proposed["blueprint"]["open_decisions"]
        == sections["open_decisions"]
    )
    assert not saved.design_proposed["approved"]
    assert question in result.data["decision_required"]


@pytest.mark.asyncio
async def test_unknown_proposal_envelope_key_is_not_echoed_or_ignored(
    bind_fresh_graph_context_for_async_tests,
):
    thread = await _thread("sess-unknown-envelope", 1)
    result = await dispatch_tool(
        "integral_propose_design",
        {
            "summary": "Contacts",
            "proposal": _PROPOSAL,
            "blueprint": _BLUEPRINT,
            "private-sentinel": "secret-value",
        },
        principal_id="u1",
        scope="ws1",
        session_id="sess-unknown-envelope",
    )
    assert result.error_code == "invalid_arguments"
    assert "private-sentinel" not in result.message
    assert "secret-value" not in result.message
    assert not (await ChatThread.get(thread.id)).design_proposed
