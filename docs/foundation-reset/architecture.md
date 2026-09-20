# Architecture decision: governed modular monolith

**Decision ID:** FR-001
**Date:** 2026-09-20
**Status:** Modular-monolith direction accepted; module and contract details proposed for implementation qualification.
**Decision owner:** Integral product owner; implementation owners validate contracts against executable spikes.
**Scope:** Core runtime, authoring, execution, public extension surfaces, client experience and supporting documentation.

## Context and constraints

Core has useful graph, access, Content Profile, capability, extension, resident and durable-work implementations. Responsibility is distributed across `backend/app/services`, `backend/app/agentive`, HTTP handlers, view renderers and skill instructions. We will reuse verified capabilities while eliminating competing semantic and execution paths.

The current architecture document still contains Space-era terminology and historical deployment descriptions beside newer decisions. The replacement must distinguish target design, implemented behavior and proven release guarantees.

Preserve the existing jvspatial object-spatial contract until explicitly amended through a reviewed decision: rooted graph writes, typed edge semantics, walkers for multi-hop behavior, public endpoint/error/schema conventions, and domain-free Core. This plan is not permission to bypass existing invariants. No substrate code changes occur as part of planning.

A modular monolith means one versioned backend product with enforceable internal boundaries and a shared transactional persistence boundary. It does not mean one module, one conversation, or mandatory future microservices. The first supported deployment has one API worker and one coordinated durable executor in the same runtime; PostgreSQL and attachment storage remain infrastructure dependencies. A separately launched executor using the same artifact is a later qualified topology, not an implied launch guarantee.

## Decision

The model interprets intent and proposes actions. Core determines validity, authority, effects and evidence. Every entry point uses the same application contracts. Operational features do not depend on a model being available.

### Logical modules and ownership

| Module | Owns | Public interface | Must not own |
|---|---|---|---|
| Identity and policy | Principals, workspace membership, effective permissions, scoped authorization, policy revisions | Resolve scope, authorize capability/resource, explain access | App domain rules, model reasoning |
| Information | Records, stable type/field identities, relationships, attachments, comments, revisions and change events | Typed reads/writes within a unit of work; bounded graph traversal | Approval decisions, chat orchestration |
| Applications | Versioned definitions, profiles, compiler, capability registry, package lifecycle, migrations and customization provenance | Validate/preview/publish definitions; resolve commands/queries/views | Direct provider calls or separate record store |
| Execution | Work plans, obligations, approvals, attempts, leases, transactional command dispatch, receipts, outbox and recovery | Propose/authorize/execute/cancel/reconcile; inspect evidence | Domain-specific mutations or model-specific prompts |
| Query and projections | Governed query plans, aggregate semantics, projection freshness, retrieval and evidence envelopes | Execute bounded queries under identity and policy | A second source of authoritative business records |
| Intelligence | Resident sessions, model/harness adapters, skill selection, intent clarification, planning and explanation | Consume capability contracts and request execution | Direct persistence, bypass permissions, infer successful effects |
| Experience | Shared shell, forms, views, dashboards, design preview, approvals, progress and accessibility | HTTP/MCP/extension adapters and generated client types | Private execution machinery or divergent field semantics |

Implementation need not place frontend code inside Python modules. These are ownership boundaries across backend and frontend.

### Dependency rules

- Shared contracts contain only stable IDs, DTOs, error/evidence types and narrow protocols. They do not become a common business-service bucket.
- Identity/policy and information consume infrastructure ports and contracts, never intelligence or experience.
- Applications uses policy/information APIs. Command definitions are registered here; execution invokes handlers through a registry port, with scoped capabilities supplied by the composition root. Applications does not import the executor.
- Query/projections uses policy and public information/application read contracts. Information emits change facts without importing query consumers.
- Execution uses policy, information's unit-of-work port and registered command handlers. It has no dependency on the resident.
- Intelligence consumes application discovery, governed queries and execution. Experience adapters consume these same public module interfaces.
- Infrastructure implements storage, model, notification and external-system ports; `bootstrap` wires implementations. Dynamic loading is restricted to registered extension entry points.
- Cross-module private imports, direct writes to another module's records and callbacks that disguise cycles fail a dependency gate. Events describe committed facts, not a way to bypass the command contract.
- App packages use the public SDK only. Core must boot without domain packages. No employee/payroll/rental-specific convenience APIs in Core.

