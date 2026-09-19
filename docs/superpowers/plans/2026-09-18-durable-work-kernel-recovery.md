# Durable Work Kernel and Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make capability-delegated, scheduled, event-triggered, and approval-resumed work durable, leased, replay-safe, cancellable, and restart-recoverable while leaving reasoning and stream production in jvagent.

**Architecture:** Add I-GRAPH-02 records (`WorkItem`, `WorkOutboxEntry`, `WorkApproval`, `EventTriggerDeclaration`, `ChangeEventTriggerCheckpoint`) around existing `AgentRun`, `RunStep`, `CapabilityBroker`, staging, and `RoutineTask` paths. Propagate one `WorkExecutionContext` through the resident tool path. A bounded worker claims work with public database compare-and-set, invokes only static Core handlers, and records transition facts through atomic Postgres units. Recovery reclaims expired leases and reconciles orphaned runs without reopening terminal authority. Routine and ChangeEvent consumers become idempotent enqueue adapters.

**Tech Stack:** Python 3.10+, Pydantic 2, coordinated jvspatial/jvagent releases beyond 0.0.20, Postgres/asyncpg through jvspatial public APIs, pytest/pytest-asyncio/pytest-xdist, subprocess chaos workers.

**Authority:** `docs/superpowers/specs/2026-09-18-durable-work-kernel-recovery-design.md`

---

## Practical file boundaries

- `backend/app/agentive/work_models.py`: persisted Object declarations only.
- `backend/app/schemas/agentive/work.py`: status/kind/failure/retry/context types only.
- `backend/app/agentive/services/work_items.py`: enqueue, transitions, leases, controls.
- `backend/app/agentive/services/work_outbox.py`: atomic units, delivery, reconciliation.
- `backend/app/agentive/services/work_execution.py`: static handler dispatch + supervision.
- `backend/app/agentive/services/work_recovery.py`: recovery scans.
- `backend/app/agentive/services/work_approvals.py`: approval authority CAS.
- `backend/app/agentive/services/work_worker.py`: loops and lifecycle only.
- `backend/app/agentive/services/work_events.py`: ChangeEvent checkpoint consumer.
- Keep receipts in `execution_runs.py` / `capability_broker.py`.
- Keep schedule CRUD in `routine_tasks.py`; replace only direct execution.
- Keep card presentation in `staging.py` / `staging_store.py`.
- No CQRS aggregates, workflow language, mutable handler registry, duplicate receipt model, or raw DB drivers.

## Invariant contract

Add to `docs/INVARIANTS.md`:

- **I-WORK-01** lease authority — `test_work_item_leases.py` + Postgres contracts.
- **I-WORK-02** effect identity — `test_work_execution.py`.
- **I-WORK-03** atomic work units — `test_work_kernel_postgres.py`.
- **I-WORK-04** approval fail-closed — `test_work_approvals.py` + Postgres contracts.
- **I-WORK-05** recovery monotonicity — `test_work_recovery.py`.
- **I-WORK-06** event checkpoint replay — `test_event_work_items.py`.

Preserve I-GRAPH-01/02, I-APPROVAL-01..03, I-SUBSTRATE-01, I-EXT-01, I-HOOK-01, workspace scope, and ChangeEvent single-emission.

---

### Task 0: Coordinated jvspatial + jvagent transaction/CAS release

**Files:**
- Create/modify jvspatial transaction APIs and tests (sibling repo).
- Create/modify jvagent pin, work-context callback plumbing, and tests (sibling repo).
- Modify: `backend/pyproject.toml`
- Modify: `backend/uv.lock`
- Create: `backend/tests/contract/test_jvspatial_work_txn_api.py`

- [ ] **Step 1: Write failing Integral probe tests**

Assert public Postgres transaction handles expose:

```python
updated = await transaction.find_one_and_update(
    "o",
    expected_state_query,
    {"$set": transition_fields},
)
await transaction.insert_if_absent("o", outbox_document)
```

Also assert Integral rejects Mongo as Phase B production store and refuses missing methods at probe time.

- [ ] **Step 2: Run RED**

