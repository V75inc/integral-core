# Integral Core: state, access control, stability and code quality

Date: 14 October 2026 (America/Guyana)
Review type: broad source review, existing evidence inspection, read-only runtime/CI checks.
Verdict: substantial governed substrate, with unresolved consistency and recovery boundaries. Continue controlled local qualification; do not treat this snapshot as a closed production hardening milestone.

## 1. Exact baseline and limits

- Reviewed checkout: `/private/tmp/integral-core-hardening`, branch `codex/harness-hardening-qualification`, HEAD `71ec0c55f28f44aad9d4d5b8d54ba9156a456222`, plus its current staged/unstaged/untracked source changes. Those local changes include collaborator counts, relation identity, approval failure reporting, adaptive chat and chat diagnostics.
- GitHub PR #136 is open, not draft, at that same committed SHA. Backend, frontend, PostgreSQL and independent artifact checks pass. Docker verification is **skipped**, not passed. No review decision was reported by GitHub.
- Live local API reports `integral-core==0.1.1rc18`, `jvspatial==0.2.2`, `DEBUG=False`, **durable chat disabled**. `/api/health/ready` returns ready with intelligence available. This is a point-in-time health check, not sustained availability or recovery proof.
- The web runs `integral-business-debug-payload-web:local`; the API remains the locally built adaptive-context image. The shared RC version string does not identify the full local source delta.
- Reviewed the canonical permission/scope and identity readers, governance contract, invitation/share handling, command/effect/transaction boundaries, approval application, event/audit release, harness/session/usage handling, selected frontend state and debug consumers, release workflows, and regression sources. This is not a line-by-line inspection of every module or an independent penetration test.
- **No new tests, fault injection, migrations, commits, pushes or deployment changes were performed for this review.** Existing CI and earlier test receipts are distinguished below from current-source inspection. A separate failure-injection pass is necessary to close the races identified here.

## 2. What is strong today

### Authority is implemented outside the model

`services/resource_location.py` resolves canonical rooted graph location; scalar parent/workspace IDs do not independently create authority. `services/identity_admission.py` checks actual profile/auth bindings and rejects ambiguous identity facts. `services/permissions.py::resolve_role` enters an isolated live authorization context rather than treating a warmed process role as permission.

The current contract separates ordinary resource roles from protected workspace-owner governance. Owner stewardship covers canonical workspace resources, including private descendants; inherited ordinary owner/admin grants cap at editor. Ordinary workspace administrators do not automatically become resource owners. App function/skill authority remains distinct from data stewardship. This is a deliberate advance beyond the earlier milestone's owner-governance limitation, not evidence that every journey is closed.

### Fail-closed behavior has improved

Exclusion lookup errors raise `AuthorizationUnavailableError`; they do not mean “no exclusion.” Canonical scope admission refuses malformed or inaccessible explicit workspace hints. Human policy evaluation delegates to canonical helpers, while agent/connector evaluation requires declared policy and current ceilings. Internal/system authority remains a separate trusted-code perimeter; it must never be accepted from model/client inputs.

Verified email is now required for invitation discovery/by-ID acceptance (`api/shared_with_me.py`, `services/invitations.py`). Tokens are hashed; share redemption is bounded and authenticated for collaborator links. Suspension, blocks, issuer provenance and grant-source handling have dedicated services rather than being improvised in model prompts.

### Transactional command foundations are credible

`authorization_scope.py`, `identity_admission.py`, `entry_effect_scope.py` and the public jvspatial graph transaction seam order current authority with protected effects on PostgreSQL. Entry writes use observed record revisions; conditional entry updates now pass through the canonical guarded command. The old ledger description of state-only CAS is therefore not an accurate statement of the present helper.

Operation replay calls `authorize_replayed_result` before returning a cached read result (`app_operations/dispatch.py:510`). This is an important repair to the former replay-disclosure path. Task ownership checks prevent another asyncio task silently inheriting an open graph transaction.

### The harness has useful separation

Light chat has no workspace function tools and can request typed operational escalation. Its run/principal/workspace/thread binding is validated before inference. Model accounting gets `light-<run_id>`; operational checkpoints are preserved separately. A null pre-turn persistent provider session is not missing internal execution identity.

Operational capabilities remain brokered, authorized and approved independently of the model's routing decision. Private reasoning and instruction envelopes are deliberately withheld from public transcripts; observations/checkpoints are encrypted. Prompt confidentiality still depends partly on model behavior for paraphrases and encodings; instructions must contain no credentials. Pydantic validation does not prove authorization or semantic confidentiality.

### Quality gates and regression coverage are substantial

Frozen dependency exports, substrate/domain/convention guards, typed schemas, backend/frontend checks, PostgreSQL qualification and independent wheel/SDK/App artifact lanes exist. Existing tests cover malformed authority facts, exclusions, replay revocation, scope mismatches, approvals and adaptive routing. These are meaningful controls; a green suite is not closure of transport races or deployment restore.

