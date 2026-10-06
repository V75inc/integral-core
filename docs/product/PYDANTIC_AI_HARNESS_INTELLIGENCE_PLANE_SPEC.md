# Pydantic AI Harness and Integral Intelligence Plane Specification

**Status:** Draft for architecture review

**Date:** 2026-10-04

**Decision direction (revision 2):** compose Pydantic AI and Pydantic AI Harness capabilities for the resident lifecycle, planning, context and persistence. Integral owns tenancy, graph continuity, policy, work authority, observability contracts and usage facts. Prefer upstream behavior and bounded adapters to rebuilding the loop.

**Scope:** Requirements and delivery pathway. This document is not an implementation plan or a dependency approval.

**Implementation baseline:** [Pydantic AI implementation plan](PYDANTIC_AI_HARNESS_IMPLEMENTATION_PLAN.md) provides bounded packages, dependencies, migration and acceptance gates. It selects the existing PostgreSQL work kernel and LiteLLM SDK integration first; additional workflow/gateway services require demonstrated need.

## 1. Purpose and decision

Integral Core needs a first-party resident harness that is safe across many users and workspaces, can continue conversations and recover work, and provides trustworthy operational and financial evidence. Integral Business must be able to use Core's usage records for entitlements, quotas, cost allocation, and customer billing without rebuilding the instrumentation.

Use **Pydantic AI and Pydantic AI Harness** as a composed native harness, subject to a capability-by-route compatibility spike. The implementation plan §2.3 defines mandatory, optional and deferred surfaces; its revision 3 retains the broader adoption decisions and defines coding-agent execution; these decisions take precedence over earlier primitive-only language. Build an **Integral Intelligence Plane** around it. The plane is a Core-owned set of contracts and services, not a second model framework:

1. **Harness runtime:** turn control, model invocation, typed tool request/response, streaming, context construction, and resume/cancel behavior.
2. **Conversation and session service:** graph-backed Integral threads and messages; runtime session state and provider continuation references.
3. **Capability plane:** canonical capability snapshot, tool discovery, policy check, approval/staging, idempotent invocation, and receipts.
4. **Observability plane:** correlated operational traces, events, metrics, redaction, retention, and operator diagnostics.
5. **Usage and commercial metering plane:** append-only model/tool/compute usage events, reconciliation, aggregation, and quota signals. Integral Business owns price plans, entitlement decisions, invoices, and customer-facing billing policy.

Pydantic AI is not the source of truth for tenants, messages, authority, usage, or billing. LiteLLM remains a strong initial provider gateway and cost-observation source because it is already a Core dependency through jvagent and Pydantic AI documents a LiteLLM integration. Keep it behind a `ModelGateway` interface so Core is not locked to LiteLLM internals.

## 2. Current baseline and concrete gaps

Current code already provides useful foundations:

- `ChatThread` and `ChatMessage` are graph `Node`s. Threads carry `user_id`, `workspace_id`, provider and agent references; messages connect under the thread with `CONTAINS`.
- `AgentRun` and `RunStep` are currently `Object` records in `services/execution_runs.py`, and runs capture a capability snapshot. Steps record capability decisions, approvals, idempotency, and results.
- Chat currently maps `ChatThread.provider_session_id` 1:1 to jvagent session. The current chat concurrency registry and stream maps are process-local; `docs/backend/ai-chat.md` explicitly documents the single-worker constraint.
- `backend/pyproject.toml` currently pins jvagent rc20 and `litellm>=1.101.0,<1.102`; comments identify LiteLLM as jvagent's only model backend. OpenAI SDK is separately used elsewhere.

The design must close these gaps before claiming multi-tenant production readiness or accurate commercial metering:

