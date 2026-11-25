"""Inert draft tests through the actual native Harness, without model/network I/O."""

from app.agentive.harness.connection_profile import (
    NativeConnectionProfile,
    build_connection_profile_tools,
)
from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.harness.pydantic_ai_compat import InMemoryStepStore, TestModel
from app.agentive.harness.runtime import build_native_runtime
from app.agentive.services.connection_profiles import (
    ConnectionGrant,
    ConnectionProfile,
    ConnectionProfileExecutor,
)


async def test_profile_draft(draft, workspace, ceiling):
    """Test the profile draft without producing live effects."""
    audience = draft.get("audience", draft["key"])
    calls = []

    async def inert(grant, session, arguments):
        calls.append(arguments)
        return {"message": "Simulation only; no action performed."}

    async def resolve(*_: object):
        return ConnectionGrant(
            "draft-test",
            "synthetic",
            workspace,
            "draft",
            audience,
            verified_staff=audience == "staff",
        )

    async def authorize(*_: object):
        return True

    executor = ConnectionProfileExecutor(
        (
            ConnectionProfile(
                "draft",
                audience,
                frozenset(draft["tools"]),
                frozenset(draft["omissions"]),
                human_only_tools=frozenset(
                    name for name in draft["tools"] if ceiling[name].get("human_only")
                ),
            ),
        ),
        resolve,
        authorize,
        {name: inert for name in ceiling},
    )
    binding = await NativeConnectionProfile.open(executor, "draft-test", "synthetic")
    declarations = {
        name: {
            "description": definition["description"],
            "input_schema": {"type": "object", "properties": {}},
        }
        for name, definition in ceiling.items()
    }
    scope = binding.bind_scope(
        HarnessExecutionScope(
            tenant_id=workspace,
            workspace_id=workspace,
            principal_id="synthetic",
            thread_id="draft-test",
            session_id="draft-test",
            run_id="draft-test",
            permission_revision="draft",
            capability_version="draft",
        )
    )
    model = TestModel()
    agent, _ = build_native_runtime(
        model=model,
        instructions=draft["persona"],
        tools=build_connection_profile_tools(binding, declarations),
        scope=scope,
        agent_name="profile-draft-test",
        step_store_backend=InMemoryStepStore(),
        connection_profile=binding,
    )
    await agent.run(
        "Exercise the available synthetic tools.", conversation_id="draft-test"
    )
    offered = sorted(t.name for t in model.last_model_request_parameters.function_tools)
    return {
        "offered": offered,
        "withheld": sorted(set(ceiling) - set(offered)),
        "simulated_calls": len(calls),
        "real_actions": 0,
    }