### Proposed source layout

```text
backend/app/
  bootstrap/                   # registration and dependency composition
  contracts/                   # small versioned shared vocabulary
  modules/
    identity/                  # public API, internal model/services, tests
    information/
    applications/
    execution/
    query/
    intelligence/
  adapters/http/               # @endpoint; schemas remain app/schemas
  adapters/mcp/
  infrastructure/              # jvspatial/provider/storage adapters
  schemas/                     # public transport models per current contract
frontend/src/
  shell/
  features/{authoring,operations,work,collaboration}/
  platform/{api,contracts,views,fields,extensions}/
sdk/python/integral_sdk/        # separately buildable supported SDK
examples/                      # independent external extension proofs
```

Names are a target, not a mandate to mass-move files first. Establish public seams and tests before relocation. Update import-sensitive discovery, serialization discriminators, guards, package resources and agent guidance in the same slice as each move.

## Canonical contracts

### 1. Information and field identity

- Retain Workspace → App → Track → Entry and the existing graph meanings. Map App to an operational application, Track to a typed collection, Entry to a record. Users need not learn persistence vocabulary.
- A field has a stable ID, display label, type, namespace, ownership and schema revision. New authoring clients create and preserve that ID; older manifests receive a deterministic compatibility ID when compiled. Rename changes the label/key mapping, not identity. System lifecycle fields and business fields cannot collide.
- A declared field resolves from its namespace even when its value is null; no
  reader may infer a different field from a populated fallback value.
- The compatibility adapter maps legacy Entry top-level attributes to the platform namespace and its `custom_fields` bag to the business namespace; it preserves existing record IDs and storage while adapters migrate to stable field IDs.
- Relations are typed references, with declared target and deletion behavior; no parallel JSON relation truth. A computed field is publishable only when Core has a deterministic evaluator and read-only projection contract; unsupported computed declarations fail during compilation rather than becoming writable JSON.
- Every write carries expected record/schema revisions where needed. The effective Content Profile publication version is the initial schema-revision source; entry creation stamps it, and a later write compares the submitted value against the current effective profile before applying. Conflicting changes produce structured conflicts, not silent overwrite.
- A read returns object identity, revision, permitted field values and relevant provenance. Source ownership for imported data is explicit: local authority, external authority or read-only projection.

### 2. ApplicationDefinition and compiler

The versioned definition includes entity types, fields, relations, constraints, commands, query capabilities, view bindings, routines, permissions, dependencies, migrations and requirement assertions. Content Profiles remain the schema/composition component; packages, installed instances and definitions remain distinct concepts.

Pipeline: interpret need → draft definition → resolve supported capabilities → validate → semantic diff/preview → authorize revision → materialize → verify requirements.

Compiler checks include relation targets, field existence/types, writable versus computed fields, valid view bindings, query completeness requirements, permissions, routine destinations/time zones, dependency compatibility and migration safety. Unsupported business behavior produces a precise limitation or extension requirement. Arbitrary generated Python does not execute as a side effect of conversational authoring.

A greenfield proposal and a packaged App compile to the same effective contract. Runtime customization records base package revision plus local changes. An upgrade uses a three-way merge; conflicts remain explicit before publication. Draft/publish stays a specialized authoring lifecycle, as in ADR-012, while sharing durable execution and evidence infrastructure.

### 3. Commands, transactions and effect receipts

A command descriptor declares typed inputs/outputs, permissions, effects, invariants, concurrency behavior and recovery class. Generic record edits are commands too; protected business state can be changed only through its declared command.

Each invocation binds principal, workspace, App instance, definition revision, logical operation ID, request hash, expected record revisions and authorization context. Scope is explicit and validated; malformed explicit scope must not silently fall back to a personal workspace. Resolve policy again at the effect boundary.

Local transactional effects, audit/outbox facts and the durable idempotency result commit in one supported unit of work. Concurrency-safe claiming is required; lookup-then-execute and process-memory fallback are insufficient. Reusing an operation ID with a different payload is a conflict.

External effects use an outbox, provider correlation IDs and reconciliation. Outcomes include `unknown_outcome`; never promise universal exactly-once delivery. Retry only when safe according to the adapter contract. Compensation is an explicit new operation and is not described as database rollback.

