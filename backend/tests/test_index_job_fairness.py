"""One workspace's due jobs cannot hide the next workspace in a delivery slice."""

import asyncio
import copy

import pytest

from app.services.retrieval.indexing import (
    ENTITY,
    SCHEDULE,
    _claim_index_job,
    _select_due_index_jobs,
)


class _Jobs:
    def __init__(self, rows):
        self.rows = list(rows)
        self.saved = {}

    async def get(self, _collection, identity):
        return self.saved.get(identity)

    async def save(self, _collection, record):
        self.saved[record["id"]] = record
        return record

    async def find(self, _collection, query, sort=None, limit=None):
        assert query["entity"] == ENTITY
        after = (query.get("context.workspace_id") or {}).get("$gt", "")
        matched = []
        for row in self.rows:
            context = row["context"]
            if after and context["workspace_id"] <= after:
                continue
            pending = context["status"] == "pending" and context.get("due", 0) <= 100
            expired = (
                context["status"] == "working" and context.get("lease_until", 0) <= 100
            )
            if pending or expired:
                matched.append(row)
        matched.sort(
            key=lambda row: (
                row["context"]["workspace_id"],
                row["context"].get("due", 0),
                row["id"],
            )
        )
        if limit is not None:
            matched = matched[:limit]
        return matched


def _job(workspace, number, *, status="pending", due=1):
    return {
        "id": f"job-{workspace}-{number}",
        "entity": ENTITY,
        "context": {
            "workspace_id": workspace,
            "status": status,
            "due": due,
            "lease_until": due,
            "generation": f"g-{workspace}-{number}",
        },
    }


@pytest.mark.asyncio
async def test_saturated_workspace_admits_the_next_due_workspace():
    """A full page from one workspace still reserves turns for the next one."""
    rows = [_job("alpha", n) for n in range(20)] + [_job("beta", 1)]
    database = _Jobs(rows)

    selected = await _select_due_index_jobs(database, 100, limit=4)

    assert [row["context"]["workspace_id"] for row in selected] == [
        "alpha",
        "beta",
        "alpha",
        "alpha",
    ]
    assert database.saved[SCHEDULE]["context"]["after_workspace"] == "alpha"


@pytest.mark.asyncio
async def test_two_saturated_workspaces_split_the_slice():
    """Due work in two workspaces shares one delivery slice evenly."""
    database = _Jobs(
        [_job("alpha", n) for n in range(20)] + [_job("beta", n) for n in range(20)]
    )

    selected = await _select_due_index_jobs(database, 100, limit=4)

    assert [row["context"]["workspace_id"] for row in selected] == [
        "alpha",
        "beta",
        "alpha",
        "beta",
    ]


@pytest.mark.asyncio
async def test_cursor_wrap_fills_unused_slots_from_earlier_workspace():
    """A short page past the cursor still uses the rest of the slice."""
    database = _Jobs([_job("alpha", n) for n in range(20)] + [_job("beta", 1)])
    database.saved[SCHEDULE] = {
        "id": SCHEDULE,
        "entity": "EntryIndexSchedule",
        "context": {"after_workspace": "alpha"},
    }

    selected = await _select_due_index_jobs(database, 100, limit=4)

    assert [row["context"]["workspace_id"] for row in selected] == [
        "beta",
        "alpha",
        "alpha",
        "alpha",
    ]
    assert len({row["id"] for row in selected}) == 4


class _Leases:
    def __init__(self, row):
        self.row = row
        self.lock = asyncio.Lock()

    async def find_one_and_update(self, _collection, query, update):
        async with self.lock:
            context = self.row["context"]
            if self.row["id"] != query["id"]:
                return None
            if context.get("generation") != query.get("context.generation"):
                return None
            if context.get("status") != query.get("context.status"):
                return None
            if (
                "context.lease" in query
                and context.get("lease") != query["context.lease"]
            ):
                return None
            for key, value in update["$set"].items():
                context[key.split(".", 1)[1]] = value
            return self.row


@pytest.mark.asyncio
async def test_one_lease_wins_when_two_workers_claim():
    """Compare-and-set lets one worker claim a job the other still sees as pending."""
    database = _Leases(_job("alpha", 1))
    snapshot = copy.deepcopy(database.row)

    first, second = await asyncio.gather(
        _claim_index_job(database, snapshot),
        _claim_index_job(database, copy.deepcopy(snapshot)),
    )

    leases = [lease for lease in (first, second) if lease]
    assert leases == [database.row["context"]["lease"]]


@pytest.mark.asyncio
async def test_tenant_job_quota_caps_each_workspace(monkeypatch):
    """One workspace cannot take an unlimited share of a delivery cycle."""
    monkeypatch.setenv("INDEX_TENANT_JOB_QUOTA", "2")
    crowded = _Jobs(
        [_job("alpha", n) for n in range(20)] + [_job("beta", n) for n in range(20)]
    )

    selected = await _select_due_index_jobs(crowded, 100, limit=16)

    assert [row["context"]["workspace_id"] for row in selected].count("alpha") == 2
    assert [row["context"]["workspace_id"] for row in selected].count("beta") == 2

    alone = _Jobs([_job("alpha", n) for n in range(20)])
    selected = await _select_due_index_jobs(alone, 100, limit=16)
    assert [row["context"]["workspace_id"] for row in selected] == ["alpha", "alpha"]


@pytest.mark.asyncio
async def test_only_due_workspace_keeps_the_slice():
    """A lone workspace with due work can use the whole delivery slice."""
    database = _Jobs([_job("alpha", n) for n in range(20)])

    selected = await _select_due_index_jobs(database, 100, limit=4)

    assert [row["context"]["workspace_id"] for row in selected] == ["alpha"] * 4


@pytest.mark.asyncio
async def test_cursor_reaches_a_workspace_behind_a_saturated_page():
    """The ring wraps when the saturated workspace is the last one past the cursor."""
    database = _Jobs([_job("alpha", 1)] + [_job("zulu", n) for n in range(30)])
    database.saved[SCHEDULE] = {
        "id": SCHEDULE,
        "entity": "EntryIndexSchedule",
        "context": {"after_workspace": "mid"},
    }

    selected = await _select_due_index_jobs(database, 100, limit=4)

    assert {row["context"]["workspace_id"] for row in selected} == {"alpha", "zulu"}


@pytest.mark.asyncio
async def test_future_retries_do_not_take_a_turn():
    """A retry that is not due yet does not count as the next workspace."""
    database = _Jobs([_job("alpha", n) for n in range(20)] + [_job("beta", 1, due=500)])

    selected = await _select_due_index_jobs(database, 100, limit=4)

    assert {row["context"]["workspace_id"] for row in selected} == {"alpha"}