- Tenant isolation and thread authorization cannot depend on scalar `user_id`/`workspace_id` filters alone. They must be checked against the graph ownership edges and current workspace policy at every read, resume, tool call, export, and billing query.
- A provider session ID is a continuation handle, not Integral conversation identity. Provider state may be missing, deleted, or change during migration; Integral must own the transcript and continuity contract.
- In-process locks do not enforce one active turn per thread across workers. Session concurrency needs a shared, durable coordination primitive.
- Usage totals on a completed Pydantic AI run are valuable but are not sufficient for billing. Cancelled streams may have incomplete usage; cost can be unknown; provider price tables change; model gateways can retry upstream. Persist raw per-request facts and accounting certainty, then reconcile.
- A trace is not a billable ledger, and a spend dashboard aggregate is not an invoice line. These records need separate contracts and retention.

## 3. Terms and identifiers

Use these meanings consistently:

- **Tenant:** the isolation/security boundary for data: normally an Integral `Workspace`. A user may be a member of many tenants.
- **Billing account:** the commercial payer or contract owner. It may cover one or more workspaces and is resolved by Integral Business, not guessed from `workspace_id`.
- **Conversation/thread:** a durable, user-visible sequence of messages and agent turns. Integral `ChatThread` remains canonical.
- **Session:** resumable runtime continuity for a thread and binding. It includes checkpoints, context summary versions, and zero or more external provider continuation references. A session is not the same thing as a run.
- **Run:** one bounded execution attempt initiated by a user, routine, event, or system action. A run may pause and resume; retries/continuations have explicit attempt or segment identity.
- **Step:** a model request, capability invocation, approval wait, retrieval, or other metered/observable unit within a run.
- **Usage event:** immutable facts for a billable or cost-bearing operation. It is not a mutable running total.
- **Provider request:** one outbound LLM/API request. Its provider request ID is retained when available for deduplication and reconciliation.

Identifiers are opaque, unguessable, globally unique, and never interpreted as authorization. Every query accepts and enforces an authorization context independently of supplied IDs.

## 4. Tenancy and isolation requirements

### 4.1 Tenant boundary

Every persistent harness record must carry or derive:

- `tenant_workspace_id` (nullable only for explicitly system-scoped operations);
- `principal_id` and `principal_kind` (human, system, or approved channel identity);
- trusted opaque usage-attribution reference when supplied by a commercial extension, with source/version; Core does not resolve customer billing accounts;
- `conversation_id`, `run_id`, and operation correlation IDs where applicable;
- data classification and retention class.

The workspace is the default isolation partition. Billing account grouping is not a data-access boundary. A billing account may aggregate records only for authorized commercial reporting; it must never let one tenant query another tenant's content or traces.

### 4.2 Authorization enforcement

- Resolve tenant scope from authenticated request state and Integral's workspace resolver; reject absent, malformed, stale, or unauthorized scope. Do not infer scope from thread IDs or provider metadata.
- For graph entities, verify access through canonical graph edges and the current permission resolver. Cached `user_id`/`workspace_id` values aid lookup only.
- At run creation, bind a signed/immutable execution context to the run: principal, facet, workspace, app focus, capability snapshot fingerprint, policy version, and model policy. It is audit context, not a permanent authorization grant.
- At every tool call and every resume, re-check current access, policy, connector credentials, staging requirements, and capability availability. Revocation while suspended takes effect before resumption.
- Tool code receives a least-authority `RunContext` with principal/workspace and a capability broker handle. It must not receive ambient database authority, cross-tenant service clients, or arbitrary framework state.
- App skills and toolsets are resolved per `(workspace_id, principal_id, app_id, profile_version)`. Never share mutable agent instances, toolsets, context variables, or conversation histories across tenants. Cache only immutable profile artifacts with complete tenant/version cache keys.
- Provider prompts and provider conversation IDs are tenant data. Do not place them in globally shared caches, labels, public URLs, or unredacted logs.

### 4.3 Isolation threat tests required before release

Demonstrate, across separate users, workspaces, workers, and billing accounts, that guessed thread/run/step/trace IDs cannot read or mutate another tenant's data; stale provider IDs cannot cross-link sessions; cached skills/tools cannot bleed into another tenant; pagination/search/list counts do not reveal foreign records; and an organization member's current role changes take effect on a resumed run. Exercise exports, support tooling, analytics, and deletion paths as well as the chat API.

## 5. Conversation, session, and graph persistence

### 5.1 Source of truth

