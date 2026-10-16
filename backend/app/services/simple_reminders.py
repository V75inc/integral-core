"""Bounded reminder proposals through the existing governed scheduling capability."""

from app.agentive.harness.adaptive import SimpleReminderRequest
from app.agentive.harness.broker_tools import _idempotency_key, _tool_work_context
from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.services.capability_broker import invoke_declared_capability
from app.agentive.services.work_items import authorized_work_item_effect
from app.schemas.agentive.work import WorkExecutionContext


async def stage_simple_reminder(
    *,
    proposal: SimpleReminderRequest,
    scope: HarnessExecutionScope,
    work_context: WorkExecutionContext | None,
    no_workspace_writes: bool = False,
) -> tuple[str, list[dict]]:
    """One typed model output selects this proposal, never an approval or write."""
    if no_workspace_writes:
        return (
            "I haven’t saved a reminder because you asked me not to save changes.",
            [],
        )
    name = "integral_schedule_task"
    call_id = "simple-reminder"
    arguments = {
        "instruction": proposal.message,
        "delivery_mode": "reminder",
        **proposal.model_dump(exclude_none=True, exclude={"message"}),
    }
    tool_context = (
        _tool_work_context(work_context, call_id, name) if work_context else None
    )

    async def invoke():
        return await invoke_declared_capability(
            principal_id=scope.principal_id,
            workspace_id=scope.workspace_id,
            capability_key=name,
            origin="chat",
            source="core",
            op_class="propose",
            arguments=arguments,
            run_id=scope.run_id,
            session_id=scope.thread_id,
            work_execution_context=tool_context,
            idempotency_key=_idempotency_key(scope.run_id, call_id, name),
        )

    if work_context is None:
        result = await invoke()
    else:
        async with authorized_work_item_effect(work_context):
            result = await invoke()
    event = {
        "type": "tool-call",
        "toolCallId": call_id,
        "name": name,
        "args": arguments,
        "result": result.for_model(),
        "status": "complete" if result.ok else "error",
    }
    if not result.ok:
        return "I couldn’t prepare the reminder: " + result.message, [event]
    return (
        "Please approve the reminder card. Once approved, it will deliver the saved message on your chosen schedule.",
        [event],
    )


async def current_reminder_confirmation(thread) -> str | None:
    """Bind a closed, single-reminder approval episode to its live saved row.

    No old model checkpoint or assistant summary can select the resource.
    Mixed/multi-part episodes retain the general operational continuation.
    """
    from app.agentive.edges import HAS_ROUTINE_TASK
    from app.agentive.nodes import RoutineTask
    from app.agentive.staging import get_token
    from app.services.permissions import get_user_node
    from app.services.prompt_queue import get_queue

    queue = get_queue(thread)
    items = queue.get("items") or []
    if queue["status"] != "closed" or not queue.get("closed_at") or len(items) != 1:
        return None
    item = items[0]
    if (
        item.get("kind") != "staged_write"
        or item.get("write_kind") != "routine_task_create"
    ):
        return None
    token = str(item.get("token") or "")
    change = await get_token(token) if token else None
    if (
        change is None
        or change.user_id != thread.user_id
        or change.workspace_id != thread.workspace_id
        or change.session_id != thread.id
        or change.kind != "routine_task_create"
        or change.payload.get("delivery_mode", "agent") != "reminder"
    ):
        return None
    if item.get("status") == "rejected" and change.state == "revoked":
        return "The reminder was not created."
    if item.get("status") != "approved" or change.state != "consumed":
        return None
    user = await get_user_node(thread.user_id)
    rows = (
        await user.nodes(
            edge=[HAS_ROUTINE_TASK],
            node=[{"RoutineTask": {"source_staging_token": token}}],
            direction="out",
            limit=2,
        )
        if user
        else []
    )
    if len(rows) != 1:
        return "The reminder approval was recorded, but I couldn’t verify the saved reminder."
    routine = await RoutineTask.get(rows[0].id)
    if (
        routine is None
        or routine.user_id != thread.user_id
        or routine.workspace_id != thread.workspace_id
        or routine.thread_id != thread.id
        or routine.delivery_mode != "reminder"
        or routine.instruction != change.payload.get("instruction")
    ):
        return "The reminder approval was recorded, but I couldn’t verify the saved reminder."
    if routine.status == "active":
        return "Your reminder is active. You can review its schedule or pause it in Background Tasks."
    return f"Your reminder is {routine.status}. You can review it in Background Tasks."
