"""Saved-text reminders retain staging, live admission and durable delivery."""

from dataclasses import replace

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from app.agentive.harness.adaptive import (
    LightTurnResult,
    SimpleReminderRequest,
    build_light_agent,
)
from app.agentive.services import routine_tasks
from app.api.errors import BadRequestError

pytestmark = pytest.mark.smoke


async def _setup_approval_turn(monkeypatch):
    """Use a live auth identity for tests that actually apply approved writes."""
    import uuid

    from jvspatial.api.auth.models import User as AuthUser

    from app.models.nodes import User
    from tests.native_harness.test_adaptive_context import setup_turn

    ctx, thread, run, calls = await setup_turn(monkeypatch)
    profile = await User.find_one({"id": ctx.user_id})
    auth = await AuthUser.create(
        email=f"routine-approval-{uuid.uuid4().hex}@example.com",
        password_hash="fixture",
    )
    profile.user_id = auth.id
    await profile.save()
    return ctx, thread, run, calls


@pytest.mark.asyncio
async def test_reminder_stream_rechecks_authority_after_tool_event(monkeypatch):
    from app.schemas.agentive.work import WorkError
    from app.services.chat_providers.pydantic_ai_provider import PydanticAIProvider
    from tests.native_harness.test_adaptive_context import setup_turn

    ctx, _, _, _ = await setup_turn(monkeypatch)
    provider = PydanticAIProvider()
    prepared = await provider._prepare(ctx)
    prepared.tool_events = [{"type": "tool-call", "name": "proposal"}]

    async def prepare(_):
        return prepared

    revoked = False

    async def assert_current(_):
        if revoked:
            raise WorkError("work.lease_lost", "lease changed")

    monkeypatch.setattr(provider, "_prepare", prepare)
    monkeypatch.setattr(
        "app.services.chat_providers.pydantic_ai_provider._assert_work_output_authority_current",
        assert_current,
    )
    stream = provider.stream_turn(ctx)
    try:
        assert (await anext(stream))["type"] == "tool-call"
        revoked = True
        with pytest.raises(WorkError):
            await anext(stream)
    finally:
        await stream.aclose()


@pytest.mark.parametrize(
    "data",
    [
        {"message": "Water"},
        {"message": "Water", "cron": "* * * * *", "run_at": "2040-01-01T00:00:00Z"},
        {"message": " ", "cron": "* * * * *"},
        {"message": "Water", "cron": "* * * * *", "max_runs": 0},
        {"message": "Water", "cron": "* * * * *", "workspace_id": "foreign"},
    ],
)
def test_reminder_output_has_no_authority_fields_and_one_cadence(data):
    with pytest.raises(ValidationError):
        SimpleReminderRequest(**data)


@pytest.mark.parametrize(
    "mode,scope,text",
    [
        ("unknown", [], "Water"),
        ("reminder", [{"resource_type": "track", "resource_id": "t"}], "Water"),
        ("reminder", [], "x" * 2001),
    ],
)
def test_static_reminder_cannot_gain_write_scope(mode, scope, text):
    with pytest.raises(BadRequestError):
        routine_tasks.validate_delivery_mode(mode, text, scope)


@pytest.mark.asyncio
async def test_one_model_request_can_prepare_simple_reminder_without_discovery():
    def respond(messages, info):
        assert not info.function_tools
        assert "search_capabilities" not in str(messages)
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "propose_simple_reminder",
                    {
                        "message": "Take a drink of water.",
                        "cron": "* * * * *",
                        "timezone": "America/Guyana",
                    },
                    "reminder",
                )
            ]
        )

    result = await build_light_agent(FunctionModel(respond)).run(
        "Remind me every minute to drink water"
    )
    assert isinstance(result.output, SimpleReminderRequest)
    assert result.output.message == "Take a drink of water."
    assert result.usage.requests == 1


@pytest.mark.asyncio
async def test_provider_reminder_proposal_skips_heavy_context_and_keeps_approval(
    monkeypatch,
):
    from app.agentive.nodes import RoutineTask
    from app.models.nodes import ChatThread
    from app.services.chat_providers.pydantic_ai_provider import PydanticAIProvider
    from app.services.prompt_queue import get_queue
    from tests.native_harness.test_adaptive_context import setup_turn

    ctx, thread, _, _ = await setup_turn(monkeypatch)
    ctx = replace(ctx, text="Remind me every minute to drink water")

    def respond(messages, info):
        assert not info.function_tools
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "propose_simple_reminder",
                    {
                        "message": "Take a drink of water.",
                        "cron": "* * * * *",
                    },
                    "reminder",
                )
            ]
        )

    monkeypatch.setattr(
        "app.services.chat_providers.pydantic_ai_provider.build_litellm_sdk_model",
        lambda **kwargs: FunctionModel(respond),
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("simple reminder loaded full workspace skills")

    monkeypatch.setattr(
        "app.agentive.workspace_agent_profile.compose_workspace_agent_profile",
        forbidden,
    )
    result = await PydanticAIProvider()._prepare(ctx)
    assert isinstance(result, LightTurnResult)
    assert "approve" in result.text
    assert result.tool_events[0]["status"] == "complete", result.tool_events
    assert result.tool_events[0]["result"]["state"] == "pending"
    queue = get_queue(await ChatThread.get(thread.id))
    assert len(queue["items"]) == 1
    assert not await RoutineTask.find({"user_id": ctx.user_id})
    assert (await ChatThread.get(thread.id)).active_harness_session_id is None


@pytest.mark.asyncio
async def test_agent_routine_approval_keeps_general_outcome_path(monkeypatch):
    from app.agentive.services.staging_apply import bless_and_execute
    from app.agentive.tooling.dispatch import dispatch_tool
    from app.models.nodes import ChatThread
    from app.services.prompt_queue import reconcile_staged_write_items
    from app.services.simple_reminders import current_reminder_confirmation

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "55" * 32)
    ctx, thread, _, _ = await _setup_approval_turn(monkeypatch)
    staged = await dispatch_tool(
        "integral_schedule_task",
        {
            "instruction": "Review current records and report changes.",
            "cron": "0 9 * * *",
        },
        principal_id=ctx.user_id,
        scope=ctx.workspace_id,
        session_id=thread.id,
    )
    assert not staged.is_error, staged
    approved = await bless_and_execute(user_id=ctx.user_id, token=staged.data["token"])
    assert not approved["execute_result"].get("error"), approved
    thread = await ChatThread.get(thread.id)
    await reconcile_staged_write_items(user_id=ctx.user_id, thread=thread)
    thread = await ChatThread.get(thread.id)
    assert await current_reminder_confirmation(thread) is None


