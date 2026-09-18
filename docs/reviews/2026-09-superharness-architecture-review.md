# Superharness architecture review

**Date:** 2026-09-18
**Status:** architectural scrutiny and proposed recovery sequence
**Scope:** Integral Core as a multi-tenant agent harness, operational substrate,
and modular application runtime.

## Verdict

Integral is a promising **agentive substrate with a well-developed jvagent
integration**. It is not yet a superharness.

It already does several difficult things correctly: it carries human and agent
actions through a shared policy model, represents operational knowledge in a
structured graph, makes mutation staging visible, exposes a substantial MCP
surface, supports workspace/App skills, and has begun separating Core from
Apps. Those are meaningful advantages.

The missing layer is a durable, provider-neutral **execution control plane**.
Today the runtime is principally a web application that streams a jvagent turn
and dispatches tools. A superharness must instead make every agent execution a
durable, governed process over a versioned substrate and a bounded environment.
It must stay correct across multiple users, workspaces, sessions, workers,
restarts, retried external calls, App upgrades, and provider swaps.

The present architecture is therefore credible as a foundation, but the product
claim is ahead of the runtime architecture. Do not describe it externally as a
general superharness until the kernel, capability, and reliability gaps below
are closed.

## Scope decision: strengthen Integral around jvagent

**Decision:** pursue the foundation, but name the near-term milestone the
**Harness Kernel** (or **Integral Runtime**). Keep jvagent as the sole
production and reference provider while the Kernel is built. The Kernel makes
jvagent more reliable and makes a future compatible provider possible; it does
not replace jvagent, recreate a general-purpose orchestration framework, or
turn provider choice into the product.

The ownership boundary is intentional:

| jvagent owns | Integral Runtime owns |
| --- | --- |
| Model interaction, reasoning loop, skill selection, provider-session continuation, stream production | Principal/workspace/App identity, durable session/run state, context and capability compilation, policy, approvals, audit, idempotency, substrate operations, App lifecycle, environment effects, recovery |

This is a refinement of the singular-resident decision, not a reversal. The
resident remains one coherent user-facing mind per active provider binding.
The Runtime supports many isolated sessions and runs for that resident across
users, workspaces, Apps, and workers.

### Explicit exclusions for this milestone

The Harness Kernel must **not** include any of the following:

- a generic visual workflow engine or user-authored DAG platform;
- a peer-agent/A2A fabric or hidden agent-to-agent messaging;
- a marketplace of harness providers;
- another production provider integration before jvagent passes conformance;
- a server-side arbitrary-code runtime for untrusted skills;
- per-token persistence or a database round trip on every stream delta.

These exclusions keep the work attached to Integral's actual value: governed
intelligence over an application substrate and real client environments.

### Latency and maintainability constraints

Persist and authorize at execution boundaries, never in the token stream:

```
create/resume run → compile context + capability snapshot → stream normally
tool/effect step  → policy + idempotency + receipt             → continue
terminal state     → persist outcome and audit                 → finish
```

The first-token path may resolve one session and one immutable capability
snapshot. It must not wait on a workflow scheduler or write each model event.
Tool and external-effect boundaries already dominate by service/network time;
their durable receipt is a correctness cost worth paying. Runtime data may be
cached during a run, but cached authority must be versioned and invalidated on
permission, App, or connector change.

The implementation cost is real: a provider protocol and durable execution
records add concepts that do not exist today. The design earns that complexity
only by deleting parallel state machines, process-local authority, and
provider-specific mutation paths. Each Kernel work package must identify which
existing path it retires or consolidates; additions that merely layer another
abstraction on top of the old path do not qualify.

## What the term must mean

A superharness is not a chat UI, an agent SDK wrapper, or a collection of MCP
tools. It has six inseparable properties:

1. **Intelligence portability.** A harness adapter can bind a language model
   or agent engine without letting provider-specific state become Core state.
