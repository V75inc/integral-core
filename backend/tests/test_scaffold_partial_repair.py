"""Partial repairs preserve approved identity, successful cursors and atomic storage."""

import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.agentive import staging, staging_store
from app.services.design_blueprint import validate_blueprint
from app.services.scaffold_repair import repaired_operations

pytestmark = pytest.mark.smoke


def blueprint():
    return {
        "app": {"id": "app", "name": "Task Tracking"},
        "tracks": [
            {
                "id": "tasks",
                "name": "Tasks",
                "entry_types": [
                    {
                        "id": "task",
                        "name": "Task",
                        "fields": [
                            {
                                "id": "status",
                                "key": "status",
                                "name": "Status",
                                "type": "select",
                                "required": True,
                                "options": ["To do", "Done"],
                            }
                        ],
                    }
                ],
            }
        ],
        "seeds": [
            {"id": "sample", "track": "tasks", "title": "Due today", "fields": {}}
        ],
    }


@pytest.mark.parametrize("fields", [{}, {"status": " "}, {"status": "Unknown"}])
def test_missing_or_invalid_required_seed_rejected_before_proposal(fields):
    raw = blueprint()
    raw["seeds"][0]["fields"] = fields
    canonical, error = validate_blueprint(raw)
    assert canonical is None
    assert "cannot be saved" in error


def test_valid_required_seed_accepted():
    raw = blueprint()
    raw["seeds"][0]["fields"] = {"status": "To do"}
    assert validate_blueprint(raw)[1] is None


def partial():
    operations = [
        {"kind": "create_app", "payload": {"name": "Task Tracking"}},
        {
            "kind": "create_entry",
            "payload": {
                "track_id": "{{track.id:Tasks}}",
                "title": "Due today",
                "fields": {},
            },
            "diff_machine": {"fields": {}},
        },
        {
            "kind": "schedule_task",
            "payload": {"cron": "0 9 * * *", "timezone": "America/Guyana"},
        },
    ]
    return SimpleNamespace(
        payload={"operations": operations},
        progress={
            "completed": 1,
            "results": [{"kind": "create_app", "result": {"id": "existing-app"}}],
        },
    )


def test_repair_changes_only_pending_fields_preserving_schedule_and_prefix():
    old = blueprint()
    revised = copy.deepcopy(old)
    revised["seeds"][0]["fields"] = {"status": "To do"}
    staged = partial()
    updated = repaired_operations(old, revised, staged)
    assert updated[0] == staged.payload["operations"][0]
    assert updated[-1] == staged.payload["operations"][-1]
    assert updated[1]["payload"]["fields"] == {"status": "To do"}
    assert staged.payload["operations"][1]["payload"]["fields"] == {}


@pytest.mark.parametrize("change", ["app", "sample", "completed"])
def test_repair_refuses_structural_or_completed_changes(change):
    old, staged = blueprint(), partial()
    revised = copy.deepcopy(old)
    revised["seeds"][0]["fields"] = {"status": "To do"}
    if change == "app":
        revised["app"]["name"] = "Duplicate"
    elif change == "sample":
        revised["seeds"][0]["title"] = "Different"
    else:
        staged.progress["completed"] = 2
    with pytest.raises(ValueError):
        repaired_operations(old, revised, staged)


