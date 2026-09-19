# Capability broker and environment receipts

**Status:** locked implementation specification
**Phase:** Superharness foundation — capability execution
**Depends on:** durable `AgentRun` and `RunStep` records
**Provider posture:** jvagent remains the reference reasoning and streaming
provider. The broker owns no model loop.

## Objective

Make one Core path authoritative for executing a declared capability from the
resident, HTTP App operations, MCP, or an extension-view bridge. That path
must enforce the run's authority, workspace scope, policy, confirmation,
idempotency, and result contract before it performs a query or effect. It must
leave a durable, redacted receipt on every outcome.

This phase turns the current `AgentRun.capability_snapshot` from an audit
record into an execution gate. It does not replace jvspatial services with a
general CQRS framework or introduce a second agent harness.

## Locked decisions

1. **Broker depth — wrap, then extract locally.** Add the broker around all
   four existing dispatch surfaces this phase. Preserve the working
   `dispatch_tool` and App-operation service implementations behind adapters.
   When a migrated path exposes route-only logic, extract just that query or
   command into a pure scoped service. Do not halt on a repository-wide CQRS
   rewrite; no migrated surface may retain a direct route-handler execution
   bypass.
2. **Receipt record — evolve `RunStep`.** `RunStep` is the sole receipt record.
   Add the fields and constraints needed for authoritative capability receipts;
   do not introduce `CapabilityReceipt` as parallel state. Model, tool, query,
   command, approval wait, and environment effect remain `RunStep.kind`
   variants.
3. **Run identity — always present.** Every broker invocation has an
   `AgentRun`. A chat turn attaches its existing run. HTTP, MCP, and view calls
   mint a short-lived run with `origin` respectively `http`, `mcp`, or `view`,
   and terminate it with the invocation. The caller cannot supply or widen a
   run's principal or workspace.
4. **Resume and revocation — fail closed for effects.** On resuming a paused
   run, a missing, revoked, upgraded, or otherwise mismatched capability
   causes `propose` and `execute` to fail with a stable reauthorization result;
   any prior approval is unusable. Reads may resolve current grants only after
   fresh authorization and must emit a new receipt that marks the snapshot
   divergence. Core never silently rewrites a run snapshot.
5. **QuerySpec delivery — two sequential plans in this phase.** Broker and
   receipts land first. QuerySpec follows immediately on the broker substrate;
   the broker gate does not wait for the compiler. The only open QuerySpec is
   the locked hybrid C contract: bounded Core Entry/Track/App graph reads.
   Apps expose declared query capabilities and parameters only.
6. **Environment cut — include mounted connectors.** First migration covers
   Core tools, App operations, and connector declarations already captured in
   a run snapshot. Live mounted-MCP invocation uses the broker. This phase does
   not add a new `EnvironmentBinding` schema; existing connector records remain
   the declaration source until that later model is justified.

## Non-goals

- A generic workflow engine, worker leasing/outbox/retry orchestration, or
  full execution-kernel rewrite.
- A second provider or harness integration.
- Arbitrary App graph traversal or custom query language.
- A new environment-domain model.
- Replacing the existing approval UX; this phase makes approval authority
  broker-enforced and traceable.

## Target contract

### Capability invocation

The broker receives an internal `CapabilityInvocation` value, never a raw
client request:

- `run_id`, principal, workspace, origin, optional App focus;
- capability key and declared version/source (`core`, `app`, `connector`);
- typed input and an idempotency key derived from run/step/capability;
- operation class: `read`, `propose`, or `execute`;
- deadline and budget envelope.

It must, in order:

1. load the run and bind identity/scope from it;
2. resolve the capability only from its stored snapshot;
3. revalidate current workspace/App/connector grant and policy;
4. validate input and cost/deadline limits;
5. create or resume the single idempotent `RunStep` receipt;
6. enforce confirmation for `propose`/`execute` and record the pending or
   denied state;
7. call a scoped Core service or adapter;
8. finalize the receipt and return a normalized, redacted result envelope.

No route, view bridge, MCP handler, provider adapter, or App handler executes
an effect directly after migration.

### Authoritative `RunStep` receipt

Extend `RunStep` with the following semantic fields (names may vary only if
one documented wire contract is retained):

- `capability_key`, `capability_version`, source and optional App/connector ID;
- `origin`, principal/workspace/App scope fingerprints;
- `idempotency_key`, attempt number, policy decision and denial code;
- approval/staging reference and snapshot fingerprint;
- redacted input/output fingerprints, result classification, timing, and
  adapter error code;