2. **Durable execution.** Sessions, turns, runs, plans, approvals, waits,
   retries, cancellation, and recovery have durable identity and replay-safe
   semantics.
3. **Governed capabilities.** The model receives a capability snapshot, not
   ambient access to routes, credentials, filesystem state, or mutable
   registries. Every call has authorization, declared purpose, cost/timeout,
   idempotency, audit, and revocation behavior.
4. **An operational substrate.** The harness can query, reason over, mutate,
   and evolve a permission-filtered application graph with provenance and
   clear transaction boundaries.
5. **Environment interaction.** External systems are represented as governed
   environment bindings with scoped credentials, egress policy, observations,
   action receipts, compensation semantics, and human control.
6. **Composable applications.** Apps supply schema, operations, skills, views,
   migrations, and environment bindings through public, versioned contracts;
   they do not patch Core or depend on one specific harness.

The harness's *resident* character is a product posture. It does **not** mean
one in-memory agent process or one provider conversation. The correct runtime
unit is:

```
ExecutionContext = principal × workspace × app-context × session ×
                   harness-binding × capability-snapshot × substrate-revision
```

One resident persona can serve many such contexts concurrently. A provider
session is only an adapter-owned continuation handle attached to a durable
Integral session; it is never the authoritative identity of the work.

## Current architecture: strengths worth preserving

| Area | What is real today | Why it matters |
| --- | --- | --- |
| Substrate | Workspace/App/Track/Entry graph, typed edges, graph invariants, permission-aware retrieval | A better operational memory model than chat transcript alone |
| Shared governance | Tool manifest, policy gate, staging, change-event audit, scoped MCP perimeter | The right instinct: agent actions should use the same rules as humans |
| Provider seam | Embedded and streamed jvagent paths normalize into a common chat envelope; smoke provider exists | A useful beginning for pluggable intelligence |
| Sessions | Chat threads persist provider session IDs and recover an orphaned provider session once | Acknowledges that model state fails and must be rebound |
| App surface | Package manifests, typed operations, skills, schedules, trusted tools, extension views, lifecycle work | The right shape for the commercial App future |
| External systems | Connector/MCP mount work includes OAuth origin checks, credential encryption, tool discovery, and workspace registration | Better than treating every external API as an unbounded model tool |
| Safety posture | Default propose/bless behavior and per-resource policy evaluation | Appropriate for a cocreative operational system |

## Critical findings

### SH-01 — There is no durable execution kernel

**Severity: blocking.** Chat/provider sessions, routine tasks, staging cards,
and connector operations exist, but they are not unified as durable executions
with a common lifecycle. There is no authoritative `Run` record that answers:
what initiated this work, which model/provider/configuration ran it, which
capability snapshot it had, which inputs were read, which effects were
attempted, what is retryable, and whether it completed, failed, was cancelled,
or is waiting for a human.

Consequences:

- An interrupted streamed turn has no general recovery strategy beyond
  provider-session rebinding and transcript persistence.
- Scheduled work, chat work, App schedules, and external action work have
  different state machines rather than one execution model.
- There is no durable distributed lease to prevent two workers from advancing
  the same run or emitting duplicate effects.
- There is no universal resumable step cursor, retry policy, deadline, or
  cancellation contract.

The staging token is a useful approval primitive. It is not a workflow engine.

### SH-02 — Process-local registries make correctness deployment-dependent

**Severity: blocking for multi-worker production.** App tools/hooks and mounted
MCP tools are held in module-level dictionaries and reconstructed on startup.
The code explicitly describes this as an in-process registry. This causes
correctness to depend on every worker booting, successfully replaying every
installed App/connector, and retaining consistent code/artifact paths.

Consequences:

- An install, pause, upgrade, or connector mount handled by one worker can
  leave another worker with a stale capability set until rehydration.
- Best-effort startup replay hides a failed capability registration behind a
  log entry rather than an App health state.
- Horizontal scale turns App behavior into a cache-coherence problem.

