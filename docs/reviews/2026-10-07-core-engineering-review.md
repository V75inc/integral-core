# Integral Core engineering review — 7 October 2026

## Verdict and scope

The connector write path has tenant-isolation and schema-integrity defects that should block its production qualification. Native chat also remains expensive in an established conversation, and interrupted model requests lose known usage from the aggregate accounting summary. These are operational issues, not cosmetic cleanup.

This review examined the working tree based on `da059fbf` on `codex/pr-113-staging`: resident harness composition, model transport and observations, history and discovery, broker and staging, filing/scaffolding skills, graph writes, operational-model compilation, view contracts and extensions, the Python SDK, connector synchronization, and browser presentation. Concurrent stream-recovery changes were already staged when the review began; they were preserved. This is a targeted architecture and source review with selected executable checks and a live browser trace, not an exhaustive security audit or qualification of every connector and provider.

Branding was implemented during this review. The current approved artwork is the **frameless split-square**, from `docs/branding/logo-exploration-2026-10-06/`; the earlier O16 artwork remains provenance. Application marks, native-agent avatar, activity marks, authentication backdrop, theme variants, favicon, touch icons and manifest icons use that geometry. Customer avatars and tenant-specific logos retain their own identity.

## Ranked findings

### R1 — P1: Connector upserts can select another workspace's record

**Evidence:** `backend/app/services/connectors/base.py:96`, `backend/app/services/connectors/sync_runtime.py:398`, `backend/app/agentive/connectors/gmail.py:156`, `backend/app/agentive/connectors/quickbooks.py:152`.

The default key is a hash of connector **type** and upstream record ID, not connector instance or Integral workspace. Gmail's override likewise uses its upstream thread ID; QuickBooks includes the upstream realm but still not the Integral binding. The runtime searches all `Entry` records by that key, chooses `existing[0]`, and updates it without checking that it belongs to the current bound track/workspace and connector provenance. Two customers legitimately connecting the same upstream source can collide. The second workspace may receive no independent record while the first workspace's record is updated by the second sync.

**Confidence:** high. Key collision and unscoped selection are source-confirmed; a pure key calculation reproduced the shared key. No destructive cross-tenant live test was performed.

**Remedy:** Make the runtime own a canonical namespace of workspace + connector instance + upstream key. Bound lookup and mutation to the resolved destination and matching provenance. Use an atomic unique upsert. Plan migration carefully: quarantine ambiguous existing collisions rather than blindly re-keying records or assigning ownership from whichever connector happens to sync next.

**Acceptance:** Same upstream record in two workspaces produces two isolated entries; two separate instances in one workspace have deliberate documented identity semantics; repeated and concurrent syncs of one binding produce one record; mismatched provenance cannot update an existing entry.

### R2 — P1: Connector writes bypass the canonical typed-entry contract

**Evidence:** `backend/app/services/connectors/sync_runtime.py:80`, `:476`, `:487`, `:493`; compare `backend/app/services/entry_create.py:223`, `:259`.

Updates directly assign fields and save. Creates construct `Entry`, save it, then wire `CONTAINS`. Routing uses `entry_type_key`, but this create path does not resolve/set `type_id` or wire `IS_OF_TYPE`. It also bypasses the canonical field validation, relation/tag graph reconciliation, protected-field handling and revision checks. Calling connector materialization hooks afterwards does not supply these missing contracts. Saving before attachment without a shared transaction also creates a failure window for an unrooted node.

**Confidence:** high, source-confirmed. No live vendor integration was qualified in this round.

**Remedy:** Route connector creates and updates through the same generic entry command service used by other substrate clients. Supply a connector actor, immutable scope, policy and provenance explicitly. Validate the schema, reconcile typed graph relationships, fence concurrent updates, and commit the entry, structural edges and durable event/receipt together. Vendor adapters should continue to map payloads; they should not own another persistence implementation.

**Acceptance:** Invalid fields and cross-workspace relations have no effects; synchronized entries work identically in typed filters, views and relation traversal; a failure between creation and attachment rolls back; protected fields and revision conflicts have the same semantics as API and App operations.

