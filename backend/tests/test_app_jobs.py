"""Background App jobs: queueing, running, progress, status and schedules.

An App queues one of its own tools as a durable ``app_tool`` work item. These
tests cover the boundaries that matter when work runs later, as another
person, in a worker: who may queue what, what happens when the tool or the
principal changes before the job runs, that progress only comes from the
worker that holds the lease, and that a schedule runs a slot once.
"""

from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.agentive.services import app_schedules, work_items, work_worker
from app.services.hooks import registry
from app.services.hooks.registry import ToolContext
from app.services.operational_model_compile import (
    OperationalModelValidationError,
    _tool_schedule,
)

WS = "aj-ws-1"
USER = "aj-user-1"


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(registry._TOOLS)
    registry._TOOLS.clear()
    work_worker.clear_test_handlers()
    yield
    registry._TOOLS.clear()
    registry._TOOLS.update(saved)
    work_worker.clear_test_handlers()


def _tool(monkeypatch, key="run_job", *, bundle="demo-app", result=None, impl=None, schedule=None):
    """Register one tool for ``WS`` whose handler is ``impl`` (or returns ``result``)."""
    seen: list = []

    async def handler(payload, ctx):
        seen.append({"payload": payload, "ctx": ctx})
        if impl is not None:
            return await impl(payload, ctx)
        return result if result is not None else {"ok": True}

    module = types.ModuleType("aj_fake_tools")
    setattr(module, key, handler)
    monkeypatch.setitem(sys.modules, "aj_fake_tools", module)
    spec = {
        "key": key,
        "handler_ref": f"aj_fake_tools:{key}",
        "parameters_schema": {"type": "object"},
        "output_schema": {},
        "_bundle_slug": bundle,
    }
    if schedule:
        spec["schedule"] = schedule
    registry._TOOLS.setdefault(WS, {})[key] = spec
    return seen


def _ctx(bundle="demo-app", **kw) -> ToolContext:
    return ToolContext(user_id=USER, workspace_id=WS, scope="test", bundle_slug=bundle, **kw)


async def _resolve_app(monkeypatch, app_id=None):
    """The App id a queued job is bound to (None: not bound to a stored App)."""
    monkeypatch.setattr(
        "app.services.relation_runtime.resolve_target_app",
        AsyncMock(return_value=app_id),
    )


# --- queueing ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_job_queues_an_app_tool_item(monkeypatch) -> None:
    _tool(monkeypatch)
    await _resolve_app(monkeypatch)
    out = await _ctx().enqueue_job("run_job", {"entry_id": "e1"}, idempotency_key="k1", label="Do it")
    from app.agentive.work_models import WorkItem

    item = await WorkItem.get(work_items._object_id(out["job_id"]))
    assert out["status"] == "queued"
    assert item.kind == "app_tool"
    assert item.principal_id == USER and item.workspace_id == WS
    assert item.input_payload["tool_key"] == "run_job"
    assert item.input_payload["bundle_slug"] == "demo-app"
    assert item.input_payload["input"] == {"entry_id": "e1"}
    assert item.input_payload["label"] == "Do it"


@pytest.mark.asyncio
async def test_enqueue_job_is_idempotent_per_app_tool_and_key(monkeypatch) -> None:
    _tool(monkeypatch)
    await _resolve_app(monkeypatch)
    first = await _ctx().enqueue_job("run_job", {}, idempotency_key="same")
    again = await _ctx().enqueue_job("run_job", {}, idempotency_key="same")
    other = await _ctx().enqueue_job("run_job", {}, idempotency_key="different")
    assert first["job_id"] == again["job_id"]
    assert other["job_id"] != first["job_id"]


@pytest.mark.asyncio
async def test_enqueue_job_refuses_another_apps_tool(monkeypatch) -> None:
    _tool(monkeypatch, bundle="someone-elses-app")
    with pytest.raises(PermissionError, match="not a tool of this App"):
        await _ctx(bundle="demo-app").enqueue_job("run_job", {}, idempotency_key="k")