Persist declarations and versions in the database; derive a signed capability
snapshot per execution. In-memory maps may remain only as bounded,
version-keyed caches with invalidation, never as the source of authorization or
dispatch truth.

### SH-03 — The current dispatcher crosses the wrong architectural boundary

**Severity: blocking.** Many agent bindings call FastAPI route handlers through
synthesized request objects. That reuses existing behavior quickly, but it
couples the agent runtime to HTTP handler signatures, request-shape quirks,
and API implementation details. It makes the source of policy and validation
harder to prove, and it prevents an App operation, a human UI request, an MCP
call, and a scheduled job from naturally sharing a pure command/query service.

The target shape is:

```
HTTP / MCP / resident / routine / App view bridge
                 → Command or Query service
                 → policy + idempotency + transaction/outbox + audit
                 → substrate and environment adapters
```

Routes should only authenticate, parse, and serialize. Agent tooling should
invoke the same command/query service directly, never manufacture an HTTP
request.

### SH-04 — The provider seam is an adapter, not a harness contract

**Severity: high.** The jvagent stream translator normalizes output events,
but Core lacks a formal provider contract covering context assembly, capability
injection, session lifecycle, tool-call protocol, cancellation, checkpointing,
model configuration, usage/cost, error classes, and recovery guarantees.

This makes the current switcher a useful integration selector, not proof that
other harnesses can dock cleanly. A second provider would likely duplicate
hidden jvagent assumptions in chat, stream translation, session handling, and
tool activity rendering.

Create a narrow `HarnessProvider` protocol and a provider conformance suite.
Providers must consume a Core-owned `ExecutionContext` and emit Core-owned
`RunEvent` records. The protocol must prohibit provider-owned mutation paths.

### SH-05 — “Singular resident” is being asked to carry two incompatible jobs

**Severity: high.** The documents correctly reject an ungoverned peer-agent
fleet, but risk treating a singular resident mind as an execution constraint.
That becomes untenable when users have several workspaces, each workspace has
multiple active conversations, Apps install specialists, routines fire, and
external MCP calls arrive concurrently.

Keep a singular resident as the default **experience and policy persona**.
Internally, support many isolated execution contexts and optionally many named
App skill invocations. Do not revive general A2A as a shortcut. Coordination
should occur through durable work items, App operations, events, and explicit
delegation records in the substrate—not through hidden agent-to-agent chat.

### SH-06 — Queryability is not yet a first-class, bounded language

**Severity: high.** The present `integral_query` surface is primarily
retrieval-oriented and the page-context path explicitly identifies itself as a
UI shell stub, not a substrate QuerySpec. That is insufficient for agents that
must make defensible operational claims or Apps that need stable analytical
capabilities.

Lock the earlier **hybrid QuerySpec choice (C)**:

- Apps expose declared named queries for their business semantics.
- Core exposes a bounded, typed open QuerySpec only over Entry/Track/App graph
  primitives.
- A QuerySpec compiler enforces scope, field/edge allowlists, traversal depth,
  pagination, cost budgets, result shape, and claim provenance before query
  execution.

The model must not synthesize raw graph queries or infer facts from page-shell
metadata. Every factual response should be able to reference the query/result
set that supports it.

### SH-07 — Memory is storage, not yet a managed cognitive system

**Severity: high.** Scratch tracks and hybrid retrieval are useful. They do
not yet define what context should be loaded, what is durable memory versus
working scratch, how assertions age, how conflicts are resolved, how user
corrections supersede model conclusions, or how memory is revoked when access
changes.

Add a Core-owned context compiler that produces a budgeted, provenance-bearing
context pack from the current execution context. Define four explicit classes:

| Class | Purpose | Lifecycle |
| --- | --- | --- |
| Conversation transcript | Interaction record | Immutable/audited, retention-controlled |
| Working memory | Short-lived reasoning material | Session/run scoped, TTL and promotion rules |
| Operational knowledge | Entries and graph facts | Normal substrate authorization and versioning |
| Learned preference/assertion | User-approved, attributable guidance | Source, confidence, conflict, expiry, revocation |

