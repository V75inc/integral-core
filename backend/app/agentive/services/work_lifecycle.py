"""Work-kernel boot probes, recovery, and background loops (Task 10)."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, List

log = logging.getLogger(__name__)


class WorkKernelBootError(RuntimeError):
    """Production fail-closed boot failure for the durable work kernel."""


def _is_dev_boot() -> bool:
    from app.config import settings

    return bool(
        settings.DEBUG or os.getenv("PYTEST_CURRENT_TEST") or os.getenv("TESTING")
    )


def _db_type() -> str:
    return (os.environ.get("JVSPATIAL_DB_TYPE") or "postgres").strip().lower()


async def probe_transaction_capabilities(db: Any = None) -> None:
    """Require public CAS + insert_if_absent on the Postgres transaction handle."""
    from jvspatial.db import get_prime_database

    database = db or get_prime_database()
    # Unwrap ObservableDatabase when present.
    inner = getattr(database, "_db", None) or getattr(database, "db", None) or database
    begin = getattr(inner, "begin_transaction", None)
    if begin is None:
        raise WorkKernelBootError("database missing begin_transaction")
    txn = await begin()
    try:
        for method in ("find_one_and_update", "insert_if_absent", "get"):
            if not hasattr(txn, method):
                raise WorkKernelBootError(
                    f"transaction handle missing public method {method}"
                )
    finally:
        rollback = getattr(inner, "rollback_transaction", None)
        if rollback is not None:
            try:
                await rollback(txn)
            except Exception:  # noqa: BLE001
                pass


async def assert_work_kernel_store_posture() -> None:
    """Fail closed for Mongo / multi-worker non-Postgres in production."""
    db_type = _db_type()
    if db_type == "mongo":
        raise WorkKernelBootError("Mongo is unsupported for the Phase B work kernel")
    if db_type != "postgres" and not _is_dev_boot():
        raise WorkKernelBootError(
            f"production work kernel requires postgres, found {db_type!r}"
        )
    if db_type == "postgres":
        await probe_transaction_capabilities()


async def run_startup_recovery() -> Any:
    from app.agentive.services.work_recovery import run_recovery_pass

    return await run_recovery_pass(reclaim_worker_id="startup-recovery")


async def _recovery_loop(*, idle_sleep: float = 15.0) -> None:
    from app.agentive.services.work_recovery import run_recovery_pass

    while True:
        try:
            await run_recovery_pass(reclaim_worker_id="periodic-recovery")
        except Exception:  # noqa: BLE001
            log.exception("work recovery loop failed")
        await asyncio.sleep(idle_sleep)


async def start_work_kernel_background(
    background_tasks: List[asyncio.Task],
) -> None:
    """Probe store, reconcile once, then spawn worker/recovery/event loops."""
    await assert_work_kernel_store_posture()
    try:
        from app.agentive.services.work_outbox import reconcile_missing_outbox_facts

        await reconcile_missing_outbox_facts()
    except Exception:  # noqa: BLE001
        if not _is_dev_boot():
            raise
        log.warning("work outbox reconcile skipped", exc_info=True)

    try:
        report = await run_startup_recovery()
        log.info(
            "work kernel startup recovery: expired=%s reclaimed=%s "
            "retry_waited=%s terminalized=%s outbox_backfilled=%s",
            report.expired,
            report.reclaimed,
            report.retry_waited,
            report.terminalized,
            report.outbox_backfilled,
        )
    except Exception:  # noqa: BLE001
        if not _is_dev_boot():
            raise
        log.warning("work kernel startup recovery failed", exc_info=True)

    from app.agentive.services import work_events, work_worker

    background_tasks.append(
        asyncio.create_task(
            work_worker.worker_loop(worker_id="work-worker-0"),
            name="work-worker",
        )
    )
    background_tasks.append(asyncio.create_task(_recovery_loop(), name="work-recovery"))
    background_tasks.append(
        asyncio.create_task(
            work_events.event_consumer_loop(), name="work-event-consumer"
        )
    )
    try:
        work_events.register_wake_hint()
    except Exception:  # noqa: BLE001
        log.debug("work event wake hint registration failed", exc_info=True)


__all__ = [
    "WorkKernelBootError",
    "assert_work_kernel_store_posture",
    "probe_transaction_capabilities",
    "run_startup_recovery",
    "start_work_kernel_background",
]
