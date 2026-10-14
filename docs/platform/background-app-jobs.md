# Background jobs for Apps

An App tool can hand long work to Core and return at once. Core stores the work durably, runs the tool later on a worker, retries it on a transient failure, picks it up again after a restart, and shows its progress. Use it for anything that can outlast an HTTP request or a page: a batch import, a month-end run, a nightly clean-up.

This guide is the how-to. The [extension contract](extension-contract-v1.md#background-app-jobs) is the reference, and [ADR 014](../backend/adr/014-app-background-jobs.md) records the boundary.

## What you get

- `ctx.enqueue_job(tool_key, payload, *, idempotency_key, label="")` queues one of **this App's own tools**. It returns `{job_id, status}` immediately.
- `ctx.report_progress(done=None, total=None, label="")` is called from inside the job to record progress.
- `ctx.job_status(job_id)` reads a job this App queued in this workspace.
- A tool may declare `schedule` (a cron string or `{cron, timezone}`) and Core queues it at each due slot.
- Background tasks lists the job with your label and progress. The `region-system/processing-status` region can show the same on a record page.

## A minimal example

Declare the tool that does the work, and keep the job entry point out of the assistant's reach:

```yaml
tools:
  - key: run_import_job
    handler_ref: tools.import:run_import_job
    agent_callable: false          # the job entry point is queued by your own tool, not called by the assistant
    parameters_schema:
      type: object
      required: [entry_id]
      properties:
        entry_id: {type: string}
    output_schema: {type: object}
```

Queue it from the tool that handles the user's click. Fall back when the running Core has no job support:

```python
import uuid

async def start_import(input, ctx):
    entry_id = input["entry_id"]
    await ctx.update_entry_fields(entry_id, {"status": "importing", "cursor": 0})
    enqueue = getattr(ctx, "enqueue_job", None)
    if enqueue is None:
        return {"ok": True, "status": "importing", "driver": "page"}   # the caller runs the steps
    out = await enqueue(
        "run_import_job",
        {"entry_id": entry_id},
        idempotency_key=f"{entry_id}:{uuid.uuid4().hex}",
        label="Import",
    )
    return {"ok": True, "status": "importing", "job_id": out["job_id"]}
```

The job does one short step at a time, saves its place on the record, and reports progress:

```python
async def run_import_job(input, ctx):
    entry_id = input["entry_id"]
    while True:
        state = await import_next_chunk(ctx, entry_id)          # reads the cursor, does a bounded chunk, saves it
        await ctx.report_progress(done=state["done"], total=state["total"], label=f'{state["done"]} of {state["total"]}')
        if state["finished"]:
            return {"ok": True, "object_refs": [{"kind": "entry", "id": entry_id}]}
        if state.get("error"):
            return {"ok": False, "reason": state["error"]}
```

## The contract

| Rule | Why |
|---|---|
| **Delivery is at least once.** A step can run twice (a restart, a retry). Make every step safe to repeat. | The lease expires if a worker dies; the next worker starts again. |
| **Save your place on the record**, not in memory. | That is what lets a job resume, and lets the page show the same progress. |
| **The job runs as the person who queued it**, in their workspace, with the usual permission checks on every read and write. | Core never lets a tool choose another person or workspace. |
| **Only your own tools can be queued.** | Another App's tool needs `call_app_tool` and its exported-tool rules. |
| **Current workspace access is required to enqueue and run.** Privileged tools require owner or admin authority, checked again on every attempt. | Removing membership, disabling the identity, or demoting an admin prevents subsequent execution, including retries and scheduled jobs. |
| **Job status follows the work observer rules.** The initiator and workspace owners/admins can inspect it while they retain workspace access. | Sharing an App does not expose another member's job progress, failure details or result references. |
| **One idempotency key returns the same job.** Use a unique key for each user action and a stable key where you want "once only". | Prevents a double click queuing the work twice. |
| **Mark job entry points `agent_callable: false`.** | They are internal; the assistant should call your start tool. |
| **Keep the label short** (200 characters) and return at most 20 `object_refs` (`kind: entry`). | These show in Background tasks and the job result. |

Core checks again every time a job starts or is retried: the lease, the deadline, a cancel request, that the tool still belongs to the App that queued it, and that the App is still the version the job was queued against. If the App was upgraded or removed in between, the job fails with `work.definition_stale` and your tool never runs. The user starts the action again.

## How a tool's result is read

| Your tool returns or does | The job becomes |
|---|---|
| `{ok: true, ...}` | `succeeded` (with `app_tool:<key>` and your `object_refs` as `entry:<id>`) |
| `{ok: false, reason: "..."}` | `failed`, not retried; the reason is shown |
| `{ok: false, reason: "...", retry: true}` | retried with backoff |
| raises an exception | retried with backoff, then `dead_letter` after the attempt limit |
| the lease is lost (worker died, restart) | picked up again from the start of the tool |

## Scheduled tools

```yaml
  - key: nightly_cleanup
    handler_ref: tools.cleanup:nightly_cleanup
    agent_callable: false
    schedule: {cron: "0 2 * * *", timezone: "America/Guyana"}
```

Core checks every minute, queues each due slot once (the slot is part of the idempotency key, so two processes cannot run it twice), and runs it as the App's installing person. A slot missed while the service was down is skipped, not replayed.

## Showing progress on a record page

Put the status and progress on the record itself (a status field, a text progress field, an error field), then place `region-system/processing-status` on the record page and name those fields. See [Region system](../operational-models/REGION_SYSTEM.md). An App that needs a bespoke look can use its own extension view instead and read progress through a declared **read-only query**; do not poll a write operation, because each call takes a full write path.

## Testing a job

Call your job tool directly in a test with a fake context that implements `report_progress` and `enqueue_job`, and test the interruption case: run one step, drop the context, start again, and check that nothing is duplicated. Core's own tests for the mechanism are in `backend/tests/test_app_jobs.py`.

## Not for

- Anything that must finish inside the user's click (keep it synchronous).
- Work that cannot be repeated safely.
- Cross-App orchestration. Queue your own tool, and let it call another App's exported tool.