No model-generated assertion should silently become durable memory.

### SH-08 — External interaction lacks a unified environment model

**Severity: high.** Connectors and mounted MCP servers have promising security
work, but Core does not yet model an external environment as a governed target
with capabilities, ownership, data classification, egress restrictions, rate
limits, action contracts, receipts, and compensation semantics.

Every external binding needs:

```
EnvironmentBinding
  → credential reference (secret broker, never model context)
  → declared capabilities and schemas
  → egress/origin policy + rate/cost budget
  → observation sync policy and freshness
  → effect policy: read / propose / execute / irreversible
  → receipt, idempotency key, reconciliation and compensation behavior
```

MCP tool discovery alone cannot establish trust in a remote environment. An
agent must see a capability descriptor, not arbitrary remote instructions.

### SH-09 — Staging is safer than direct writes, but durability is intentionally soft

**Severity: high.** Outstanding staged cards have a durable mirror, but the
store explicitly treats persistence failures as best-effort and falls back to
in-memory behavior. That is a reasonable local-development fallback; it is
not suitable for a system whose human approval is the authority for agent
effects. Approval loss or duplicate execution is a correctness failure.

For production, approval, execution, and audit need an atomic state transition
with a durable idempotency key and outbox record. If persistence is unavailable,
the system should fail closed for new approvals and clearly surface degraded
state; it must not claim that an approval has been safely recorded.

### SH-10 — Observability cannot yet explain or reproduce agent behavior

**Severity: high.** The stream translator records useful usage and tool
activity, while operation contexts include correlation fields. There is no
uniform distributed trace and evaluation model joining prompt/context assembly,
provider calls, model/tool decisions, policy decisions, staging, external
requests, substrate revisions, and user outcomes.

Without this, a production incident becomes a transcript-reading exercise and
a commercial App cannot establish quality or regression evidence.

Make `run_id`, `trace_id`, `causation_id`, `idempotency_key`, and substrate/App
version mandatory through every boundary. Store replay-safe event payloads and
redacted prompt/context fingerprints. Add task-level evaluation fixtures before
promoting autonomous behavior.

### SH-11 — The App surface is ahead in intent but incomplete in public reality

**Severity: high.** The prior App-extension assessment remains valid: the SDK
does not model all runtime operations it expects Apps to use; custom frontend
modules have a separate commercial route; custom skills are declared but not
executed by the overlay; and source fixtures do not prove clean artifact
installation. This means Apps cannot yet be treated as independent products
that extend every superharness surface.

The App closure plan is a dependency of commercial retrofit, not optional
polish. The superharness must expose the same capability, session, query,
environment, and observability contracts to App code.

### SH-12 — Documents and terminology still reveal architectural drift

**Severity: medium, but compounding.** Current documents mix historical
Space/WorkspaceApp language with App, describe different BYOA surfaces in
different places, and label several designs as shipped while later listing
their crucial runtime behavior as deferred. This makes it hard for a developer
or coding agent to distinguish a contract from an aspiration.

Create one architecture source of truth with four labels only:
`implemented`, `contracted`, `experiment`, and `retired`. Build generated
capability and status tables from code/tests; never maintain maturity claims by
hand in several documents.

## Architecture to lock

### 1. Core-owned execution model

Introduce durable objects or records for:

- `AgentSession`: Integral-owned conversation/work continuity. Bound to a
  principal, workspace, optional App focus, retention policy, and provider
  continuation handles.
- `AgentRun`: one initiated unit of work. Has immutable capability and
  substrate/App version snapshots, model configuration, deadline, budget,
  state, and causation.
- `RunStep`: model call, query, tool invocation, human wait, external action,
  or checkpoint. Each has deterministic input/output fingerprints and a retry
  policy.
