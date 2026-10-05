"""Native provider joins chat events to scoped resumable Harness runs."""

from __future__ import annotations

import hashlib
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic_ai import Agent, CancellationToken
from pydantic_ai.models.test import TestModel
from pydantic_ai_harness.step_persistence import InMemoryStepStore, continue_run

from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.harness.runtime import build_native_runtime
from app.api.errors import ResourceConflictError
from app.services.chat_providers.base import ChatTurnContext
from app.services.chat_providers.pydantic_ai_provider import (
    PydanticAIProvider,
    _bind_integral_turn_context,
    _resume_history,
)


def _scope(run_id: str) -> HarnessExecutionScope:
    return HarnessExecutionScope(
        tenant_id="workspace-a",
        principal_id="user-a",
        workspace_id="workspace-a",
        thread_id="thread-a",
        session_id="session-a",
        run_id=run_id,
        permission_revision="membership-v1",
        capability_version="tools-v1",
    )


@pytest.mark.asyncio
async def test_provider_lists_one_resident_agent_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expose one Integral Native agent when the harness is configured."""
    monkeypatch.setenv("INTEGRAL_NATIVE_HARNESS_ENABLED", "true")
    monkeypatch.setenv("INTEGRAL_NATIVE_MODEL", "ollama/gemma4:26b")

    agents = await PydanticAIProvider().list_agents()

    assert len(agents) == 1
    assert agents[0]["id"] == "integral_core"


class _Session:
    last_run_id: str | None = None
    last_checkpoint_id: str | None = None
    last_checkpoint_run_id: str | None = None
    transcript_codec_version: str = "pydantic-ai-messages-v1"

    async def save(self) -> None:
        return None


@pytest.mark.asyncio
async def test_provider_persists_session_and_resumes_previous_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two turns share a session while resuming separate durable runs."""
    backend = InMemoryStepStore()
    session = _Session()
    scopes = [_scope("run-a"), _scope("run-b")]

    async def prepare(_ctx):
        scope = scopes.pop(0)
        agent, store = build_native_runtime(
            model=TestModel(custom_output_text=f"answer-{scope.run_id}"),
            instructions="",
            tools=[],
            step_store_backend=backend,
            scope=scope,
            agent_name="integral_core",
        )
        history = (
            await continue_run(store, run_id=session.last_run_id)
            if session.last_run_id
            else []
        )
        return scope, session, agent, store, history, tempfile.TemporaryDirectory()

    provider = PydanticAIProvider()

    from app.agentive.harness.checkpoint_manifests import manifest_record_id
    from app.agentive.harness.plan_store import JvSpatialPlanStore
    from app.agentive.work_models import WorkApproval

    async def no_observations(*, scope):
        return []

    async def no_plans(_self):
        return []

    async def no_approvals(_cls, _query=None, **_kwargs):
        return []

    async def save_manifest(manifest):
        return manifest_record_id(
            scope=manifest.scope,
            snapshot_id=manifest.framework_snapshot_id,
        )

    async def advance_pointer(*, scope, snapshot_id):
        session.last_checkpoint_id = manifest_record_id(
            scope=scope, snapshot_id=snapshot_id
        )
        session.last_checkpoint_run_id = scope.run_id
        return session

    async def claim_run(*, scope):
        session.last_run_id = scope.run_id
        return session

    monkeypatch.setattr(
        "app.agentive.harness.model_observations.list_model_request_observations",
        no_observations,
    )
    monkeypatch.setattr(JvSpatialPlanStore, "get_items", no_plans)
    monkeypatch.setattr(WorkApproval, "find", classmethod(no_approvals))
    monkeypatch.setattr(
        "app.services.chat_providers.pydantic_ai_provider.persist_checkpoint_manifest",
        save_manifest,
    )
    monkeypatch.setattr(
        "app.services.chat_providers.pydantic_ai_provider.advance_harness_checkpoint_pointer",
        advance_pointer,
    )
    monkeypatch.setattr(
        "app.services.chat_providers.pydantic_ai_provider.claim_harness_run",
        claim_run,
    )
    monkeypatch.setattr(provider, "_prepare", prepare)
    ctx = ChatTurnContext(
        user_id="user-a",
        user_email="user@example.test",
        text="continue",
        thread_id="thread-a",
        session_id="session-a",
    )

    first = [event async for event in provider.stream_turn(ctx)]
    assert first[0] == {"type": "_meta", "provider_session_id": "session-a"}
    assert first[-1]["type"] == "message-finish"
    assert session.last_run_id == "run-a"
    assert session.last_checkpoint_id

    second = [event async for event in provider.stream_turn(ctx)]
    assert second[0]["type"] == "_meta"
    assert second[-1]["type"] == "message-finish"
    assert session.last_run_id == "run-b"
    assert any(event.get("type") == "text-delta" for event in second)