```bash
cd backend
TESTING=1 INTEGRAL_TEST_DB=postgres \
  .venv/bin/python -m pytest -q --tb=short \
  tests/contract/test_jvspatial_work_txn_api.py
```

Expected: `PostgresTransaction` lacks `find_one_and_update`.

- [ ] **Step 3: Release the dependency chain**

In jvspatial, add `find_one_and_update` to the public transaction ABC and Postgres implementation, with unit and transaction rollback tests. Publish a release beyond `0.0.20`.

In jvagent, pin that jvspatial release, preserve host-supplied `WorkExecutionContext` through embedded resident tool callbacks, publish a compatible release, and do not require model-visible work fields.

In Integral, pin both releases and regenerate:

```bash
cd backend
uv lock
uv sync --frozen --extra dev --extra test
```

Do not use `PostgresTransaction._connection`, import `asyncpg` in Integral, or weaken production atomicity.

- [ ] **Step 4: Run GREEN**

Rerun the Task 0 command. Expected: probes pass.

- [ ] **Step 5: Commit atomically**

```bash
git add backend/pyproject.toml backend/uv.lock \
  backend/tests/contract/test_jvspatial_work_txn_api.py
git commit -m "chore(deps): pin transactional work-kernel jvspatial"
```

---

### Task 1: Work contracts and deterministic identity

**Files:**
- Create: `backend/app/schemas/agentive/work.py`
- Create: `backend/app/agentive/work_models.py`
- Create: `backend/app/agentive/services/work_items.py`
- Create: `backend/tests/test_work_items.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Write failing schema and identity tests**

Cover unknown kind rejection, retry-policy bounds, deterministic namespaced id, identical-identity reuse, changed-input conflict, and illegal transitions using the design transition table.

Required identity:

```python
canonical = json.dumps(
    [kind, origin, principal_id, workspace_id, idempotency_key],
    separators=(",", ":"),
    sort_keys=False,
)
expected_id = "o.WorkItem." + hashlib.sha256(canonical.encode()).hexdigest()
```

- [ ] **Step 2: Run RED**

```bash
cd backend
.venv/bin/python -m pytest -q --tb=short tests/test_work_items.py
```

Expected: modules missing.

- [ ] **Step 3: Implement typed records**

Define exact literals from the design (`WorkStatus`, `WorkKind`, `FailureClass`, `WorkApprovalStatus`, `RetryPolicy`, `WorkFailure`, `WorkExecutionContext`). Declare `WorkItem`, `WorkOutboxEntry`, `WorkApproval`, `EventTriggerDeclaration`, and `ChangeEventTriggerCheckpoint` Objects with required indexes. Register them in `_ensure_model_indexes`. Production boot fails closed if indexes cannot be ensured.

Enqueue alone is not yet transactional; Task 2 supplies the atomic create+outbox unit.

- [ ] **Step 4: Run GREEN**

Rerun Task 1 command. Expected: all identity/schema tests pass.

- [ ] **Step 5: Commit atomically**

```bash
git add backend/app/schemas/agentive/work.py \
  backend/app/agentive/work_models.py \
  backend/app/agentive/services/work_items.py \
  backend/tests/test_work_items.py backend/app/main.py
git commit -m "feat(agentive): add durable work item contracts"
```

---

### Task 2: Transactional enqueue/outbox primitive

**Files:**
- Create: `backend/app/agentive/services/work_outbox.py`
- Modify: `backend/app/agentive/services/work_items.py`
- Create: `backend/tests/test_work_outbox.py`
- Create: `backend/tests/contract/test_work_kernel_postgres.py`

- [ ] **Step 1: Write failing atomic-unit tests**

Cover:

- create WorkItem + initial outbox fact together;
- transition + outbox fact together;
- duplicate delivery dedupe by `outbox_id`;
- failed consumer retry without losing entry;
- development reconciliation backfill;
- Postgres commit and rollback for create and transition units.

APIs accept an optional public transaction handle.

- [ ] **Step 2: Run RED**

```bash
cd backend
.venv/bin/python -m pytest -q --tb=short tests/test_work_outbox.py
TESTING=1 INTEGRAL_TEST_DB=postgres \
  .venv/bin/python -m pytest -q --tb=short \
  -m "contract and postgres" \
  tests/contract/test_work_kernel_postgres.py