- `WorkItem`: durable delegated/scheduled/event-triggered work, assigned to a
  session/facet and advanced through a leased worker.
- `Approval`: durable authority record separate from presentation cards.
- `CapabilitySnapshot`: resolved Core/App/connector tool set and grants for one
  run. It is immutable, signed/versioned, and revocation-aware.

State transitions must be explicit:

```
created → running → waiting_for_human | waiting_for_event | retry_wait
        → succeeded | failed | cancelled | expired
```

Only a leased worker can advance a run. Every effect carries an idempotency key
derived from run/step identity. State update, audit event, and outbox message
commit together.

### 2. Capability broker and environment adapters

Replace route-handler reuse and per-process registration as the authority with
a Core `CapabilityBroker`:

1. Resolve `CapabilitySnapshot` from execution context.
2. Validate declared input schema, budget, policy, and environment grant.
3. Call a pure query/command service or environment adapter.
4. Persist a receipt, audit event, and result schema.
5. Return a model-safe result with provenance and redaction.

The broker is the only route for resident, MCP, App view, schedule, and custom
skill effects. HTTP becomes merely one broker client.

### 3. Query compiler

Implement hybrid QuerySpec C as a public contract. The compiler must validate
not only authorization but computational cost and explainability. Query results
return a `result_set_id`, normalized plan, graph/substrate revision, and item
provenance. Generated responses can cite that result set internally and in UI
debug surfaces.

### 4. Context and memory compiler

Build context from immutable inputs: user utterance, session state, App focus,
capability snapshot, current page pointer, approved memories, and compiled
query results. Context selection must be observable, budgeted, permission
checked at read time, and revalidated before an effect.

### 5. Provider conformance layer

Define `HarnessProvider` over Core-owned execution events. Required support:

- create/resume/terminate provider continuation;
- stream normalized model, reasoning, tool, and terminal events;
- accept cancellation/deadline/budget;
- receive only compiled context and capability descriptors;
- report model/version/usage/error data;
- make no direct Core or environment mutation.

Ship conformance fixtures using Echo, embedded jvagent, streamed jvagent, and
one intentionally failing provider. Do not add Hermes, LangGraph, or another
harness until it passes this contract; otherwise each integration increases
architectural divergence.

## Delivery sequence

### Phase A — establish architectural truth and freeze expansion

- Publish the superharness vocabulary and a runtime status matrix.
- Freeze new direct tool bindings, route-handler reuse, provider-specific
  state, commercial frontend manifests, and in-memory capability authorities.
- Lock QuerySpec C and the durable execution model.
- Adopt the scope decision above: jvagent is the only production provider;
  other integrations wait for provider conformance.
- Establish latency budgets for first token, tool dispatch, approval, and
  recovery. Measure the current jvagent path before changing it, then enforce
  no material regression in the Kernel acceptance suite.
- Inventory every existing tool, routine, connector, schedule, App operation,
  and chat path against the new execution/capability contracts.

**Gate:** each item is classified as retain, adapt, replace, or retire. No
uncategorized execution path remains.

### Phase B — build the durable kernel

- Implement `AgentSession`, `AgentRun`, `RunStep`, `WorkItem`, `Approval`, and
  durable worker leasing using the production database.
- Add transactional outbox, idempotency, cancellation, deadlines, retries,
  and restart recovery.
- Move staging from best-effort persistence to fail-closed approval authority.
- Convert routines and App schedules to create work items rather than invoke
  provider turns directly.

**Gate:** kill/restart a worker at every run state; no duplicated external or
substrate effect, lost approval, or stuck work item remains.

### Phase C — unify capability and environment execution

- Extract pure command/query services from HTTP routes.
- Build `CapabilityBroker`; migrate resident, MCP, App operations, and view
  bridge to it.
- Replace module-level registries with versioned persisted declarations and
  cache invalidation.
- Model connector/MCP bindings as environments with grants, receipts, and
  reconciliation.