@pytest.mark.asyncio
async def test_enqueue_job_refuses_an_unknown_tool(monkeypatch) -> None:
    with pytest.raises(PermissionError, match="not a tool of this App"):
        await _ctx().enqueue_job("nope", {}, idempotency_key="k")


@pytest.mark.asyncio
async def test_enqueue_job_requires_a_running_app_tool(monkeypatch) -> None:
    _tool(monkeypatch)
    with pytest.raises(PermissionError, match="only a running app tool"):
        await _ctx(bundle="").enqueue_job("run_job", {}, idempotency_key="k")


@pytest.mark.asyncio
async def test_enqueue_job_refuses_a_read_only_context(monkeypatch) -> None:
    _tool(monkeypatch)
    with pytest.raises(PermissionError, match="read-only"):
        await _ctx(read_only=True).enqueue_job("run_job", {}, idempotency_key="k")


@pytest.mark.asyncio
async def test_enqueue_job_requires_an_idempotency_key(monkeypatch) -> None:
    _tool(monkeypatch)
    with pytest.raises(ValueError, match="idempotency_key"):
        await _ctx().enqueue_job("run_job", {}, idempotency_key="  ")


@pytest.mark.asyncio
async def test_enqueue_job_truncates_a_long_label(monkeypatch) -> None:
    _tool(monkeypatch)
    await _resolve_app(monkeypatch)
    out = await _ctx().enqueue_job("run_job", {}, idempotency_key="k", label="x" * 500)
    from app.agentive.work_models import WorkItem

    item = await WorkItem.get(work_items._object_id(out["job_id"]))
    assert len(item.input_payload["label"]) == 200


# --- running ----------------------------------------------------------------


async def _queue_and_run(monkeypatch, *, key="run_job", payload=None, bundle="demo-app"):
    await _resolve_app(monkeypatch)
    item = await work_items.enqueue_work_item(
        kind="app_tool",
        origin="app_tool",
        principal_id=USER,
        workspace_id=WS,
        idempotency_key=f"aj-{key}-{datetime.now().timestamp()}",
        input_payload={"tool_key": key, "bundle_slug": bundle, "input": payload or {"a": 1}, "label": "L"},
    )
    return await work_worker.process_one_due_item(
        worker_id="aj-worker", work_item_id=item.work_item_id, lease_seconds=30
    )


@pytest.mark.asyncio
async def test_job_runs_the_tool_as_the_queuing_person_in_their_workspace(monkeypatch) -> None:
    seen = _tool(monkeypatch)
    done = await _queue_and_run(monkeypatch)
    assert done.status == "succeeded"
    assert done.result_refs == ["app_tool:run_job"]
    ctx = seen[0]["ctx"]
    assert seen[0]["payload"] == {"a": 1}
    assert (ctx.user_id, ctx.workspace_id, ctx.bundle_slug) == (USER, WS, "demo-app")
    assert ctx.job_id == done.work_item_id
    assert ctx.job_lease and ctx.job_lease[0]


@pytest.mark.asyncio
async def test_job_result_lists_the_records_it_acted_on(monkeypatch) -> None:
    refs = [{"kind": "entry", "id": f"n.Entry.{i}"} for i in range(30)]
    refs += [{"kind": "entry", "id": "n.Entry.1"}, {"kind": "track", "id": "t1"}, {"kind": "entry", "id": "x" * 500}]
    _tool(monkeypatch, result={"ok": True, "object_refs": refs})
    done = await _queue_and_run(monkeypatch)
    assert done.result_refs[0] == "app_tool:run_job"
    listed = done.result_refs[1:]
    assert listed[0] == "entry:n.Entry.0"
    assert len(listed) == 20  # capped
    assert len(set(listed)) == len(listed)  # no repeats
    assert not any(r.startswith("track:") for r in listed)  # only entries
    assert not any(len(r) > 130 for r in listed)  # oversized ids dropped


