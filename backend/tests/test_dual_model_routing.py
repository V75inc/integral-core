"""Optional model roles, atomic owner settings and shared physical budgets."""

import asyncio
import base64
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agentive.harness import model_routing
from app.agentive.harness.contracts import (
    HarnessExecutionScope,
    PhysicalModelRequest,
    ResolvedModelRoute,
)
from app.agentive.harness.model_errors import NativeModelRequestError
from app.agentive.harness.pydantic_ai_compat import UsageLimitExceeded
from app.models.credentials import UserModelCredential
from app.schemas.ai_configuration import ConnectionWrite, ProfileWrite, RoutingWrite
from app.services import ai_configuration as service
from app.services.chat_latency import TurnLatency, current_latency, turn_usage_limits

pytestmark = pytest.mark.smoke


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv(
        "INTEGRAL_CREDENTIAL_ENC_KEY",
        base64.urlsafe_b64encode(b"r" * 32).decode().rstrip("="),
    )
    monkeypatch.setattr(service.settings, "INTEGRAL_AGENT_KEY_MODE", "hybrid")
    monkeypatch.setattr(
        service, "validate_provider_api_key", AsyncMock(return_value=(True, "verified"))
    )
    monkeypatch.setenv("INTEGRAL_NATIVE_ROUTING_MODE", "single")
    monkeypatch.delenv("INTEGRAL_NATIVE_HEAVY_MODEL_SETTINGS", raising=False)
    monkeypatch.delenv("INTEGRAL_NATIVE_LIGHT_MODEL_SETTINGS", raising=False)


async def profiles():
    connection = await service.save_connection(
        "owner", ConnectionWrite(name="Local", provider="ollama_local")
    )
    light = await service.save_profile(
        "owner",
        ProfileWrite(name="Light", connection_id=connection["id"], model="local-light"),
    )
    heavy = await service.save_profile(
        "owner",
        ProfileWrite(name="Heavy", connection_id=connection["id"], model="local-heavy"),
    )
    await service.activate_profile("owner", heavy["id"])
    return light, heavy


@pytest.mark.asyncio
async def test_atomic_role_selection_owned_profiles_revision_and_deletion():
    light, heavy = await profiles()
    current = (await service.configuration("owner"))["routing"]
    body = RoutingWrite(
        mode="light_heavy",
        light_profile_id=light["id"],
        heavy_profile_id=heavy["id"],
        revision=current["revision"],
    )
    await service.save_routing("owner", body)
    record = await UserModelCredential.find_one({"user_id": "owner"})
    assert (record.routing_mode, record.light_profile_id, record.active_profile_id) == (
        "light_heavy",
        light["id"],
        heavy["id"],
    )
    with pytest.raises(ValueError, match="changed"):
        await service.save_routing("owner", body)
    with pytest.raises(ValueError, match="unavailable"):
        await service.save_routing("other", body)
    with pytest.raises(ValueError, match="light model"):
        await service.remove_item("owner", light["id"], connection=False)
    await service.save_routing(
        "owner",
        body.model_copy(
            update={
                "mode": "single",
                "light_profile_id": "",
                "revision": record.updated_at,
            }
        ),
    )
    await service.remove_item("owner", light["id"], connection=False)


@pytest.mark.asyncio
async def test_invalid_pair_never_changes_default_or_enables_routing():
    _, heavy = await profiles()
    before = await UserModelCredential.find_one({"user_id": "owner"})
    with pytest.raises(ValueError, match="unavailable"):
        await service.save_routing(
            "owner",
            RoutingWrite(
                mode="light_heavy",
                heavy_profile_id=heavy["id"],
                light_profile_id="missing",
                revision=before.updated_at,
            ),
        )
    after = await UserModelCredential.get(before.id)
    assert after.model_dump() == before.model_dump()


def route(model, **settings):
    return ResolvedModelRoute(
        provider="ollama_chat",
        model=model,
        credential_source="local",
        generation_settings=settings,
    )