Keep `ChatThread` and `ChatMessage` as the source of truth for product conversation history. Reuse the current rooted workspace chat catalogue and thread ownership/access model; `ChatThread → ChatMessage` uses `CONTAINS`. Verify the exact existing graph edges before migration rather than infer them from cached user/workspace fields. Continue using the transcript envelope needed by the frontend, with provider metadata isolated and minimized.

Do not make Pydantic AI's serialized message format the storage schema. Write explicit, versioned converters between Integral's canonical message parts and Pydantic AI's model messages. Preserve tool calls/results, multimodal references, usage references, and correlation metadata needed to reproduce or inspect a run. Sanitize all client-supplied history; only server-loaded, validated Integral messages enter a model request.

### 5.2 Session graph model

Add graph participants only through a substrate-touching plan that satisfies I-GRAPH-01. Candidate model (names provisional):

- `HarnessSession` node: one current runtime session for a `ChatThread` and binding; tenant/principal cache fields; active/suspended/closed state; checkpoint schema/version; last activity; retention/deletion state.
- `RuntimeCheckpoint` Object: encrypted immutable operational record used by a custom Harness StepStore. Reference it from the graph session with validated scalar metadata; it is not a graph participant and has no graph edge. Keep framework formats in a versioned private payload with public transcript converters and a capability-state manifest; never put large serialized state directly on a hot graph node.
- `HarnessProviderBinding` or binding reference: provider kind/version and opaque continuation reference. Secrets and plaintext credentials never belong in session nodes.
- Edges: thread-to-session uses a named typed edge. Runs/checkpoints stored as `Object` records use validated scalar references rather than graph edges. A new node must attach to a rooted parent in the same transaction. Avoid a floating session sidecar.

One thread may have multiple historical sessions over time (provider switch, session reset, migration) but one active session per binding unless a documented product use case requires concurrent branches. One session has many runs; a run may have multiple execution segments/resumptions. Enforce uniqueness and transitions transactionally.

### 5.3 Session lifecycle and continuity

Define and persist transitions: `active → suspended → active`, and terminal `closed`, `expired`, `revoked`, or `failed`. Session close must not delete the conversation. Thread archive/delete and tenant deletion have explicit retention semantics across transcripts, checkpoints, provider state, traces, and usage.

On provider switch, load Integral history and rebuild supported context; do not require the prior provider to remain available. Checkpoint restore is versioned and can be rejected with a recoverable migration path. Provider continuation handles are optimization hints only.

## 6. Run lifecycle, durability, and orchestration

### 6.1 Pydantic AI boundary

Compose public Pydantic AI/Harness capabilities for typed interaction, planning, progressive discovery, skills loading, context strategies, instrumentation and StepPersistence. Keep the Integral controller thin: work coordination and authority rather than a second model/tool loop. Build custom runtime behavior only for a documented compatibility or requirement gap. Build dynamic toolsets from the run's immutable capability snapshot. Each tool is a thin adapter that submits a request to Integral's broker and returns a normalized result; it does not call substrate services directly.

Use `UsageLimits` and Harness `SpendLimits` as runtime guards. SpendLimits supports scoped/shared stores, but threshold admission does not reserve in-flight spend. Integral enforces atomic reservations and captures each physical/auxiliary request through the gateway. Unknown prices remain unknown; the framework's unpriced-as-zero option cannot settle a Core charge as free.

### 6.2 Core-owned run state

Extend the existing `AgentRun`/`RunStep` records or introduce a formally versioned run store. Required run attributes include:

- tenant, principal, trusted opaque attribution reference, thread/session, origin and initiating event;
- provider/model route and immutable model policy snapshot;
- capability snapshot ID/fingerprint, prompt/skill/profile versions, runtime version;
- status, attempt/segment, start/update/terminal timestamps, deadline, cancellation state;
- checkpoint reference/version, provider continuation reference (encrypted or opaque), failure classification;
- request, token, tool, elapsed-time, and estimated-cost summaries as cached rollups only;
- trace ID, event sequence watermark, and idempotency scope.