```

Expected: outbox service absent.

- [ ] **Step 3: Implement units**

On Postgres, one public transaction handle performs CAS/create and `insert_if_absent` for the deterministic outbox fact, then commits or rolls back both. On JSON/SQLite, ordered writes under the process lock plus reconciliation. Mongo remains rejected for production.

- [ ] **Step 4: Run GREEN**

Rerun both Task 2 commands. Expected: unit and Postgres atomicity tests pass.

- [ ] **Step 5: Commit atomically**

```bash
git add backend/app/agentive/services/work_outbox.py \
  backend/app/agentive/services/work_items.py \
  backend/tests/test_work_outbox.py \
  backend/tests/contract/test_work_kernel_postgres.py
git commit -m "feat(agentive): add transactional work outbox"
```

---

### Task 3: Lease claim, heartbeat, fence, stale-worker rejection

**Files:**
- Modify: `backend/app/agentive/services/work_items.py`
- Create: `backend/tests/test_work_item_leases.py`
- Modify: `backend/tests/contract/test_work_kernel_postgres.py`

- [ ] **Step 1: Write failing lease tests**

Cover claim, second-claim rejection, heartbeat renewal at `<= lease/3`, stale completion after reclaim, process lock for non-production, and Postgres concurrent claim.

- [ ] **Step 2: Run RED**

```bash
cd backend
.venv/bin/python -m pytest -q --tb=short tests/test_work_item_leases.py
```

Expected: lease APIs missing.

- [ ] **Step 3: Implement lease operations through the outbox primitive**

Expose `claim_due_candidate`, `heartbeat_lease`, `transition_leased`, and `reclaim_expired_lease`. Never mutate a loaded Object and `save()` for lease authority. Heartbeat interval must be `<= lease_seconds / 3`.

- [ ] **Step 4: Run GREEN**

Rerun Task 3 command plus Postgres concurrent-claim tests.

- [ ] **Step 5: Commit atomically**

```bash
git add backend/app/agentive/services/work_items.py \
  backend/tests/test_work_item_leases.py \
  backend/tests/contract/test_work_kernel_postgres.py
git commit -m "feat(agentive): enforce durable work leases"
```

---

### Task 4: Durable retry, deadlines, cancellation, recovery

**Files:**
- Modify: `backend/app/agentive/services/work_items.py`
- Create: `backend/app/agentive/services/work_recovery.py`
- Create: `backend/tests/test_work_controls.py`
- Create: `backend/tests/test_work_recovery.py`

- [ ] **Step 1: Write failing control and recovery tests**

Cover all failure classes, deterministic backoff, deadline checks, queued/waiting cancel, running cancel request, exhaustion to `dead_letter`, and every recovery rule from the design. Run recovery twice and assert the second pass is a no-op.

- [ ] **Step 2: Run RED**

```bash
cd backend
.venv/bin/python -m pytest -q --tb=short \
  tests/test_work_controls.py tests/test_work_recovery.py
```

Expected: controls/recovery missing.

- [ ] **Step 3: Implement controls and recovery**

Use the design formulas and transition table. Attempt increments only on successful claim. Waiting AgentRuns finish as `succeeded` with metadata outcome; WorkItem remains authoritative. Never reopen terminal work.

- [ ] **Step 4: Run GREEN**

Rerun Task 4 command.

- [ ] **Step 5: Commit atomically**

```bash
git add backend/app/agentive/services/work_items.py \
  backend/app/agentive/services/work_recovery.py \
  backend/tests/test_work_controls.py \
  backend/tests/test_work_recovery.py
