"""Retained retrieval passages must pass live PostgreSQL scope and revision checks."""

from types import SimpleNamespace

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async
from pydantic_ai import Agent, ToolReturn
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai_harness.compaction import ClearToolResults, SummarizingCompaction

from app.agentive.harness.contracts import HarnessExecutionScope, ResolvedModelRoute
from app.agentive.harness.litellm_model import build_litellm_sdk_model
from app.agentive.harness.model_errors import NativeModelRequestError
from app.agentive.harness.pydantic_ai_compat import (
    build_integral_history_compaction,
    build_integral_working_context,
    classify_integral_harness_exception,
)
from app.models.edges import COLLABORATES_ON, CONTAINS, IS_MEMBER_OF
from app.models.nodes import Entry
from app.services.permissions import get_user_node
from app.services.retrieval.history import (
    RETRIEVAL_EVIDENCE,
    RetrievalEvidenceStale,
    retained_retrieval_is_current,
    retrieval_result_evidence,
)
from app.services.retrieval.indexing import index_document
from tests.contract.test_staging_command_apply_postgres import proposal

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


@pytest.mark.parametrize("summarize", [False, True])
async def test_retained_passage_revalidates_after_edit_and_revocation(
    postgres_raw_db, monkeypatch, summarize
):
    async def summary(_self, _history, _ctx):
        return [ModelRequest(parts=[SystemPromptPart("Earlier procedure summarized")])]

    monkeypatch.setattr(SummarizingCompaction, "compact", summary)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        _, workspace, track, _ = await proposal()
        auth, _, _, _ = await proposal()
        user = await get_user_node(auth.id)
        await user.connect(workspace, edge=IS_MEMBER_OF, role="guest")
        entry = await Entry.create(track_id=track.id, title="First canonical procedure")
        await track.connect(entry, edge=CONTAINS)
        await user.connect(entry, edge=COLLABORATES_ON, role="viewer")

        async def stored_history():
            document = await index_document(entry)
            field, text = next(iter(document["texts"].items()))
            row = {
                "entry_id": entry.id,
                "track_id": track.id,
                "title": document["title"],
                "source_revision": document["source_revision"],
                "mode_origin": ["semantic"],
                "score": 1,
                "snippet": text,
                "passages": [
                    {
                        "field": field,
                        "start": 0,
                        "end": len(text),
                        "text": text,
                        "tokens": 4,
                    }
                ],
            }
            content = {"results": [row]}
            history = [
                ModelRequest(parts=[UserPromptPart("Read the current procedure")]),
                ModelResponse(parts=[ToolCallPart("integral_query", {}, "retrieval")]),
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            tool_name="integral_query",
                            tool_call_id="retrieval",
                            content=content,
                            metadata={
                                RETRIEVAL_EVIDENCE: retrieval_result_evidence(content)
                            },
                        )
                    ]
                ),
                ModelRequest(parts=[UserPromptPart("Continue with the task")]),
            ]
            ctx = SimpleNamespace(
                tools={
                    "integral_query": SimpleNamespace(
                        metadata={"integral_op_class": "read"}
                    )
                },
                active_capability_ids=set(),
            )
            if summarize:
                compactor = build_integral_history_compaction()
                compactor.max_history_bytes = 64
                history = await compactor.compact(history, ctx)
                assert RETRIEVAL_EVIDENCE in history[0].metadata
                assert not any(
                    isinstance(part, ToolReturnPart)
                    for message in history
                    for part in message.parts
                )
            else:
                history = await ClearToolResults(
                    max_tokens=1, keep_pairs=0, min_clear_tokens=0
                ).compact(history, ctx)
                assert any(
                    isinstance(part, ToolReturnPart) and isinstance(part.content, str)
                    for message in history
                    for part in message.parts
                )
            serialized = ModelMessagesTypeAdapter.dump_json(history)
            assert text.encode() not in serialized
            return serialized

        async def current(serialized):
            # Re-read the saved evidence under a fresh graph identity cache.
            async with scoped_default_context_async(
                GraphContext(database=postgres_raw_db)
            ):
                return await retained_retrieval_is_current(
                    ModelMessagesTypeAdapter.validate_json(serialized),
                    principal_id=auth.id,
                    workspace_id=workspace.id,
                )

        original = await stored_history()
        assert await current(original)
        entry.title = "Corrected canonical procedure"
        entry.record_revision += 1
        await entry.save()
        assert not await current(original)
        corrected = await stored_history()
        assert await current(corrected)
        await user.disconnect(entry, edge_type=COLLABORATES_ON)
        assert not await current(corrected)


