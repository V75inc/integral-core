"""Phase 5 Plan 05-02 — OperationalModel.migration_status rollup.

Rolls per-Entry migration_status values up into a single OperationalModel-level
status. Persists to ``published_cp.migration_status`` and returns the new
status string. Consumed by the async runner at the end of every per-Entry
pass; also exposed for callers that want to recompute without spawning the
runner.

Status semantics (locked decision §A5 / RESEARCH §"Migration tracker"):

  - ``"failed"``       — at least one Entry reports ``migration_status="failed"``
  - ``"in_progress"``  — at least one Entry reports ``"pending"`` or ``"running"``
  - ``"complete"``     — every Entry reports ``"complete"``

Reads ``Entry.migration_status`` via ``getattr`` with a ``"complete"`` default
so this works against Entry rows pre-dating the Plan 05-01 field addition
(idempotent: rows without the field are treated as ``"complete"``).
"""

from typing import List

from app.models.nodes import Entry, OperationalModel


async def rollup_cp_status(
    *,
    published_cp: OperationalModel,
    affected_entries: List[Entry],
) -> str:
    """Compute + persist the CP-level migration_status. Returns the new status."""
    statuses = [
        str(getattr(e, "migration_status", "complete") or "complete")
        for e in affected_entries
    ]
    if any(s == "failed" for s in statuses):
        new_status = "failed"
    elif any(s in {"pending", "running"} for s in statuses):
        new_status = "in_progress"
    else:
        new_status = "complete"
    # Use setattr so this works against OperationalModel rows pre-dating the
    # Plan 05-01 field addition (Pydantic Node allows attribute assignment
    # even when the field isn't formally declared).
    try:
        published_cp.migration_status = new_status
        await published_cp.save()
    except Exception:  # noqa: BLE001 — best-effort persistence
        pass
    return new_status