git commit -m "feat(agentive): add durable work controls and recovery"
```

---

### Task 5: WorkExecutionContext through resident effect path

**Files:**
- Modify: `backend/app/schemas/capability_broker.py`
- Modify: `backend/app/agentive/services/capability_broker.py`
- Modify: `backend/app/agentive/services/capability_adapters.py`
- Modify: `backend/app/agentive/services/execution_runs.py`
- Modify: `backend/app/agentive/tooling/dispatch.py`
- Modify: `backend/app/agentive/tooling/context.py` or current `ToolContext` home
- Modify: connector/MCP proxy modules that perform effects
- Modify: jvagent embed/provider callback path as required by Task 0 pin
- Create: `backend/tests/test_work_execution.py`
- Modify: `backend/tests/test_capability_broker.py`
- Modify: `backend/tests/test_execution_runs.py`

- [ ] **Step 1: Write failing context/effect tests**

Assert deterministic `run_id = workrun:{sha256(work_item_id:attempt)}`, stable `logical_step_key`, effect key `sha256(work_item_id:logical_step_key)`, lease/deadline/cancel checks before adapter call, unsupported adapters fail with `work.non_replayable_effect` before invocation, and provider ordinal conflict fails closed.

- [ ] **Step 2: Run RED**

```bash
cd backend
.venv/bin/python -m pytest -q --tb=short \
  tests/test_work_execution.py \
  tests/test_capability_broker.py \
  tests/test_execution_runs.py
```

Expected: work-aware fields/context propagation missing.

- [ ] **Step 3: Propagate context end-to-end**

Add `work_item_id`/`deadline_at` to `AgentRun` and `work_item_id` to `RunStep`. Thread `WorkExecutionContext` through chat extra data, resident callback, broker, adapters, dispatch, `ToolContext`, App ops, and MCP proxy. Keep `RunStep` as the sole receipt. Existing non-WorkItem surfaces remain valid with empty context.

- [ ] **Step 4: Run GREEN**

Rerun Task 5 command. Expected: all tests pass, including existing four-surface broker behavior.

- [ ] **Step 5: Commit atomically**

```bash
git add backend/app/schemas/capability_broker.py \
  backend/app/agentive/services/capability_broker.py \
  backend/app/agentive/services/capability_adapters.py \
  backend/app/agentive/services/execution_runs.py \
  backend/app/agentive/tooling \
  backend/tests/test_work_execution.py \
  backend/tests/test_capability_broker.py \
  backend/tests/test_execution_runs.py
git commit -m "feat(agentive): propagate durable work execution context"
```

Before committing, inspect the staged tooling/proxy diff and unstage unrelated files.

---

### Task 6: Static worker handlers and supervised provider loop

**Files:**
- Create: `backend/app/agentive/services/work_execution.py`
- Create: `backend/app/agentive/services/work_worker.py`
- Create: `backend/tests/test_work_worker.py`

- [ ] **Step 1: Write failing worker tests**

Assert handled kinds, unknown-kind permanent failure, principal/workspace recheck, broker-only capability effects, supervised heartbeat during a provider call longer than one lease interval, and lease-loss cancellation that blocks later effects/completion.

- [ ] **Step 2: Run RED**

```bash
cd backend
.venv/bin/python -m pytest -q --tb=short tests/test_work_worker.py
```

Expected: worker modules missing.

- [ ] **Step 3: Implement one execution path**

Literal `if/elif` dispatch only. Heartbeat every `<= lease/3` during provider calls under a supervised task group. Do not persist token deltas.

- [ ] **Step 4: Run GREEN**

Rerun Task 6 command.

- [ ] **Step 5: Commit atomically**

```bash
git add backend/app/agentive/services/work_execution.py \
  backend/app/agentive/services/work_worker.py \
  backend/tests/test_work_worker.py
git commit -m "feat(agentive): execute leased work through static handlers"
```

---

### Task 7: Fail-closed durable approvals on the original WorkItem

**Files:**
- Create: `backend/app/agentive/services/work_approvals.py`
- Modify: `backend/app/agentive/staging_store.py`
- Modify: `backend/app/agentive/staging.py`
- Modify: `backend/app/agentive/services/staging_apply.py`
- Modify: `backend/app/services/approval_executor.py`
- Modify: `backend/app/agentive/services/capability_broker.py`
- Create: `backend/tests/test_work_approvals.py`
- Modify: `backend/tests/test_staging_persistence.py`
- Modify: `backend/tests/test_staging_apply_claim.py`
- Modify: `backend/tests/test_staging_terminal_durability.py`
- Modify: `backend/tests/contract/test_work_kernel_postgres.py`

- [ ] **Step 1: Write failing approval tests**

Cover propose fail-closed, atomic unit
`WorkApproval + StagedChangeRecord + running→waiting_for_human + outbox`,
one-decision CAS, approve `waiting_for_human→queued`, reject/expire terminalization, restart after decision, and Postgres concurrent approve races. No child resume WorkItem.

- [ ] **Step 2: Run RED**

```bash
cd backend
.venv/bin/python -m pytest -q --tb=short \
  tests/test_work_approvals.py \
  tests/test_staging_persistence.py \
  tests/test_staging_apply_claim.py \
  tests/test_staging_terminal_durability.py
