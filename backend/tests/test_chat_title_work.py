"""Auxiliary labels must not share chat completion or its execution lease."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agentive.services import execution_runs, work_execution, work_items
from app.agentive.work_models import WorkItem
from app.schemas.agentive.work import WorkError
from app.services import chat_titles
from app.services.chat_latency import current_latency
from app.services.chat_providers.pydantic_ai_provider import PydanticAIProvider

pytestmark = [pytest.mark.asyncio, pytest.mark.smoke]


@pytest.mark.parametrize("targeted", [False, True])
async def test_title_claims_use_platform_worker_not_app_or_chat_lanes(targeted):
    """Both targeted claims and polling keep cosmetic work out of other lanes."""
    from app.agentive.services import work_worker

    item = await work_items.enqueue_work_item(
        kind="chat_title",
        origin="chat_title",
        principal_id="title-owner",
        workspace_id="title-workspace",
        idempotency_key="title-worker-separation",
    )
    selection = {"work_item_id": item.work_item_id} if targeted else {}
    assert "chat_title" in work_worker.HANDLED_KINDS
    assert work_worker.APP_JOB_KINDS == frozenset({"app_tool"})
    assert (
        await work_items.claim_due_candidate(
            worker_id="apps", kinds=work_worker.APP_JOB_KINDS, **selection
        )
        is None
    )
    assert (
        await work_items.claim_due_candidate(
            worker_id="chat", kinds=frozenset({"chat_turn"}), **selection
        )
        is None
    )
    claimed = await work_items.claim_due_candidate(
        worker_id="platform",
        exclude_kinds=work_worker.APP_JOB_KINDS | frozenset({"chat_turn"}),
        **selection,
    )
    assert claimed is not None and claimed.work_item_id == item.work_item_id


@pytest.fixture
def title_work(monkeypatch):
    """Bind a child to one successful, owned interactive conversation."""
    parent = WorkItem(
        work_item_id="parent",
        kind="chat_turn",
        origin="interactive_chat",
        status="succeeded",
        principal_id="owner",
        workspace_id="workspace",
        thread_id="thread",
        input_payload={},
    )
    item = WorkItem(
        work_item_id="title",
        kind="chat_title",
        origin="chat_title",
        status="running",
        principal_id="owner",
        workspace_id="workspace",
        thread_id="thread",
        parent_work_item_id="parent",
        attempt=1,
        lease_token="lease",
        lease_fence=1,
    )
    thread = SimpleNamespace(
        id="thread",
        user_id="owner",
        workspace_id="workspace",
        provider_id="integral_native",
        title="How do we organize the clinic?",
        title_source="fallback",
    )
    get = AsyncMock(return_value=parent)
    enqueue = AsyncMock()
    monkeypatch.setattr(WorkItem, "get", get)
    monkeypatch.setattr(work_items, "enqueue_work_item", enqueue)
    monkeypatch.setattr(work_execution, "assert_effect_boundary_allowed", AsyncMock())
    validate = AsyncMock(return_value=(thread, "member"))
    monkeypatch.setattr(PydanticAIProvider, "_validate_chat_scope", validate)
    model = object()
    build = AsyncMock(return_value=model)
    monkeypatch.setattr(PydanticAIProvider, "_build_turn_model", build)
    infer = AsyncMock(return_value=True)
    monkeypatch.setattr(chat_titles, "infer_conversation_title", infer)
    run = SimpleNamespace(capability_version="current-capabilities")
    start = AsyncMock(return_value=run)
    finish = AsyncMock()
    monkeypatch.setattr(execution_runs, "start_run", start)
    monkeypatch.setattr(execution_runs, "finish_run", finish)
    return SimpleNamespace(
        parent=parent,
        item=item,
        thread=thread,
        get=get,
        enqueue=enqueue,
        validate=validate,
        model=model,
        build=build,
        infer=infer,
        start=start,
        finish=finish,
        run=run,
    )


async def test_enqueue_is_atomic_and_contains_no_transcript_or_credentials(title_work):
    """The answer transaction queues identity only, with no model execution."""
    title_work.get.return_value = None
    transaction = object()
    await chat_titles.enqueue_conversation_title(
        parent=title_work.parent, thread=title_work.thread, transaction=transaction
    )
    kwargs = title_work.enqueue.call_args.kwargs
    assert kwargs["transaction"] is transaction
    assert kwargs["kind"] == "chat_title"
    assert kwargs["parent_work_item_id"] == "parent"
    assert kwargs["dependency_work_item_ids"] == ["parent"]
    assert kwargs["retry_policy"].max_attempts == 1
    assert "input_payload" not in kwargs
    title_work.build.assert_not_awaited()
    title_work.infer.assert_not_awaited()


async def test_existing_title_attempt_is_not_requeued_on_every_turn(title_work):
    """A stored attempt keeps rapid follow-up turns from multiplying title calls."""
    await chat_titles.enqueue_conversation_title(
        parent=title_work.parent, thread=title_work.thread, transaction=object()
    )
    title_work.enqueue.assert_not_awaited()


@pytest.mark.parametrize("source", ["manual", "generated"])
async def test_named_threads_do_not_queue_or_resolve_a_model(title_work, source):
    """Manual and already generated labels need no auxiliary model work."""
    title_work.thread.title_source = source
    await chat_titles.enqueue_conversation_title(
        parent=title_work.parent, thread=title_work.thread, transaction=object()
    )
    await chat_titles.run_conversation_title_work(title_work.item)
    title_work.enqueue.assert_not_awaited()
    title_work.build.assert_not_awaited()


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "cancelled"),
        ("principal_id", "other"),
        ("workspace_id", "other"),
        ("thread_id", "other"),
        ("origin", "mandate_review"),
    ],
)
async def test_wrong_or_cancelled_parent_cannot_fund_a_title(title_work, field, value):
    """A queued title never creates authority independent of its owned parent."""
    setattr(title_work.parent, field, value)
    with pytest.raises(WorkError, match="completed scoped chat"):
        await chat_titles.run_conversation_title_work(title_work.item)
    title_work.start.assert_not_awaited()
    title_work.build.assert_not_awaited()


async def test_title_has_its_own_run_and_uses_auxiliary_admission(title_work):
    """The child uses current routing, one-attempt transport and its own lease."""
    prior = current_latency.get()
    await chat_titles.run_conversation_title_work(title_work.item)
    title_work.finish.assert_awaited_once_with(
        title_work.start.call_args.kwargs["run_id"], status="succeeded"
    )
    assert current_latency.get() is prior
    args, kwargs = title_work.build.call_args
    assert args[1].run_id != "parent"
    assert args[2].work_item_id == "title"
    assert kwargs == {"model_role": "light", "auxiliary": True}
    infer = title_work.infer.call_args.kwargs
    assert infer["model"] is title_work.model
    assert infer["work_execution_context"].work_item_id == "title"
    assert callable(infer["assert_authority"])
    await infer["assert_authority"]()
    assert title_work.validate.await_count == 2


async def test_title_failure_only_fails_child_and_restores_task_context(title_work):
    """No mutation of the succeeded answer or parent state follows model failure."""
    title_work.infer.return_value = False
    prior = current_latency.get()
    with pytest.raises(WorkError, match="optional title generation unavailable"):
        await chat_titles.run_conversation_title_work(title_work.item)
    assert title_work.parent.status == "succeeded"
    title_work.finish.assert_awaited_once_with(
        title_work.start.call_args.kwargs["run_id"],
        status="failed",
        error={"code": "work.title_unavailable"},
    )
    assert current_latency.get() is prior


async def test_cancellation_closes_model_transport_and_does_not_touch_parent(
    title_work, monkeypatch
):
    """Task cancellation must release its transport and accounting ContextVar."""
    client = SimpleNamespace(close=AsyncMock())

    async def build(*args: object, **kwargs: object):
        current_latency.get().clients.append(client)
        return title_work.model

    monkeypatch.setattr(PydanticAIProvider, "_build_turn_model", build)
    title_work.infer.side_effect = asyncio.CancelledError()
    prior = current_latency.get()
    with pytest.raises(asyncio.CancelledError):
        await chat_titles.run_conversation_title_work(title_work.item)
    client.close.assert_awaited_once()
    assert current_latency.get() is prior
    assert title_work.parent.status == "succeeded"


async def test_auxiliary_transport_preserves_admission_with_one_physical_attempt(
    monkeypatch,
):
    """Title transport keeps the scoped meter while tightening retry and time caps."""
    from unittest.mock import Mock

    from app.agentive.harness import model_routing
    from app.agentive.harness.pydantic_ai_compat import UsageLimitExceeded
    from app.agentive.services.work_model_admission import WorkModelAdmission
    from app.config import settings
    from app.services.chat_latency import TurnLatency
    from app.services.chat_providers import pydantic_ai_provider as module

    thread = SimpleNamespace(model_profile_id="", model_reasoning_efforts={})
    monkeypatch.setattr(module.ChatThread, "get", AsyncMock(return_value=thread))
    route = SimpleNamespace(model="light-model", generation_settings={})
    plan = SimpleNamespace(
        light=route, heavy=route, mode="light_heavy", budget_settings={}
    )
    monkeypatch.setattr(
        model_routing, "conversation_turn_routes", AsyncMock(return_value=plan)
    )
    monkeypatch.setattr(model_routing, "bind_durable_routing", AsyncMock())
    monkeypatch.setattr(settings, "INTEGRAL_NATIVE_MODEL_MAX_ATTEMPTS", 3)
    builder = Mock(return_value=object())
    monkeypatch.setattr(module, "build_litellm_sdk_model", builder)
    timing = TurnLatency()
    timing.freeze_budget({"model_request_limit": 1, "turn_timeout": 15})
    token = current_latency.set(timing)
    try:
        await PydanticAIProvider()._build_turn_model(
            SimpleNamespace(thread_id="thread", workspace_id="workspace", user_id="u"),
            object(),
            object(),
            "owner",
            model_role="light",
            auxiliary=True,
        )
        kwargs = builder.call_args.kwargs
        assert isinstance(kwargs["admission"], WorkModelAdmission)
        assert kwargs["max_attempts"] == 1
        assert kwargs["timeout_seconds"] <= 10
        kwargs["dispatch_guard"]()
        with pytest.raises(UsageLimitExceeded):
            kwargs["dispatch_guard"]()
    finally:
        current_latency.reset(token)