`AgentRun`/`RunStep` currently subclass `Object`, which aligns with I-GRAPH-02 for operational records. Keep these records queryable by strict tenant-scoped service methods. Promote something to a graph `Node` only if it truly participates in graph traversal, ownership, permissions, or lifecycle relationships and a rooted edge plan is defined.

### 6.3 Durable execution and concurrency

Reuse Integral's existing PostgreSQL WorkItem/WorkApproval/outbox kernel, lease fencing and recovery services. Use Harness StepPersistence through a scoped, encrypted jvspatial StepStore and versioned checkpoint reconstruction. Map each physical framework invocation to a unique run ID and the Core logical run/attempt/segment. Message snapshots do not restore all capability or workspace state: every enabled capability needs a restore/reset/rebuild policy. Only consistent settled snapshots automatically resume; reconcile interrupted effects before reuse. Framework effect records remain observations subordinate to Core receipts. Prototype this boundary before selecting a dependency. Temporal/DBOS or another engine is a later architecture decision only if the existing kernel demonstrably cannot satisfy a required behavior; do not introduce a second work authority by default.

The authoritative user-visible lifecycle remains Core-owned. Framework checkpoints are subordinate and referenced from Core. Activities that can cause effects must route through idempotent Core operations; replaying a workflow may not repeat an effect. Durable replay must not replay external calls blindly; each call uses an idempotency receipt and a recorded result.

Turn lock: enforce one active run per conversation/session by database or shared durable lease with fencing token, not an in-process dictionary. A stale worker must be unable to append events or finalize after losing the lease. Parallelism limits are tenant/principal-wide and shared across workers.

### 6.4 Broader adoption constraints

Skills discovery reads only the authorized profile; include/exclude filters are not an access boundary and instruction loading does not execute scripts. Adapt Harness memory/search to existing scratch/promotion and owner-filtered conversation services with provenance and retention. Compaction preserves protected obligations and receipts outside summaries and rebuilds context after revocation. Code Mode is an optional measured read-tool pilot: broker and meter every nested call, disable eager/speculative execution initially, and exclude deferred-approval tools until continuation is qualified. Shell/browser/connectors use isolated execution and scoped credentials. Subagents, dynamic workflows and runtime capability creation are deferred architectural expansions under ADR-003 and App trust rules.

The §2.3 adoption matrix, §5 composition contract and WP-00 proof in the implementation plan are authoritative for delivery. API names are provisional until exact packages are pinned.

Integral Core skill files follow the full Agent Skills standard without JV frontmatter extensions. Implementation plan §2.5 requires hyphenated names, standard field types, migrated file references and runtime resource/execution conformance; no inheritance or Action dependency is retained through a native adapter. Tool/App authority stays in the broker and manifests.

## 7. Capability and policy plane

- Canonical capability catalogue comes from the current manifest/broker and includes schema/version, operation class, policy action, source App/version, staging requirement, and metering category.
- At run creation, resolve and freeze the allowed catalogue. At each invocation, re-check current authorization and approval state; a snapshot aids deterministic replay and explanation but cannot freeze permission.
- Model-produced tool arguments are untrusted. Validate with typed schemas, canonical IDs, scope constraints, and output size limits before dispatch.
- Effects flow through prepare → bless → execute as specified in `RESIDENT_HARNESS.md`. Approval creates a suspended run state when continuation is needed. Approval identity binds to the exact effect payload and capability version. Edits invalidate prior approval.
- Each invocation has a stable idempotency key derived by Core from run/step/capability/approved effect identity. Framework retries reuse the same logical key; new user intent gets a new key.
- Tool return values are sanitized, bounded, and tagged with trusted provenance. A textual model statement that a tool ran is not evidence; only a Core receipt is.

## 8. Observability specification

### 8.1 Correlation model

Every request, stream event, model request, tool step, durable activity, and usage event should carry compatible IDs: `tenant_workspace_id`, `principal_id`, `conversation_id`, `session_id`, `run_id`, `run_segment_id`, `step_id`, `provider_request_id`, `trace_id`, and `idempotency_key` as applicable. IDs are not authentication tokens.

