# Durable Work Kernel and Recovery Design

**Status:** locked for implementation after coordinated dependency release
**Date:** 2026-09-18
**Authority:** `docs/reviews/2026-09-superharness-architecture-review.md`, Phase B

## Objective

Make capability-delegated, scheduled, event-triggered, and approval-resumed
work durable, leased, replay-safe, cancellable, and recoverable without moving
reasoning or stream production out of jvagent.

“Delegated work” in this phase means a `kind="capability"` WorkItem containing
one declared capability invocation. It does not introduce plans, child-agent
delegation, peer messaging, or a generic workflow language.

Core owns work authority. jvagent remains responsible for reasoning, tool
selection, continuation, and streams. Existing `AgentRun`, `RunStep`,
`CapabilityBroker`, `RoutineTask`, and staging paths are adapted rather than
replaced.

## Non-goals

- No full CQRS rewrite.
- No token-delta persistence.
- No new provider or model runtime.
- No generic workflow language or mutable handler registry.
- No domain-specific App behavior in Core.
- No replacement of `RoutineTask` as the user-facing schedule definition.
- No duplicate effect-receipt model; `RunStep` remains the sole receipt.
- No raw database driver or private transaction-handle access in Integral.
- No production multi-worker guarantee for JSON/SQLite.
- No Mongo production certification in Phase B.
- No provider-conformance or context/memory compiler in this phase.

## Dependency and production boundary

Phase B begins with one coordinated release chain:

1. jvspatial publishes a release whose public Postgres transaction handle
   supports `find_one_and_update` compare-and-set and `insert_if_absent` on the
   same transaction.
2. jvagent updates to that jvspatial release and publishes a compatible release.
   It also preserves host-supplied work execution context through embedded
   resident tool callbacks.
3. Integral pins both released versions and regenerates `backend/uv.lock`.

Integral must not emulate the missing transaction API through
`PostgresTransaction._connection`, raw SQL, or `asyncpg`.

Postgres is the only Phase B production-certified store. JSON and SQLite are
single-worker development stores with reconciliation. Mongo is explicitly
unsupported for the Phase B work kernel; a production boot configured for
Mongo fails closed rather than claiming equivalent transaction or lease
semantics.

## Durable records

### `WorkItem`

`WorkItem` is a `jvspatial.core.Object` under I-GRAPH-02. It is queue-shaped,
scalar-keyed, and participates in no graph traversal or cascade.

It contains:

- deterministic `work_item_id` and object `id`;
- `kind`, `origin`, `principal_id`, `workspace_id`, optional thread/App ids;
- optional parent/causation ids and immutable input payload/fingerprint;
- status, attempt, retry policy, next-attempt time, deadline, cancellation;
- lease owner/token/fence/expiry and transition sequence;
- current deterministic `run_id`;
- timestamps, bounded result refs/fingerprint, and normalized failure.

Required indexed fields are `work_item_id`, `status`, `workspace_id`,
`principal_id`, `next_attempt_at`, `lease_expires_at`, and `idempotency_key`.

Payloads contain references, never credentials. Connector secrets remain in
connector bindings. Result bodies are not stored.

### `WorkOutboxEntry`

`WorkOutboxEntry` is an I-GRAPH-02 `Object`. It contains deterministic
`outbox_id`, topic, redacted payload, work/run/causation ids, status, delivery
attempt, available/deadline times, and delivery lease fields.

Required indexed fields are `outbox_id`, `status`, `topic`, `available_at`,
`lease_expires_at`, and `work_item_id`.

Delivery is at-least-once. Consumers deduplicate by `outbox_id`.

### `WorkApproval`

`WorkApproval` is an I-GRAPH-02 authority record separate from presentation
cards. It links `work_item_id`, `run_id`, `run_step_id`, staging token or
policy Approval id, authority digest, status, decider, and timestamps.

Statuses are exactly:

```text
pending | approved | rejected | expired
```

Required indexed fields are `work_approval_id`, `work_item_id`, `status`,
`staging_token`, `policy_approval_id`, and `expires_at`.