### R3 — P1: Scheduled and manual connector syncs can overlap

**Evidence:** `backend/app/services/connectors/sync_scheduler.py:56–74`, `backend/app/services/connectors/sync_runtime.py:530`, `backend/app/api/connectors.py:82`.

Scheduling eligibility depends on `last_synced_at`, updated after the pull. Dispatch is an untracked `asyncio.create_task`. An unfinished slow pull can be dispatched again on later ticks; separate API workers and manual sync create further overlap. The record path is a find-then-create sequence, so overlap can also create duplicates. There is no durable exclusive lease or fencing at this entry point.

**Confidence:** high, source-confirmed race; no live multi-worker fault injection performed.

**Remedy:** Reuse Core's durable work-item lease/fencing pattern for connector work, including manual triggers. Make record upserts atomic. Supervise tasks and shutdown rather than leaving detached work as the execution contract.

**Acceptance:** A pull lasting several scheduler ticks has one active owner; manual and scheduled triggers converge; two workers cannot write as simultaneous owners; an expired lease can be reclaimed and a stale worker cannot commit after reclamation.

### R4 — P1: Interrupted request totals discard known token and cost facts

**Evidence:** `backend/app/agentive/harness/litellm_model.py:333`, `:420`, `:432`; `backend/app/agentive/harness/model_observations.py:181–192`.

The transport captures provider usage/cost observations on cancellation and close. The aggregator, however, considers only transitions with outcome `responded`. If none exists, it increments unresolved count and skips all usage. A cancelled request with known usage is therefore persisted but omitted from aggregate spend.

**Executable check:** A synthetic cancelled physical request with **100 input tokens, 12 output tokens and USD 0.004 known cost** produced reported totals of **0 input, 0 output and null cost**, with unresolved count 1. The incompleteness flags were correct; the known partial totals were lost.

**Confidence:** high, reproduced. This does not imply LiteLLM drops all cost metadata; the defect is the aggregation rule.

**Remedy:** Separate execution outcome from accounting completeness. Reconcile authoritative known facts once per physical request across all terminal transitions. Preserve partial totals with explicit incomplete status. Keep unknown cost null and distinguish provider-reported, estimated and billable amounts. BYOK spend attribution must remain separate from Business invoicing.

**Acceptance:** Cancelled/failed streams retain known tokens and cost; later reconciliation adds missing facts without double counting; repeated observations and close/response races are idempotent; unpriced requests remain unknown, not free.

### R5 — P2: Repeated-read suppression breaks legitimate recovery and read-after-write

**Evidence:** `backend/app/agentive/harness/broker_tools.py:253–271`.

The broker records a read signature before execution. The same arguments subsequently return `repeated_read_suppressed`, regardless of whether the first call failed or whether a write changed the result. The guard is not a cache: it returns an error instead of data. This blocks a normal “read, add, verify” sequence and transient-error recovery, and can add another model repair round.

**Confidence:** high, source-confirmed behavior. The browser trace in R6 failed tool availability instead; it was not this guard.

**Remedy:** Use framework usage limits for bounded execution. If read deduplication is needed, cache only successful safe reads under immutable scope plus relevant resource/schema revision, return the cached data, and invalidate after mutations. Failed attempts must remain retryable according to the tool's error contract. No lexical intent gates are needed.

**Acceptance:** Empty query → create → identical query returns the new record; a transient failed read can recover; an unchanged duplicate can reuse a safe result; a real non-progress loop stops with a useful explanation and no hidden repair agent.

### R6 — P2: Restored history and fresh tool disclosure still waste model requests and context

**Evidence:** `backend/app/agentive/harness/pydantic_ai_compat.py:80–160`, `:313–335`; `backend/app/agentive/harness/runtime.py:108`; `backend/app/services/chat_providers/pydantic_ai_provider.py:1239`; `backend/app/config.py:119`.

**Live browser input:** “Who is in my customer list?” in the existing Bike Repair Shop conversation and workspace, using `ollama_chat/deepseek-v4.1-flash:cloud`.

