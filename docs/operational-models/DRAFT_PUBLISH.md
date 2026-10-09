# Draft and publish a model

A draft is a proposed schema/composition change, not current execution authority. Forking attaches the draft to its published model through `HAS_DRAFT_PROFILE`; drafts must remain rooted and are not cataloged as published library artifacts.

Validate the candidate, inspect the diff, and compute existing-record impact. Publishing updates the stable model through the canonical lifecycle and schedules required migration work. Capability authority for installed Apps also depends on the active ApplicationDefinition; do not replace it with a draft pointer.

Unhandled breaking changes are rejected. A separately authorized force path bypasses that rejection but does not migrate existing records. It can leave manual repair work and must be presented accordingly.

Migration progress is a separate result from model publication. While the applicable model is queued or in progress, normal Entry writes are blocked with `migration_in_progress` to prevent mixed-schema mutation. Inspect status and failed-item diagnostics before retry.

Discarding a draft removes the draft through its lifecycle rather than leaving an orphan. Updating a library model does not automatically update every attached instance. See [migrations](MIGRATIONS.md).
