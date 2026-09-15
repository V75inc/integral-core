"""Multi-op staging executors RESUME on re-approval instead of replaying.

A batch / bulk token that fails part-way stays ``blessed`` with a
``last_error`` and the card invites re-approval. Before the persisted cursor,
that re-approval re-ran the op list from index 0 — so a 5-op scaffold that
failed at op 4 created the app and three tracks a SECOND time. These tests pin
the cursor, the reference re-seeding a resumed batch needs, and the
already-deleted tolerance a resumed bulk delete needs.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.agentive import staging, staging_executors
from app.services.mutation_provenance import (
    bind_mutation_provenance,
    reset_mutation_provenance,
)


@pytest.fixture(autouse=True)
def _clean_staging():
    """Reset the in-memory staging store around each test."""
    staging._reset_for_tests()
    yield
    staging._reset_for_tests()


@pytest.fixture(autouse=True)
def _no_durable_store(monkeypatch):
    """Progress round-trips through the in-memory token, not a live DB."""

    async def _noop(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(staging.staging_store, "persist", _noop)
    monkeypatch.setattr(staging.staging_store, "remove", _noop)


async def _blessed_token(kind: str, payload: Dict[str, Any]) -> str:
    sc = await staging.create_staged_change(
        user_id="u1",
        session_id="s1",
        kind=kind,
        summary=f"{kind} card",
        diff_human="- change",
        diff_machine={"op": kind},
        payload=payload,
    )
    await staging.bless_token(user_id="u1", token=sc.token)
    return sc.token


class _Provenance:
    """Bind a staging token as mutation provenance, as ``bless_and_execute`` does."""

    def __init__(self, token: str) -> None:
        self._token = token
        self._reset: Any = None

    def __enter__(self) -> "_Provenance":
        _, self._reset = bind_mutation_provenance(staging_token=self._token)
        return self

    def __exit__(self, *_exc: Any) -> None:
        reset_mutation_provenance(self._reset)


# ---------------------------------------------------------------------------
# batch
# ---------------------------------------------------------------------------

_OPS = [
    {"kind": "create_app", "payload": {"name": "CRM"}},
    {"kind": "create_track", "payload": {"title": "Contacts", "app_id": "{{app.id}}"}},
    {"kind": "create_entry", "payload": {"track_id": "{{track.id}}", "title": "Ada"}},
]


@pytest.mark.asyncio
async def test_batch_reapproval_resumes_and_does_not_replay(monkeypatch):
    """A batch that failed at op 3 re-runs ONLY op 3 on the next approval."""
    calls: List[str] = []
    fail_third = {"on": True}

    async def _fake_dispatch(*, user_id: str, kind: str, payload: Dict[str, Any]):
        calls.append(kind)
        if kind == "create_entry" and fail_third["on"]:
            return {"error": True, "message": "denied"}
        if kind == "create_app":
            return {"app": {"id": "n.App.A1"}}
        if kind == "create_track":
            return {"track": {"id": "n.Track.T1"}}
        return {"entry": {"id": "n.Entry.E1"}}

    monkeypatch.setattr(staging_executors, "dispatch", _fake_dispatch)
    token = await _blessed_token("batch", {"operations": _OPS})

    with _Provenance(token):
        first = await staging_executors._x_batch("u1", {"operations": _OPS})
    assert first["error"] is True
    assert first["completed"] == 2 and first["total"] == 3
    assert calls == ["create_app", "create_track", "create_entry"]

    sc = await staging.get_token(token)
    assert sc is not None and sc.progress == {
        "completed": 2,
        "results": sc.progress["results"],
    }
    assert len(sc.progress["results"]) == 2

    # Re-approval: the two completed ops must NOT run again.
    calls.clear()
    fail_third["on"] = False
    with _Provenance(token):
        second = await staging_executors._x_batch("u1", {"operations": _OPS})
    assert second.get("batched") is True
    assert second["completed"] == 3
    assert calls == ["create_entry"], "already-applied ops must not be replayed"


@pytest.mark.asyncio
async def test_batch_resume_reseeds_intra_batch_refs(monkeypatch):
    """``{{app.id}}`` / ``{{track.id}}`` still resolve after a resume."""
    seen: List[Dict[str, Any]] = []
    fail = {"on": True}

    async def _fake_dispatch(*, user_id: str, kind: str, payload: Dict[str, Any]):
        seen.append({"kind": kind, "payload": payload})
        if kind == "create_entry" and fail["on"]:
            return {"error": True, "message": "denied"}
        if kind == "create_app":
            return {"app": {"id": "n.App.A1", "name": "CRM"}}
        if kind == "create_track":
            return {"track": {"id": "n.Track.T1", "title": "Contacts"}}
        return {"entry": {"id": "n.Entry.E1"}}

    monkeypatch.setattr(staging_executors, "dispatch", _fake_dispatch)
    token = await _blessed_token("batch", {"operations": _OPS})

    with _Provenance(token):
        await staging_executors._x_batch("u1", {"operations": _OPS})
    fail["on"] = False
    seen.clear()
    with _Provenance(token):
        await staging_executors._x_batch("u1", {"operations": _OPS})

    assert len(seen) == 1
    # The id came from the FIRST run's recorded result, not from this run.
    assert seen[0]["payload"]["track_id"] == "n.Track.T1"


@pytest.mark.asyncio
async def test_batch_without_provenance_still_replays_whole_list(monkeypatch):
    """No bound token (direct call / legacy path) → today's behaviour, no crash."""
    calls: List[str] = []

    async def _fake_dispatch(*, user_id: str, kind: str, payload: Dict[str, Any]):
        calls.append(kind)
        return {"ok": True}

    monkeypatch.setattr(staging_executors, "dispatch", _fake_dispatch)
    res = await staging_executors._x_batch("u1", {"operations": _OPS})
    assert res["completed"] == 3
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_nested_bulk_op_does_not_read_the_batch_cursor(monkeypatch):
    """A bulk sub-op inside a batch must not resume off the batch's cursor."""
    token = await _blessed_token("batch", {"operations": []})
    await staging.record_execute_progress(
        token=token, progress={"completed": 2, "results": [{}, {}]}
    )
    deleted: List[str] = []

    async def _fake_delete(user_id: str, payload: Dict[str, Any]):
        deleted.append(payload["entry_id"])
        return {"ok": True}

    monkeypatch.setattr(staging_executors, "_x_delete_entry", _fake_delete)
    with _Provenance(token):
        res = await staging_executors._x_bulk_delete_entries(
            "u1", {"entry_ids": ["e1", "e2", "e3"]}
        )
    assert res["deleted"] == 3
    assert deleted == ["e1", "e2", "e3"]