**Run:** `df8de984-3617-45e6-a92c-dd4f9f372777`.

| Metric | Observed |
|---|---:|
| Input tokens | 114,031 |
| Output tokens | 362 |
| Model requests | 4 |
| Tool steps | 3 |
| Tool retries | 1 |
| Peak input tokens/request | 30,406 |
| Backend run latency | 10.731 seconds |

The UI correctly returned Ana, the single saved customer. The trace first showed `integral_query_entries` rejected as **unknown**, with only discovery/history tools available; then `search_capabilities`; then a successful `integral_query_entries`. History retained a usable tool name while the fresh run did not retain that tool's availability. Recovery succeeded but consumed an extra request. Four calls repeatedly processed a substantial context for a one-record lookup.

Compaction currently clears older tool pairs at an estimated 32,768 tokens, retains five pairs and excludes `load_capability`. It does not, by itself, bound every instruction, loaded skill or prose-history component. The 600,000-token turn allowance is an emergency bound, not evidence of efficiency. There is no evidence that every workspace record is blindly injected; do not confuse workspace size with proven context composition.

**Confidence:** high for this performance and disclosure incident; medium for attribution of total spend to particular context components. No controlled cold/warm A/B comparison was performed in this round.

**Remedy:** Measure per-request context components, including instructions, catalogue metadata, active skills, history and tool payloads. Reconcile framework-recorded discovery/skill state with restored messages and current authorized tools; reuse it only while permissions, schema and capability revision remain valid. Keep fresh discovery when the task actually needs it without making stale tool names provoke avoidable errors. Bound tool payloads and review active skill retention through supported public library APIs. Preserve history rather than improving benchmarks by deleting it.

**Acceptance:** Replay the same natural request in cold, warm and long populated conversations; record physical calls and input/output separately; require accurate readback and no unavailable remembered-tool attempt. Establish measured per-task budgets from these baselines. Stop raising token ceilings as a substitute for reducing repeated input.

### R7 — P2: Compound record creation has a fragmented approval experience

**Evidence:** Existing browser conversation: request to add Ana, her blue Trek bicycle and a brake-repair job. Run `04b92a81-eb97-48cd-82e5-936aeffb5f27`: 10 model calls, 13 tool steps, 211,620 total tokens. See `backend/app/agentive/staging.py:431` and `:1907` for existing batch-reference support.

The user asked for a coherent related set. The interaction backed down to staging Ana alone, then required another proposal for the bicycle before the job. Ana was saved after approval; the bicycle was subsequently rejected, so the current workspace has no bicycle/job from this request. The substrate already has backward batch references; this should not be described as a missing batch primitive without further tracing.

**Confidence:** high for observed UX; medium for the precise planner/stager boundary responsible. This was a reinspection of existing browser evidence, not a newly repeated write test.

**Remedy:** Make existing dependency-aware batches usable through one concrete proposal, one approval and one receipt for a compound request. Validate symbolic references before effects and resolve actual IDs during execution. Preserve tenant checks, exact approved snapshots and partial/unknown-write reconciliation. Do not introduce a second LLM planner or global “yes” parser.

**Acceptance:** Ordinary language creating three related records yields one understandable preview/decision, saved relations and readback. Rejection writes nothing; changed design gets one new concrete proposal; failure cannot silently replay prior effects. Follow with queries, updates, deletion and ambiguous attachment filing.

### R8 — P2: Filing normalization silently drops unrecognized optional fields

**Evidence:** `backend/app/agentive/tooling/stagers_filing.py:41–91`, `:375`, `:448`.

The normalizer uses canonical schema keys, names and a global English alias list. Keys that still do not match are omitted without reporting them. Required-field validation can catch some omissions, but optional supplied data disappears before proposal/execution. Multiple source keys mapping to one canonical key can also overwrite each other. A schema-grounded operation should not silently decide that supplied information is expendable.

**Confidence:** high, source-confirmed.