@pytest.mark.asyncio
async def test_reminder_approval_confirmation_uses_saved_current_episode(monkeypatch):
    from app.agentive.nodes import RoutineTask
    from app.agentive.services.staging_apply import bless_and_execute
    from app.agentive.tooling.dispatch import dispatch_tool
    from app.models.nodes import ChatThread
    from app.services.chat_providers.pydantic_ai_provider import PydanticAIProvider
    from app.services.prompt_queue import reconcile_staged_write_items
    from app.services.simple_reminders import current_reminder_confirmation

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "55" * 32)
    ctx, thread, _, _ = await _setup_approval_turn(monkeypatch)
    user = ctx.user_id
    workspace = ctx.workspace_id
    staged = await dispatch_tool(
        "integral_schedule_task",
        {
            "instruction": "Take a drink of water.",
            "delivery_mode": "reminder",
            "cron": "* * * * *",
        },
        principal_id=user,
        scope=workspace,
        session_id=thread.id,
    )
    assert not staged.is_error, staged
    approved = await bless_and_execute(user_id=user, token=staged.data["token"])
    assert not approved["execute_result"].get("error"), approved
    thread = await ChatThread.get(thread.id)
    await reconcile_staged_write_items(user_id=user, thread=thread)
    thread = await ChatThread.get(thread.id)
    confirmation = await current_reminder_confirmation(thread)
    assert confirmation and "reminder is active" in confirmation
    routines = await RoutineTask.find({"user_id": user})
    assert routines[0].delivery_mode == "reminder"
    assert routines[0].source_staging_token == staged.data["token"]

    def forbidden_model(*args, **kwargs):
        raise AssertionError("approval confirmation called a model")

    monkeypatch.setattr(
        "app.services.chat_providers.pydantic_ai_provider.build_litellm_sdk_model",
        forbidden_model,
    )
    outcome_ctx = replace(
        ctx,
        text="",
        extra_data={**ctx.extra_data, "staging_outcome_continuation": True},
    )
    from app.agentive.harness.adaptive import LightTurnResult

    response = await PydanticAIProvider()._prepare(outcome_ctx)
    assert isinstance(response, LightTurnResult)
    assert "reminder is active" in response.text

    await routine_tasks.update_routine_task(
        user_id=user, routine_id=routines[0].id, status="paused"
    )
    assert "paused" in await current_reminder_confirmation(thread)


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_durable_static_delivery_is_one_message_without_model(monkeypatch):
    from app.agentive.services import work_execution, work_items, work_worker
    from app.services import routine_task_scheduler as scheduler
    from app.services.chat_threads import list_messages
    from tests.test_routine_tasks import _bootstrap_user_workspace_track_thread

    user, workspace, _, thread = await _bootstrap_user_workspace_track_thread(
        "reminder-delivery@example.com"
    )
    routine = await routine_tasks.create_routine_task(
        user_id=user,
        workspace_id=workspace,
        thread_id=thread.id,
        instruction="Take a drink of water.",
        cron="* * * * *",
        delivery_mode="reminder",
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("static delivery invoked the model")

    monkeypatch.setattr(scheduler, "_run_agent_turn", forbidden)
    item = await routine_tasks.enqueue_routine_turn_work(
        routine_id=routine.id,
        principal_id=user,
        workspace_id=workspace,
        thread_id=thread.id,
        scheduled_for="slot-1",
    )
    leased = await work_items.claim_due_candidate(
        worker_id="reminder-test", work_item_id=item.work_item_id, lease_seconds=30
    )
    assert leased is not None
    context = work_execution.build_work_execution_context(
        work_item=leased,
        logical_step_key=work_execution.logical_step_key_for(
            kind="routine_turn", ordinal=0
        ),
    )
    await routine_tasks.execute_routine_turn_under_lease(leased, context)
    await routine_tasks.execute_routine_turn_under_lease(leased, context)
    messages = await list_messages(thread)
    assert len(messages) == 1
    assert messages[0].parts == [{"type": "text", "text": "Take a drink of water."}]
    assert messages[0].provider_metadata["steps"] == []
    await work_items.cancel_work_item(item.work_item_id)
    from app.schemas.agentive.work import WorkError

    with pytest.raises(WorkError):
        await routine_tasks.execute_routine_turn_under_lease(leased, context)
    assert len(await list_messages(thread)) == 1