## 3. Findings requiring action

Severity is impact-based. “Source-confirmed” identifies a code path; it does not claim this review executed an exploit. Race findings explicitly retain their need for fault injection.

### R1 — P1: legacy approval execution claims are process-local

**Evidence:** `agentive/staging.py:1348-1389`. `claim_execution` uses the module `_lock`, loads a token, and sets `sc.executing=True` in memory. It does not persist an atomic execution claim. `release_execution_claim` likewise only changes the local token. `staging_store.py` provides durable pending/blessed storage, but that alone does not serialize independent workers' claims.

**Failure condition:** two processes load the same blessed proposal and each sees no execution in progress. PostgreSQL serializes an individual mutation, but cannot deduplicate a logical proposal without a shared proposal/execution identity. `_x_create_entry` routes to HTTP creation without a proposal idempotency key (`staging_executors.py:158-233`, `api/entries.py:430`); the canonical creation helper's optional idempotency key remains empty on that route.

**Impact:** duplicate approved effects are possible across workers or restart/retry windows. Current single-process tests do not establish multi-worker exclusivity. No duplicate was deliberately created in the live workspace during this review.

**Repair:** atomically claim the proposal in durable storage with lease/fencing identity; bind every effect to a stable proposal command key and durable result receipt. Test two independent workers, restart after claim and restart after commit. Keep single-process deployment limits explicit until qualified.

### R2 — P1: successful effect and approval consumption are separate recovery steps

**Evidence:** `agentive/services/staging_apply.py:175-262`. The executor completes before design receipt persistence and `consume_token`. A missing design receipt raises `StagingError`; its catch releases the claim and leaves `consumed=False`. The following unconditional `record_execute_outcome(error=None)` clears the retained explanation even after `consume_warning`.

**Failure condition:** mutation succeeds, then receipt/consumption fails. A still-blessed proposal can be retried through its executor although the effect may already exist. Generic failures outside the caught `StagingError` path can also leave a claim stuck until process recovery.

**Impact:** uncertain applied state, misleading approval UI, and potential replay of a completed effect. This is a source-confirmed recovery window; exact outcomes need injection for each executor, including partial batches.

**Repair:** make command result/receipt durable with its mutation; retry reconciles that result instead of rerunning a completed effect. Separate `applied`, `receipt_pending`, `unapplied`, and `unknown` states. Persist consumption/receipt errors; only clear them after reconciliation. Qualify receipt failure, consume failure, cancellation after commit and partial batch restart.

### R3 — P1 qualification gap: authorization is not checked at every actual transport release

**Evidence:** `services/event_subscription_registry.py:132-153` checks a user once, then awaits sends to each socket. `services/chat_turn_streaming.py:31-60` checks scope once per page, then yields up to 200 events. Each await/yield permits other work, including revocation, before later bytes leave.

**Impact:** authorization can change after the check and before a subsequent release. The code proves per-broadcast/per-page checking, not the stronger instant revocation guarantee. Existing “next broadcast denies” tests do not close the in-flight window. The durable chat path is currently disabled locally; its rollout still needs this qualification.

**Repair:** define the allowed release/revocation ordering, then enforce it through an admitted revision/release seam and current checks at each chunk/socket release. Never hold a graph transaction indefinitely across a stalled network send. Test revocation while the first socket blocks, mid-page SSE delivery, disconnect, and replay after offboarding. Bytes already sent cannot be recalled.

### R4 — P2: audit and event polling ignore the bound request workspace

**Evidence:** `api/audit_log.py:63-85` and `api/events_polling.py:71-99` resolve the principal, fetch optional caller-selected event scope, and apply per-resource authorization. Neither resolves/intersects `X-Integral-Scope` with the candidate workspace.

**Impact:** a principal authorized in A and B can receive B history while making an A-bound request, or receive a union with no scope filter. This is a bound-context consistency defect, not proof that an unauthorized person can read B. It also risks sending the wrong organization's context into downstream agent work.

**Repair:** intersect canonical event resource workspace with immutable request scope before exposure, retain per-event/resource checks, and define personal/account-level audit as an explicit separate surface. Test two authorized workspaces and stale/foreign header scopes on HTTP and native/MCP consumers.

### R5 — P2: capped audit responses still perform unbounded history scans

**Evidence:** both routes call `ChangeEventLogger.find_all`; `change_event_logger.py:256-282` uses `database.find("object", query)` without limit/cursor. Routes hydrate, authorize and sort the full candidate history before applying the response page limit.

**Impact:** response size is bounded but CPU, memory, database work and authorization queries grow with history, including history unrelated to the active workspace. This can become a UI latency and resource-exhaustion problem. No load threshold was measured in this review.