**Remedy:** Use stable schema field keys/IDs and explicitly declared aliases. Return structured unknown/collision errors before staging, allowing the primary model to correct against the current schema or clarify an ambiguity. Avoid domain-word dictionaries in a domain-neutral substrate. Preserve the distinction between omission and an explicit clear where updates support it.

**Acceptance:** An unknown optional field cannot silently vanish; conflicting aliases trigger clarification; labels in another language work through declared schema metadata; schema revision changes invalidate the proposal before writes.

### R9 — P2: Extension-view failure state survives a change of view identity

**Evidence:** `frontend/src/components/views/ExtensionViewWidget.tsx:69`, `:110`, `:119`, `:131`, `:153`; `frontend/src/views/registry.tsx`.

The query key changes with app, view and workspace. The local `failed` flag only changes to true; it is not reset for a new identity or successful retry. React can reuse this widget for another extension view, leaving a healthy destination stuck on the prior fallback.

**Confidence:** high from lifecycle inspection; no failing iframe scenario was injected in this browser round.

**Remedy:** Scope failure state to app/view/workspace identity and add an explicit retry/renew lifecycle. Keep iframe origin, sandbox, handshake and operation authorization intact.

**Acceptance:** Failed view A → healthy view B renders B in the same mounted widget; workspace changes do not reuse authorization; handshake expiry and user retry recover without widening permissions.

### R10 — P2: An empty extension declaration set disables reference validation

**Evidence:** `backend/app/services/operational_model_compile.py:116–135`, `:1363–1364`, `:3715`.

The compiler rejects an unknown extension key only when the known-key set is truthy. An authoritative empty declaration set therefore accepts a view key that cannot resolve at runtime. The helper also collapses “context unavailable” and “known empty” into the same set.

**Confidence:** high, source-confirmed condition. Standalone compilation's external-registration contract needs to be preserved deliberately.

**Remedy:** Distinguish unknown compile context from an authoritative allowlist, including an empty one. Full App compilation must reject undeclared references; standalone compilation must receive or explicitly declare its external registry context.

**Acceptance:** App with no extension declarations and a referenced extension key fails before install; valid declarations compile; standalone external-view handling has a documented, tested contract.

### R11 — P2: Public SDK typing omits a supported optimistic-concurrency argument

**Evidence:** `sdk/python/integral_sdk/context.py:39–41`; `backend/app/services/app_operations/context.py:203–209`, `:307–308`.

The runtime's `update_entry_fields` supports `expected_record_revision`; the public protocol omits it. External Apps cannot use that guard through the advertised typed SDK surface without bypassing typing. Tests asserting exported type names are insufficient to prove signature parity.

**Confidence:** high, source-confirmed mismatch.

**Remedy:** Align the public protocol with the supported keyword and conflict result contract. Add signature/behavior conformance tests and compile a minimal external App using only `integral_sdk`, without Core imports.

**Acceptance:** A typed SDK client supplies an expected revision; stale writes are rejected; current writes succeed; public documentation and runtime semantics agree.

### R12 — P3: Legacy documentation and guard accumulation obscure the intended architecture

**Evidence:** Root and agentive `AGENTS.md`; `backend/app/services/request_scope.py:21` versus `:224–233`; `backend/app/agentive/tooling/scaffold_build.py` legacy prose checks; `backend/app/agentive/harness/broker_tools.py` per-tool limits; frontend lint output.

Some architecture instructions still describe jvagent as the default or an absent agentive kill switch. Scope documentation describes bare headers falling back, whereas current resolution rejects invalid supplied headers. Scaffolding retains prose-based compatibility checks, although structured blueprints bypass important legacy checks. Tool-specific broker quotas coexist with framework limits. Frontend ESLint reports 453 warnings, including hook and stability concerns. These are maintenance signals, not proof that every warning or large file is a defect.

**Confidence:** high for documented drift; medium for which legacy branches can now be retired.

**Remedy:** Update documentation from the current binding/configuration; inventory actual legacy callers before removal; consolidate loop limits around framework semantics and typed tool errors. Prioritize warnings that alter closure/lifecycle behavior, then prevent new warnings in touched areas. Avoid a sweeping abstraction layer or cosmetic rewrite.