@pytest.mark.asyncio
async def test_tool_that_returns_ok_false_fails_the_job_without_retry(monkeypatch) -> None:
    _tool(monkeypatch, result={"ok": False, "reason": "no employees"})
    done = await _queue_and_run(monkeypatch)
    assert done.status == "failed"
    assert done.failure["code"] == "app_tool.failed"
    assert "no employees" in done.failure["message"]
    assert done.failure["retryable"] is False


@pytest.mark.asyncio
async def test_tool_that_asks_for_a_retry_is_retried(monkeypatch) -> None:
    _tool(monkeypatch, result={"ok": False, "reason": "busy", "retry": True})
    done = await _queue_and_run(monkeypatch)
    assert done.status == "retry_wait"
    assert done.failure["retryable"] is True


@pytest.mark.asyncio
async def test_a_raising_tool_is_retried_not_left_running(monkeypatch) -> None:
    async def boom(payload, ctx):
        raise RuntimeError("kaboom")

    _tool(monkeypatch, impl=boom)
    done = await _queue_and_run(monkeypatch)
    assert done.status == "retry_wait"
    assert done.failure["code"] == "work.transient_app_tool"
    assert "RuntimeError: kaboom" in done.failure["message"]


@pytest.mark.asyncio
async def test_a_tool_that_keeps_raising_ends_in_dead_letter(monkeypatch) -> None:
    async def boom(payload, ctx):
        raise RuntimeError("always")

    _tool(monkeypatch, impl=boom)
    item = await work_items.enqueue_work_item(
        kind="app_tool",
        origin="app_tool",
        principal_id=USER,
        workspace_id=WS,
        idempotency_key="aj-dead-letter",
        input_payload={"tool_key": "run_job", "bundle_slug": "demo-app", "input": {}},
    )
    from app.agentive.work_models import WorkItem

    last = None
    for _ in range(12):
        last = await work_worker.process_one_due_item(
            worker_id="aj-worker", work_item_id=item.work_item_id, lease_seconds=30
        )
        if last is not None and last.status in {"dead_letter", "failed"}:
            break
        row = await WorkItem.get(work_items._object_id(item.work_item_id))
        row.next_attempt_at = ""  # skip the backoff wait
        await row.save()
    assert last is not None and last.status == "dead_letter"


@pytest.mark.asyncio
async def test_a_job_queued_against_an_older_app_version_does_not_run(monkeypatch) -> None:
    seen = _tool(monkeypatch)
    item = await work_items.enqueue_work_item(
        kind="app_tool",
        origin="app_tool",
        principal_id=USER,
        workspace_id=WS,
        idempotency_key="aj-stale-definition",
        input_payload={"tool_key": "run_job", "bundle_slug": "demo-app", "input": {}},
    )
    from app.agentive.work_models import WorkItem

    row = await WorkItem.get(work_items._object_id(item.work_item_id))
    row.app_id, row.definition_id = "aj-app-1", "definition-at-enqueue"
    await row.save()
    upgraded = types.SimpleNamespace(active_definition_id="definition-after-upgrade")
    with patch("app.models.nodes.App.get", AsyncMock(return_value=upgraded)):
        done = await work_worker.process_one_due_item(
            worker_id="aj-worker", work_item_id=item.work_item_id, lease_seconds=30
        )
    assert done.status == "failed"
    assert done.failure["code"] == "work.definition_stale"
    assert seen == []  # the upgraded App's code never ran an old job


@pytest.mark.asyncio
async def test_a_job_whose_app_was_removed_does_not_run(monkeypatch) -> None:
    seen = _tool(monkeypatch)
    item = await work_items.enqueue_work_item(
        kind="app_tool",
        origin="app_tool",
        principal_id=USER,
        workspace_id=WS,
        idempotency_key="aj-removed-app",
        input_payload={"tool_key": "run_job", "bundle_slug": "demo-app", "input": {}},
    )
    from app.agentive.work_models import WorkItem

    row = await WorkItem.get(work_items._object_id(item.work_item_id))
    row.app_id, row.definition_id = "aj-app-1", "definition-at-enqueue"
    await row.save()
    with patch("app.models.nodes.App.get", AsyncMock(return_value=None)):
        done = await work_worker.process_one_due_item(
            worker_id="aj-worker", work_item_id=item.work_item_id, lease_seconds=30
        )
    assert done.status == "failed"
    assert done.failure["code"] == "work.definition_stale"
    assert seen == []