**Gate:** the same capability invocation has equivalent result, policy,
idempotency, audit, and error semantics through HTTP, MCP, resident, routine,
and App view clients.

### Phase D — make intelligence substrate-native

- Ship QuerySpec C compiler and result provenance.
- Ship the context/memory compiler, approved assertion lifecycle, and access
revocation behavior.
- Establish a first-class plan/work-item surface rather than storing plans only
in conversation text or staging payloads.
- Add event-triggered work through the outbox/event stream, not process-local
subscriptions.

**Gate:** a complex multi-step task can pause for user approval, resume after a
restart, explain each factual claim and effect, and remain isolated across two
users and two workspaces.

### Phase E — complete the public App runtime

- Execute the App-extension closure plan: complete SDK, shared operations,
  public view host, honest custom-skill contract, independent artifacts,
  migration/recovery certification, and retrofit pilot.
- Bind App contributions into capability snapshots, QuerySpec declarations,
  context packs, environment grants, and run traces.

**Gate:** an independently built App adds schema, view, skill, operation,
schedule, and external environment capability without Core source imports or a
provider-specific integration.

### Phase F — operational excellence and commercial retrofit

- Add traces, redacted replay, quality evaluations, adversarial prompt/tool
tests, capability fuzzing, load tests, and chaos recovery drills.
- Certify the first commercial retrofit against a pinned Core release, then
build a repeatable retrofit factory.

**Gate:** every release has evidence for correctness, isolation, recovery,
security, model/provider compatibility, and App compatibility—not merely unit
tests and a successful chat demo.

### Provider expansion is a later decision gate

Only consider Hermes, LangGraph, or another harness after all of the following
are true:

1. jvagent runs exclusively through the `HarnessProvider` contract for normal,
   failed, cancelled, and resumed turns.
2. A provider conformance suite covers session continuation, stream ordering,
   duplicate terminal events, cancellation, tool errors, budgets, and recovery.
3. The new provider offers a concrete user or operational advantage that
   jvagent cannot reach through its own roadmap.
4. The adapter requires no Core exception, provider-specific App API, or new
   authority path.

Until then, hardening jvagent is both cheaper and strategically clearer.

## Non-negotiable acceptance scenarios

1. Two users in different workspaces run the same App skill concurrently;
   neither can observe the other's context, skills, mounted tools, result sets,
   or pending approvals.
2. A worker dies after an external system accepts a mutation but before Integral
   records completion. Recovery reconciles by idempotency receipt without
   duplicate effect.
3. A user changes App capability or revokes a connector while a run is paused.
   Resume re-resolves authority and safely fails or requests new approval.
4. An App upgrade occurs while runs reference the prior App version. Existing
   runs complete against their immutable snapshot or are explicitly migrated;
   none silently cross semantic versions.
5. An agent makes an operational claim. The UI can show the bounded QuerySpec,
   result set, permissions, and substrate revision supporting it.
6. A provider sends malformed stream events, duplicate final messages, tool
   calls after cancellation, or stale session identifiers. The Core run state
   remains single-terminal and effect-safe.
7. An untrusted App skill, extension view, remote MCP server, or prompt
   attempts to exceed declared capabilities. The broker rejects it and records
   an explainable denial without exposing secrets or cross-workspace metadata.

## Decisions to make immediately

1. Approve the distinction: **resident is a product persona; the execution
   kernel is multi-session, multi-run, and provider-neutral.**
2. Lock **hybrid QuerySpec C** as the only open query path.
3. Make the durable execution kernel and capability broker the next foundation
   milestone, ahead of another harness integration or more chat polish.
4. Keep jvagent as the reference provider while its integration is brought
   behind the provider contract. Defer additional harnesses until conformance
   tests exist.
5. Treat the public App surface as part of the superharness kernel: an App must
   extend capabilities and environments through contracts, never through Core
   internals or provider-specific code.