Receipts include attempted/applied changes, before/after revisions where permitted, effect IDs, errors, remaining obligations and verification evidence. Proposal, authorization, application and verification are distinct states.

### 4. Durable work and approvals

Work plans and their dependent obligations survive restart, including uncommitted authoring work. A plan has a revision and logical steps; attempts are separate from effect identity. An approval binds an exact semantic diff, targets, effect scope and revision. Replanning invalidates authorization for changed effects only.

Use the existing durable-work kernel as the consolidation candidate; do not introduce another scheduler or approval store. Inventory and reconcile current staging, prompt queue, capability broker, operation idempotency, migration runner and scheduled work before selecting canonical records.

Cancellation stops pending work; it does not erase committed work. Partial outcomes remain visible. Expired approvals and revoked access fail closed. A continuation reads receipts and outstanding obligations, not a synthetic 'please continue' instruction.

### 5. Governed query and projection

One query contract defines scope, field identity, filters, sorting, pagination, aggregation, time zone/date boundaries, policy and result completeness. App-protected records retain declared query capabilities under ADR-012. Agent discovery exposes available authorized queries rather than arbitrary storage inspection.

Entry business fields use qualified paths such as `custom_fields.rental_status` in every query surface. Platform `status` and business `custom_fields.status` remain distinct even when one value is null.

No aggregate silently counts a capped page. A partial result is marked incomplete; errors are not empty results. Tables, calendars, boards, dashboards and agent answers use the same definitions. Index/search/vector projections are rebuildable and carry freshness markers. Authorization applies to source records, joins, aggregates, cached results and cited evidence.

### 6. Extensions and skills

SDKs expose scoped reads, commands, declared query registration, migrations and view capabilities. Trusted server code is privileged installed code, not made safe merely by using a facade; installation policy must state that trust boundary. Untrusted executable packages require a separately designed isolation mechanism and are outside initial release.

Custom views use a constrained bridge, validated capability calls and workspace policy; no raw privileged tokens. Install/upgrade/pause/uninstall, signature/digest checks and revocation cover frontend assets as well as server code.

Skills describe intent interpretation, clarification and use of capabilities. They cannot redefine authority, invariants or execution state. Runtime skills are versioned product resources and are packaged/tested accordingly. External agents use MCP; the resident uses the same governed contracts without requiring network round trips internally.

## Options and tradeoffs

| Option | Benefit | Cost/risk | Decision |
|---|---|---|---|
| Keep adding fixes to existing service/tool paths | Small immediate changes | Semantic drift and duplicate execution remain structural | Reject as the foundation strategy; retain urgent fixes only with regression tests |
| Rewrite the entire runtime at once | Clean initial layout | Discards working behavior and delays feedback; large migration risk | Reject |
| Adapt to explicit modules through end-to-end slices | Reuses proven assets; boundaries can be checked continuously | Temporary adapters need disciplined removal | Adopt |
| Split into microservices now | Process isolation and independent deployment | Distributed transactions, more operations and failure modes | Defer until measured need |

The tradeoff is more up-front contract design and stricter extension rules in exchange for predictable operation. Flexibility exists inside a published capability envelope. 'Without fail' becomes measurable correctness and recovery, not a claim that models or external systems never fail.

## Consequences and decisions to qualify

- Preserve Python/React/PostgreSQL/jvspatial and the replaceable resident binding initially; changing those is not prerequisite to fixing responsibility boundaries.
- Prove transaction participation across jvspatial nodes, edges and durable records before committing to the write contract. If a framework capability is missing, add a supported framework capability or revise the transaction design with an ADR; never bypass the current driver restrictions silently.
- Keep a single supported production topology until shared admission, invalidation and event delivery are demonstrated. SQLite remains a development option only if contract parity is explicitly tested; otherwise label its limitations.
- Do not require moving all customer data into Core. Define source ownership and connector reconciliation, but defer a universal federation engine.
- Performance budgets, supported model/provider matrix and dataset scale are frozen from measurements in WP-00; budget failures block qualification rather than silently changing targets.
- Default migration assumption: preserve real customer data and package identity; disposable development databases may be reset only after explicit identification as disposable. A documentation reset never implies data deletion.