@pytest.mark.asyncio
async def test_role_resolution_keeps_one_owner_and_fixed_profile_uses_one_model(
    monkeypatch,
):
    heavy, light = route("heavy", turn_timeout=120), route("light", turn_timeout=30)
    owner = SimpleNamespace(
        provider="ollama_local",
        routing_mode="light_heavy",
        light_profile_id="light-profile",
        updated_at="revision",
    )
    monkeypatch.setattr(
        model_routing, "_workspace_owner_credential", AsyncMock(return_value=owner)
    )
    resolver = AsyncMock(
        side_effect=lambda **kw: (
            light if kw.get("profile_id") == "light-profile" else heavy
        )
    )
    plan = await model_routing.resolve_turn_routes(
        workspace_id="workspace", resolver=resolver
    )
    assert plan.mode == "light_heavy" and plan.light == light and plan.heavy == heavy
    assert plan.budget_settings["turn_timeout"] == 30
    resolver.assert_any_await(
        workspace_id="workspace", default_model="", profile_id="light-profile"
    )
    fixed = await model_routing.resolve_turn_routes(
        workspace_id="workspace", profile_id="fixed", resolver=resolver
    )
    assert fixed.mode == "single" and fixed.light == fixed.heavy == heavy


@pytest.mark.asyncio
async def test_platform_roles_never_take_a_personal_light_profile(monkeypatch):
    monkeypatch.setenv("INTEGRAL_NATIVE_ROUTING_MODE", "light_heavy")
    monkeypatch.setenv("INTEGRAL_NATIVE_LIGHT_MODEL", "openai/gpt-4.1-mini")
    monkeypatch.setenv("INTEGRAL_NATIVE_LIGHT_MODEL_SETTINGS", '{"max_tokens":1000}')
    lookup = AsyncMock(
        side_effect=AssertionError("platform routing consulted personal credentials")
    )
    monkeypatch.setattr(model_routing, "_workspace_owner_credential", lookup)
    heavy = ResolvedModelRoute(
        provider="openai", model="openai/gpt-5.2", credential_source="platform"
    )
    plan = await model_routing.resolve_turn_routes(
        workspace_id="workspace", resolver=AsyncMock(return_value=heavy)
    )
    assert plan.mode == "light_heavy"
    assert plan.light.model == "openai/gpt-4.1-mini" and plan.light.api_key is None
    assert plan.light.generation_settings == {"max_tokens": 1000}
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_shared_deadline_cannot_extend_and_physical_retries_exhaust_budget():
    timing = TurnLatency()
    token = current_latency.set(timing)
    try:
        async with asyncio.timeout(1000) as deadline:
            timing.deadline = deadline
            timing.freeze_budget(
                {"model_request_limit": 2, "turn_timeout": 30, "turn_token_limit": 1000}
            )
            original = deadline.when()
            timing.freeze_budget({"model_request_limit": 8, "turn_timeout": 120})
            assert deadline.when() <= original
            assert original - deadline.when() < 0.01
            timing.reserve_dispatch()
            assert turn_usage_limits().request_limit == 1
            timing.reserve_dispatch()
            with pytest.raises(UsageLimitExceeded):
                timing.reserve_dispatch()
    finally:
        current_latency.reset(token)


@pytest.mark.parametrize(
    "code,allowed",
    [
        ("model_request_timeout", True),
        ("model_endpoint_unreachable", True),
        ("model_authentication_failed", False),
        ("model_rate_limited", False),
        ("image_access_denied", False),
    ],
)
def test_only_transient_light_failures_can_escalate(code, allowed):
    plan = model_routing.TurnModelRoutes(route("heavy"), route("light"), "light_heavy")
    assert (
        model_routing.may_escalate_light_failure(NativeModelRequestError(code), plan)
        is allowed
    )
    assert not model_routing.may_escalate_light_failure(asyncio.CancelledError(), plan)
    assert not model_routing.may_escalate_light_failure(
        RuntimeError("host persistence failed"), plan
    )
    assert not model_routing.may_escalate_light_failure(
        NativeModelRequestError(code), plan.__class__(plan.heavy, plan.heavy)
    )