@pytest.mark.postgres
@pytest.mark.asyncio
@pytest.mark.parametrize("fail_binding", [False, True])
async def test_replacement_cursor_survives_restart_or_rolls_back(
    fail_binding, monkeypatch
):
    now = datetime.now(timezone.utc)
    fixture = partial()
    original = staging.StagedChange(
        token=f"repair-{fail_binding}",
        user_id="repair-user",
        session_id="repair-session",
        workspace_id="repair-workspace",
        kind="batch",
        summary="Task Tracking",
        diff_human="Task Tracking",
        diff_machine={},
        payload=fixture.payload,
        progress=fixture.progress,
        state="blessed",
        created_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    await staging_store.persist(original)
    async with staging._lock:
        staging._tokens[original.token] = original
    revised = copy.deepcopy(fixture.payload["operations"])
    revised[1]["payload"]["fields"] = {"status": "To do"}
    revised[1]["diff_machine"]["fields"] = {"status": "To do"}
    bound = []

    async def bind(token):
        bound.append(token)
        if fail_binding:
            raise RuntimeError("Simulated conversation save failure")

    try:
        if fail_binding:
            with pytest.raises(RuntimeError, match="Simulated"):
                await staging.replace_partial_batch(
                    original=original,
                    operations=revised,
                    user_id=original.user_id,
                    session_id=original.session_id,
                    workspace_id=original.workspace_id,
                    bind_revision=bind,
                )
            assert original.state == "blessed"
            assert await staging_store.load(bound[0]) is None
            loaded = await staging_store.load(original.token)
            assert loaded.payload == original.payload
        else:
            replacement = await staging.replace_partial_batch(
                original=original,
                operations=revised,
                user_id=original.user_id,
                session_id=original.session_id,
                workspace_id=original.workspace_id,
                bind_revision=bind,
            )
            assert original.state == "revoked"
            assert await staging_store.load(original.token) is None
            async with staging._lock:
                staging._tokens.clear()
            loaded = await staging.get_token(replacement.token)
            assert loaded.progress == original.progress
            assert loaded.payload["operations"] == revised
            assert loaded.payload["supersedes_token"] == original.token
            from app.agentive import staging_executors

            calls = []

            async def execute(*, user_id, kind, payload):
                calls.append((kind, payload))
                return {"id": f"new-{kind}"}

            monkeypatch.setattr(
                staging_executors, "_active_staging_token", lambda: loaded.token
            )
            monkeypatch.setattr(staging_executors, "dispatch", execute)
            result = await staging_executors._x_batch(original.user_id, loaded.payload)
            assert result["completed"] == 3
            assert [kind for kind, _ in calls] == ["create_entry", "schedule_task"]
            assert calls[0][1]["fields"] == {"status": "To do"}
            assert calls[1][1]["timezone"] == "America/Guyana"
            assert result["results"][0] == original.progress["results"][0]
    finally:
        for token in [original.token, *bound]:
            await staging_store.remove(token)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid", ["owner", "workspace", "session", "busy", "expired", "cursor", "prefix"]
)
async def test_replacement_rejects_foreign_busy_or_changed_work(invalid):
    now = datetime.now(timezone.utc)
    fixture = partial()
    original = staging.StagedChange(
        token=f"guard-{invalid}",
        user_id="guard-user",
        session_id="guard-session",
        workspace_id="guard-workspace",
        kind="batch",
        summary="Task Tracking",
        diff_human="Task Tracking",
        diff_machine={},
        payload=fixture.payload,
        progress=fixture.progress,
        state="blessed",
        created_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    operations = copy.deepcopy(fixture.payload["operations"])
    if invalid == "busy":
        original.executing = True
    elif invalid == "expired":
        original.expires_at = now - timedelta(seconds=1)
    elif invalid == "cursor":
        original.progress["results"] = []
    elif invalid == "prefix":
        operations[0]["payload"]["name"] = "Duplicate app"
    async with staging._lock:
        staging._tokens[original.token] = original

    async def bind(_token):
        pytest.fail("Invalid repair must not bind a new token")

    try:
        with pytest.raises(staging.StagingError):
            await staging.replace_partial_batch(
                original=original,
                operations=operations,
                user_id="foreign" if invalid == "owner" else original.user_id,
                session_id="foreign" if invalid == "session" else original.session_id,
                workspace_id=(
                    "foreign" if invalid == "workspace" else original.workspace_id
                ),
                bind_revision=bind,
            )
        assert original.state == "blessed"
    finally:
        async with staging._lock:
            staging._tokens.pop(original.token, None)