Existing policy `Approval` Nodes remain graph participants and link by scalar
id to `WorkApproval`; they are not demoted to Objects.

### Event trigger declarations and checkpoint

`EventTriggerDeclaration` and `ChangeEventTriggerCheckpoint` are I-GRAPH-02
Objects in the prime database. A declaration is a generic, persisted mapping
from action/resource/scope predicates to one declared Core capability or
resident turn template. It contains no App-specific behavior. The checkpoint
stores the consumer name, last `logged_at`, and last DBLog `id`.

ChangeEvents remain DBLog Objects in the separately configured logging
database. No cross-database transaction is claimed.

## Exact WorkItem transition table

Only these transitions are legal:

| Source | Allowed targets |
| --- | --- |
| `queued` | `running`, `cancelled`, `expired` |
| `running` | `waiting_for_human`, `waiting_for_event`, `retry_wait`, `succeeded`, `failed`, `cancelled`, `expired`, `dead_letter` |
| `waiting_for_human` | `queued`, `failed`, `cancelled`, `expired` |
| `waiting_for_event` | `queued`, `failed`, `cancelled`, `expired` |
| `retry_wait` | `queued`, `cancelled`, `expired`, `dead_letter` |
| `succeeded` | none |
| `failed` | none |
| `cancelled` | none |
| `expired` | none |
| `dead_letter` | none |

Approval maps onto the original WorkItem:

- approve: `waiting_for_human → queued`;
- reject: `waiting_for_human → failed` with
  `class="policy_denied"`, `code="work.approval_rejected"`;
- approval expiry: `waiting_for_human → expired`;
- explicit cancellation: `waiting_for_human → cancelled`.

Approval never creates a replacement WorkItem. Resumption requeues the original
identity.

## Identity, attempts, runs, and logical steps

### Enqueue identity

Callers supply an idempotency identity. Core derives:

```text
o.WorkItem.{sha256(kind, origin, principal, workspace, idempotency_key)}
```

Creation is atomic with the initial outbox fact on Postgres. Reusing an
identity with a different input fingerprint fails with
`work.idempotency_conflict`; it never rewrites payload or authority.

### Attempt numbering and AgentRun identity

`attempt` starts at `0` while queued. A successful claim atomically increments
it to `1`; each later successful claim increments it once. Retry scheduling
does not increment it.

Each attempt has one deterministic AgentRun id:

```text
workrun:{sha256(work_item_id + ":" + decimal_attempt)}
```

`AgentRun` keeps its existing status vocabulary:
`running | succeeded | failed | cancelled`. Waiting is not added as an
AgentRun status. When an attempt yields human/event waiting, its AgentRun
finishes as `succeeded` with `metadata.outcome="waiting_for_human"` or
`"waiting_for_event"`; the WorkItem remains authoritative. Resume creates the
next deterministic attempt run.

`AgentRun.work_item_id`, `AgentRun.deadline_at`, and `RunStep.work_item_id`
link audit records to authority.

### Stable logical step identity

A `kind="capability"` WorkItem has logical step `capability:0`.

Provider-driven turns use persisted ordinal slots `provider:0`, `provider:1`,
and so on. Before invoking a tool, Core persists the slot with capability key
and input fingerprint. On a provider retry:

- the same ordinal + capability + input fingerprint reuses the same RunStep
  effect identity;
- a changed capability or input at an occupied ordinal fails closed as
  `work.logical_step_conflict`;
- a later new ordinal receives a new slot.

Effect identity is:

```text
sha256(work_item_id + ":" + logical_step_key)
```

It is independent of AgentRun attempt and provider tool-call ids.

## `WorkExecutionContext`

Core defines one immutable context propagated through every effect path:

```text
WorkExecutionContext
  work_item_id
  attempt
  run_id
  principal_id
  workspace_id
  logical_step_key
  effect_key
  lease_token
  lease_fence
  deadline_at
  cancellation_signal
```

It travels through:

