"""Source-bound memory projection: actual graph, durable rows and scope gates."""

import asyncio
import base64
import secrets
from datetime import datetime, timedelta, timezone
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from jvspatial.core.context import get_default_context

from app.models.edges import IS_MEMBER_OF
from app.services import native_voice
from app.services import working_memory as memory
from app.services.chat_threads import append_message, create_thread
from tests.fixtures.workspaces import make_org_workspace
from tests.native_harness.wp_03.test_chat_turn_worker_postgres import (
    postgres_graph_context,
)
from tests.test_native_voice import Browser, Provider, event, request


@pytest.fixture(autouse=True)
def encryption(monkeypatch):
    monkeypatch.setenv(
        "INTEGRAL_CREDENTIAL_ENC_KEY",
        base64.b64encode(secrets.token_bytes(32)).decode(),
    )


async def _fixture():
    from app.services.app_graph import catalog_workspace, ensure_integral_app_graph

    await ensure_integral_app_graph(include_library=False)
    workspace = await make_org_workspace("Working memory fixture")
    await catalog_workspace(workspace)
    owners = await workspace.nodes(
        edge=[IS_MEMBER_OF], node=["User"], direction="in", limit=2
    )
    scope = memory.MemoryScope(owners[0].id, workspace.id)
    thread = await create_thread(
        user_id=scope.principal,
        workspace_id=scope.workspace,
        provider_id="integral_native",
    )
    return scope, thread