**Acceptance:** One accurate architecture/operations narrative, tested migration boundaries for legacy callers, no unsupported kill-switch instructions, and a tracked warning baseline with no new lifecycle warnings.

## Architecture diagnosis

The native runtime **does use Pydantic AI and the separate Pydantic AI Harness package**. It composes `pydantic_ai.Agent` with public Harness capabilities rather than replacing the model loop with an Integral loop. `backend/pyproject.toml:104` pins `pydantic-ai-harness[skills]==0.36.0`; `backend/app/agentive/harness/pydantic_ai_compat.py` is the library-facing boundary. `runtime.py` installs instrumentation, deferred disclosure, step persistence, bounded tool-result compaction, conversation search and approved skills. Planning is supported separately. No private `pydantic_ai._…` imports were found in the reviewed integration.

Core should continue to own immutable tenant/principal scope, credentials and BYOK, permissions, schema and graph semantics, staging/approval snapshots, durable effects and spend attribution. The library should own agent execution, tool schema/disclosure, retries within bounds, skill mechanics and supported persistence/compaction hooks. The right corrective work is to repair those boundaries, not add another orchestration framework.

| Agent layer | Assessment and evidence |
|---|---|
| Instructions | Active skill/state guidance is centralized; persistent loaded instructions and broad catalog/history cost need component measurements (R6). |
| Session history | Scoped persistence is present; remembered tool names can outlive fresh-run availability (R6). |
| Long-term memory | Reviewed step-store and conversation-search paths are tenant/principal/thread/session scoped; no demonstrated cross-tenant chat-memory leak. Connector record isolation is a separate confirmed concern (R1). |
| Distillation | Tool-result clearing is explicit; it is not a full prose-history summarizer. No inferred claim that distilled memories caused this lookup. |
| Active recall | Conversation search uses the exact scoped store, with generic archive-search prompting disabled. Relevance is not authorization. |
| Tool selection | Model-guided catalog discovery is present; disclosure restoration and broad skill-loading requirements require refinement (R6). |
| Tool execution | Core broker and App dispatcher enforce policy/scope; read suppression is overbroad (R5), and connectors bypass canonical commands (R1–R3). |
| Tool interpretation | Structured receipts and current staging outcomes provide authoritative state; filing can lose data before validation (R8). |
| Answer shaping | Native output settles through `SettledTextBuffer` before final display, protecting against invalid/retried answer duplication. Generated first-token latency should be distinguished from first-visible-answer latency. |
| Platform rendering | Shared branding is now tested against the vector master; auto-scroll followed this lookup. Extension error lifecycle needs correction (R9). |
| Hidden repairs | This browser trace exposes the failed call and recovery. No separate hidden LLM judge was found in the reviewed native approval path; deterministic normalization and legacy compatibility branches still need retirement boundaries. |
| Persistence | Scoped checkpoints, durable receipts and execution fencing are strengths. Connector task/upsert ownership and interrupted-usage aggregation remain gaps (R1–R4). |

### Substrate and extension strengths to preserve

- The immutable execution scope and App operation dispatcher remain the authorization boundary. Catalog ranking and a loaded skill do not grant access.
- `ScopedStepStore` validates scoped conversation identity; encrypted persistence and work-item leases provide an existing foundation for recovery and fencing.
- App/bundle tools use the `ToolContext` facade. Domain behavior belongs in extension contracts and hooks, not Core imports or hardcoded bundle names.
- Operational-model field/view contracts and registry tests provide a useful compatibility source of truth. Extension iframes use a handshake and controlled bridge; do not weaken this to fix recovery UX.
- Core skills use standard Agent Skills metadata and hyphenated names, with compliance checks. Domain-neutrality is weakened by the global filing alias dictionary, not by the existence of declarative skills.
- Native reasoning display is **intentionally disabled**: `events.py:253` ignores provider thinking parts, `IntegralNativeProvider` declares no reasoning surface, and the activity panel says private reasoning is not displayed. Expose tool activity and concise provider-approved summaries if desired; do not imply the absence of raw chain of thought is a streaming bug.