Emit OpenTelemetry-compatible spans/events. Pydantic AI/Logfire instrumentation may be used as a source or development UI, but production's canonical trace interface should be exportable through OpenTelemetry to the deployment's chosen backend. LiteLLM proxy logs/callbacks are correlated by request/trace metadata, not treated as the complete Integral trace.

### 8.2 Required events and measurements

Capture timestamps and outcome for run accepted, queue wait, context assembly, each model request, each tool invocation, policy/approval decisions, checkpoint write/restore, stream disconnect/reconnect, cancellation, and finalization. Include model/provider/route, retry/fallback count, latency, tokens by modality/cache category, tool name and result class, prompt/profile versions, and spend status.

Metrics should include completion/failure/timeout/cancel rate, time-to-first-token, model/tool latency, queue wait, retries/fallbacks, tokens and estimated cost, tool-call efficiency, approval wait, recovery/resume rate, unknown-usage fraction, provider/gateway reconciliation drift, and budget enforcement outcomes. Use low-cardinality labels (provider, model family, operation, status); keep user/workspace identifiers in access-controlled traces and billing tables, not Prometheus labels.

### 8.3 Privacy and operator access

Default production telemetry records metadata, token/usage values, bounded result summaries, and redacted error classes—not full prompts, completions, or tool payloads. Payload capture is opt-in, encrypted, time-limited, purpose-bound, and tenant-admin authorized. API keys, OAuth secrets, authorization headers, raw MCP credentials, sensitive personal fields, and unredacted attachments must never be emitted. Support personnel require audited, scoped access; aggregate analytics must not reveal other tenants' content.

Trace retention, checkpoint retention, transcript retention, and financial-ledger retention are separate policies. Tenant deletion purges or cryptographically erases content-bearing traces/checkpoints according to retention obligations while preserving only the minimum legally/financially required non-content accounting records.

## 9. Usage accounting and Integral Business metering

### 9.1 Accounting principle

Persist **one immutable usage event per metered operation** and derive aggregates from those events. Never bill from prompt token estimates, UI state, an in-memory run counter, a Pydantic AI total alone, or a mutable LiteLLM dashboard total. Separate:

1. **Observed usage:** provider/gateway-reported counts and request identity.
2. **Estimated provider cost:** pricing-table calculation with version/source and uncertainty.
3. **Reconciled provider cost:** matched to gateway/provider settlement or invoice evidence.
4. **Customer billable quantity/amount:** Integral Business tariff, contract, entitlement, and rounding policy applied to immutable usage events.

These amounts differ legitimately for platform-paid versus BYOK credentials, retries, discounts, markup, commitments, and bundled plans.

### 9.2 Append-only usage event

Create a versioned `UsageEvent` record (likely an `Object`/ledger table rather than a graph `Node`) with at least:

- `usage_event_id`, schema version, event type, unique idempotency key, source system and source event ID;
- tenant workspace, principal, trusted opaque attribution, capability/App provenance, conversation/session/run/step, origin; Business supplies product/account mapping;
- provider, gateway, model requested, model served, route/deployment, credential mode (`platform`, `workspace`, `user BYOK`), provider request ID;
- operation and modality (LLM input/output, cached input, audio, image, embedding, rerank, tool/connector, storage/compute if metered);
- raw provider usage JSON (redacted, minimal) plus normalized decimal/integer quantities with unit and tokenizer/provider source;
- currency, rate-card ID/version, estimated cost as high precision Decimal, cost basis, billable quantity and pricing-rule version when assigned;
- timestamps for requested/started/completed/recorded, ingestion source, reconciliation status, correction/reversal reference;
- trust/completeness status (`provider_reported`, `gateway_reported`, `estimated`, `partial_cancelled`, `unknown`, `reconciled`, `disputed`).

Events are append-only. Corrections are compensating events linked to original records; never rewrite usage facts already used for an invoice. Use database decimal numeric types or integer micros with explicit scale, never binary float for money. Preserve raw units and round only at the billing rule's declared boundary.

### 9.3 LLM request-level capture

