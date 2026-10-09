# Declarative migrations

Migrations describe supported transformations of existing records when a published model changes. Dispatch comes from one `_OP_HANDLERS` catalog in `operational_model_migrations.py`; workers do not invent a separate recovery implementation.

Supported handlers include rename, default filling, deletion, enum pruning, coercion, adding a defaulted field, EntryType rename, and view-type changes. Inspect the live handler implementation for exact payload shapes. `move_field` has a declared handler boundary but requires manual/pending handling; do not promise an automatic cross-record transfer.

Renames must reconcile declared configuration references, including qualified custom-field paths, without confusing a custom state field with the base Entry status. Track-level operations execute at their declared scope rather than once for every row.

A migration enqueues idempotent work bound to the candidate fingerprint. Durable per-entry tracking exposes progress and failed items. Recovery respects active WorkItems; legacy orphan reconciliation must not race a worker and mark its work failed.

Retry uses the same supported idempotent catalog and targets the appropriate failed work. The migration-status and retry endpoints require current authority. Applicable Entry create/update paths reject writes while migration is active.

Only declarative supported operations are executable in the manifest. Inline Python, eval, and arbitrary migration classes are outside this contract. A forced publish skips automatic migration and leaves existing data for deliberate repair.

Test real affected records, interrupted work, duplicate retry, configuration reference updates, unsupported operations, and write exclusion on the intended store. See [draft/publish](DRAFT_PUBLISH.md).
