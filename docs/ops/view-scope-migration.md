# Saved-View scope migration

This migration adds the canonical `Track —HasView→ View` relationship for
legacy saved Track Views. OperationalModel catalog membership organizes
configuration; it does not establish a unique Track when models are shared.
The runtime withholds saved Views whose named scope is absent or inconsistent.

Run the inventory against a restored copy first, using the repository's pinned
environment and existing database configuration. Preserve the pre-migration
backup and inventory as operator evidence. This procedure has been exercised
in isolated test stores; running it here does not qualify production restore,
independent browser behavior, or the remaining access-control migration.

From `backend/`:

```bash
.venv/bin/python -m scripts.backfill_view_scope_edges > view-scope-before.json
```

The default is read-only. The report includes identifiers, status, structural
reason and, for proven paths, Workspace/Track/model IDs. It excludes View text
and configuration. Review the counts and individual decisions:

| Status | Meaning | Disposition |
|---|---|---|
| `eligible` | One complete catalog/model/Track path agrees with persisted caches and rooted location | May be wired by apply |
| `already_wired` | Existing named scope passes canonical validation | Preserve |
| `model_only` | Template or a View with no saved Track cache | Requires separately authorized model handling; this is not Track access |
| `quarantined` | Missing, ambiguous, malformed or conflicting ownership facts | Withhold; investigate and obtain explicit owner review |

A shared model attached to two Tracks is ambiguous even if the legacy scalar
`track_id` picks one. Do not resolve that case by choosing the first attachment
or by accepting the scalar as authority. The migration has no automatic repair
for quarantined rows; owner-reviewed repair and its access-center interface
remain implementation work.

After reviewing the dry run, apply against the configured PostgreSQL store:

```bash
.venv/bin/python -m scripts.backfill_view_scope_edges --apply > view-scope-apply.json
.venv/bin/python -m scripts.backfill_view_scope_edges > view-scope-after.json
```

Apply requires supported graph transactions. Each eligible item is reclassified
under the held Workspace authority unit and View row lock before wiring. A
changed plan, unavailable primary facts or failed post-wire validation aborts
that item and rolls back its scope edge and authority revision. Earlier items
may already have committed; this is not one transaction over the entire
inventory. Stop concurrent topology/configuration maintenance while migrating;
non-cooperating writers are not protected by this per-item protocol.

Compare all three reports. Successfully applied rows become `already_wired`
on the new inventory; ambiguous rows stay withheld. A fresh invocation is
idempotent for valid existing scope edges. Reusing a stale in-memory eligible
item is rejected rather than treated as an accepted replay receipt.

Tests in `test_view_scope_migration.py` cover dry-run nonmutation, ambiguous
shared models, source failure, PostgreSQL apply/reinventory, stale-plan refusal
and rollback after a scope-wire failure. Tests in `test_view_scope_authority.py`
cover exact parent binding, legitimate cross-workspace shares, foreign active
scope, current grant removal, corrupted anchors, current raw configuration and
staging refusal before the executor runs. These are narrow migration/runtime
receipts; complete platform recovery and release fencing remain open.