async def test_active_turn_refuses_model_after_source_read_is_revoked(postgres_raw_db):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        _, workspace, track, _ = await proposal()
        auth, _, _, _ = await proposal()
        user = await get_user_node(auth.id)
        await user.connect(workspace, edge=IS_MEMBER_OF, role="guest")
        entry = await Entry.create(track_id=track.id, title="Restricted procedure")
        await track.connect(entry, edge=CONTAINS)
        await user.connect(entry, edge=COLLABORATES_ON, role="viewer")
        scope = HarnessExecutionScope(
            tenant_id=workspace.id,
            workspace_id=workspace.id,
            principal_id=auth.id,
            thread_id="thread",
            session_id="session",
            run_id="run",
            permission_revision="review",
            capability_version="capabilities",
        )
        calls = []

        async def respond(messages, _info):
            calls.append(messages)
            return ModelResponse(
                parts=[ToolCallPart("integral_query", {}, "retrieval")]
            )

        agent = Agent(
            FunctionModel(respond),
            capabilities=[
                build_integral_working_context(
                    retrieval_scope=scope,
                    clearable_tools=frozenset({"integral_query"}),
                )
            ],
        )

        @agent.tool_plain
        async def integral_query():
            document = await index_document(entry)
            field, text = next(iter(document["texts"].items()))
            result = {
                "results": [
                    {
                        "entry_id": entry.id,
                        "track_id": track.id,
                        "source_revision": document["source_revision"],
                        "mode_origin": ["semantic"],
                        "score": 1,
                        "passages": [
                            {
                                "field": field,
                                "start": 0,
                                "end": len(text),
                                "text": text,
                                "tokens": 4,
                            }
                        ],
                    }
                ]
            }
            await user.disconnect(entry, edge_type=COLLABORATES_ON)
            return ToolReturn(
                result, metadata={RETRIEVAL_EVIDENCE: retrieval_result_evidence(result)}
            )

        with pytest.raises(RetrievalEvidenceStale) as refused:
            await agent.run("Read the current procedure")
        assert (
            classify_integral_harness_exception(refused.value)
            == "harness_retrieval_history_stale"
        )
        assert len(calls) == 1


@pytest.mark.parametrize("stream", [False, True])
async def test_physical_retry_rechecks_retrieval_access(
    postgres_raw_db, monkeypatch, stream
):
    from pydantic_ai.exceptions import ModelConnectionError

    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", "/tmp/integral-litellm-test-cache")
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        _, workspace, track, _ = await proposal()
        auth, _, _, _ = await proposal()
        user = await get_user_node(auth.id)
        await user.connect(workspace, edge=IS_MEMBER_OF, role="guest")
        entry = await Entry.create(track_id=track.id, title="Restricted procedure")
        await track.connect(entry, edge=CONTAINS)
        await user.connect(entry, edge=COLLABORATES_ON, role="viewer")
        scope = HarnessExecutionScope(
            tenant_id=workspace.id,
            workspace_id=workspace.id,
            principal_id=auth.id,
            thread_id="thread",
            session_id="session",
            run_id="run",
            permission_revision="review",
            capability_version="capabilities",
        )
        document = await index_document(entry)
        field, text = next(iter(document["texts"].items()))
        result = {
            "results": [
                {
                    "entry_id": entry.id,
                    "track_id": track.id,
                    "source_revision": document["source_revision"],
                    "mode_origin": ["semantic"],
                    "score": 1,
                    "passages": [
                        {
                            "field": field,
                            "start": 0,
                            "end": len(text),
                            "text": text,
                            "tokens": 4,
                        }
                    ],
                }
            ]
        }
        history = [
            ModelRequest(parts=[UserPromptPart("Find the procedure")]),
            ModelResponse(parts=[ToolCallPart("integral_query", {}, "retrieval")]),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        "integral_query",
                        result,
                        "retrieval",
                        metadata={
                            RETRIEVAL_EVIDENCE: retrieval_result_evidence(result)
                        },
                    )
                ]
            ),
        ]
        observations = []
        calls = []

        async def observe(event):
            observations.append((event.attempt, event.outcome))

        async def sdk(**_kwargs):
            calls.append("physical call")
            raise NativeModelRequestError("model_rate_limited")

        async def retry_status(attempt, _maximum):
            assert attempt == 2
            await user.disconnect(entry, edge_type=COLLABORATES_ON)

        model = build_litellm_sdk_model(
            route=ResolvedModelRoute(
                provider="openai",
                model="openai/synthetic",
                credential_source="platform",
            ),
            scope=scope,
            observer=observe,
            completion=sdk,
            retry_status=retry_status,
            max_attempts=2,
        )
        try:
            agent = Agent(
                model,
                capabilities=[
                    build_integral_working_context(
                        clearable_tools=frozenset({"integral_query"}),
                        retrieval_scope=scope,
                    )
                ],
            )
            with pytest.raises(ModelConnectionError) as refused:
                if stream:
                    async with agent.run_stream(
                        "Summarize the procedure", message_history=history
                    ) as response:
                        await response.get_output()
                else:
                    await agent.run("Summarize the procedure", message_history=history)
        finally:
            await model.client.close()
        assert calls == ["physical call"], observations
        assert (
            classify_integral_harness_exception(refused.value)
            == "harness_retrieval_history_stale"
        )
        assert observations == [
            (1, "dispatch_intent"),
            (1, "failed"),
            (2, "dispatch_intent"),
            (2, "failed"),
        ]