async def _remember(scope, thread, text, *, role="user", days=0):
    message = await append_message(
        thread=thread, role=role, parts=[{"type": "text", "text": text}]
    )
    if days:
        message.created_at = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).isoformat()
        await message.save()
    await memory.remember_committed_message(
        principal=scope.principal,
        workspace=scope.workspace,
        thread_id=thread.id,
        message_id=message.id,
    )
    return message


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.contract
async def test_weeks_old_source_idempotency_correction_deletion_and_scopes(
    postgres_graph_context,
):
    scope, thread = await _fixture()
    before = perf_counter()
    source = await _remember(scope, thread, "The Cedar milestone is Thursday.", days=28)
    write_ms = (perf_counter() - before) * 1000
    index = memory.MemoryIndex(scope)
    rev, pointers = await index.read()
    assert rev == 1 and len(pointers) == 1
    assert pointers[0].message == source.id and pointers[0].thread == thread.id
    raw = await get_default_context().database.get("object", scope.record_id)
    assert "Cedar" not in raw["context"]["payload_ciphertext"]
    await memory.remember_committed_message(
        principal=scope.principal,
        workspace=scope.workspace,
        thread_id=thread.id,
        message_id=source.id,
    )
    assert (await index.read())[0] == rev
    before = perf_counter()
    result = await memory.recall(scope, "Cedar milestone")
    recall_ms = (perf_counter() - before) * 1000
    assert result and result.text == "The Cedar milestone is Thursday."
    assert await memory.validate_recall(scope, result)
    print(
        f"Working memory PostgreSQL: index write {write_ms:.1f} ms; authorized recall {recall_ms:.1f} ms; zero model calls"
    )
    assert not await memory.recall(
        memory.MemoryScope("other-user", scope.workspace), "Cedar milestone"
    )
    assert not await memory.recall(
        memory.MemoryScope(scope.principal, "other-workspace"), "Cedar milestone"
    )
    assert not await memory.recall(
        memory.MemoryScope(scope.principal, scope.workspace, "other-profile"),
        "Cedar milestone",
    )
    other = await create_thread(
        user_id=scope.principal,
        workspace_id=scope.workspace,
        provider_id="integral_native",
    )
    await _remember(scope, other, "The Cedar milestone moved to Friday.")
    assert await memory.recall(scope, "Cedar milestone") is None  # competing statements
    await source.delete()
    assert not await memory.validate_recall(scope, result)
    assert await memory.recall(scope, "Cedar milestone") is None


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.contract
async def test_committed_submission_indexes_authorized_caller_not_uncommitted_turn(
    postgres_graph_context,
):
    from app.schemas.agentive.work import ChatTurnSubmissionRequest
    from app.services.chat_turn_submissions import submit_chat_turn

    scope, thread = await _fixture()
    receipt = await submit_chat_turn(
        ChatTurnSubmissionRequest(
            principal_id=scope.principal,
            workspace_id=scope.workspace,
            thread_id=thread.id,
            client_request_id="fixture-cedar",
            parts=[{"type": "text", "text": "My basalt notebook is on the desk."}],
        )
    )
    result = await memory.recall(scope, "basalt notebook")
    assert result is not None and result.pointer.message == receipt.message_id
    # A current recall request is not its own historical evidence.
    from app.services.agent_scope import (
        current_chat_thread_id,
        current_scope_workspace_id,
    )

    capture = memory.MemoryCapture(
        scope.principal, scope.workspace, thread.id, receipt.message_id
    )
    tokens = [
        (memory.active_capture, memory.active_capture.set(capture)),
        (current_chat_thread_id, current_chat_thread_id.set(thread.id)),
        (current_scope_workspace_id, current_scope_workspace_id.set(scope.workspace)),
    ]
    try:
        response = await memory.recall_for_dispatch(
            user_id=scope.principal, query="basalt notebook"
        )
        assert response["statements"] == []
    finally:
        for var, token in reversed(tokens):
            var.reset(token)
    assert (
        await memory.recall_for_dispatch(
            user_id=scope.principal, query="basalt notebook"
        )
    )["coverage"] == "unavailable"

    # The shared catalogue advertises the tool but an authenticated MCP caller
    # outside a native WorkItem must not receive the stored quotation.
    from app.agentive.mcp.server import _call_tool_impl

    external = await _call_tool_impl(
        "integral_recall_prior_conversation",
        {"query": "basalt notebook"},
        principal_id=scope.principal,
        scope=scope.workspace,
    )
    assert isinstance(external, dict) and external["coverage"] == "unavailable"
    assert external["statements"] == []


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.contract
async def test_changed_source_and_archive_invalidate_before_readout(
    postgres_graph_context,
):
    scope, thread = await _fixture()
    message = await _remember(scope, thread, "Saffron handoff is Tuesday.")
    result = await memory.recall(scope, "Saffron handoff")
    assert result
    message.parts = [{"type": "text", "text": "Saffron handoff is Monday."}]
    await message.save()
    assert not await memory.validate_recall(scope, result)
    assert not await memory.recall(scope, "Saffron handoff")
    await memory.remember_committed_message(
        principal=scope.principal,
        workspace=scope.workspace,
        thread_id=thread.id,
        message_id=message.id,
    )
    result = await memory.recall(scope, "Saffron handoff")
    assert result and "Monday" in result.text
    thread.archived = True
    await thread.save()
    assert not await memory.validate_recall(scope, result)
    assert not await memory.recall(scope, "Saffron handoff")


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.contract
async def test_evidence_capture_only_from_bound_search_and_no_historical_result_replay(
    postgres_graph_context,
):
    from app.services.agent_scope import current_chat_thread_id

    scope, thread = await _fixture()
    capture = memory.MemoryCapture(scope.principal, scope.workspace, thread.id)
    context = SimpleNamespace(
        workspace_id=scope.workspace,
        user_id=scope.principal,
        app_id="n.WorkspaceApp.fixture",
    )
    result = SimpleNamespace(
        entry_id="n.Entry.fixture", source_revision=3, title="Cedar receipt"
    )
    memory.capture_retrieved_evidence(context, [result], query="Cedar milestone")
    assert capture.evidence == {}
    capture_token = memory.active_capture.set(capture)
    thread_token = current_chat_thread_id.set(thread.id)
    try:
        memory.capture_retrieved_evidence(context, [result], query="Cedar milestone")
        memory.capture_retrieved_evidence(context, [result], query="Cedar milestone")
    finally:
        current_chat_thread_id.reset(thread_token)
        memory.active_capture.reset(capture_token)
    assert len(capture.evidence) == 1
    message = await append_message(
        thread=thread,
        role="assistant",
        parts=[{"type": "text", "text": "Cedar milestone was Thursday."}],
    )
    await memory.remember_committed_message(
        principal=scope.principal,
        workspace=scope.workspace,
        thread_id=thread.id,
        message_id=message.id,
        evidence=tuple(capture.evidence.values()),
    )
    _, pointers = await memory.MemoryIndex(scope).read()
    evidence = next(p for p in pointers if p.kind == "evidence")
    assert (
        evidence.entry,
        evidence.entry_revision,
        evidence.app,
        evidence.message,
    ) == (result.entry_id, 3, context.app_id, message.id)
    assert await memory.recall(scope, "Cedar milestone") is None