@pytest.mark.asyncio
async def test_a_tool_missing_at_run_time_is_retried_not_run(monkeypatch) -> None:
    # Queued, then the registry has no such tool (not registered yet, or the App was removed).
    done = await _queue_and_run(monkeypatch, key="gone")
    assert done.status == "retry_wait"
    assert done.failure["code"] == "work.transient_tool_unavailable"


@pytest.mark.asyncio
async def test_a_tool_that_changed_owner_fails_permanently(monkeypatch) -> None:
    seen = _tool(monkeypatch, bundle="another-app")
    done = await _queue_and_run(monkeypatch, bundle="demo-app")
    assert done.status == "failed"
    assert done.failure["code"] == "work.policy_denied"
    assert seen == []  # the tool never ran


@pytest.mark.asyncio
async def test_a_job_whose_principal_is_gone_does_not_run(monkeypatch) -> None:
    seen = _tool(monkeypatch)
    await _resolve_app(monkeypatch)
    item = await work_items.enqueue_work_item(
        kind="app_tool",
        origin="app_tool",
        principal_id=USER,
        workspace_id=WS,
        idempotency_key="aj-gone-principal",
        input_payload={"tool_key": "run_job", "bundle_slug": "demo-app", "input": {}},
    )
    from app.schemas.agentive.work import WorkError

    with patch.object(
        work_worker,
        "_recheck_principal_workspace",
        AsyncMock(side_effect=WorkError("work.policy_denied", "no longer a member")),
    ):
        done = await work_worker.process_one_due_item(
            worker_id="aj-worker", work_item_id=item.work_item_id, lease_seconds=30
        )
    assert done.status == "failed"
    assert done.failure["code"] == "work.policy_denied"
    assert seen == []


# --- progress ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_progress_is_recorded_and_shown_while_running(monkeypatch) -> None:
    async def work(payload, ctx):
        assert await ctx.report_progress(3, 10, "Doing 3 of 10") is True
        mid = await ctx.job_status(ctx.job_id)
        return {"ok": True, "mid": mid}

    seen = _tool(monkeypatch, impl=work)
    await _resolve_app(monkeypatch)
    done = await _queue_and_run(monkeypatch)
    assert done.status == "succeeded"
    from app.agentive.work_models import WorkItem

    item = await WorkItem.get(work_items._object_id(done.work_item_id))
    assert item.progress == {"label": "Doing 3 of 10", "done": 3, "total": 10}


@pytest.mark.asyncio
async def test_progress_label_is_bounded(monkeypatch) -> None:
    async def work(payload, ctx):
        await ctx.report_progress(label="y" * 900)
        return {"ok": True}

    _tool(monkeypatch, impl=work)
    done = await _queue_and_run(monkeypatch)
    from app.agentive.work_models import WorkItem

    item = await WorkItem.get(work_items._object_id(done.work_item_id))
    assert len(item.progress["label"]) == 200


@pytest.mark.asyncio
async def test_progress_outside_a_job_is_a_no_op() -> None:
    assert await _ctx().report_progress(1, 2, "x") is False


