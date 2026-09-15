"""Phase 5 Plan 05-02 — migrations subpackage.

MIG-02 + MIG-03 — async migration runner + per-Entry tracker + CP rollup +
no-migration-path reject gate. Extends
``backend/app/services/content_profile_migrations.py`` (the 9-op declarative
runner) with the publish-path automation surface. Does NOT replace the
existing runner — locked decision #2 (extension, not greenfield).

Public surface:
  - ``run_migration_async`` — public entry point spawned by
    ``content_profile_atomic_swap.publish_draft`` immediately after the
    atomic swap. Marks each affected Entry ``migration_status='pending'``
    synchronously (before HTTP return), spawns the per-Entry async runner.
  - ``_async_migration_runner`` — the asyncio.create_task body. Walks each
    Entry, marks ``running``/``complete``/``failed``, emits a single
    ``migration.run`` ChangeEvent on completion. Underscored because it is
    spawned, never awaited inline by callers outside this package.
  - ``rollup_cp_status`` — ContentProfile.migration_status rollup over per-Entry
    statuses (``in_progress`` / ``complete`` / ``failed``).
  - ``detect_unhandled_breaks`` — cross-references the manifest-diff impact
    against the manifest's declared ``migrations[].ops[]`` list. Returns the
    list of would-break entry_types that have no declared migration op.

Locked decision §A5 (RESEARCH Pitfall 2): orphaned ``pending`` / ``running``
Entry rows on process restart are a documented v1 limitation. A startup-hook
scan + manual retry endpoint is future work (out-of-scope for this phase).
The limitation is recorded in ``docs/INVARIANTS.md`` (I-MIG-02).
"""

from .reject_gate import detect_unhandled_breaks
from .rollup import rollup_cp_status
from .runner import _async_migration_runner, run_migration_async

__all__ = [
    "run_migration_async",
    "_async_migration_runner",
    "rollup_cp_status",
    "detect_unhandled_breaks",
]
