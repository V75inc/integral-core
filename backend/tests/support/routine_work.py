"""Real rooted routine authority for generic worker/lease test handlers."""

import uuid

from app.agentive.services.routine_mandate import routine_mandate_fingerprint
from app.agentive.services.routine_tasks import create_routine_task
from app.agentive.services.work_items import enqueue_work_item
from tests.test_routine_tasks import _bootstrap_user_workspace_track_thread


async def make_routine_work(*, key: str):
    user_id, workspace_id, _, thread = await _bootstrap_user_workspace_track_thread(
        f"worker-{uuid.uuid4().hex}@example.com"
    )
    routine = await create_routine_task(
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread.id,
        instruction="Synthetic worker boundary proof.",
        cron="0 9 * * *",
    )
    return await enqueue_work_item(
        kind="routine_turn",
        origin="scheduler",
        principal_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread.id,
        idempotency_key=key,
        input_payload={
            "routine_id": routine.id,
            "routine_mandate_fingerprint": routine_mandate_fingerprint(routine),
        },
    )
