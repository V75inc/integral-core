"""Work-kernel lifecycle posture (Task 10)."""

from __future__ import annotations

import pytest

from app.agentive.services import work_lifecycle


@pytest.mark.asyncio
async def test_mongo_rejected_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JVSPATIAL_DB_TYPE", "mongo")
    monkeypatch.setattr(work_lifecycle, "_is_dev_boot", lambda: False)
    with pytest.raises(work_lifecycle.WorkKernelBootError, match="Mongo"):
        await work_lifecycle.assert_work_kernel_store_posture()


@pytest.mark.asyncio
async def test_sqlite_allowed_in_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JVSPATIAL_DB_TYPE", "sqlite")
    monkeypatch.setattr(work_lifecycle, "_is_dev_boot", lambda: True)
    await work_lifecycle.assert_work_kernel_store_posture()


@pytest.mark.asyncio
async def test_startup_recovery_runs() -> None:
    report = await work_lifecycle.run_startup_recovery()
    assert report.expired >= 0
    assert report.reclaimed >= 0