1. routine/event/capability worker handler;
2. `ChatTurnContext.extra_data` and the embedded resident provider;
3. the jvagent host tool callback;
4. `CapabilityInvocation`;
5. `capability_adapters.dispatch_capability`;
6. `tooling.dispatch.dispatch_tool`;
7. `ToolContext`;
8. App operation dispatch and MCP proxy/client calls.

Identity and lease fields are host context, never model arguments.

Before invocation, every effect adapter declares durable effect-key support.
Unsupported App/environment/MCP adapters fail before any call with
`work.non_replayable_effect`. Supporting adapters accept the Core effect key,
persist or forward it to the target, and return/reconcile by that key.

## Lease and provider supervision

Only the current lease token and fence may advance running work. Claim,
heartbeat, and completion use public database compare-and-set.

- A worker heartbeat runs at an interval no greater than one third of the
  lease duration.
- The provider operation and heartbeat run under one supervised task group.
- Heartbeat CAS failure signals cancellation to the local provider operation.
- After lease loss, Core rejects all new effect boundaries and completion,
  even if provider cancellation is slow.
- An expired lease is reclaimed only by recovery and receives a new token and
  incremented fence.
- JSON/SQLite use a process lock for development only.

## Atomic Postgres units

All service APIs participating in these units accept an optional public
transaction handle and use it rather than opening nested independent writes.

Postgres commits each unit atomically:

1. WorkItem creation + initial outbox fact.
2. Every WorkItem transition + transition outbox fact.
3. WorkApproval + `StagedChangeRecord` projection +
   `running → waiting_for_human` + outbox fact.
4. Approval decision CAS + original WorkItem transition + outbox fact.

Approve requeues the original WorkItem. Reject and expiry terminalize it.

JSON/SQLite perform ordered writes under the process lock and reconcile
deterministic missing facts/projections. They are not production certified.

## Retry, deadline, cancellation, and failures

Failure classes are exactly:

```text
transient
rate_limited
dependency_unavailable
permanent
policy_denied
cancelled
deadline_exceeded
non_replayable
```

The normalized record contains `class`, stable `code`, redacted model-safe
`message`, and `retryable`.

For failed attempt number `n >= 1`:

```text
base = min(max_delay, base_delay * 2^(n-1))
u = uint64(sha256(work_item_id + ":" + n)[0:8]) / 2^64
factor = (1 - jitter_ratio) + (2 * jitter_ratio * u)
delay = min(max_delay, base * factor)
```

This yields deterministic symmetric jitter in
`[1-jitter_ratio, 1+jitter_ratio)`. `next_attempt_at` is persisted.

Deadlines are checked before claim, before every effect boundary, and before
completion. Queued/waiting cancellation terminalizes immediately. Running
cancellation persists the request, signals the supervised provider operation,
and blocks later effects/completion.

## Recovery

Startup and periodic recovery:

1. expires overdue queued/waiting items;
2. reclaims running items whose lease expired;
3. places retryable orphans into `retry_wait`;
4. terminalizes non-replayable or exhausted items;
5. resumes pending outbox delivery;
6. reconciles running AgentRuns against WorkItems;
7. reconciles development-store atomic-unit gaps.

Recovery never reopens terminal, cancelled, denied, or expired work.

## Routine and App schedule migration

`RoutineTask` stays the schedule definition. A due fire atomically enqueues:

```text
kind=routine_turn
idempotency_key=routine:{routine_id}:{scheduled_for}
```

The scheduler no longer invokes `agent_turn` directly. The worker invokes the
existing routine execution body under a lease. App default schedules already
materialize as `RoutineTask` and inherit this path.

A routine turn that causes a real brokered effect must pass
`WorkExecutionContext`; crash after the durable RunStep receipt but before
WorkItem completion replays the receipt and does not repeat the effect.

## Durable event-trigger design

`event_wake` is a latency hint only.

`ChangeEventLogger.find_after_checkpoint` reads already-persisted DBLog rows
from the logging database with:

- `entity="DBLog"`;
- `context.log_level="CHANGE_EVENT"`;
- strict ordering by `context.logged_at`, then `id`;
- a bounded `limit`;
- rows strictly after `(last_logged_at, last_event_id)`.

