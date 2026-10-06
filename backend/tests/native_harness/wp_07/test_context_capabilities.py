"""WP-07 context capabilities are composed through the pinned Harness API."""

import pytest
from pydantic_ai.capabilities import ToolSearch
from pydantic_ai.capabilities.combined import CombinedCapability
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel
from pydantic_ai_harness.compaction import ClearToolResults
from pydantic_ai_harness.conversation_search import ConversationSearch
from pydantic_ai_harness.step_persistence import InMemoryStepStore

from app.agentive.harness.capability_search import pydantic_tool_search_strategy
from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.harness.runtime import build_native_runtime


def _scope(*, principal_id: str = "principal-a", session_id: str = "session-a"):
    return HarnessExecutionScope(
        tenant_id="workspace-a",
        principal_id=principal_id,
        workspace_id="workspace-a",
        thread_id="thread-a",
        session_id=session_id,
        run_id="run-a",
        permission_revision="permissions-1",
        capability_version="capabilities-1",
    )


def _capabilities(agent):
    root = agent._root_capability
    assert isinstance(root, CombinedCapability)
    return root.capabilities


def test_runtime_uses_zero_cost_compaction_and_session_scoped_search() -> None:
    """Compose upstream context controls over the tenant-scoped StepStore."""
    agent, scoped_store = build_native_runtime(
        model=TestModel(custom_output_text="ready"),
        instructions="Reply briefly.",
        tools=(),
        step_store_backend=InMemoryStepStore(),
        scope=_scope(),
        agent_name="integral-core",
    )

    compaction = next(
        item for item in _capabilities(agent) if isinstance(item, ClearToolResults)
    )
    search = next(
        item for item in _capabilities(agent) if isinstance(item, ConversationSearch)
    )

    assert compaction.max_tokens is None
    assert compaction.max_fraction == 0.7
    assert compaction.fallback_context_window == 32_768
    assert compaction.keep_pairs == 8
    assert compaction.exclude_tools == frozenset({"load_capability"})
    assert compaction.clear_tool_inputs is True
    assert search.effective_scope == "conversation"
    assert scoped_store._scope == _scope()
    assert search.source._store is scoped_store


def test_runtime_uses_pydantic_tool_search_with_integral_semantic_strategy() -> None:
    """Pydantic AI owns deferred discovery with Integral's non-gating ranking."""
    agent, _ = build_native_runtime(
        model=TestModel(),
        instructions="Reply briefly.",
        tools=(),
        step_store_backend=InMemoryStepStore(),
        scope=_scope(),
        agent_name="integral-core",
    )

    search = next(item for item in _capabilities(agent) if isinstance(item, ToolSearch))

    assert search.strategy is pydantic_tool_search_strategy
    assert search.max_results == 8


def test_conversation_search_store_isolated_by_integral_execution_scope() -> None:
    """Shared persistence cannot widen Harness search across principals."""
    shared_backend = InMemoryStepStore()
    agent_a, store_a = build_native_runtime(
        model=TestModel(),
        instructions="Reply briefly.",
        tools=(),
        step_store_backend=shared_backend,
        scope=_scope(principal_id="principal-a", session_id="session-a"),
        agent_name="integral-core",
    )
    agent_b, store_b = build_native_runtime(
        model=TestModel(),
        instructions="Reply briefly.",
        tools=(),
        step_store_backend=shared_backend,
        scope=_scope(principal_id="principal-b", session_id="session-b"),
        agent_name="integral-core",
    )

    search_a = next(
        item for item in _capabilities(agent_a) if isinstance(item, ConversationSearch)
    )
    search_b = next(
        item for item in _capabilities(agent_b) if isinstance(item, ConversationSearch)
    )

    assert search_a.source._store is store_a
    assert search_b.source._store is store_b
    assert store_a._prefix != store_b._prefix


@pytest.mark.asyncio
async def test_zero_cost_compaction_keeps_loaded_skill_and_tool_pairing() -> None:
    """Drop stale tool data without losing the active skill's instructions."""
    from pydantic_ai_harness.compaction import ClearToolResults

    messages = [
        ModelRequest(parts=[UserPromptPart("Help me organize our tools.")]),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="load_capability",
                    args={"capability_id": "integral-scaffold"},
                    tool_call_id="skill-call",
                ),
                ToolCallPart(
                    tool_name="old_workspace_read",
                    args={},
                    tool_call_id="read-call",
                ),
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="load_capability",
                    content="scaffold instructions " * 500,
                    tool_call_id="skill-call",
                ),
                ToolReturnPart(
                    tool_name="old_workspace_read",
                    content="stale workspace data " * 500,
                    tool_call_id="read-call",
                ),
            ]
        ),
    ]
    compaction = ClearToolResults(
        max_tokens=1,
        keep_pairs=0,
        exclude_tools=frozenset({"load_capability"}),
    )

    compacted = await compaction.compact(messages, None)

    returns = compacted[-1].parts
    assert isinstance(returns[0], ToolReturnPart)
    assert returns[0].tool_name == "load_capability"
    assert returns[0].content == "scaffold instructions " * 500
    assert isinstance(returns[1], ToolReturnPart)
    assert returns[1].content == "[tool result cleared]"
