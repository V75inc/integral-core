# Pydantic AI Harness Blueprint — Technical and Coding-Agent Execution Review

**Date:** 2026-10-04

**Reviewed deliverable:** [Implementation plan, revision 3](PYDANTIC_AI_HARNESS_IMPLEMENTATION_PLAN.md)

**Outcome:** the revision 2 technical review and revision 3 author review support beginning WP-00. All identified major delivery issues and the package-brief gap are resolved. Actual dependency compatibility, tenant isolation, recovery, accounting and efficacy are unproven until the implementation gates produce evidence.

## 1. Strategic revision

The native binding now composes Pydantic AI and the broader Harness capability library. The earlier primitive-only recommendation and default custom-loop direction are superseded. Integral retains authenticated scope, graph continuity, work/approval authority, broker receipts and durable usage facts; upstream capabilities provide substantially more reasoning, planning, discovery, context, persistence and instrumentation behavior.

The [adoption matrix](PYDANTIC_AI_HARNESS_IMPLEMENTATION_PLAN.md#23-capability-adoption-matrix) names mandatory surfaces, separately qualified accelerators and deferred architectural expansions. A custom behavior requires a documented gap and maintenance owner. Independent Core distributions use supported composition and admission contracts; Integral Business owns commercial pricing, payer mapping and paywalls.

## 2. Review method and evidence limits

The author reviewed the plan against the current repository contracts, invariant catalogue, agentive-layer instructions and current official Harness documentation. The blueprint skill's independent adversarial reviewer then examined the revised plan for architecture, authority, dependency, estimate, migration, rollback and acceptance errors. A follow-up read verified the corrections below and confirmed no remaining blockers or major findings for starting WP-00.

This is a document review. No dependencies were installed, no implementation tests or paid model calls ran, and no production setting, provider default or billing policy changed. No commit, push or PR was created. Existing unrelated working-tree changes were preserved. Exact package/API/license qualification belongs to WP-00; release qualification belongs to WP-16/17.

## 3. Findings and dispositions

| Finding | Severity | Correction and evidence in the revised plan | Disposition |
|---|---|---|---|
| First paid native slice preceded atomic reservation implementation | Major | WP-12 Core admission moved before WP-06; WP-04 distinguishes gateway fixtures from integrated paid admission; waves and dependency graph updated | Resolved; independently rechecked |
| WP-06 real UI acceptance depended on later WP-10 | Major | Minimum frontend native adapter/selector explicitly owned by WP-06; WP-10 extends that path; effort is not counted twice | Resolved; independently rechecked |
| WP-16 demanded pilot evidence before WP-17 could start pilot | Major | WP-16 closes controlled pilot entry G0–G3, G5–G9, G11; WP-17 gathers G4/G10 and reconfirms the release profile | Resolved; independently rechecked |
| 8–12 calendar weeks contradicted serial package estimates | Major | Withdrawn; 103–161 engineer-days retained; whole-package longest pre-pilot path recalculated as 69–107 working days, approximately 14–22 working weeks; re-estimation required after WP-00 | Resolved; independently rechecked |
| Cold-start briefs prescribed but not supplied | Minor | Filled WP-00 assignment brief added; completed, reviewed briefs are entry gates for subsequent packages | Resolved; independently rechecked |
| Companion documents still described primitive-only adoption and billing-account ownership in Core | Consistency | Specification/research synchronized; Core fields use trusted opaque attribution, with account/product mapping in Business | Resolved; companion spec rechecked |
| Native-only import behavior could be confused with dependency independence | Author review | WP-15 explicitly owns package metadata/extras; native-only artifacts cannot require jvagent installation; dual-provider compatibility decision frozen before packaging changes | Resolved in blueprint; artifact proof deferred |

The review did not find an architectural requirement to rebuild a second model/tool loop, add a second work queue, move Business pricing into Core or introduce peer-agent delegation.

## 4. Architecture and engineering once-over

| Concern | Blueprint disposition | Required implementation evidence |
|---|---|---|
| Tenant/principal isolation | Trusted ExecutionContext; per-run stateful capabilities; full-key immutable caches; scoped stores | Concurrent authorization, profile/client/store and trace/usage isolation tests |
| Graph/object integrity | Rooted HarnessSession; operational snapshots, events and usage remain Objects | Atomic create/edge wiring, reachability, migration and strict scoped queries |
| Upstream feature adoption | Mandatory composition matrix and explicit gap register | Exact exports, selected extras, hook ordering and compatibility by model route |
| Loop and work authority | Upstream model/tool lifecycle; thin Core coordinator and final broker/gateway checks | Nested-call coverage, fencing, deadline and cancellation conformance |
| Persistence and effects | Unique framework invocation IDs; stable Core logical effect keys; safe-pointer CAS | Interrupted effects, framework/Core partial-write recovery and duplicate delivery probes |
| Approval and clarification | Framework interactions adapt to Prompt Sheet/WorkApproval; fresh fenced resume | Payload edit/revocation/expiry and restart continuation evidence |
| Memory and skills | Approved discovery; existing scratch/promotion and owner-filtered transcript adapters | Provenance, script/resource parity, scope, revocation and retention checks |
| Context management | Upstream compaction/output bounds with protected obligations/receipts outside summaries | Long-context fidelity, output-spill scope and auxiliary metering evidence |
| Financial facts and admission | Immutable request observations and atomic Core reservations; upstream guards supplemental | Race, unknown-price, retries, cancellation, reconciliation and Business conformance |
| Observability | Redacted upstream OTel/capability events correlated with durable Core records | Export outage/redaction tests and operator access evidence; sampled spans excluded from totals |
| Performance accelerators | Code Mode read-tool pilot; optional Advisor; speculation disabled initially | Paired same-model task results, total physical/auxiliary cost and recoverability |
| Commercial distribution | MIT notices and exact selected-extra inventory; hosted Pydantic services optional | Wheel/container notices, dependency metadata and independent Core-only installation |
| Derivations and MCP | Published generic contracts, composition profile example and same broker perimeter | Business-free Core install, downstream adapter conformance and external-client parity |
| Rollout and rollback | Separate pilot entry/final gates; sticky binding and guarded admission kill switch | Representative pilot, accounting completeness, rollback drill and explicit release decision |

## 5. Remaining decisions and next action

**Skill format decision:** the user's subsequent direction supersedes preservation of JV extensions. Core skill files now target Agent Skills only, with hyphenated names and standard fields; vendor inheritance/Action dependencies are removed rather than adapted. The migration passed all 22 shipped-package format checks and 56 targeted compatibility/compliance tests (13 domain/slow tests deselected), backend lint for the touched parser/tooling files and frontend type checking. The broader authoring selection exposed one existing private-skill listing test that omits the App focus required by the current visibility boundary; it is not a format migration failure. The earlier independent review was a blueprint review, not proof of this later implementation change.

Begin WP-00 using its assignment brief. It must resolve exact compatible versions, model-route capability loss through LiteLLM, state/store protocols, mandatory sandbox parity, pricing uncertainty handling, packaging defaults and the measured benefit of the proposed composition. A required behavior that fails the proof produces a bounded architecture decision rather than a silent scope reduction.

The revised estimate is conservative, not a delivery commitment. Upstream reuse may shorten parts of the runtime, but isolation, recovery and commercial accounting still need proof. WP-00 publishes a revised dependency/staffing schedule before feature work; no capability catalogue alone justifies reducing the schedule.

The first read-only native UI slice is WP-06 after contracts, sessions, work authority, gateway, ledger and admission. Core-only readiness can be qualified independently. Commercial deployment and native-default readiness require the later consumer and pilot gates.

## 6. Synchronized artifacts

- [Implementation plan](PYDANTIC_AI_HARNESS_IMPLEMENTATION_PLAN.md) — delivery authority, adoption matrix, packages, dependencies, recovery, gates and controlled changes.
- [Intelligence-plane specification](PYDANTIC_AI_HARNESS_INTELLIGENCE_PLANE_SPEC.md) — requirements, ownership and continuity/accounting contracts.
- [Research and blueprint](NATIVE_HARNESS_RESEARCH_AND_BLUEPRINT.md) — comparative assessment with the revised recommendation.

Current official capability sources are linked in the implementation plan §16. API claims remain qualification inputs, not guarantees about an installed release.


## 7. Revision 3 — coding-agent execution review

**Review type:** author review of the execution contract and dependency graph; the earlier independent technical review does not constitute an independent review of this revision. No harness implementation or evaluation was executed during this documentation update.

| Check | Disposition |
|---|---|
| Human-team assumptions | Replaced package leads/disciplines and engineer-day/calendar forecasts with coding-agent assignments and evidence-based capacity calibration |
| Cold-start execution | Shared dispatch template plus explicit WP-00..17 slice sequences, input/baseline requirements, test namespaces and expected evidence; proposed APIs still require WP-00 proof |
| Concurrent editing | Coordinator owns shared integration/schema/lock files; transfer of StepStore, events and minimum UI adapter ownership is explicit; independent fixtures/browser/sandbox identities required |
| Resume and handoff | Durable task ledger, accepted dependency receipts, exact patch/config identity, command outcomes, blockers, rollback and next action |
| Agent review independence | Fresh-context read-only review followed by bounded repair and integrated reproduction; sequential roles supported when multiple sessions are unavailable |
| Authority and gates | Core governance, standard-only skills, mandatory capabilities, Business separation and G0–G11 preserved; coding parallelism does not add runtime peer agents |
| Serial dependencies | WP-12 Core admission precedes WP-06; WP-10 continuation waits for WP-08/09; WP-16 opens pilot, WP-17 supplies actual G4/G10 evidence |
| Release and time constraints | No automatic push/PR/deployment/default switch; live probes need scoped budgets; pilot volume and observation period cannot be replaced by simulated agent output |

**Result:** the plan now supplies bounded coding-agent assignments throughout, rather than deferring all later package briefs to human leads. Execution still requires resolving checkout-specific paths/pins, accepted producer contracts and external service inputs at dispatch. Missing evidence blocks the affected gate; it is never inferred from an agent’s completion message.