**Repair:** indexed workspace/time/id candidate windows, stable keyset cursors, bounded scans with explicit completeness/continuation metadata, and authorization before each returned item. Keep counts honest when filtering. Also stop returning an ordinary empty feed when the log database read fails (`find_all:283-285`); distinguish unavailable evidence from zero events.

### R6 — P2: `integral_get_scope` reports a preference instead of execution binding

**Evidence:** `agentive/tooling/bindings.py:3616` binds directly to `api/users.py::get_my_scope`. That endpoint deliberately uses `skip_header=True` (`users.py:862-873`), returning saved/default scope. The tool manifest describes the result as the agent's bound context and declares a different shape (`tool_manifest.yaml:215-225`).

**Impact:** the reported workspace can differ from the immutable tool invocation workspace. Earlier retained local read-only evidence recorded Business Admin App results alongside Acme scope. This is grounded by current source, not a new unverified assumption. It encourages confused-agent explanations even when resource tools enforce the correct scope.

**Repair:** keep the UI preference endpoint unchanged; add a tool-facing execution-scope projection using the dispatcher binding and admitted membership/creation rights. Match the declared return schema and make saved preference a separate optional diagnostic field. Test a principal belonging to two workspaces with a different saved preference.

## 4. Deployment and product risks, distinct from reproduced defects

- **Durable chat is an opt-in rollout candidate:** `config.py:131` defaults false, and the live stack reports false. Encrypted operational checkpoints and run records do not by themselves make the HTTP chat acceptance/stream/recovery path durable. Finish the existing WorkItem recovery rollout before claiming restart-transparent chat.
- **Backend guarantees differ:** PostgreSQL supplies transaction/fencing seams. JSON/memory local command locks intentionally do not provide cross-process rollback/fencing. SQLite and other adapter capabilities must be qualified explicitly rather than inferred from sharing model classes.
- **Rate limiting is process-local:** `middleware/rate_limit.py:1-6`. Multiple workers/replicas multiply its effective allowance; restarts reset it. jvspatial also has its own auth cap, so this is not a claim that all authentication throttling is absent. Use a shared limiter/admission boundary for a horizontally scaled service.
- **Global user discovery is deliberate but broad:** authenticated `/users` and `/users/{id}` expose safe profile projections plus linked email (`users.py:128-154`). This is current intended directory behavior, not a newly proven resource-ACL bypass. Multi-tenant privacy expectations should explicitly approve this policy or replace it with scoped invitation/search results.
- **Snapshot projection is not fully closed:** `event_access.py:108-115` authorizes the event's resource. It does not independently project every nested referenced object in before/after snapshots. The existing handoff already identifies nested-reference and deleted-resource audit qualification as open. Do not call this a confirmed nested-content exploit without a concrete payload/field reproduction.
- **Legacy relation repair depends on retained evidence:** `track_relation_identity.py` uses stable declared identity, otherwise a unique validated creation snapshot, otherwise current title. Already-renamed Tracks without retained snapshots still require reviewed migration; the rename fix is not retroactive repair of every database.
- **Prompt routing is not authorization:** the model may select the wrong light/heavy path or answer a workspace question conversationally. Light mode has no mutation capability, but answer correctness still needs evaluation. Preserve current-read/readback requirements, test ambiguity and multilingual cases, and never allow routing confidence to confer authority.
- **Dependency gate exceptions remain accepted risks:** `.ci/dependency_audit.sh` explicitly ignores named backend advisories and handles actionable frontend findings. This review did not re-query advisory databases; passing that gate is not a claim of zero vulnerabilities. Review the recorded exceptions and actual runtime algorithm configuration before release.

## 5. Code quality assessment

### Overall: coherent architecture, uneven maintainability

The best parts have clear contracts: rooted location, admitted identity, canonical commands, typed capability inputs, durable observations, transaction ownership and immutable scope. The main liability is the number of alternate paths around those contracts, plus large orchestration modules that mix HTTP handling, state transitions, profile discovery, model execution, recovery and transcript projection.

Measured source inventory (Python app files and TSX files only, not all repository code):

| Inventory | Files | Lines | Files over 1,000 lines |
|---|---:|---:|---:|
| `backend/app/**/*.py` | 715 | 219,517 | 40 |
| `frontend/src/**/*.tsx` | 621 | 125,669 | 12 |

Large examples: model compiler 4,687 lines; tool bindings 4,092; connector API 3,146; chat API 3,047; Kanban widget 2,241; Entry form 2,151; Entry detail 2,067. Size alone is not a defect, but these files make invariant review, exception-path understanding and focused regression coverage costly.

Recommended structural changes:

1. One command/receipt application contract for legacy staging and declared operations. Eliminate repeated “execute then reconcile state” sequences.
2. Separate preference scope, execution scope, ordinary resource authority and protected governance in types and API names; remove compatibility aliases only after enumerating consumers.
3. Centralize bounded event selection and release projection for polling, audit and WebSocket consumers, without making one stale allow reusable across releases.
4. Split harness preparation/routing, model dispatch/accounting, operational checkpointing and public event output into typed components. Replace tuple-shape compatibility branches with an explicit prepared-turn union.
5. Split large Entry/Kanban components by data admission, commands and rendering. Preserve UI behavior and existing generic widgets; do not move domain App UI into Core.
6. Review swallowed exceptions by category: unauthorized/unavailable source facts must deny or surface failure; optional cosmetic enrichment can remain best-effort; applied-but-unrecorded effects require reconciliation.
7. Reconcile docs and generated tool contracts. The old harness hardening report still says “not deployed,” while the later adaptive report establishes a local deployment. Keep historical evidence dated, and make current status explicit instead of maintaining contradictory undated claims.

### Evidence coverage needs to follow the changed source

The closure ledger currently has **22 findings: 4 closed, 12 in progress, 6 open**, and **76 scenarios: 67 open, 9 in progress**. These are qualification states, not a count of 18 still-reproducible original bugs. Several descriptions document old defects whose relevant code has since changed. Reconcile each row from present source and named acceptance evidence; do not close rows by test totals.

CI run `38494462429` at the committed baseline is green. Backend timing is about 23 minutes: the extension-contract step takes about 10 minutes and smoke about 12 minutes; install and guards are seconds. The full scheduled suite was skipped in this PR run. The earlier locally completed broad backend run and final targeted/hook receipts are retained, but the broad run started before final adaptive changes. The current entire dirty tree does not have a fresh unified release receipt.

## 6. Suggested repair and qualification order

1. **Approval integrity:** durable proposal execution claims; stable command identity; atomic mutation/result receipt; honest applied-but-reconciliation-pending states. First qualify two-worker concurrency and post-commit failures.
2. **Output perimeter:** exact request workspace for feeds; bounded keyset selection; per-release authority ordering; admitted nested reference projections and governed deletion/tombstone history.
3. **Harness correctness:** fix the execution-scope tool/manifest mismatch and session terminology; qualify durable chat acceptance, cancellation, timeout, replay and restart before enabling it.
4. **Operational recovery:** complete deployment PostgreSQL restore and independent checkpoint review, credentials/session invalidation, migration qualification and owner-facing evidence review already recorded in the access handoff.
5. **Maintainability:** narrowly extract typed orchestration seams while preserving canonical authority helpers; migrate legacy callers and remove dead compatibility paths with an explicit consumer inventory.
6. **Release qualification:** exact final source digest, full required source/backend/frontend/PostgreSQL gates, clean artifact install, multi-principal browser/API/MCP/native allow/deny journeys, provider fault injection and realistic latency/load probes. Passing CI on an older committed source is insufficient.

## 7. Practical assessment

- Appropriate today: controlled local testing and further qualification with known single-process/rollout limits.
- Not established by current evidence: fully closed multi-tenant authorization, multi-worker approval exactly-once behavior, instantaneous transport revocation, restart-transparent chat, deployment restore readiness or production load capacity.
- Architectural recommendation: finish and unify the existing enforcement/receipt/release seams. There is no evidence here that replacing Pydantic or rewriting Core would be the cleanest remedy.

References: current access contract; access-control closure ledger and milestone handoff; adaptive harness latency report; current source locations above; [PR #136](https://github.com/V75inc/integral-core/pull/136); [CI run 38494462429](https://github.com/V75inc/integral-core/actions/runs/38494462429).

## Subsequent checkpoint qualification — 14 October 2026

Following the user's instruction to publish the recent changes, the full default-selected backend suite completed successfully with four xdist workers. The frontend suite passed 1,753 tests across 312 files. Type checks, configured pre-commit hooks, staged substrate guards, the current frontend build, and reproducible public wheel/import checks passed.

The PostgreSQL CI lane and changed-path adaptive/relation/collaboration/staging selection passed against a disposable local PostgreSQL service. The CI lane explicitly skipped one backup drill because its pg_dump transport could not reach that service's host port; restore remains unqualified by this run.

An initially concurrent host-wide CI-style run failed six fixed-deadline graph-context cases under excessive worker contention and was stopped to investigate. All 42 graph-context cases then passed separately with the original assertions and timeout. The complete smoke selection was rerun with `PYTEST_XDIST_AUTO_NUM_WORKERS=2` on an archived source snapshot; its outcome is recorded in the PR qualification checkpoint. The worker cap changes scheduling, not selection, assertions or security deadlines.

Raw local logs are retained under `/private/tmp/integral-browser-latency/recent-*`; they are not bundled into the PR. This qualification does not close the review's approval execution, transport release, restore, or deployment-rollout findings. No runtime redeployment is implied by these source commits.