@pytest.mark.asyncio
async def test_native_adapter_passes_distinct_models_and_observes_both_roles(
    monkeypatch,
):
    from app.services import chat_image_inputs
    from app.services.chat_providers import pydantic_ai_provider as provider

    scope = HarnessExecutionScope(
        tenant_id="workspace",
        workspace_id="workspace",
        principal_id="owner",
        thread_id="thread",
        session_id="session",
        run_id="run",
        permission_revision="p",
        capability_version="v",
    )
    plan = model_routing.TurnModelRoutes(
        route("heavy", model_request_limit=5),
        route("light", model_request_limit=3),
        "light_heavy",
    )
    monkeypatch.setattr(
        model_routing, "resolve_turn_routes", AsyncMock(return_value=plan)
    )
    monkeypatch.setattr(
        provider.ChatThread,
        "get",
        AsyncMock(
            return_value=SimpleNamespace(
                model_profile_id="", model_routing_mode="inherit"
            )
        ),
    )
    persist = AsyncMock()
    monkeypatch.setattr(provider, "persist_model_request_observation", persist)
    monkeypatch.setattr(chat_image_inputs, "assert_model_image_access", AsyncMock())
    captured = []

    def build(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(client=SimpleNamespace(close=AsyncMock()))

    monkeypatch.setattr(provider, "build_litellm_sdk_model", build)
    timing = TurnLatency()
    token = current_latency.set(timing)
    try:
        native = provider.PydanticAIProvider()
        ctx = SimpleNamespace(thread_id="thread", workspace_id="workspace")
        await native._build_turn_model(ctx, scope, None, "owner", model_role="light")
        await captured[0]["request_validator"]({})
        captured[0]["dispatch_guard"]()
        now = datetime.now(timezone.utc)
        await captured[0]["observer"](
            PhysicalModelRequest(
                request_id="request",
                scope=scope,
                provider="ollama_chat",
                model="light",
                attempt=1,
                dispatched_at=now,
                outcome="dispatch_intent",
            )
        )
        await native._build_turn_model(ctx, scope, None, "owner")
        assert [c["route"].model for c in captured] == ["light", "heavy"]
        assert persist.call_args.args[0].model_role == "light"
        assert (
            timing.budget_settings["model_request_limit"] == 3
            and timing.physical_requests == 1
        )
        assert len(timing.clients) == 2
    finally:
        current_latency.reset(token)


def test_role_metadata_is_bounded_and_preserved_in_public_replay():
    from app.services.chat_turn_events import normalize_public_event
    from app.services.chat_turn_transcript import _public_metadata

    step = {"type": "step", "modelId": "provider/model", "modelRole": "light"}
    assert normalize_public_event(step)["modelRole"] == "light"
    assert (
        _public_metadata(
            {"steps": [{"modelId": "provider/model", "modelRole": "heavy"}]}
        )["steps"][0]["modelRole"]
        == "heavy"
    )
    with pytest.raises(ValueError, match="model role"):
        normalize_public_event({**step, "modelRole": "private prompt"})


def test_wrapped_budget_failure_remains_a_harness_limit():
    from app.agentive.harness.pydantic_ai_compat import (
        classify_integral_harness_exception,
    )

    try:
        try:
            raise UsageLimitExceeded("shared limit")
        except UsageLimitExceeded as exc:
            raise RuntimeError("SDK wrapper") from exc
    except RuntimeError as exc:
        assert classify_integral_harness_exception(exc) == "harness_usage_limit"


@pytest.mark.asyncio
async def test_heavy_only_conversation_selection_preserves_admission_pointer(
    authenticated_client,
):
    from app.models.nodes import ChatThread

    created = await authenticated_client.post(
        "/api/chat/threads", json={"provider_id": "integral_native", "title": "Routing"}
    )
    thread_id = created.json()["id"]
    chosen = await authenticated_client.post(
        "/api/users/me/conversation-model",
        json={"thread_id": thread_id, "routing_mode": "heavy"},
    )
    assert chosen.status_code == 200
    assert (
        chosen.json()["routing_mode"] == "heavy" and chosen.json()["profile_id"] == ""
    )
    thread = await ChatThread.get(thread_id)
    thread.active_work_item_id = "busy"
    await thread.save()
    blocked = await authenticated_client.post(
        "/api/users/me/conversation-model",
        json={"thread_id": thread_id, "routing_mode": "inherit"},
    )
    assert blocked.status_code == 400
    thread = await ChatThread.get(thread_id)
    assert thread.active_work_item_id == "busy" and thread.model_routing_mode == "heavy"


@pytest.mark.asyncio
async def test_heavy_only_bypasses_light_configuration_and_its_budget(monkeypatch):
    heavy = route("heavy", model_request_limit=8)
    owner = SimpleNamespace(
        provider="ollama_local",
        routing_mode="light_heavy",
        light_profile_id="deleted",
        updated_at="revision",
    )
    monkeypatch.setattr(
        model_routing, "_workspace_owner_credential", AsyncMock(return_value=owner)
    )
    resolver = AsyncMock(return_value=heavy)
    plan = await model_routing.resolve_turn_routes(
        workspace_id="workspace", resolver=resolver, heavy_only=True
    )
    assert plan.heavy == plan.light == heavy and plan.mode == "single"
    assert plan.budget_settings["model_request_limit"] == 8
    assert resolver.await_count == 1


@pytest.mark.asyncio
async def test_durable_pair_binding_restores_usage_and_rejects_route_changes(
    monkeypatch,
):
    from contextlib import asynccontextmanager

    from app.agentive.harness import model_observations
    from app.agentive.services import work_items
    from app.agentive.services.execution_runs import AgentRun
    from app.schemas.agentive.work import WorkError

    @asynccontextmanager
    async def fenced(_context):
        yield

    monkeypatch.setattr(work_items, "authorized_work_item_effect", fenced)
    scope = HarnessExecutionScope(
        tenant_id="workspace",
        workspace_id="workspace",
        principal_id="owner",
        thread_id="thread",
        session_id="session",
        run_id="durable",
        permission_revision="p",
        capability_version="v",
    )
    run = AgentRun(
        run_id=scope.run_id,
        user_id="owner",
        workspace_id="workspace",
        thread_id="thread",
        provider_id="integral_native",
        metadata={"retained": "other fields"},
    )
    await run.save()
    monkeypatch.setattr(
        model_observations,
        "list_run_model_request_observations",
        AsyncMock(return_value=[]),
    )
    plan = model_routing.TurnModelRoutes(route("heavy"), route("light"), "light_heavy")
    timing = TurnLatency()
    async with asyncio.timeout(1000) as deadline:
        timing.deadline = deadline
        timing.freeze_budget({"turn_timeout": 120})
        await model_routing.bind_durable_routing(scope, object(), plan, timing)
    saved = await AgentRun.get(run.id)
    assert saved.metadata["retained"] == "other fields"
    assert len(saved.metadata["model_routing"]["fingerprint"]) == 64
    assert "light" not in saved.metadata["model_routing"]
    observation = PhysicalModelRequest(
        request_id="prior",
        scope=scope,
        model_role="light",
        provider="ollama_chat",
        model="light",
        attempt=1,
        dispatched_at=datetime.now(timezone.utc),
        outcome="dispatch_intent",
    )
    monkeypatch.setattr(
        model_observations,
        "list_run_model_request_observations",
        AsyncMock(return_value=[observation]),
    )
    resumed = TurnLatency()
    await model_routing.bind_durable_routing(scope, object(), plan, resumed)
    assert resumed.physical_requests == 1
    changed = model_routing.TurnModelRoutes(
        route("different"), route("light"), "light_heavy"
    )
    with pytest.raises(WorkError, match="model_route_stale"):
        await model_routing.bind_durable_routing(
            scope, object(), changed, TurnLatency()
        )
    with pytest.raises(WorkError, match="model_route_stale"):
        await model_routing.bind_durable_routing(
            scope,
            object(),
            model_routing.TurnModelRoutes(plan.heavy, plan.heavy),
            TurnLatency(),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_native_turn_closes_owned_sdk_clients_on_finish_and_cancel(
    monkeypatch, cancel
):
    from app.services.chat_providers.pydantic_ai_provider import PydanticAIProvider

    closed = AsyncMock()
    native = PydanticAIProvider()

    async def stream(_context):
        timing = current_latency.get()
        timing.clients.append(SimpleNamespace(close=closed))
        yield {"type": "text-delta", "delta": "answer"}
        if cancel:
            raise asyncio.CancelledError()
        yield {"type": "message-finish"}

    monkeypatch.setattr(native, "_stream_turn", stream)

    async def consume():
        return [event async for event in native.stream_turn(SimpleNamespace())]

    if cancel:
        with pytest.raises(asyncio.CancelledError):
            await consume()
    else:
        result = await consume()
        assert any(event.get("type") == "message-finish" for event in result)
    closed.assert_awaited_once()
    assert current_latency.get() is None


def test_deployment_ollama_chat_alias_keeps_native_context_controls(monkeypatch):
    from app.agentive.harness.model_route import resolve_deployment_model_route

    monkeypatch.setenv("OLLAMA_API_BASE", "http://localhost:11434")
    resolved = resolve_deployment_model_route("ollama_chat/local-model")
    assert resolved.provider == "ollama_chat" and resolved.credential_source == "local"
    assert resolved.ollama_num_ctx is not None and resolved.api_key is None


@pytest.mark.asyncio
async def test_unusable_owner_key_does_not_mix_into_local_deployment_pair(monkeypatch):
    owner = UserModelCredential(
        user_id="owner",
        provider="openai",
        model="broken",
        routing_mode="light_heavy",
        light_profile_id="private-light",
    )
    monkeypatch.setattr(
        model_routing, "_workspace_owner_credential", AsyncMock(return_value=owner)
    )
    monkeypatch.setenv("INTEGRAL_NATIVE_ROUTING_MODE", "light_heavy")
    monkeypatch.setenv("INTEGRAL_NATIVE_LIGHT_MODEL", "ollama_chat/deployment-light")
    monkeypatch.setenv("INTEGRAL_NATIVE_MODEL", "ollama_chat/deployment-heavy")
    resolver = AsyncMock(return_value=route("deployment-heavy"))
    plan = await model_routing.resolve_turn_routes(
        workspace_id="workspace", resolver=resolver
    )
    assert plan.light.model == "ollama_chat/deployment-light"
    assert plan.heavy.model == "deployment-heavy"
    assert resolver.await_count == 1
    summary = await model_routing.effective_routing_summary("workspace")
    assert {key: summary[key] for key in ("mode", "light_model", "heavy_model")} == {
        "mode": "light_heavy",
        "light_model": "ollama_chat/deployment-light",
        "heavy_model": "ollama_chat/deployment-heavy",
    }


@pytest.mark.asyncio
async def test_deployment_summary_exposes_validated_role_settings_without_secrets(
    monkeypatch,
):
    monkeypatch.setattr(service.settings, "INTEGRAL_AGENT_KEY_MODE", "platform_only")
    monkeypatch.setenv("INTEGRAL_NATIVE_ROUTING_MODE", "light_heavy")
    monkeypatch.setenv("INTEGRAL_NATIVE_MODEL", "openai/gpt-4.1")
    monkeypatch.setenv("INTEGRAL_NATIVE_LIGHT_MODEL", "openai/gpt-4.1-mini")
    monkeypatch.setenv(
        "INTEGRAL_NATIVE_HEAVY_MODEL_SETTINGS",
        '{"max_tokens":8192,"model_request_limit":8}',
    )
    monkeypatch.setenv(
        "INTEGRAL_NATIVE_LIGHT_MODEL_SETTINGS",
        '{"max_tokens":2048,"request_timeout":30}',
    )
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-must-not-be-exposed")
    summary = await model_routing.effective_routing_summary("workspace")
    assert summary["heavy_generation_settings"] == {
        "max_tokens": 8192,
        "model_request_limit": 8,
    }
    assert summary["light_generation_settings"] == {
        "max_tokens": 2048,
        "request_timeout": 30,
    }
    assert "synthetic-must-not-be-exposed" not in str(summary)


@pytest.mark.parametrize("clock_offset", [235353.944, -235353.944])
def test_turn_deadlines_use_event_loop_clock_with_a_different_epoch(
    monkeypatch, clock_offset
):
    """macOS uvloop and Python monotonic clocks need not share an epoch."""
    from app.services import chat_latency

    loop_now = 1_000_000 + clock_offset
    expires = [loop_now + 900]
    deadline = SimpleNamespace(
        when=lambda: expires[0], reschedule=lambda value: expires.__setitem__(0, value)
    )
    timing = TurnLatency(started=999_990, deadline=deadline)
    with monkeypatch.context() as scoped:
        scoped.setattr(chat_latency.time, "monotonic", lambda: 1_000_000)
        scoped.setattr(
            chat_latency.asyncio,
            "get_running_loop",
            lambda: SimpleNamespace(time=lambda: loop_now),
        )
        timing.freeze_budget({"turn_timeout": 30})
        assert timing.remaining_deadline_seconds() == pytest.approx(20)
        timing.clamp_remaining_deadline(5)
        assert expires[0] == pytest.approx(loop_now + 5)
        timing.clamp_remaining_deadline(60)
        assert timing.remaining_deadline_seconds() == pytest.approx(5)


def test_real_uvloop_does_not_immediately_expire_a_fresh_model_turn():
    """Exercise the event loop used by the installed/local Uvicorn deployment."""
    uvloop = pytest.importorskip("uvloop")

    async def turn():
        timing = TurnLatency()
        async with asyncio.timeout(5) as deadline:
            timing.deadline = deadline
            timing.freeze_budget({"turn_timeout": 30})
            assert 0 < timing.remaining_deadline_seconds() <= 5
            await asyncio.sleep(0.01)
            assert timing.remaining_deadline_seconds() > 0

    with asyncio.Runner(loop_factory=uvloop.new_event_loop) as runner:
        runner.run(turn())


@pytest.mark.parametrize(
    "provider,model,base,expected",
    [
        ("openai", "openai/gpt-5", None, "openai"),
        ("anthropic", "anthropic/claude-sonnet", None, "anthropic"),
        ("openrouter", "openrouter/anthropic/claude-sonnet", None, "openrouter"),
        ("ollama_chat", "ollama_chat/gpt-oss:120b", "https://ollama.com", "ollama"),
        (
            "ollama_chat",
            "ollama_chat/gpt-oss:120b",
            "http://localhost:11434",
            "ollama_local",
        ),
        (
            "openai",
            "openai/private-model",
            "https://example.invalid/v1",
            "custom_openai",
        ),
    ],
)
def test_reasoning_overrides_keep_provider_contracts_and_saved_budgets(
    monkeypatch, provider, model, base, expected
):
    """All provider choices retain their own capability contract and immutable defaults."""
    from app.services import model_settings

    seen = []

    def capabilities(p, m):
        seen.append((p, m))
        return {"reasoning_efforts": ["low", "high"]}

    monkeypatch.setattr(model_settings, "model_capabilities", capabilities)
    original = ResolvedModelRoute(
        provider=provider,
        model=model,
        api_base=base,
        credential_source="workspace_byok",
        api_key="synthetic-test-key",
        generation_settings={"model_request_limit": 4},
    )
    plan = model_routing.TurnModelRoutes(original, original)
    changed = model_routing.apply_conversation_reasoning(plan, {"heavy": "high"})
    assert changed.heavy.generation_settings["reasoning_effort"] == "high"
    assert changed.light == changed.heavy
    assert changed.budget_settings == plan.budget_settings == {"model_request_limit": 4}
    assert original.generation_settings == {"model_request_limit": 4}
    assert all(p == expected for p, _ in seen)
    with pytest.raises(ValueError, match="not supported"):
        model_routing.apply_conversation_reasoning(plan, {"light": "low"})
    with pytest.raises(ValueError, match="not supported"):
        model_routing.apply_conversation_reasoning(plan, {"heavy": "max"})


@pytest.mark.asyncio
async def test_conversation_reasoning_roundtrip_reset_and_busy_guard(
    authenticated_client, monkeypatch
):
    """Conversation tuning never mutates profiles and cannot race an active turn."""
    from app.models.nodes import ChatThread
    from app.services import model_settings

    monkeypatch.setattr(
        model_settings,
        "model_capabilities",
        lambda *_: {"reasoning_efforts": ["low", "high"]},
    )
    created = await authenticated_client.post(
        "/api/chat/threads",
        json={"provider_id": "integral_native", "title": "Reasoning QA"},
    )
    thread_id = created.json()["id"]
    heavy = ResolvedModelRoute(
        provider="openai",
        model="openai/gpt-5",
        credential_source="platform",
        generation_settings={"reasoning_effort": "low"},
    )
    light = ResolvedModelRoute(
        provider="openai", model="openai/small", credential_source="platform"
    )
    plan = model_routing.TurnModelRoutes(heavy, light, "light_heavy")
    monkeypatch.setattr(
        model_routing, "resolve_turn_routes", AsyncMock(return_value=plan)
    )
    endpoint = "/api/users/me/conversation-model"
    changed = await authenticated_client.post(
        endpoint, json={"thread_id": thread_id, "reasoning_efforts": {"heavy": "high"}}
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["reasoning_efforts"] == {"heavy": "high"}
    assert changed.json()["reasoning_choices"]["heavy"]["default_effort"] == "low"
    assert heavy.generation_settings == {"reasoning_effort": "low"}
    readback = await authenticated_client.post(endpoint, json={"thread_id": thread_id})
    assert readback.json()["reasoning_efforts"] == {"heavy": "high"}
    invalid = await authenticated_client.post(
        endpoint, json={"thread_id": thread_id, "reasoning_efforts": {"light": "max"}}
    )
    assert invalid.status_code == 400
    assert (await ChatThread.get(thread_id)).model_reasoning_efforts == {
        "heavy": "high"
    }
    reset = await authenticated_client.post(
        endpoint, json={"thread_id": thread_id, "reasoning_efforts": {"heavy": None}}
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["reasoning_efforts"] == {}
    thread = await ChatThread.get(thread_id)
    thread.active_work_item_id = "active-turn"
    await thread.save()
    busy = await authenticated_client.post(
        endpoint, json={"thread_id": thread_id, "reasoning_efforts": {"heavy": "high"}}
    )
    assert busy.status_code == 400
    assert (await ChatThread.get(thread_id)).model_reasoning_efforts == {}


@pytest.mark.asyncio
async def test_stale_conversation_efforts_follow_current_model_contract(monkeypatch):
    """Shared routing changes cannot strand a conversation on unsupported settings."""
    from app.services import model_settings

    monkeypatch.setattr(
        model_settings,
        "model_capabilities",
        lambda *_: {"reasoning_efforts": ["low", "high"]},
    )
    route = ResolvedModelRoute(
        provider="openai", model="openai/o3", credential_source="platform"
    )
    plan = model_routing.TurnModelRoutes(route, route)

    async def resolve(**_):
        return plan

    monkeypatch.setattr(model_routing, "resolve_turn_routes", resolve)
    current = await model_routing.conversation_turn_routes(
        reasoning_efforts={"light": "low", "heavy": "max"}
    )
    assert current == plan
    with pytest.raises(ValueError, match="not supported"):
        model_routing.apply_conversation_reasoning(plan, {"heavy": "max"})
    current = await model_routing.conversation_turn_routes(
        reasoning_efforts={"heavy": "high", "light": "low"}
    )
    assert current.heavy.generation_settings == {"reasoning_effort": "high"}
    assert current.light == current.heavy
    assert route.generation_settings == {}


@pytest.mark.asyncio
async def test_conversation_reasoning_cannot_bypass_owner_or_platform_policy(
    authenticated_client, monkeypatch
):
    """The new preference body uses the existing thread and funding perimeter."""
    from app.models.nodes import ChatThread
    from app.services import ai_configuration

    created = await authenticated_client.post(
        "/api/chat/threads",
        json={"provider_id": "integral_native", "title": "Reasoning authority QA"},
    )
    thread_id = created.json()["id"]
    with pytest.raises(ValueError, match="Conversation is unavailable"):
        await ai_configuration.conversation_model(
            "another-user", thread_id, reasoning_efforts={"heavy": "high"}
        )
    monkeypatch.setattr(
        ai_configuration.settings, "INTEGRAL_AGENT_KEY_MODE", "platform_only"
    )
    denied = await authenticated_client.post(
        "/api/users/me/conversation-model",
        json={"thread_id": thread_id, "reasoning_efforts": {"heavy": "high"}},
    )
    assert denied.status_code == 400
    assert (await ChatThread.get(thread_id)).model_reasoning_efforts == {}