```

Expected: authority service and atomic units absent.

- [ ] **Step 3: Implement authority**

All APIs accept optional public transaction handles. Compatibility cards without `work_approval_id` keep the existing inline path. Policy `Approval` Nodes remain rooted and linked by scalar id.

- [ ] **Step 4: Run GREEN**

Rerun Task 7 command plus Postgres approval contract tests.

- [ ] **Step 5: Commit atomically**

```bash
git add backend/app/agentive/services/work_approvals.py \
  backend/app/agentive/staging_store.py \
  backend/app/agentive/staging.py \
  backend/app/agentive/services/staging_apply.py \
  backend/app/services/approval_executor.py \
  backend/app/agentive/services/capability_broker.py \
  backend/tests/test_work_approvals.py \
  backend/tests/test_staging_persistence.py \
  backend/tests/test_staging_apply_claim.py \
  backend/tests/test_staging_terminal_durability.py \
  backend/tests/contract/test_work_kernel_postgres.py
git commit -m "feat(agentive): make approvals durable and fail-closed"
```

---

### Task 8: Migrate RoutineTask and App schedules to WorkItems

**Files:**
- Modify: `backend/app/services/routine_task_scheduler.py`
- Modify: `backend/app/agentive/services/routine_tasks.py`
- Modify: `backend/app/agentive/services/work_execution.py`
- Modify: `backend/tests/test_routine_tasks.py`
- Modify: `backend/tests/test_routine_write_scope_fail_closed.py`
- Create: `backend/tests/test_routine_work_items.py`
- Modify: `backend/tests/contract/test_warranty_schedule.py`

- [ ] **Step 1: Write failing routine adapter tests**

Assert due fires enqueue `kind=routine_turn` with
`idempotency_key=routine:{routine_id}:{scheduled_for}`, no direct `_run_agent_turn`, App schedules inherit via `RoutineTask`, and a crash after a real brokered effect receipt does not repeat the effect.

- [ ] **Step 2: Run RED**

```bash
cd backend
.venv/bin/python -m pytest -q --tb=short \
  tests/test_routine_work_items.py \
  tests/test_routine_tasks.py \
  tests/test_routine_write_scope_fail_closed.py \
  tests/contract/test_warranty_schedule.py
```

Expected: scheduler still invokes turns directly.

- [ ] **Step 3: Convert scheduler to enqueue adapter**

Preserve permission re-check, write-scope fail-closed, max-runs, busy-turn retry, and auto-pause. Execute only under lease through the worker.

- [ ] **Step 4: Run GREEN**

Rerun Task 8 command.

- [ ] **Step 5: Commit atomically**

```bash
git add backend/app/services/routine_task_scheduler.py \
  backend/app/agentive/services/routine_tasks.py \
  backend/app/agentive/services/work_execution.py \
  backend/tests/test_routine_work_items.py \
  backend/tests/test_routine_tasks.py \
  backend/tests/test_routine_write_scope_fail_closed.py \
  backend/tests/contract/test_warranty_schedule.py