@pytest.mark.asyncio
async def test_provider_stream_never_publishes_serialized_private_reasoning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the full native provider with a flattened-message fixture."""
    backend = InMemoryStepStore()
    session = _Session()
    scope = _scope("run-private-envelope")
    private_marker = "PRIVATE_REASONING_MUST_NOT_ESCAPE"
    raw_output = (
        f'thought": "{private_marker}", "role": "assistant", '
        '"content": "safe final answer"}'
    )

    async def prepare(_ctx):
        agent, store = build_native_runtime(
            model=TestModel(custom_output_text=raw_output),
            instructions="",
            tools=[],
            step_store_backend=backend,
            scope=scope,
            agent_name="integral_core",
        )
        return scope, session, agent, store, [], tempfile.TemporaryDirectory()

    from app.agentive.harness.checkpoint_manifests import manifest_record_id
    from app.agentive.harness.plan_store import JvSpatialPlanStore
    from app.agentive.work_models import WorkApproval

    async def no_observations(*, scope):
        return []

    async def no_plans(_self):
        return []

    async def no_approvals(_cls, _query=None, **_kwargs):
        return []

    async def save_manifest(manifest):
        return manifest_record_id(
            scope=manifest.scope,
            snapshot_id=manifest.framework_snapshot_id,
        )

    async def advance_pointer(*, scope, snapshot_id):
        session.last_checkpoint_id = manifest_record_id(
            scope=scope, snapshot_id=snapshot_id
        )
        session.last_checkpoint_run_id = scope.run_id
        return session

    async def claim_run(*, scope):
        session.last_run_id = scope.run_id
        return session

    monkeypatch.setattr(
        "app.agentive.harness.model_observations.list_model_request_observations",
        no_observations,
    )
    monkeypatch.setattr(JvSpatialPlanStore, "get_items", no_plans)
    monkeypatch.setattr(WorkApproval, "find", classmethod(no_approvals))
    monkeypatch.setattr(
        "app.services.chat_providers.pydantic_ai_provider.persist_checkpoint_manifest",
        save_manifest,
    )
    monkeypatch.setattr(
        "app.services.chat_providers.pydantic_ai_provider.advance_harness_checkpoint_pointer",
        advance_pointer,
    )
    monkeypatch.setattr(
        "app.services.chat_providers.pydantic_ai_provider.claim_harness_run",
        claim_run,
    )
    provider = PydanticAIProvider()
    monkeypatch.setattr(provider, "_prepare", prepare)
    ctx = ChatTurnContext(
        user_id="user-a",
        user_email="user@example.test",
        text="answer exactly",
        thread_id="thread-a",
        session_id="session-a",
        workspace_id="workspace-a",
    )

    events = [event async for event in provider.stream_turn(ctx)]
    text = "".join(
        event.get("delta", "") for event in events if event.get("type") == "text-delta"
    )

    assert text == "safe final answer"
    assert private_marker not in repr(events)
    assert events[-1]["type"] == "message-finish"


@pytest.mark.asyncio
async def test_provider_suppresses_output_after_work_item_fence_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A late model event is not yielded after durable lease authority is lost."""
    from app.agentive.services import work_items
    from app.schemas.agentive.work import WorkError, WorkExecutionContext

    scope = _scope("run-fenced-output")
    session = _Session()
    authority = WorkExecutionContext(
        work_item_id="work-item-fenced-output",
        attempt=1,
        run_id=scope.run_id,
        principal_id=scope.principal_id,
        workspace_id=scope.workspace_id,
        thread_id=scope.thread_id,
        logical_step_key="native-harness:0",
        effect_key="effect-fenced-output",
        lease_token="opaque-fence-token",
        lease_fence=4,
    )
    authority_checks = 0

    async def assert_authority(_context):
        nonlocal authority_checks
        authority_checks += 1
        if authority_checks == 3:
            raise WorkError("work.lease_lost", "execution lease is no longer current")

    monkeypatch.setattr(
        work_items, "assert_work_item_execution_current", assert_authority
    )

    class FakeStream:
        async def __aenter__(self) -> "FakeStream":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        def __aiter__(self):
            async def events():
                yield "first"
                yield "late"

            return events()

    class FakeAgent:
        def run_stream_events(self, *_args: object, **_kwargs: object) -> FakeStream:
            return FakeStream()

    class Translator:
        def translate(self, event):
            yield {"type": "text-delta", "delta": event}

    async def prepare(_ctx):
        return (
            scope,
            session,
            FakeAgent(),
            object(),
            [],
            tempfile.TemporaryDirectory(),
            authority,
        )

    async def claim_run(*, scope, work_execution_context=None):
        assert work_execution_context == authority
        session.last_run_id = scope.run_id
        return session

    monkeypatch.setattr(
        "app.services.chat_providers.pydantic_ai_provider.claim_harness_run",
        claim_run,
    )
    monkeypatch.setattr(
        "app.services.chat_providers.pydantic_ai_provider.PydanticAIEventTranslator",
        Translator,
    )
    provider = PydanticAIProvider()
    monkeypatch.setattr(provider, "_prepare", prepare)
    ctx = ChatTurnContext(
        user_id=scope.principal_id,
        user_email="user@example.test",
        text="produce two events",
        thread_id=scope.thread_id,
        session_id=scope.session_id,
        workspace_id=scope.workspace_id,
    )

    observed = []
    with pytest.raises(WorkError) as error:
        async for event in provider.stream_turn(ctx):
            observed.append(event)

    assert error.value.code == "work.lease_lost"
    assert observed == [
        {"type": "_meta", "provider_session_id": scope.session_id},
        {"type": "text-delta", "delta": "first"},
    ]
    assert "late" not in repr(observed)
    assert scope.thread_id not in provider._active_tokens