For each provider request capture prompt/input and output tokens; cached read/write counts; audio/image tokens or duration where billable; request count; provider request ID; finish/cancel reason; retry/fallback lineage; latency; rate-card version; and usage source.

Pydantic AI's `RequestUsage`/`RunUsage` provides normalized request and run usage and best-effort price calculations. Use those for live budget guards and cross-checks. Do not use best-effort cost as invoice truth where provider rates, discounts, batch multipliers, cache pricing, or proprietary usage fields are unknown.

LiteLLM supports proxy spend attribution and SDK callbacks. Prefer all model traffic through one Core-owned `ModelGateway` abstraction, initially using the already-installed LiteLLM SDK where attempt-level accounting can be proven. Add a Proxy adapter when deployment requirements justify it. Attach Integral IDs as trusted metadata at the gateway boundary, preserve upstream request IDs, persist capture intents and success/error observations, and make ingestion idempotent. Never trust a client-supplied `user_id`, `team_id`, workspace, or spend tag to determine attribution.

### 9.4 Accuracy, retries, cancellation, and reconciliation

- Provider final usage is authoritative when available; gateway data is a second observation; local estimates are explicitly lower confidence.
- Retries are separate provider requests and separate usage events even if the user sees one logical turn. Fallback model requests are separately attributed. Deduplicate event ingestion by gateway/provider event identity, not by run ID.
- Canceled streams can omit final usage or may continue consuming provider-side resources. Record partial usage and mark it incomplete; later reconcile. Apply configurable conservative reservation before dispatch and settle/refund the reservation after reported usage arrives.
- Unknown pricing is not zero. Mark the cost unknown, prevent “fully metered” claims, and optionally fail closed for paid/unmetered models according to Business policy. Model price updates are versioned, and retroactive rate changes create recalculations/adjustment events rather than silently rewriting history.
- Reconcile gateway captured requests to Core usage events and provider invoices/API usage exports on a schedule. Track capture rate, missing IDs, duplicates, price mismatch, and unmatched provider line items. Alert when unexplained mismatch exceeds configured thresholds.
- BYOK: record tokens and relevant non-secret metadata for product analytics if contract/policy allows, but record provider cost as customer-paid/unknown-to-Integral unless actual cost evidence is available. Never charge customer for provider charges made directly on the customer's own key absent an explicit contract.
- Non-LLM costs (speech, embeddings, search, connector API, storage, compute) use separate event types and units. Do not imply model-token metering covers all variable cost.

### 9.5 Business integration contract

Core exposes authorized, paginated, replay-safe usage feeds and aggregates with explicit dimensions, completeness, and watermark. Integral Business consumes normalized facts and applies its own pricing/entitlement rules. Core exposes quota/budget reservation decisions to the harness before expensive requests; Business remains the authority on plan entitlements and customer billing policy. APIs must distinguish provider cost from customer charge and estimated from reconciled amounts.

Minimum Business-facing aggregates: account/workspace/user/app/model/period totals; input/output/cache/audio/embedding counts; provider cost status; billable quantity; reconciliation status; and event watermark. Invoice close uses an immutable cutoff plus adjustments after close.

## 10. Model gateway and provider-key architecture

- Define `ModelGateway.generate/stream/cancel` and supported capability profiles independent of LiteLLM. Route policy chooses provider/model based on configured availability, capabilities, data residency, credential ownership, cost limits, and tenant settings.
- Use LiteLLM as an initial gateway candidate because Core already depends on the library and current jvagent uses it. Confirm whether the Business deployment runs the proxy or SDK; these are different operational models and spend attribution guarantees.
- Platform keys are held by a secret manager and never flow into tenant-visible objects. BYOK credentials remain encrypted under the current credential service, scoped to owner/workspace and resolved per request.
- Pin LiteLLM and Pydantic AI compatible ranges, run provider contract tests, and prevent direct model SDK calls from bypassing the gateway for harness-generated LLM requests. Existing non-harness Core use (e.g., embeddings) must either enter the same metering contract or be separately metered.
- Fail closed for a paid request when neither token usage nor a defensible cost state can be recorded, unless explicit policy allows best-effort service. Provider/gateway outage behavior must preserve queued work and avoid duplicate charges.