## Ordered remediation and qualification

Each work package should have a focused diff, deterministic regression checks, then browser tests with layperson prompts. Keep fixes in existing service/adapter boundaries and preserve unrelated staged stream work.

| Order | Work package | Required proof |
|---|---|---|
| 1 | Connector identity, canonical writes and durable sync ownership (R1–R3) | Two-workspace collision fixture, schema/graph rollback checks, overlapping worker/manual trigger tests, browser readback of synchronized typed records. No new connector release until qualified. |
| 2 | Physical-request accounting reconciliation (R4) | Successful/cancelled/failed stream fact matrix, duplicate transition reconciliation, tenant/BYOK attribution and browser spend readout. |
| 3 | Read recovery, restored tool availability and context efficiency (R5–R6) | Same cold/warm/long-chat requests, actual per-request metrics, read-after-write, transient failures and no remembered-tool availability error. |
| 4 | Compound filing/scaffolding and transparent normalization (R7–R8) | One compound proposal/approval/receipt; relations and supplied fields read back; reject/change/unknown-write paths; ambiguous destination clarification. |
| 5 | View compile/lifecycle and public SDK parity (R9–R11) | Invalid declaration rejection, failed-to-healthy extension navigation, workspace changes, token renewal, typed external SDK client with revision conflict. |
| 6 | Documentation and legacy retirement (R12) | Current default binding/config docs, explicit compatibility call inventory, warning baseline and no new lifecycle drift. |

### Browser acceptance matrix

Use realistic user language; do not mention internal tool or skill names to make the test pass.

| Scenario | Example request / action | Verify |
|---|---|---|
| App creation | “I run a bicycle repair shop. Help me keep customers, bikes and repair jobs organized.” | Design, one meaningful decision, app/tracks/schema and useful dashboard; dashboard aggregates have executed query provenance. |
| Related records | “Add Ana, her blue Trek bicycle, and a brake repair due Friday.” | One understandable compound preview; approved links and records are actually saved. |
| Queries | “Who is in my customer list?” / “Which jobs are overdue?” | Correct scoped results, bounded payload/calls, repeatability in existing rich chats. |
| Updates | “Move Ana's repair to next Tuesday.” | Target ambiguity resolved, revision guard, updated calendar/table/query readback. |
| Delete | “Remove that duplicate job.” | Exact target and policy-appropriate decision; no accidental related-record deletion. |
| Attachment filing | Supply a synthetic receipt and say “File this.” | Extraction, schema mapping, ambiguity clarification for app/track, attachment retention and saved readback. |
| Approval recovery | Ask a question, approve verbally, reject, revise the design, reload during work. | No global word matching; one concrete snapshot; no repeated effects or stale approvals. |
| Stop/retry | Stop while streaming, reload, ask a follow-up. | Settled UI, truthful partial/unknown effects and accounting, safe continuation without blind replay. |
| Tenant/BYOK | Repeat relevant cases across two isolated workspaces and credential routes. | No cross-scope records, checkpoints, tool disclosure or spend; missing cost remains visibly unknown. |
| View extension | Navigate from an intentionally failed extension to a healthy one. | Recovery, correct scoped handshake and no stale failure flag. |

## Evidence and limitations

The new browser lookup above was run in the **existing** workspace and chat, not a blank workspace. It returned the correct saved record, showed tool recovery, settled the turn, cleared the composer and scrolled to the answer. Model/call/token metrics were read back from the backend qualification export as well as the UI. Cost was unavailable for this run; a null amount is not evidence of zero spend.

The compound-create trace was inspected in that same conversation; it was not rerun and no additional records were created in this branding round. Connector collision calculations and cancelled-usage aggregation were checked without modifying customer data. Other browser scenarios in the acceptance matrix remain future qualification requirements. Passing source suites alone does not close them.

Branding screenshots are under `docs/reviews/assets/2026-10-07-branding/`. Build/test outcomes and final browser validation are recorded in the companion branding validation note.