@pytest.mark.asyncio
async def test_progress_from_a_worker_that_lost_its_lease_is_refused(monkeypatch) -> None:
    item = await work_items.enqueue_work_item(
        kind="app_tool",
        origin="app_tool",
        principal_id=USER,
        workspace_id=WS,
        idempotency_key="aj-stale-lease",
        input_payload={"tool_key": "run_job", "bundle_slug": "demo-app", "input": {}},
    )
    claimed = await work_items.claim_due_candidate(
        worker_id="w-new", work_item_id=item.work_item_id, lease_seconds=30
    )
    good = (claimed.lease_token, int(claimed.lease_fence or 0))
    stale = ("an-old-token", good[1] - 1)
    assert await work_items.report_work_progress(claimed.work_item_id, {"label": "stale"}, lease=stale) is False
    assert await work_items.report_work_progress(claimed.work_item_id, {"label": "fresh"}, lease=good) is True
    from app.agentive.work_models import WorkItem

    after = await WorkItem.get(work_items._object_id(claimed.work_item_id))
    assert after.progress == {"label": "fresh"}


@pytest.mark.asyncio
async def test_progress_after_the_job_finished_is_refused(monkeypatch) -> None:
    _tool(monkeypatch)
    done = await _queue_and_run(monkeypatch)
    assert done.status == "succeeded"
    assert await work_items.report_work_progress(done.work_item_id, {"label": "late"}) is False


# --- status -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_job_status_shows_label_and_progress_to_the_owning_app_only(monkeypatch) -> None:
    _tool(monkeypatch)
    await _resolve_app(monkeypatch)
    out = await _ctx().enqueue_job("run_job", {}, idempotency_key="st", label="Pay run")
    status = await _ctx().job_status(out["job_id"])
    assert status["status"] == "queued"
    assert await _ctx(bundle="other-app").job_status(out["job_id"]) is None
    other_ws = ToolContext(user_id=USER, workspace_id="aj-ws-other", scope="test", bundle_slug="demo-app")
    assert await other_ws.job_status(out["job_id"]) is None
    assert await _ctx().job_status("") is None
    assert await _ctx().job_status("does-not-exist") is None


# --- who serves which work ---------------------------------------------------


@pytest.mark.asyncio
async def test_app_jobs_are_only_claimed_by_the_app_worker(monkeypatch) -> None:
    item = await work_items.enqueue_work_item(
        kind="app_tool",
        origin="app_tool",
        principal_id=USER,
        workspace_id=WS,
        idempotency_key="aj-split",
        input_payload={"tool_key": "run_job", "bundle_slug": "demo-app", "input": {}},
    )
    # Targeted claims honour the same filters...
    assert (
        await work_items.claim_due_candidate(
            worker_id="main", work_item_id=item.work_item_id, exclude_kinds=work_worker.APP_JOB_KINDS
        )
        is None
    )
    assert (
        await work_items.claim_due_candidate(
            worker_id="chat", work_item_id=item.work_item_id, kinds=frozenset({"chat_turn"})
        )
        is None
    )
    # ...and so does the scan a real worker does (no id): the main worker
    # never picks an App job, the App worker does.
    main = await work_items.claim_due_candidate(worker_id="main", exclude_kinds=work_worker.APP_JOB_KINDS)
    assert main is None or main.kind != "app_tool"
    apps = await work_items.claim_due_candidate(worker_id="apps", kinds=work_worker.APP_JOB_KINDS)
    assert apps is not None and apps.kind == "app_tool"


def test_app_tool_is_a_handled_kind() -> None:
    assert "app_tool" in work_worker.HANDLED_KINDS
    assert work_worker.APP_JOB_KINDS == frozenset({"app_tool"})


# --- schedules ----------------------------------------------------------------


def test_tool_schedule_accepts_a_cron_string_or_object() -> None:
    assert _tool_schedule("0 2 * * *", where="t") == {"cron": "0 2 * * *", "timezone": "UTC"}
    assert _tool_schedule({"cron": "*/5 * * * *", "timezone": "America/Guyana"}, where="t") == {
        "cron": "*/5 * * * *",
        "timezone": "America/Guyana",
    }
    assert _tool_schedule(None, where="t") is None


@pytest.mark.parametrize(
    "raw",
    ["not a cron", {"cron": "0 2 * * *", "timezone": "Mars/Base"}, {"timezone": "UTC"}, 42],
)
def test_tool_schedule_rejects_bad_input(raw) -> None:
    with pytest.raises(OperationalModelValidationError):
        _tool_schedule(raw, where="t")