## 11. Versioning, migrations, and compatibility

- Canonical chat history and provider-neutral event types are versioned independently of Pydantic AI message objects.
- Session/checkpoint schema carries `runtime_provider`, `runtime_version`, `checkpoint_version`, `prompt_version`, `skill_profile_version`, and model/tool manifest versions. Provide explicit migration or safe restart from canonical transcript.
- Preserve current provider binding and `provider_session_id` during migration as an adapter-specific field. Do not reuse provider IDs as Integral IDs.
- Maintain jvagent adapter support during pilot. New Core surfaces remain harness-neutral. Retire jvagent-only skill conversion, tool namespace assumptions, lock deployment, and staging transcript hacks only as each replacement is proven and migration evidence is recorded.
- Keep Pydantic AI, LiteLLM, gateway, trace, and ledger versions in run metadata so historical incidents and billing can be reconstructed.

## 12. Required acceptance evidence

The harness is not production-ready until all of the following are evidenced:

### Security and tenant isolation

- Cross-tenant adversarial tests cover threads, sessions, runs, checkpoints, traces, usage feeds, exports, and support tools.
- Workspace membership/policy revocation blocks resumed runs and subsequent capability calls.
- No credential or sensitive payload leaks to logs, spans, metrics, errors, or other tenants.
- Multiple workers coordinate turns and rate/budget limits consistently; fencing rejects stale workers.

### Conversation and durability

- Pydantic AI messages round-trip to/from Integral's transcript format without dropping tool call/result or multimodal state required to continue.
- Provider switch and runtime upgrade resume from Core transcript or perform a safe restart.
- Approval wait, cancellation, worker crash, provider timeout, and duplicate delivery recover without duplicate side effects.
- A conversation/session can be exported and deleted under defined retention rules.

### Observability and usage

- Every model request and metered operation maps to exactly one Core usage event or an explicit capture failure record.
- Token counts, provider IDs, retry lineage, cost confidence, and attribution survive process restart and event replay.
- Reconciliation reports captured-request rate and unexplained cost variance against gateway/provider evidence.
- Dashboard aggregates reproduce from immutable events, with unknown/partial states visible.
- Pydantic AI instrumentation and LiteLLM callbacks are correlated to the same Integral run and step without storing secrets or unredacted content.

### Product efficacy

- On a fixed, representative evaluation corpus, compare jvagent and the new runtime for task success, evidence quality, repair behavior, unnecessary tool use, time-to-first-token, p95 completion time, provider cost, and recovery rate.
- Policy/staging violations, cross-tenant access, duplicate effects, and usage attribution to the wrong billing account are hard failures.

## 13. Phased pathway

### Phase A — capability composition and compatibility spike

Inventory current LiteLLM usage and whether deployment uses SDK or Proxy; enumerate all model calls including embeddings, speech, and external connector operations. Prototype the mandatory composed Harness capabilities against configured models through the proposed gateway, prove hook/state/store/meter boundaries, capture one tool loop and streaming trace, round-trip Integral transcript parts, and inspect cancellation/final usage behavior. Pin no production dependency until the supported APIs and licenses are verified.

**Exit:** signed-off provider matrix, transcript compatibility report, observed usage completeness matrix, isolation threat model, and baseline jvagent evaluation results.

### Phase B — contracts and ledger

Specify and implement provider-neutral events, model gateway, tenant-scoped run/session APIs, append-only usage schema, shared turn lease, and event/outbox ingestion. No default harness switch yet.

**Exit:** contract tests, tenant isolation evidence, idempotent usage ingestion, and reproducible usage aggregates.

### Phase C — shadow and read-only pilot

Run the Pydantic AI harness against the frozen task set and selected opt-in traffic with read-only capabilities. Shadowing must not create a second provider bill on real user turns unless explicitly budgeted; use replay fixtures or sampled offline tasks for comparison. Compare normalized traces and usage.

**Exit:** no security hard failures; efficacy and latency targets met; usage completeness/reconciliation threshold agreed with Business.