# ---------------------------------------------------------------------------
# bulk update / delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_update_resumes_at_the_failed_entry(monkeypatch):
    """Re-approving a half-applied bulk update re-patches nothing."""
    touched: List[str] = []
    fail = {"on": True}

    async def _fake_update(user_id: str, payload: Dict[str, Any]):
        touched.append(payload["entry_id"])
        if payload["entry_id"] == "e3" and fail["on"]:
            return {"error": True, "message": "denied"}
        return {"ok": True}

    monkeypatch.setattr(staging_executors, "_x_update_entry", _fake_update)
    ids = ["e1", "e2", "e3", "e4"]
    token = await _blessed_token(
        "bulk_update_entries", {"entry_ids": ids, "updates": {"status": "done"}}
    )

    with _Provenance(token):
        first = await staging_executors._x_bulk_update_entries(
            "u1", {"entry_ids": ids, "updates": {"status": "done"}}
        )
    assert first["error"] is True and first["updated"] == 2
    assert touched == ["e1", "e2", "e3"]

    touched.clear()
    fail["on"] = False
    with _Provenance(token):
        second = await staging_executors._x_bulk_update_entries(
            "u1", {"entry_ids": ids, "updates": {"status": "done"}}
        )
    assert second == {"updated": 4, "total": 4}
    assert touched == ["e3", "e4"]


@pytest.mark.asyncio
async def test_bulk_delete_resume_tolerates_already_deleted(monkeypatch):
    """A resumed bulk delete treats a 404 as success and keeps going."""
    seen: List[str] = []
    fail = {"on": True}

    async def _fake_delete(user_id: str, payload: Dict[str, Any]):
        eid = payload["entry_id"]
        seen.append(eid)
        if eid == "e2" and fail["on"]:
            return {"error": True, "message": "boom"}
        if eid == "e3":
            return {
                "error": True,
                "status_code": 404,
                "error_code": "resource_not_found",
                "message": "Entry not found",
            }
        return {"ok": True}

    monkeypatch.setattr(staging_executors, "_x_delete_entry", _fake_delete)
    ids = ["e1", "e2", "e3", "e4"]
    token = await _blessed_token("bulk_delete_entries", {"entry_ids": ids})

    with _Provenance(token):
        first = await staging_executors._x_bulk_delete_entries("u1", {"entry_ids": ids})
    assert first["error"] is True and first["deleted"] == 1

    seen.clear()
    fail["on"] = False
    with _Provenance(token):
        second = await staging_executors._x_bulk_delete_entries(
            "u1", {"entry_ids": ids}
        )
    # Resumes at e2, and the already-gone e3 does not stop the run.
    assert second == {"deleted": 4, "total": 4}
    assert seen == ["e2", "e3", "e4"]


def test_is_already_gone_classification():
    """Only not-found shaped failures count as an idempotent no-op."""
    gone = staging_executors._is_already_gone
    assert gone({"status_code": 404}) is True
    assert gone({"error_code": "ResourceNotFoundError"}) is True
    assert gone({"message": "Entry not found"}) is True
    assert gone({"status_code": 403, "message": "forbidden"}) is False


@pytest.mark.asyncio
async def test_progress_survives_the_durable_round_trip():
    """``progress`` is on the durable record, so a restart does not lose it."""
    from app.agentive import staging_store

    sc = await staging.create_staged_change(
        user_id="u1",
        session_id="s1",
        kind="batch",
        summary="batch card",
        diff_human="-",
        diff_machine={},
        payload={"operations": []},
    )
    sc.progress = {"completed": 2, "results": [{"kind": "create_app"}]}
    fields = staging_store._sc_to_fields(sc)
    assert fields["progress"] == sc.progress
    rec = staging_store.StagedChangeRecord(**fields)
    restored = staging_store._record_to_sc(rec)
    assert restored is not None
    assert restored.progress == sc.progress