git commit -m "feat(agentive): enqueue routine fires as durable work"
```

---

### Task 9: Durable ChangeEvent trigger consumer

**Files:**
- Modify: `backend/app/services/change_event_logger.py`
- Create: `backend/app/agentive/services/work_events.py`
- Modify: `backend/app/services/event_wake.py`
- Modify: `backend/app/services/change_event.py`
- Modify: `backend/app/agentive/services/work_execution.py`
- Create: `backend/tests/test_event_work_items.py`

- [ ] **Step 1: Write failing event tests**

Add `find_after_checkpoint` to `ChangeEventLogger` via RED first. Cover checkpoint advance only after deterministic enqueue outcomes, duplicate page suppression, crash before/after enqueue, malformed-row fail-closed, and `event_wake` as latency hint only.

Idempotency key:

```text
event:{change_event_dblog_id}:{trigger_key}
```

- [ ] **Step 2: Run RED**

```bash
cd backend
.venv/bin/python -m pytest -q --tb=short tests/test_event_work_items.py
```

Expected: checkpoint consumer and `find_after_checkpoint` missing.

- [ ] **Step 3: Implement consumer**

Use logging-db DBLog reads and prime-db declarations/checkpoints. Do not claim cross-database atomicity with the original mutation. Event handler invokes brokered Core capabilities or the existing chat entrypoint only.

- [ ] **Step 4: Run GREEN**

Rerun Task 9 command.

- [ ] **Step 5: Commit atomically**

```bash
git add backend/app/services/change_event_logger.py \
  backend/app/agentive/services/work_events.py \
  backend/app/services/event_wake.py \
  backend/app/services/change_event.py \
  backend/app/agentive/services/work_execution.py \
  backend/tests/test_event_work_items.py
git commit -m "feat(agentive): enqueue durable event triggers"
```

---

### Task 10: Lifecycle wiring and production fail-closed posture

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/app/agentive/services/work_worker.py`
- Modify: `backend/app/agentive/services/work_recovery.py`
- Create: `backend/tests/test_work_kernel_lifecycle.py`
- Modify: `backend/tests/test_execution_runs.py`

- [ ] **Step 1: Write failing lifecycle tests**

Assert startup order:

```text
ensure Object indexes
→ probe transaction capabilities
→ reject Mongo / multi-worker non-Postgres
→ reconcile development facts
→ run recovery once
→ start worker/recovery/outbox/event loops
```

Assert shutdown drains work tasks before database close. Assert production fails closed when indexes or transaction methods are unavailable.

- [ ] **Step 2: Run RED**

```bash
cd backend
.venv/bin/python -m pytest -q --tb=short \
  tests/test_work_kernel_lifecycle.py tests/test_execution_runs.py
```

Expected: no work-kernel lifecycle hooks.

- [ ] **Step 3: Wire startup/shutdown**

Add one managed work-kernel task to `_background_tasks` after jvagent bootstrap and before or beside the routine producer loop. Keep process-local chat-admission warnings separate from WorkItem lease authority.

- [ ] **Step 4: Run GREEN**

Rerun Task 10 command.

- [ ] **Step 5: Commit atomically**

```bash
git add backend/app/main.py \
  backend/app/agentive/services/work_worker.py \
  backend/app/agentive/services/work_recovery.py \
  backend/tests/test_work_kernel_lifecycle.py \
  backend/tests/test_execution_runs.py
git commit -m "feat(agentive): wire work recovery into lifecycle"
```

---

### Task 11: Subprocess chaos gate

**Files:**
- Create: `backend/tests/test_work_kernel_chaos.py`
- Create: `backend/tests/support/work_chaos.py`
- Modify only service files required by chaos failures

- [ ] **Step 1: Build chaos harness**

Use subprocess workers, abrupt `os._exit` after named boundaries, reopened storage in a fresh process, a durable fake external adapter keyed by effect id, and a durable outbox consumer keyed by `outbox_id`. No environment-driven production crashes.

Named points:

```python
CRASH_POINTS = [
    "after_enqueue",
    "after_claim",
    "after_agent_run_create",
    "before_capability_effect",
    "after_effect_receipt_before_completion",
    "before_approval_decision",
    "after_approval_decision",
    "before_outbox_delivery",
    "after_outbox_delivery",
    "during_routine_provider_turn",
    "during_cancellation",
    "during_deadline_expiry",
    "during_event_enqueue_before_checkpoint",
]
```

- [ ] **Step 2: Write scenarios and run RED**

```bash
cd backend
.venv/bin/python -m pytest -q --tb=short tests/test_work_kernel_chaos.py
```

Expected: at least the first crash case exposes missing restart plumbing.

- [ ] **Step 3: Fix only owning services**

No chaos-specific recovery path. Assert no duplicate effects, retained approvals, no stuck expired leases, stale fence rejection, and unchanged principal/workspace.