@pytest.mark.asyncio
async def test_prepare_uses_host_run_and_snapshot_as_broker_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Harness tools use the same durable run that Core's chat router created."""
    from pathlib import Path
    from types import SimpleNamespace

    from app.agentive.harness.contracts import ResolvedModelRoute
    from app.schemas.agentive.work import WorkExecutionContext

    scope_args = {}
    runtime_args = {}
    model_args = {}
    tool_args = {}
    work_context = WorkExecutionContext(
        work_item_id="work-item-1",
        attempt=1,
        run_id="host-run-42",
        principal_id="user-a",
        workspace_id="workspace-a",
        thread_id="thread-a",
        logical_step_key="native-harness:0",
        effect_key="effect-1",
        lease_token="opaque-lease-token",
        lease_fence=3,
    )
    thread = SimpleNamespace(
        id="thread-a",
        user_id="user-a",
        workspace_id="workspace-a",
        provider_id="integral_native",
        active_harness_session_id=None,
    )
    core_run = SimpleNamespace(
        run_id="host-run-42",
        thread_id="thread-a",
        user_id="user-a",
        workspace_id="workspace-a",
        provider_id="integral_native",
        status="running",
        capability_version="manifest-v9",
        capability_snapshot={"fingerprint": "snapshot-fingerprint"},
    )
    session = _Session()

    async def capture_session(*, scope, binding_id, work_execution_context=None):
        scope_args.update(
            scope=scope,
            binding_id=binding_id,
            work_execution_context=work_execution_context,
        )
        return session

    async def profile(_workspace_id, *, user_id, focused_app_id):
        assert (user_id, focused_app_id) == ("user-a", None)
        return SimpleNamespace(
            profile_version="profile-v1",
            overlay_skill_docs=(
                SimpleNamespace(
                    name="example_app__workspace_guide",
                    description="Use the workspace guide.",
                    body="Follow the approved steps.",
                ),
            ),
        )

    def build_runtime(**kwargs):
        runtime_args.update(kwargs)
        return Agent(TestModel()), InMemoryStepStore()

    monkeypatch.setattr(
        "app.services.chat_providers.pydantic_ai_provider.ChatThread.get",
        lambda _thread_id: _return(thread),
    )
    monkeypatch.setattr(
        "app.services.chat_providers.pydantic_ai_provider.HarnessSession.get",
        lambda _session_id: _return(None),
    )
    monkeypatch.setattr(
        "app.services.chat_providers.pydantic_ai_provider.ensure_harness_session",
        capture_session,
    )
    monkeypatch.setattr(
        "app.agentive.workspace_agent_profile.compose_workspace_agent_profile",
        profile,
    )
    monkeypatch.setattr(
        "app.agentive.services.agent_skills.list_core_skills", lambda: []
    )
    monkeypatch.setattr(
        "app.agentive.services.execution_runs.AgentRun.find_one",
        lambda _query: _return(core_run),
    )
    monkeypatch.setattr(
        "app.services.workspace_permissions.can_access_workspace",
        lambda _user, _workspace: _return("owner"),
    )
    monkeypatch.setattr(
        "app.agentive.tooling.catalogue.build_tool_catalogue", lambda: []
    )
    monkeypatch.setattr(
        "app.services.chat_providers.pydantic_ai_provider.resolve_native_model_route",
        lambda **_kwargs: _return(
            ResolvedModelRoute(
                provider="test",
                model="test:model",
                credential_source="platform",
            )
        ),
    )

    def build_model(**kwargs):
        model_args.update(kwargs)
        return TestModel()

    def build_tools(**kwargs):
        tool_args.update(kwargs)
        return []

    monkeypatch.setattr(
        "app.services.chat_providers.pydantic_ai_provider.build_litellm_sdk_model",
        build_model,
    )
    monkeypatch.setattr(
        "app.services.chat_providers.pydantic_ai_provider.build_brokered_tools",
        build_tools,
    )
    monkeypatch.setattr(
        "app.services.chat_providers.pydantic_ai_provider.build_native_runtime",
        build_runtime,
    )
    monkeypatch.setenv("INTEGRAL_NATIVE_MODEL", "test:model")

    ctx = ChatTurnContext(
        user_id="user-a",
        user_email="user@example.test",
        text="hello",
        thread_id="thread-a",
        session_id=None,
        workspace_id="workspace-a",
        extra_data={
            "run_id": "host-run-42",
            "work_execution_context": work_context.model_dump(),
        },
    )
    scope, *_rest = await PydanticAIProvider()._prepare(ctx)
    assert scope.run_id == "host-run-42"
    assert scope_args["scope"] == scope
    assert scope_args["binding_id"] == "pydantic_ai_v1"
    assert scope_args["work_execution_context"] == work_context
    assert (
        scope.capability_version
        == hashlib.sha256(
            b'{"host_snapshot":"snapshot-fingerprint","skill_profile":"profile-v1"}'
        ).hexdigest()
    )
    assert runtime_args["allowed_skill_names"] == frozenset(
        {"example-app-workspace-guide"}
    )
    assert runtime_args["work_execution_context"] == work_context
    assert tool_args["work_execution_context"] == work_context
    assert model_args["observer"].keywords["work_execution_context"] == work_context
    assert _rest[-1] == work_context
    skill_file = (
        Path(runtime_args["skill_directories"][0])
        / "example-app-workspace-guide"
        / "SKILL.md"
    )
    import yaml

    contents = skill_file.read_text(encoding="utf-8")
    frontmatter = contents.split("---", 2)[1]
    assert set(yaml.safe_load(frontmatter)) == {"name", "description"}
    assert "Follow the approved steps." in contents