The event consumer:

1. reads the durable prime-database checkpoint;
2. fetches one bounded DBLog page;
3. reconstructs each `ChangeEventEnvelope`;
4. resolves persisted generic `EventTriggerDeclaration` records;
5. enqueues each match with
   `event:{change_event_dblog_id}:{trigger_key}`;
6. advances the checkpoint only after all matches for that event were
   successfully created or returned as idempotent winners.

The ChangeEvent mutation and event WorkItem enqueue are not atomic because the
logging and prime databases are separate. Semantics are:

- crash before enqueue: checkpoint does not advance; replay enqueues later;
- crash after enqueue before checkpoint: replay returns the same WorkItem;
- declaration or enqueue failure: checkpoint remains before that event;
- malformed DBLog row: checkpoint remains and the consumer surfaces a stable
  failure rather than silently skipping;
- duplicate page/read: deterministic WorkItem identity suppresses duplicates.

Production event triggers require ChangeEvent logging and the logging database
to be available at startup. Otherwise startup fails closed when enabled trigger
declarations exist.

## Worker contract

Static Core handlers, implemented as explicit dispatch, are:

- `capability`;
- `routine_turn`;
- `approval_resume`;
- `event_trigger`.

Unknown kinds fail permanently. Workers never call App handlers, connectors,
or graph writers directly; all effects pass through the broker/context chain.

The loop recovers expired leases, fetches bounded candidate ids, CAS-claims one,
creates the deterministic AgentRun, runs provider and heartbeat supervision,
transitions with token/fence and outbox, then releases local resources.

## Startup posture

Production startup fails closed if:

- the store is not Postgres;
- required public transaction methods are absent;
- WorkItem/Outbox/Approval/checkpoint indexes cannot be ensured;
- transaction capability probes fail;
- event triggers are enabled but ChangeEvent logging/checkpoint reads are
  unavailable.

JSON/SQLite development startup logs the single-worker/reconciliation posture.
Mongo is rejected for Phase B production certification.

## Invariants

This phase adds:

- **I-WORK-01 — Lease authority:** only the current token/fence may advance
  running work; lease loss cancels local execution.
- **I-WORK-02 — Effect identity:** every effect derives from WorkItem and stable
  logical step identity and is propagated to the final adapter.
- **I-WORK-03 — Atomic work units:** the four Postgres units defined above
  commit together.
- **I-WORK-04 — Approval fail-closed:** authority, presentation, WorkItem wait,
  decision, and resumption obey the atomic units above.
- **I-WORK-05 — Recovery monotonicity:** recovery never reopens terminal,
  cancelled, denied, or expired work.
- **I-WORK-06 — Event checkpoint replay:** ChangeEvent checkpoint advancement
  occurs only after deterministic enqueue outcomes for the event.

Preserved:

- I-GRAPH-01 / I-GRAPH-02;
- I-APPROVAL-01..03;
- I-SUBSTRATE-01 / I-EXT-01 / I-HOOK-01;
- backend-authoritative workspace scope;
- existing ChangeEvent single-emission rules.

## Acceptance gate

Chaos tests use subprocess workers, abrupt `os._exit`, reopened storage, and a
durable fake external adapter/consumer. Named crash points are:

1. after transactional enqueue;
2. after claim;
3. after AgentRun creation;
4. before capability effect;
5. after effect receipt before WorkItem completion;
6. before and after approval decision;
7. before and after outbox delivery;
8. during routine provider turn with a real brokered effect;
9. during cancellation;
10. during deadline expiry;
11. during event enqueue before checkpoint advance.

For each point:

- no external or substrate effect repeats;
- no approval decision is lost;
- no WorkItem remains permanently stuck;
- stale workers cannot effect or complete;
- workspace/principal scope never changes;
- restart reaches one terminal or explicit waiting state.

The default development-store chaos suite and Postgres production-service
contract suite both pass. Full `make verify` and `make verify-pr` remain
mandatory before push.