@pytest.mark.asyncio
async def test_voice_recall_miss_delegates_original_after_one_intent(monkeypatch):
    browser, provider = Browser(), Provider(manual_intent=True)
    work = AsyncMock(return_value="Finished.")
    relay = native_voice.VoiceRelay(
        browser,
        provider,
        native_voice.VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(),
        work=work,
    )
    relay.ready = True
    monkeypatch.setattr(native_voice, "recall", AsyncMock(return_value=None))
    reader = asyncio.create_task(relay._read_provider())
    try:
        question = "What did I say about the Cedar milestone last month?"
        await provider.inbox.put(
            {"type": "response.created", "response": {"id": "turn"}}
        )
        await provider.inbox.put(
            request(
                "tool",
                "turn",
                "",
                name=native_voice.RECALL_MEMORY,
                args={"query": "Cedar milestone", "request": question},
            )
        )
        cue = (await event(provider.outbox))["response"]
        assert cue["metadata"]["integral_intent_ack"] == "1"
        work.assert_not_awaited()
        await provider.inbox.put(
            {
                "type": "response.created",
                "response": {"id": "intent", "metadata": cue["metadata"]},
            }
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "intent",
                "delta": "AAE=",
            }
        )
        assert await event(browser.outbox) == b"\x00\x01"
        await provider.inbox.put(
            {
                "type": "response.done",
                "response": {"id": "intent", "status": "completed"},
            }
        )
        for _ in range(100):
            if work.await_count:
                break
            await asyncio.sleep(0.001)
        work.assert_awaited_once_with(relay.scope, "tool", question)
        assert relay.protocol_counts["handoff_requested"] == 1
    finally:
        reader.cancel()
        tasks = [reader] + [task for task in (relay.pending, relay.live_read) if task]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.contract
