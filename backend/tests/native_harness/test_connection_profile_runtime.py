"""Synthetic profile policy in the real native Harness composition."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic_ai.models.test import TestModel
from pydantic_ai_harness.step_persistence import InMemoryStepStore, RunRecord

from app.agentive.harness.broker_tools import build_brokered_tools
from app.agentive.harness.connection_profile import (
    NativeConnectionProfile,
    NativeProfilePolicy,
    build_connection_profile_tools,
)
from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.harness.runtime import build_native_runtime
from app.agentive.harness.scoped_store import ScopedStepStore
from app.agentive.services.connection_profiles import (
    ConnectionGrant,
    ConnectionProfile,
    ConnectionProfileExecutor,
    ProfileDenied,
)


def scope():
    return HarnessExecutionScope(
        tenant_id="ws",
        workspace_id="ws",
        principal_id="visitor",
        thread_id="thread",
        session_id="session",
        run_id="run",
        permission_revision="1",
        capability_version="1",
    )


async def setup(audience="public"):
    grants = [
        ConnectionGrant(
            "connection",
            "visitor",
            "ws",
            "p",
            audience,
            verified_staff=audience == "staff",
        )
    ]

    async def resolve(*_):
        return grants[0]

    handler = AsyncMock(return_value={"message": "Synthetic result"})
    authorize = AsyncMock(return_value=True)
    executor = ConnectionProfileExecutor(
        (
            ConnectionProfile(
                "p",
                audience,
                frozenset({"read", "omit", "confirm"}),
                frozenset({"omit"}),
                human_only_tools=frozenset({"confirm"}),
            ),
        ),
        resolve,
        authorize,
        {name: handler for name in ["read", "omit", "confirm"]},
    )
    binding = await NativeConnectionProfile.open(executor, "connection", "visitor")
    return binding, grants, handler, authorize


@pytest.mark.asyncio
async def test_native_public_disclosure_executes_only_narrow_read():
    binding, _, handler, _ = await setup()
    tools = build_connection_profile_tools(
        binding,
        {
            name: {
                "description": name,
                "input_schema": {"type": "object", "properties": {}},
            }
            for name in ["read", "omit", "confirm"]
        },
    )
    agent, _ = build_native_runtime(
        model=TestModel(),
        instructions="Use the available synthetic read.",
        tools=tools,
        scope=binding.bind_scope(scope()),
        agent_name="synthetic",
        step_store_backend=InMemoryStepStore(),
        connection_profile=binding,
    )
    await agent.run("Show the sample.", conversation_id="session")
    assert handler.await_count == 1
    assert handler.await_args.args[2] == {}


@pytest.mark.asyncio
async def test_native_execution_rechecks_revocation_after_selection():
    binding, grants, _, authorize = await setup("staff")
    policy = NativeProfilePolicy(binding)
    definitions = [SimpleNamespace(name=n) for n in ["read", "omit", "confirm"]]
    assert [x.name for x in await policy.prepare_tools(None, definitions)] == ["read"]
    authorize.return_value = False
    dispatch = AsyncMock()
    with pytest.raises(ProfileDenied):
        await policy.wrap_tool_execute(
            None, call=None, tool_def=definitions[0], args={}, handler=dispatch
        )
    dispatch.assert_not_awaited()
    grants[0] = replace(grants[0], verified_staff=False)
    with pytest.raises(ProfileDenied):
        await policy.before_run(None)


@pytest.mark.asyncio
async def test_profile_history_separate_from_personal_and_other_connection():
    binding, _, _, _ = await setup()
    original = scope()
    public = binding.bind_scope(original)
    backing = InMemoryStepStore()
    personal = ScopedStepStore(store=backing, scope=original)
    scoped = ScopedStepStore(store=backing, scope=public)
    await personal.register_run(RunRecord(run_id="old", conversation_id="session"))
    assert await scoped.list_runs() == []
    await scoped.register_run(RunRecord(run_id="new", conversation_id="session"))
    assert [r.run_id for r in await personal.list_runs()] == ["old"]
    with pytest.raises(ProfileDenied):
        build_brokered_tools(scope=public, catalogue=[])
    with pytest.raises(ProfileDenied):
        build_native_runtime(
            model=TestModel(),
            instructions="",
            tools=[],
            scope=public,
            agent_name="test",
            step_store_backend=backing,
        )


@pytest.mark.asyncio
async def test_installed_runtime_only_uses_bound_declarations_and_new_turn_scope():
    binding, _, handler, _ = await setup("staff")
    binding = replace(
        binding,
        turn_scoped=True,
        declarations={
            "read": {
                "description": "Registered read",
                "input_schema": {"type": "object", "properties": {}},
            }
        },
    )
    first = binding.bind_scope(scope())
    second = binding.bind_scope(scope().model_copy(update={"run_id": "next"}))
    assert first.connection_scope_key != second.connection_scope_key
    model = TestModel()
    agent, _ = build_native_runtime(
        model=model,
        instructions="Use the installed read.",
        tools=[],
        scope=first,
        agent_name="installed-profile",
        step_store_backend=InMemoryStepStore(),
        connection_profile=binding,
    )
    await agent.run("Read the sample.", conversation_id="session")
    assert handler.await_count == 1
    assert {t.name for t in model.last_model_request_parameters.function_tools} == {
        "read"
    }
