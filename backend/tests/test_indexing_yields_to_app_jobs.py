"""The indexer steps aside for a running App job, but never for more than a minute."""

import pytest

from app.services.retrieval import indexing


@pytest.mark.asyncio
async def test_yields_while_an_app_job_runs_then_stops_yielding(monkeypatch):
    async def running():
        return True

    monkeypatch.setattr(indexing, "_app_job_running", running)
    assert await indexing._yield_to_app_job(0) is True
    assert (
        await indexing._yield_to_app_job(indexing._MAX_TICKS_YIELDED_TO_APP_JOBS - 1)
        is True
    )
    assert (
        await indexing._yield_to_app_job(indexing._MAX_TICKS_YIELDED_TO_APP_JOBS)
        is False
    )


@pytest.mark.asyncio
async def test_does_not_yield_when_nothing_runs_or_the_check_fails(monkeypatch):
    async def idle():
        return False

    async def broken():
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(indexing, "_app_job_running", idle)
    assert await indexing._yield_to_app_job(0) is False
    monkeypatch.setattr(indexing, "_app_job_running", broken)
    assert await indexing._yield_to_app_job(0) is False