async def test_member_revocation_withholds_memory_even_for_owned_thread(
    postgres_graph_context,
):
    from app.models.nodes import User, Workspace
    from app.services.app_graph import catalog_user

    owner, _ = await _fixture()
    workspace = await Workspace.get(owner.workspace)
    member = await User.create(display_name="Memory fixture member")
    await catalog_user(member)
    await member.connect(workspace, edge=IS_MEMBER_OF, role="member")
    scope = memory.MemoryScope(member.id, workspace.id)
    thread = await create_thread(
        user_id=member.id, workspace_id=workspace.id, provider_id="integral_native"
    )
    await _remember(scope, thread, "The Amethyst parcel is ready.")
    readout = await memory.recall(scope, "Amethyst parcel")
    assert readout
    edges = await get_default_context().find_edges_between(
        member.id, workspace.id, edge_class=IS_MEMBER_OF
    )
    assert len(edges) == 1
    await edges[0].delete()
    assert await memory.recall(scope, "Amethyst parcel") is None
    assert not await memory.validate_recall(scope, readout)


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.contract
async def test_legacy_bounded_resident_recovery_populates_index(postgres_graph_context):
    from app.services.agent_scope import (
        current_chat_thread_id,
        current_scope_workspace_id,
    )

    scope, source_thread = await _fixture()
    message = await append_message(
        thread=source_thread,
        role="user",
        parts=[{"type": "text", "text": "The violet lantern milestone was Thursday."}],
    )
    message.created_at = (datetime.now(timezone.utc) - timedelta(days=21)).isoformat()
    await message.save()
    assert await memory.recall(scope, "violet lantern") is None
    current = await create_thread(
        user_id=scope.principal,
        workspace_id=scope.workspace,
        provider_id="integral_native",
    )
    capture = memory.MemoryCapture(scope.principal, scope.workspace, current.id)
    tokens = [
        (memory.active_capture, memory.active_capture.set(capture)),
        (current_chat_thread_id, current_chat_thread_id.set(current.id)),
        (current_scope_workspace_id, current_scope_workspace_id.set(scope.workspace)),
    ]
    try:
        response = await memory.recall_for_dispatch(
            user_id=scope.principal, query="violet lantern"
        )
        assert response["coverage"] == "bounded_historical"
        assert response["statements"] == [
            {
                "date": message.created_at,
                "text": "The violet lantern milestone was Thursday.",
            }
        ]
    finally:
        for var, token in reversed(tokens):
            var.reset(token)
    recalled = await memory.recall(scope, "violet lantern")
    assert recalled and recalled.pointer.message == message.id


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.contract
async def test_concurrent_projection_is_bounded_and_has_no_new_indexes(
    postgres_graph_context,
):
    scope, thread = await _fixture()
    database = get_default_context().database
    import asyncpg

    from app.services.storage_indexes.inspection import inventory, snapshot

    conn = await asyncpg.connect(database.dsn)
    try:
        before = snapshot(await inventory(conn, "public"))
    finally:
        await conn.close()
    pointers = [
        memory.MemoryPointer(
            thread=thread.id,
            message=f"n.ChatMessage.fixture{i}",
            kind="caller",
            revision="a" * 64,
            occurred_at=f"{i:04d}",
            terms=["fixture"],
        )
        for i in range(memory.MAX_POINTERS + 8)
    ]
    index = memory.MemoryIndex(scope)
    await asyncio.gather(index.merge(pointers[:130]), index.merge(pointers[130:]))
    _, saved = await index.read()
    assert len(saved) == memory.MAX_POINTERS
    assert len({p.identity for p in saved}) == memory.MAX_POINTERS
    assert saved[0].message == pointers[-1].message
    conn = await asyncpg.connect(database.dsn)
    try:
        after = snapshot(await inventory(conn, "public"))
    finally:
        await conn.close()
    assert after == before
    print(f"Physical index inventory unchanged: {len(before)} definitions")


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.contract
async def test_bounded_source_index_latency_vs_authorized_reference_scan(
    postgres_graph_context,
):
    """Measure the local lookup, not a simulated model latency or billing cost."""
    from statistics import median

    from app.services.chat_history_authority import chat_history_workspace
    from app.services.chat_threads import list_messages

    scope, first = await _fixture()
    threads = [first]
    for i in range(5):
        threads.append(
            await create_thread(
                user_id=scope.principal,
                workspace_id=scope.workspace,
                provider_id="integral_native",
            )
        )
    source = await _remember(
        scope, threads[0], "The distinctive obsidian relay is ready.", days=21
    )
    for t in threads:
        for i in range(8):
            await append_message(
                thread=t,
                role="user",
                parts=[{"type": "text", "text": f"Unrelated fixture note {i}."}],
            )
    indexed_ms, scan_ms = [], []
    for _ in range(5):
        start = perf_counter()
        matched = await memory.recall(scope, "obsidian relay")
        indexed_ms.append((perf_counter() - start) * 1000)
        assert matched and matched.pointer.message == source.id
        start = perf_counter()
        hits = []
        for t in threads:
            assert (
                await chat_history_workspace(scope.principal, t.id) == scope.workspace
            )
            hits.extend(
                m
                for m in await list_messages(t)
                if "obsidian relay" in memory._message_text(m)
            )
        scan_ms.append((perf_counter() - start) * 1000)
        assert [m.id for m in hits] == [source.id]
    print(
        f"49-message / 6-thread PostgreSQL fixture: median index {median(indexed_ms):.1f} ms, reference scan {median(scan_ms):.1f} ms; no model or encoder calls"
    )


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.contract
async def test_verified_evidence_locator_rechecks_record_revision_and_declared_app_boundary(
    postgres_graph_context,
):
    from app.services.agent_scope import (
        current_chat_thread_id,
        current_scope_workspace_id,
    )
    from tests.test_resource_location import graph

    user, workspace, app, _, entry = await graph()
    scope = memory.MemoryScope(user.id, workspace.id)
    thread = await create_thread(
        user_id=scope.principal,
        workspace_id=scope.workspace,
        provider_id="integral_native",
    )
    message = await append_message(
        thread=thread,
        role="assistant",
        parts=[{"type": "text", "text": "Previous fixture results."}],
    )
    entries = [(entry, app)]
    evidence = tuple(
        memory.RetrievedEvidence(
            e.id, app.id, e.record_revision, ("fixture", "receipt")
        )
        for e, app in entries
    )
    await memory.remember_committed_message(
        principal=scope.principal,
        workspace=scope.workspace,
        thread_id=thread.id,
        message_id=message.id,
        evidence=evidence,
    )
    capture = memory.MemoryCapture(scope.principal, scope.workspace, thread.id)
    tokens = [
        (memory.active_capture, memory.active_capture.set(capture)),
        (current_chat_thread_id, current_chat_thread_id.set(thread.id)),
        (current_scope_workspace_id, current_scope_workspace_id.set(scope.workspace)),
    ]
    try:
        response = await memory.recall_for_dispatch(
            user_id=scope.principal, query="fixture receipt"
        )
        assert [
            item["entry_id"] for item in response["verified_resource_locators"]
        ] == [entry.id]
        assert response["requires_current_lookup"] is True
        app.installed_package_slug = "fixture-declared-only"
        await app.save()
        response = await memory.recall_for_dispatch(
            user_id=scope.principal, query="fixture receipt"
        )
        assert response["verified_resource_locators"] == []
        app.installed_package_slug = ""
        await app.save()
        entry.record_revision += 1
        await entry.save()
        response = await memory.recall_for_dispatch(
            user_id=scope.principal, query="fixture receipt"
        )
        assert response["verified_resource_locators"] == []
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.contract
async def test_delete_thread_messages_prunes_projection_without_losing_other_threads(
    postgres_graph_context,
):
    from app.services.chat_threads import delete_thread_messages

    scope, thread = await _fixture()
    other = await create_thread(
        user_id=scope.principal,
        workspace_id=scope.workspace,
        provider_id="integral_native",
    )
    await _remember(scope, thread, "A ruby notebook was delivered.")
    await _remember(scope, other, "An onyx compass was delivered.")
    assert await delete_thread_messages(thread) == 1
    _, pointers = await memory.MemoryIndex(scope).read()
    assert all(p.thread != thread.id for p in pointers)
    assert await memory.recall(scope, "ruby notebook") is None
    assert await memory.recall(scope, "onyx compass") is not None


@pytest.mark.parametrize(
    "query,terms,expected",
    [
        ("violent lantern milestone", ["violet", "lantern", "milestone"], True),
        ("saffran handoff schedule", ["saffron", "handoff", "schedule"], True),
        ("violent lantern", ["violet", "lantern", "milestone"], False),
        ("violent lantern schedule", ["violet", "lantern", "milestone"], False),
        ("obsidian relay", ["obsidian", "relay"], True),
    ],
)
def test_bounded_lexical_typo_tolerance(query, terms, expected):
    assert memory._matches(set(memory._terms(query)), terms) is expected