- lifecycle state supporting `running`, `waiting_for_human`, `succeeded`,
  `failed`, `cancelled`, and `denied`.

The record is created before an adapter can effect a mutation. Replays return
or reconcile the existing result. Raw secrets, user content, and connector
credentials never enter the receipt.

### Surface adapters

| Surface | Required change | Run origin |
| --- | --- | --- |
| Resident/jvagent | Replace direct tool dispatch with broker invocation; attach the chat turn run. | `chat` |
| HTTP App operation | Route mints a short run and delegates to the broker/App-operation adapter. | `http` |
| Integral MCP server | Tool handler mints a short run and delegates to the broker; scope remains token-bound. | `mcp` |
| Extension view bridge | Bridge mints a short run and delegates only declared operations. | `view` |
| Mounted connector/MCP tool | Resolve connector declaration from snapshot and invoke through the broker adapter. | inherited caller origin |

## Plan 1 — broker and receipt gate

1. Publish typed broker, invocation/result, and receipt schemas plus stable
   denial/error codes.
2. Extend `RunStep` with the authoritative receipt fields, idempotency index,
   and terminal-state rules; migrate existing provider event logging without
   losing its diagnostic value.
3. Build the snapshot resolver and current-grant validator. Persisted App and
   connector declarations are authoritative; in-process registries are caches
   only.
4. Adapt Core resident tools first, then App operations, MCP, extension bridge,
   and mounted connector invocation. Extract a pure service only where an
   adapter would otherwise call a route handler.
5. Connect staging/approval to the broker so approval references, execution,
   denial, and invalidation are receipt-backed.
6. Add status/debug surfaces that can show capability, snapshot fingerprint,
   policy decision, and receipt identity without exposing sensitive payloads.

### Plan 1 acceptance gate

- A single declared capability produces equivalent allowed, denied, invalid,
  duplicate, and failure outcomes through resident, HTTP, MCP, and view
  surfaces.
- Cross-workspace calls fail before any adapter executes.
- Revocation or App/connector upgrade invalidates paused writes and proposals;
  reauthorization does not mutate the original snapshot.
- A repeated request and a restart between adapter acceptance and response do
  not duplicate an effect.
- Every Core/App/connector invocation has one traceable `AgentRun` and one or
  more authoritative `RunStep` receipts.

## Plan 2 — bounded QuerySpec and provenance

1. Define the public typed QuerySpec schema for only Core Entry/Track/App
   resources: allowed fields and edges, filters, sorting, projection, cursor,
   traversal depth, result/row caps, and cost ceiling.
2. Compile QuerySpec through the broker as a `read` capability. Enforce current
   scope and field/edge allowlists before graph access.
3. Return a normalized plan, `result_set_id`, graph/substrate revision, item
   provenance, redaction state, and receipt reference.
4. Make agent claim/debug surfaces point to the result set and receipt. Page
   context stays UI context; it is never mislabelled as a QuerySpec result.
5. Permit Apps to publish declared query capability descriptors only. They may
   not submit arbitrary resource/filter/traversal specs.

### Plan 2 acceptance gate

- Two users in two workspaces cannot infer each other's data through filters,
  pagination, relations, errors, counts, or provenance.
- Cost and traversal limits reject expensive specifications predictably.
- An agent factual claim can be traced to a result set, normalized plan,
  authorization decision, graph revision, and `RunStep` receipt.
- An App can use its declared query capability but cannot escape to the open
  Core QuerySpec surface.

## Delivery order and evidence

| Work package | Primary evidence |
| --- | --- |
| Broker schemas and `RunStep` migration | schema/unit tests and migration/restart test |
| Core and App adapters | four-surface equivalence contract suite |
| Connector adapter | mounted-MCP allow/deny/idempotency/revocation suite |
| Approval integration | approve/reject/expire/resume tests with receipts |
| QuerySpec compiler | scope, cost, provenance, and adversarial traversal tests |
| Release proof | clean Core/App artifact environment, two-user/two-workspace browser and API smoke run |

## Architectural guardrail

jvagent may choose a capability and stream its reasoning. It does not decide
whether the capability is currently authorized, execute effects outside the
broker, own a receipt, or rewrite a paused run's authority. Integral owns those
facts. That division keeps the superharness an application substrate instead
of a competing agent framework.
