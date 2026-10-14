# ADR 014 — Background jobs for Apps

**Status:** Proposed boundary. Implemented on `codex/app-background-jobs`; not yet qualified beyond the focused tests named below.

## Decision

An App tool may queue one of its own tools as a durable background job through `ToolContext.enqueue_job`. The job is an ordinary work item of kind `app_tool`, so it uses the existing lease, heartbeat, retry, dead-letter and restart-recovery paths. Core runs it as the person who queued it, in the workspace where it was queued, through the same `ToolContext` facade and permission checks as any tool call. Core never lets the tool choose a person or workspace.

- **Queueing.** Only the caller's own tools can be queued, and the work item is bound to the App's active definition at enqueue time. The idempotency key is scoped to App and tool.
- **Running.** Before the tool starts, and on every retry, Core runs the same pre-effect gate as other work kinds: lease, deadline, cancel request, and that the App's definition is still the one the job was queued against. The tool is re-resolved from the workspace registry and must still belong to the queuing App.
- **Progress.** `report_progress` writes a small bounded `progress` field on the work item, accepted only from the worker that holds the lease. Work item status and Background tasks show the App's label and progress.
- **Human authority.** Enqueue and every dispatch attempt use the direct-call membership and privileged-tool authorization gate. Job status uses the existing work observer authorization in addition to App and workspace containment. Revocation or demotion is read from current facts, including on retries.
- **Results.** `ok: false` fails the job without retry unless the tool adds `retry: true`. A raised exception is retried with backoff and then dead-lettered. A tool may return `object_refs`; entry references (at most 20) are recorded in the result.
- **Scheduling.** A tool may declare `schedule` (cron, optional timezone). The scheduler queues each due slot once, as the App's installing person, with the slot in the idempotency key.
- **Workspace resolution.** `ToolContext` resolves the bundle's own App in the context's workspace when it is installed there, falling back to the caller's personal workspace. This amends ADR 008's wording: a tool still cannot choose another tenant.

## Consequences

- **Worker posture (ADR 005).** App jobs run on their own asyncio loop, started in the same process as the existing work loop. This is not an additional worker process, and the supported `WORKERS` baseline is unchanged. Leases and compare-and-set updates keep two loops or processes from running one item twice, but cross-process delivery is not newly qualified by this decision.
- **At-least-once delivery.** A restart can run a step twice. Jobs must save their place on a record and make each step safe to repeat; the contract says so.
- **Boundary.** No new hook point (I-HOOK-01 is unchanged), no App identity in Core (I-SUBSTRATE-01, I-EXT-01: the status region and worker know only generic fields), and every read and write inside a job is still checked as the queuing person (I-ROLE-01/02). Durable progress and recovery follow the same pattern as I-MIG-02.
- **Not a transaction.** A job is one worker's sequential run. It does not hold a transaction across steps.

See the [how-to](../../platform/background-app-jobs.md) and the [extension contract](../../platform/extension-contract-v1.md#background-app-jobs). Tests: `backend/tests/test_app_jobs.py`, `backend/tests/test_tool_context_own_bundle_workspace.py`.