def test_due_slot_only_reports_a_slot_inside_the_window() -> None:
    sched = {"cron": "0 2 * * *", "timezone": "UTC"}
    on_time = datetime(2030, 1, 5, 2, 0, 20, tzinfo=timezone.utc)
    assert app_schedules._due_slot(sched, on_time) is not None
    assert app_schedules._due_slot(sched, on_time + timedelta(minutes=30)) is None
    assert app_schedules._due_slot({"cron": "bad", "timezone": "UTC"}, on_time) is None


class _FakeApp:
    id = "aj-app-1"
    workspace_id = WS
    lifecycle_state = "active"


def _patch_schedule_env(monkeypatch, *, app=_FakeApp(), principal=USER):
    monkeypatch.setattr(app_schedules, "_app_for", AsyncMock(return_value=app))
    # The stored App behind the fake: bind to a definition without needing a real install.
    monkeypatch.setattr(work_items, "resolve_active_definition_binding", AsyncMock(return_value="definition-1"))
    monkeypatch.setattr(
        "app.agentive.services.uplink_registry._resolve_installing_principal", AsyncMock(return_value=principal)
    )


@pytest.mark.asyncio
async def test_a_due_scheduled_tool_is_queued_once_however_often_the_pass_runs(monkeypatch) -> None:
    _tool(monkeypatch, schedule={"cron": "0 2 * * *", "timezone": "UTC"})
    _patch_schedule_env(monkeypatch)
    now = datetime(2031, 3, 4, 2, 0, 20, tzinfo=timezone.utc)
    assert await app_schedules.enqueue_due_app_schedules(now) == 1
    # A second pass (or another process) in the same minute queues the same slot, which dedupes.
    await app_schedules.enqueue_due_app_schedules(now + timedelta(seconds=30))
    from app.agentive.work_models import WorkItem

    rows = [w for w in await WorkItem.find({"kind": "app_tool", "workspace_id": WS}) if w.origin == "app_schedule"]
    assert len({w.work_item_id for w in rows}) == 1
    assert rows[0].principal_id == USER
    assert rows[0].input_payload["bundle_slug"] == "demo-app"


@pytest.mark.asyncio
async def test_nothing_is_queued_outside_the_slot(monkeypatch) -> None:
    _tool(monkeypatch, schedule={"cron": "0 2 * * *", "timezone": "UTC"})
    _patch_schedule_env(monkeypatch)
    assert await app_schedules.enqueue_due_app_schedules(datetime(2031, 3, 4, 9, 0, tzinfo=timezone.utc)) == 0


@pytest.mark.asyncio
async def test_a_scheduled_tool_is_skipped_when_its_app_is_not_active_here(monkeypatch) -> None:
    _tool(monkeypatch, schedule={"cron": "0 2 * * *", "timezone": "UTC"})
    _patch_schedule_env(monkeypatch, app=None)
    assert await app_schedules.enqueue_due_app_schedules(datetime(2031, 3, 5, 2, 0, 10, tzinfo=timezone.utc)) == 0


@pytest.mark.asyncio
async def test_a_scheduled_tool_is_skipped_without_an_installing_person(monkeypatch) -> None:
    _tool(monkeypatch, schedule={"cron": "0 2 * * *", "timezone": "UTC"})
    _patch_schedule_env(monkeypatch, principal="")
    assert await app_schedules.enqueue_due_app_schedules(datetime(2031, 3, 6, 2, 0, 10, tzinfo=timezone.utc)) == 0


@pytest.mark.asyncio
async def test_a_tool_without_a_schedule_is_never_queued(monkeypatch) -> None:
    _tool(monkeypatch)
    _patch_schedule_env(monkeypatch)
    assert await app_schedules.enqueue_due_app_schedules(datetime(2031, 3, 7, 2, 0, 10, tzinfo=timezone.utc)) == 0