def test_cancel_turn_cancels_active_pydantic_token() -> None:
    """Integral's turn registry can stop the active model request."""
    provider = PydanticAIProvider()
    token = CancellationToken()
    provider._active_tokens["thread-a"] = token
    provider.cancel_turn(thread_id="thread-a")
    assert token.cancelled


@pytest.mark.asyncio
async def test_resume_blocks_unknown_physical_model_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown provider response cannot be silently resent from old state."""
    from app.agentive.harness import model_observations

    observed_at = datetime.now(timezone.utc)
    observation = SimpleNamespace(
        request_id="request-unknown",
        outcome="outcome_unknown",
        observed_at=observed_at,
        dispatched_at=observed_at,
    )

    async def load_observations(*, scope):
        assert scope.run_id == "previous-run"
        return [observation]

    monkeypatch.setattr(
        model_observations, "list_model_request_observations", load_observations
    )
    session = SimpleNamespace(last_run_id="previous-run")

    with pytest.raises(ResourceConflictError, match="unsettled model request"):
        await _resume_history(_scope("next-run"), session, object())


def test_provider_is_opt_in_and_requires_trusted_model_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The native provider cannot appear without explicit deployment config."""
    provider = PydanticAIProvider()
    monkeypatch.delenv("INTEGRAL_NATIVE_HARNESS_ENABLED", raising=False)
    monkeypatch.delenv("INTEGRAL_NATIVE_MODEL", raising=False)
    assert not provider.is_available()
    monkeypatch.setenv("INTEGRAL_NATIVE_HARNESS_ENABLED", "true")
    assert not provider.is_available()
    monkeypatch.setenv("INTEGRAL_NATIVE_MODEL", "openai/gpt-test")
    assert provider.is_available()


def test_integral_contextvars_are_bound_and_restored() -> None:
    """Native tools inherit workspace, focus, page, and thread scope safely."""
    from app.services.agent_scope import (
        current_chat_thread_id,
        current_focused_track_id,
        current_focused_view_id,
        current_page_context,
        current_scope_workspace_id,
    )

    ctx = ChatTurnContext(
        user_id="user-a",
        user_email="user@example.test",
        text="hello",
        thread_id="thread-a",
        session_id="session-a",
        workspace_id="workspace-a",
        focused_track_id="track-a",
        focused_view_id="view-a",
        extra_data={"page_context": {"url": "/tracks/track-a"}},
    )
    with _bind_integral_turn_context(ctx):
        assert current_scope_workspace_id.get() == "workspace-a"
        assert current_focused_track_id.get() == "track-a"
        assert current_focused_view_id.get() == "view-a"
        assert current_page_context.get() == {"url": "/tracks/track-a"}
        assert current_chat_thread_id.get() == "thread-a"
    assert current_scope_workspace_id.get() is None
    assert current_chat_thread_id.get() is None


async def _return(value):
    """Tiny awaitable for monkeypatched async persistence lookups."""
    return value
