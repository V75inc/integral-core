"""The Core factory creates a fresh, explicitly scoped Harness composition."""

import pytest
from pydantic_ai.models.test import TestModel
from pydantic_ai_harness.step_persistence import InMemoryStepStore

from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.harness.runtime import build_native_agent
from app.agentive.harness.scoped_store import ScopedStepStore


def _scope() -> HarnessExecutionScope:
    return HarnessExecutionScope(
        tenant_id="workspace-1",
        principal_id="user-1",
        workspace_id="workspace-1",
        thread_id="thread-1",
        session_id="session-1",
        run_id="run-1",
        permission_revision="permissions-1",
        capability_version="capabilities-1",
    )


@pytest.mark.asyncio
async def test_native_factory_composes_with_required_store() -> None:
    """The runtime factory streams and persists with an explicit store."""
    store = InMemoryStepStore()
    scope = _scope()
    agent = build_native_agent(
        model=TestModel(call_tools=[]),
        instructions="Return one short test sentence.",
        tools=(),
        step_store_backend=store,
        scope=scope,
        agent_name="tenant-bound-smoke",
    )

    async with agent.run_stream(
        "Reply with a short readiness sentence.",
        conversation_id=scope.framework_conversation_id,
        run_id=scope.framework_run_id,
    ) as result:
        output = "".join([part async for part in result.stream_text(delta=True)])

    assert output == "success (no tool calls)"
    records = await ScopedStepStore(store=store, scope=scope).list_runs()
    assert len(records) == 1
    assert records[0].run_id == scope.framework_run_id


def test_native_factory_requires_a_source_for_selected_skills() -> None:
    """Selected skills cannot be discovered from an unscoped filesystem."""
    with pytest.raises(ValueError, match="require a Core skill source"):
        build_native_agent(
            model=TestModel(call_tools=[]),
            instructions="",
            tools=(),
            step_store_backend=InMemoryStepStore(),
            scope=_scope(),
            agent_name="tenant-bound-smoke",
            allowed_skill_names={"integral-identity"},
        )


def test_native_factory_defaults_to_integral_durable_store(monkeypatch) -> None:
    """Production composition defaults to the Core-owned durable store."""
    import app.agentive.harness.runtime as runtime

    scope = _scope()
    stores = []

    def store_factory(*, scope):
        stores.append(scope)
        return InMemoryStepStore()

    monkeypatch.setattr(runtime, "JvSpatialStepStore", store_factory)
    build_native_agent(
        model=TestModel(call_tools=[]),
        instructions="",
        tools=(),
        step_store_backend=None,
        scope=scope,
        agent_name="tenant-bound-smoke",
    )
    assert stores == [scope]