### Phase D — staged-write and durable resume pilot

Enable staged proposals for a limited pilot. Exercise approval, edit/revoke, resume, process restart, tenant role change, and duplicate delivery. Confirm every model and tool charge is attributed once or explicitly marked unresolved.

**Exit:** invariant evidence and Business sign-off on metering semantics.

### Phase E — opt-in deployment and commercial integration

Expose binding choice behind feature flags, connect Business entitlement/quota APIs, expose usage reports, establish rate-card change and invoice-close procedures, and preserve rapid rollback to jvagent.

### Phase F — default switch or retain dual harnesses

Switch default only if evaluation results and operations support it. Keep the provider-neutral binding if jvagent remains the better choice for some deployment. Remove jvagent-only dependencies only after compatibility and migration paths close.

## 14. Decisions bounded by WP-00/01

1. **Billing scope:** which operations are charged to customer plans (LLM only, or also embeddings/speech/search/compute)?
2. **Gateway topology:** deploy LiteLLM Proxy as a managed Integral service, use SDK callbacks, or support both with one normalized ingestion contract?
3. **Customer key model:** platform-paid, customer BYOK, workspace BYOK, or all three, and which modes are billable?
4. **Record residency/retention:** which provider payload metadata and checkpoints may be persisted, in which database/region, and for how long?
5. **Durability fit:** existing PostgreSQL work kernel plus custom Harness StepStore is the baseline. Name any failed recovery behavior before proposing another engine.
6. **Multi-tenant deployment target:** shared database with strict workspace scoping, database/schema per tenant, or both by deployment tier?

Where unresolved, default to shared database with strong application-level tenant authorization, encrypted provider/checkpoint payloads, gateway-reported usage plus independent provider reconciliation, and no customer charge for unknown cost.

## 15. Primary source references

Repository source of truth:

- `backend/app/models/nodes.py` — `ChatThread`, `ChatMessage`, `ConversationContext`.
- `backend/app/agentive/services/execution_runs.py` — Core `AgentRun`, `RunStep`, capability snapshots.
- `backend/app/services/chat_providers/jvagent_provider.py` — primary embedded streaming provider; `backend/app/agentive/connectors/jvagent_connector.py` — legacy HTTP connector.
- `backend/pyproject.toml` — current jvagent and LiteLLM dependency pins.
- `docs/backend/ai-chat.md` and `docs/backend/adr/005-single-worker-until-shared-turn-state.md` — session mapping and concurrency constraint.
- `docs/product/RESIDENT_HARNESS.md`, `docs/backend/workspace-agent-profile.md`, and `docs/INVARIANTS.md` — resident, skill, and graph invariants.

Current primary product documentation to revalidate at implementation time:

- [Pydantic AI Harness catalogue](https://pydantic.dev/docs/ai/harness/)
- [Step Persistence](https://pydantic.dev/docs/ai/harness/step-persistence/)
- [Spend Limits](https://pydantic.dev/docs/ai/harness/spend/)
- [Pydantic AI overview](https://pydantic.dev/docs/ai/overview/)
- [Pydantic AI models/providers, including LiteLLM](https://pydantic.dev/docs/ai/models/overview/)
- [Pydantic AI usage, token fields, estimated cost, and limits](https://pydantic.dev/docs/ai/api/pydantic-ai/usage/)
- [Pydantic AI persistence](https://pydantic.dev/docs/ai/core-concepts/persistence/)
- [Pydantic AI durable execution](https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/)
- [LiteLLM spend tracking](https://docs.litellm.ai/docs/proxy/virtual_keys)
- [LiteLLM spend capture rate](https://docs.litellm.ai/docs/proxy/spend_capture_rate)
- [LiteLLM budgets and rate limits](https://docs.litellm.ai/docs/proxy/users)

Pydantic AI reports usage normalized across model requests and provides best-effort pricing; cancelled-stream usage can be incomplete. LiteLLM documents proxy spend tracking and attribution for configured keys/users/teams. Integral must validate both behaviors against the exact pinned provider versions and deployment mode before relying on them for commercial billing.