- [ ] **Step 4: Run GREEN**

```bash
cd backend
.venv/bin/python -m pytest -q --tb=short \
  tests/test_work_kernel_chaos.py \
  tests/test_work_items.py \
  tests/test_work_item_leases.py \
  tests/test_work_outbox.py \
  tests/test_work_controls.py \
  tests/test_work_worker.py \
  tests/test_work_recovery.py \
  tests/test_work_approvals.py \
  tests/test_routine_work_items.py \
  tests/test_event_work_items.py
```

- [ ] **Step 5: Commit atomically**

Stage only the chaos harness and the exact service files fixed by failures. Do not `git add backend/app/agentive` wholesale.

```bash
git commit -m "test(agentive): prove restart recovery at work boundaries"
```

---

### Task 12: Postgres certification, invariants, full gates

**Files:**
- Modify: `backend/tests/contract/test_work_kernel_postgres.py`
- Modify: `docs/INVARIANTS.md`
- Modify: `backend/tests/conftest.py` only if collection policy requires it
- Modify: `docs/superpowers/specs/2026-09-18-durable-work-kernel-recovery-design.md` only if implementation forces an explicit amendment

- [ ] **Step 1: Complete Postgres production-service contracts**

Add `test_two_workers_exactly_one_claims`,
`test_stale_fence_cannot_complete_after_reclaim`,
`test_transition_and_outbox_are_atomic_on_rollback`,
`test_two_approval_decisions_exactly_one_wins`,
`test_effect_receipt_replay_prevents_duplicate_after_crash`, and
`test_recovery_converges_without_cross_workspace_claim`.

Every test uses production services against `postgres_raw_db`, not replacement mocks.

- [ ] **Step 2: Run Postgres RED/GREEN**

```bash
docker compose up -d db
cd backend
TESTING=1 INTEGRAL_TEST_DB=postgres \
  JVSPATIAL_PG_GIN_INDEX=off JVSPATIAL_POSTGRES_MAX_POOL_SIZE=3 \
  .venv/bin/python -m pytest -q --tb=short \
  -n 2 --dist loadfile -m "contract and postgres" \
  tests/contract/test_work_kernel_postgres.py \
  tests/contract/test_jvspatial_work_txn_api.py
```

- [ ] **Step 3: Document invariants and run legacy regressions**

Append I-WORK-01..06 with exact gates. Run:

```bash
cd backend
.venv/bin/python -m pytest -q --tb=short \
  tests/test_graph_contiguousness.py \
  tests/test_change_event_no_bypass.py \
  tests/test_policy_audit_emit.py \
  tests/test_execution_runs.py \
  tests/test_capability_broker.py \
  tests/test_routine_tasks.py \
  tests/test_routine_write_scope_fail_closed.py \
  tests/test_staging_persistence.py \
  tests/test_staging_apply_claim.py \
  tests/test_staging_terminal_durability.py
```

- [ ] **Step 4: Run repository gates**

```bash
make verify
make verify-pr
```

- [ ] **Step 5: Commit certification**

```bash
git add backend/tests/contract/test_work_kernel_postgres.py \
  backend/tests/conftest.py docs/INVARIANTS.md
# plus design amendment only if required
git commit -m "test(agentive): certify durable work kernel recovery"
```

Do not push without explicit user consent.

---

## Coverage map

- Dependency/API prerequisite: Task 0
- Schemas and deterministic identity: Task 1
- Transactional enqueue/outbox: Task 2
- Leases and fences: Task 3
- Controls and recovery: Task 4
- Effect-context propagation: Task 5
- Supervised worker: Task 6
- Durable approvals: Task 7
- Routine/App schedule migration: Task 8
- Durable event triggers: Task 9
- Lifecycle posture: Task 10
- Subprocess chaos: Task 11
- Postgres certification and full gates: Task 12

## Open blockers before implementation code

1. jvspatial must publish public transaction-handle CAS + `insert_if_absent`.
2. jvagent must release against that pin and preserve host `WorkExecutionContext`.
3. Integral Task 0 pins both releases before Tasks 2+.

Until Task 0 is green, do not begin lease/outbox implementation in Integral.
