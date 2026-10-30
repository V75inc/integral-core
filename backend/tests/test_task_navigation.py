"""Task details resolve exact readable resources without expanding polling reads."""

import pytest

from app.agentive.services import work_items
from app.services import task_navigation

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]


async def test_work_details_link_app_and_track_without_exposing_input(
    authenticated_client, test_user, monkeypatch
):
    client = authenticated_client
    app = (await client.post("/api/apps", json={"name": "Task context App"})).json()[
        "app"
    ]
    track = (
        await client.post(
            "/api/tracks", json={"title": "Task context Track", "app_id": app["id"]}
        )
    ).json()["track"]
    work = await work_items.enqueue_work_item(
        kind="app_tool",
        origin="test",
        principal_id=test_user.user_id,
        workspace_id=app["workspace_id"],
        app_id=app["id"],
        idempotency_key="context",
        input_payload={
            "input": {"track_id": track["id"], "private_token": "must-not-leak"},
            "label": "Prepare summary",
        },
    )
    detail = await client.get(f"/api/work-items/{work.work_item_id}")
    assert detail.status_code == 200, detail.text
    links = detail.json()["context_links"]
    assert [(link["kind"], link["label"], link["href"]) for link in links] == [
        ("app", "Task context App", f"/apps/{app['id']}"),
        ("track", "Task context Track", f"/tracks/{track['id']}"),
    ]
    assert "must-not-leak" not in detail.text and "input_payload" not in detail.text

    async def no_context_reads(*args, **kwargs):
        raise AssertionError("Polled work lists must not resolve task context")

    monkeypatch.setattr(task_navigation, "task_context_links", no_context_reads)
    page = await client.get(
        "/api/work-items", params={"workspace_id": app["workspace_id"]}
    )
    assert page.status_code == 200, page.text
    assert all(not row["context_links"] for row in page.json()["items"])


async def test_task_context_withholds_unreadable_foreign_and_missing_targets(
    authenticated_client, test_user, monkeypatch
):
    app = (
        await authenticated_client.post("/api/apps", json={"name": "Private context"})
    ).json()["app"]
    ref = {"resource_type": "app", "resource_id": app["id"]}
    assert (
        await task_navigation.task_context_links(
            [ref], user_id=test_user.user_id, workspace_id="other-workspace"
        )
        == []
    )

    async def deny(*args, **kwargs):
        return None

    monkeypatch.setattr(task_navigation, "resolve_role", deny)
    assert (
        await task_navigation.task_context_links(
            [ref], user_id=test_user.user_id, workspace_id=app["workspace_id"]
        )
        == []
    )
    assert (
        await task_navigation.task_context_links(
            [{"resource_type": "app", "resource_id": "missing"}],
            user_id=test_user.user_id,
            workspace_id=app["workspace_id"],
        )
        == []
    )


async def test_task_context_is_bounded_deduplicated_and_fails_closed(monkeypatch):
    from app.exceptions import AuthorizationUnavailableError

    calls = []

    async def unavailable(kind, identifier, **kwargs):
        calls.append(identifier)
        raise AuthorizationUnavailableError()

    monkeypatch.setattr(task_navigation, "locate_resource", unavailable)
    refs = [{"resource_type": "track", "resource_id": str(i)} for i in range(20)]
    refs.insert(1, dict(refs[0]))
    refs.insert(0, {"resource_type": [], "resource_id": "invalid"})
    assert (
        await task_navigation.task_context_links(
            refs, user_id="user", workspace_id="workspace"
        )
        == []
    )
    assert calls == [str(i) for i in range(12)]


async def test_schedule_links_are_saved_before_completion_can_close_chat_review(
    monkeypatch,
):
    from app.agentive.services import staging_apply
    from app.agentive.staging import create_staged_change
    from app.services import chat_threads, prompt_queue
    from app.services.agent_scope import current_scope_workspace_id
    from tests.test_routines_api import _bootstrap

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "aa" * 32)
    user_id, workspace_id, thread = await _bootstrap("task-receipt-race@example.com")
    scope = current_scope_workspace_id.set(workspace_id)
    try:
        staged = await create_staged_change(
            user_id=user_id,
            session_id=thread.id,
            kind="routine_task_create",
            summary="New routine: Check pantry stock.",
            diff_human="One reminder in this chat",
            diff_machine={"op": "routine_task_create"},
            payload={
                "workspace_id": workspace_id,
                "thread_id": thread.id,
                "instruction": "Check pantry stock.",
                "delivery_mode": "reminder",
                "run_at": "2099-01-01T09:00:00+00:00",
                "timezone": "UTC",
            },
        )
        await prompt_queue.enqueue_staged_write(
            user_id=user_id, session_id=thread.id, staged=staged.to_dict()
        )
        consume = staging_apply.consume_token
        observed = []

        async def consume_and_poll(**kwargs):
            before = await chat_threads.get_thread(thread.id)
            assert prompt_queue.get_queue(before)["items"][0]["status"] == "pending"
            result = await consume(**kwargs)
            fresh = await chat_threads.get_thread(thread.id)
            closed = await prompt_queue.get_open_queue_for_thread(
                user_id=user_id, thread=fresh
            )
            observed.append(closed["resume_text"])
            return result

        monkeypatch.setattr(staging_apply, "consume_token", consume_and_poll)
        result = await staging_apply.bless_and_execute(
            user_id=user_id, token=staged.token
        )
    finally:
        current_scope_workspace_id.reset(scope)
    assert result["consumed"], result
    routine_id = result["execute_result"]["id"]
    assert (
        observed
        and f"/background-tasks?routine={routine_id}#scheduled-routines" in observed[0]
    )
