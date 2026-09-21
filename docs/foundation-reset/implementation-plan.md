# Implementation program

**Status:** WP-00 verified; WP-01 in progress. **Baseline:** `75a0f35c2d4308b268fda0d8b15775ce9fbcacae`.
**Target:** [FR-001](architecture.md). **Documentation work:** [replacement plan](documentation-plan.md).

## Operating rules

1. Each work package has one accountable implementation owner and one reviewing owner; roles below are assignments to fill when execution begins, not claims that people have been assigned. Do not run overlapping ownership work concurrently.
2. Characterize current behavior before replacing it. Separate intended invariants from accidental behavior; do not preserve bugs for compatibility.
3. Each slice includes implementation, contract tests, meaningful integration/browser tests, current documentation and retirement of its obsolete path. Temporary adapters have a named consumer list and removal gate.
4. No package may call itself complete with a broken full gate, placeholder acceptance claim, undocumented behavioral break or unexplained expected failure. Follow the current `AGENTS.md` commit discipline; no push/PR without explicit session permission.
5. Preserve uncommitted user work. Record baseline commit, dirty paths, test conditions and artifact identities. Do not rewrite unrelated commercial repositories.
6. New target documents are authoritative for design direction, not descriptions of shipped behavior. Old guidance remains operative until its replacement and applicable code changes land together.
7. Browser qualification uses ordinary user flows. Direct database/API setup is allowed only for labeled fixture preparation and independent evidence inspection, never as a hidden repair of the tested flow.

## Current-to-target map

| Existing area | Target owner | Treatment |
|---|---|---|
| `services/permissions*`, `policy_engine`, `workspace_permissions`, scope resolution | Identity/policy | Consolidate principal/scope/policy interfaces; preserve current deny semantics until an explicit decision changes them |
| `models`, `entry_*`, `track_*`, `relation_runtime`, graph writers/walkers | Information | Establish typed field identity and unit-of-work seam before relocation |
| `operational_model_*`, `agent_profile_patches`, view contracts | Applications | Unify definition compilation, validation and evolution; retain profile draft/publish semantics |
| `app_operations`, capability catalogue, hooks and App queries | Applications + execution/query | Separate descriptor registration from execution, transaction ownership and read planning |
| `agentive/services/work_*`, work models | Execution | Reuse durable kernel; remove agent-specific ownership of general operational work |
| Staging, approval executor, prompt queue, broker, operation idempotency | Execution + intelligence adapter | Select one authoritative state path; migrate pending work; remove independent success/replay logic |
| Migration runner, routine scheduler, install/reaper/lifecycle | Applications + execution | All long-running lifecycle work uses shared durability and effect handling |
| `governed_query`, `agent_insights`, dashboards, retrieval | Query/projections | Shared semantics and completeness; retrieval stays distinct from exact operational queries |
| `jvagent_harness`, agent skills/tooling, chat providers | Intelligence | Thin adapters over discovery/query/execution; no private record writes |
| HTTP routes, MCP, extension bridge | Experience adapters | Parity through public module APIs, common errors and evidence |
| Frontend fields/views, approvals, chat | Experience | Render shared contracts and authoritative state; remove platform/business-field ambiguity |
| SDK, examples, package discovery/signing | Applications/public SDK | Independent build/install proof and explicit trust/lifecycle contract |

This is a responsibility map, not a completed import audit. WP-00 resolves actual call graphs and shared state before file moves.

## Dependency waves

```text
WP-00 baseline + design qualification
  → WP-01 module seams + policy
  → WP-02 information/schema contracts
  → WP-03 transactional commands + durable execution
  → WP-04 application compiler + lifecycle
  → WP-05 query/projection parity
  → WP-06 resident authoring + operation flow
  → WP-07 collaborative experience
  → WP-08 independent extension proof
  → WP-09 release qualification and old-path removal

Documentation replacement runs through every wave; final cutover is WP-09.
```

Query contract design begins in WP-02; query implementation can advance once those contracts freeze. SDK contract design begins in WP-01, but its end-to-end proof waits for lifecycle and UI. Frontend previews may use explicit fixtures early; they are not completion evidence. This order avoids developing skills against speculative runtime capabilities.

## WP-00 — Baseline, inventory and feasibility

**Owners:** architecture/release lead; review by persistence and experience owners. **Dependency:** none.

- Inventory execution paths, persisted models, caches, schedulers, mutation entry points, extension APIs, runtime resources and deployment topology. Record cross-module imports and writes.
- Run and record existing full verification, Core-only, contract and Postgres lanes. Catalogue failures individually; do not convert them into blanket accepted debt.
- Preserve reproducible browser traces for scaffold, dashboard, update, query and schema evolution. Include wrong-field and repeated-approval cases from the assessments.
- Enumerate deployed/retained datasets, package versions and migration obligations. Classify each environment as disposable or preserved.
- Qualify graph transaction participation with the live [transaction spike](transaction-spike.md): structural graph rollback, deterministic receipt claim, committed notification outbox, and dispatcher cutover are proven.
- Measure latency, query cardinality/scale, token accounting and recovery time. Freeze numerical release budgets and supported providers/models in the acceptance ledger before optimization begins.
- Complete documentation inventory including Markdown, YAML prompts, config comments, examples, CLI help, generated API references and hidden agent/tool guidance.

**Deliverables:** baseline evidence manifest, dependency/data ownership map, transaction feasibility ADR, compatibility matrix, documentation disposition ledger, fixed qualification scenarios and budgets.

**Exit:** every high-risk architectural assumption has a tested answer or an explicit blocking decision. Do not start a broad directory migration while transaction feasibility remains unresolved.

## WP-01 — Module seams, composition and policy

**Owners:** platform architecture and identity. **Dependency:** WP-00.

- Introduce public module interfaces and composition root around existing implementations. Keep business logic in place initially.
- Establish immutable request/execution scope and structured error vocabulary. Reject malformed explicit scope. Eliminate implicit principal/workspace substitution on effect paths.
- Define policy evaluation and revision invalidation for direct reads, queries, queued effects and extension calls. Preserve existing direct-grant versus inherited-deny rules with explicit tests.
- Add static dependency/import gates with a finite baseline allowlist; fail newly introduced violations immediately. Add runtime registration tests for dynamically loaded resources.
- Establish Core boot/health and ordinary UI/API use with the model provider unavailable. The intelligence module may be loaded but operational services must remain usable.

**Handoff:** stable contracts and policy APIs for information, execution and SDK owners.

**Exit:** representative reads/writes use the seams; cross-workspace/permission tests pass; Core works without a domain package or working LLM; no new import cycles.

## WP-02 — Information, field identity and schema revisions

**Owners:** information module; frontend contract reviewer. **Dependency:** WP-01.

- Define stable field/type IDs and schema revisions, separating platform attributes from business fields. Publish compatibility mapping from existing keys and `custom_fields` storage.
- Route record validation, relation resolution, computed-field rules and typed errors through one contract. No guessing intent from current field values.
- Introduce optimistic concurrency and expected-schema validation; define bulk-write conflict reporting.
- Migrate a populated fixture including empty/null fields, renamed labels, relations, attachments and colliding status fields. Preserve existing IDs and access edges.
- Version serialization and generate shared frontend/SDK types. Characterize and migrate old clients deliberately.

**Handoff:** schema resolver, revision contract, migration mapping, typed projection/query vocabulary.

**Exit:** API, form and query resolve the same field on empty and populated records; stale writes conflict; rename preserves relations/views; no lost data or detached nodes in migration checks.

## WP-03 — Commands and durable execution consolidation

**Owners:** execution; persistence reviewer. **Dependency:** WP-02 and successful transaction spike.

- Consolidate command dispatch, operation identity, approvals, staging, work items and effect receipts around one authority. Reuse the existing work kernel rather than replacing it wholesale.
- Enforce local transactional writes plus durable receipt/outbox, unique claim and request-hash binding. Fail closed when required durability is unavailable.
- Make protected fields reachable only by their command, across UI/HTTP/resident/MCP. Generic commands still serve unprotected records.
- Persist plans, dependencies and pre-commit drafts. Resume from receipts and remaining obligations.
- Implement cancellation, expiry, revision changes, revoked access, leases/fencing and external unknown-outcome reconciliation.
- Specify pending legacy approval/work migration: translate only when exact revision/effect identity is known; otherwise expire with an explanation and require a fresh proposal. Never silently replay old pending work.

**Handoff:** execution API, receipt schema, transition table, reusable outbox/scheduling interface.

**Cutover contract:** [WP-03 execution contract](WP-03_EXECUTION_CONTRACT.md).

**Exit:** race/retry/crash tests prove one local logical effect, no stranded partial state, no memory-only success. Repeated approval has no additional effect. Cancellation accurately reports already committed changes. Existing prompts and brokers no longer decide execution truth independently.

## WP-04 — Application compiler, authoring and lifecycle

**Owners:** applications; execution/information reviewers. **Dependency:** WP-03.

- Implement versioned ApplicationDefinition and requirement ledger over existing profiles/packages.
- Add semantic compiler validation for relations, views, queries, commands, routines, permissions and supported constraints.
- Produce a readable preview/diff with meaningful business labels, affected records, side effects and limitations.
- Bind authorization to a compiled revision. Materialize using deterministic identities and durable steps; verify requested behavior after application.
- Move installation, migration, upgrade and routine provisioning onto durable execution. Record base definition and local overrides for three-way upgrade merges.
- Define reject/backfill/maintenance strategy for incompatible schema changes. Activate a new schema only after required migration passes; large migrations are checkpointed with reads/writes governed during transition.
- Preserve customization and data on pause/uninstall according to explicit policy; fence pending jobs and revoke extension capabilities.

**Exit:** interruption during build or upgrade recovers; unsupported business rules are reported before authorization; populated migrations preserve values and bindings; a failed install cannot appear active with a partial capability catalogue.

**Implemented contract:** [WP-04 application-definition contract](WP-04_APPLICATION_DEFINITION_CONTRACT.md).

## WP-05 — Governed queries and consistent views

**Owners:** query/projections; frontend views reviewer. **Dependency:** WP-02 contracts and WP-04 effective definitions.

- Unify field paths, filters, date/time semantics, ordering and projections for exact queries and dashboards. Preserve declared App query restrictions.
- Generate/validate view bindings from definitions; prohibit success with silently ignored configuration.
- Push bounded filters/aggregates through supported persistence capabilities; remove capped fetch-and-count behavior. Explicitly mark partial/incomplete data.
- Enforce policy on joins, counts, caches, citations and relation expansion. Invalidate schema/policy-dependent caches correctly.
- Separate semantic retrieval from exact operational questions; include freshness/provenance for derived or externally sourced data.

**Exit:** table/board/calendar/dashboard and agent answers agree on a known dataset exceeding one page; unauthorized records cannot be inferred from aggregates; failed queries never become 'none found'; projection rebuild restores equivalent results.

## WP-06 — Resident flow and skill replacement

**Owners:** intelligence; execution and product reviewers. **Dependency:** WP-03–05.

- Reauthor the skill set around discover → clarify → propose → preview → authorize → execute → verify → explain. Publish one owner for each responsibility; remove overlapping orchestration instructions.
- Persist requested obligations and acceptance assertions. Ask only questions that change the design; disclose reasonable defaults.
- Use progressive capability discovery and revision-aware context; measure token accounting before claiming reduction. Never cache across unauthorized scope or stale policy.
- Generate execution-status language from receipts. Keep business explanation flexible, but prevent pending/rejected/failed state from being rendered as saved/verified.
- Continue authorized dependencies after schema changes. Separate an additional request from a retry or correction of the same operation.
- Test deterministic fake-harness flows first, then bounded live-model evaluations. Record model/version/configuration and retain failed traces.

**Exit:** no repeated approval for one resolved revision; no fictitious completion; no lost dependent task; supported journeys meet frozen coverage, latency and cost budgets without hidden operator repair.

## WP-07 — Shared operational experience

**Owners:** frontend experience; accessibility and contracts reviewers. **Dependency:** WP-04–06.

- Build a coherent shell for data, application design, work/approvals and activity. Forms, navigation and views consume the same effective definition.
- Show draft/authorized/running/applied/verified/failed/cancelled states with precise partial outcomes. Attach approvals to semantic diffs and link receipts to changed records.
- Move technical traces/token diagnostics into inspectable details; retain meaningful progress and accessible announcements.
- Test cross-user collaboration, conflict resolution, permission changes, refresh/reconnect and deep links. Cover keyboard operation and error/empty/loading states.
- Ensure users can inspect and operate supported Apps without conversing with the model.

**Exit:** in-browser journey assertions verify actual fields, rows, grouping, dates, counts, approval state and persistence after reload—not merely screenshots of rendered pages.

## WP-08 — Public SDK, package trust and independent App

**Owners:** extension/platform DX; release and policy reviewers. **Dependency:** WP-04/05/07; SDK design starts WP-01.

- Package SDK separately with explicit compatibility/version policy, runtime resources and typed operation/query interfaces.
- Eliminate private imports and domain-specific Core facade APIs with downstream migration notes.
- Build an independent Asset Register package from public docs against installed Core artifacts, not checkout imports. Include relations, protected commands, declared queries, a custom view and a scheduled notification.
- Enforce release/development trust modes and installed artifact digests for server code, frontend assets and seed handlers. Test revocation and tampering.
- Exercise install/configure/update/conflict/pause/restart/uninstall and upgrade with local customization. Prove transport parity on real operations.

**Exit:** unfamiliar-developer trial succeeds; no Core patch/source-path injection; concurrent custody operation is atomic; real restarted routine delivers one logical notification; unsupported extension behavior fails intelligibly.

## WP-09 — Release and documentation cutover

**Owners:** release/operations and documentation; all module owners review their claims. **Dependency:** all prior packages.

- Run full gates, fresh artifact installation, browser suites, failure injection, extension parity and backup/restore drill against an exact candidate digest.
- Verify restored records, relations, attachments, definitions, package identities and work state—not only that a restore command exits successfully.
- Remove transitional adapters, old imports, duplicate schedulers/approval paths, obsolete skills and inactive configuration. Any retained compatibility layer has a supported consumer and documented end date.
- Execute documentation disposition ledger; replace root/subtree `AGENTS.md` guidance, docs navigation and roadmaps together. Move all applicable `CLAUDE.md` content into the nearest authoritative `AGENTS.md`, then remove every `CLAUDE.md`. No competing active design corpus remains.
- Enforce tested-artifact promotion in CI/publication configuration. Do not publish as part of this plan without explicit authorization.

**Exit:** acceptance ledger contains pass/failed/unproven for every criterion, with no unproven mandatory criterion; docs-only operator/developer trials pass; exact artifact, supported topology and limitations are recorded.

## Acceptance matrix

| ID | Required proof | Packages |
|---|---|---|
| A01 | Clean artifact install and first login; Core boots without commercial Apps | 01, 08, 09 |
| A02 | Ordinary UI/API remains usable when model provider is unavailable | 01, 07 |
| A03 | New violations of module boundaries fail the build; final allowlist has no unexplained legacy exceptions | 01, 09 |
| A04 | Wrong/malformed scope and revoked access fail consistently at every surface/effect boundary | 01, 03, 05 |
| A05 | Stable field identity across rename, null values and platform/business name collision | 02, 05, 07 |
| A06 | Concurrent command plus injected crash produces no partial local effect or duplicate receipt | 03, 08 |
| A07 | Exact approved revision executes once; correction, expiry and cancellation preserve accurate outcomes | 03, 06, 07 |
| A08 | Build resumes after restart and fulfills its requirement ledger without duplicate objects | 04, 06 |
| A09 | Exact queries and all rendered views agree above page limits and across date boundaries | 05, 07 |
| A10 | Schema alteration with populated records preserves data/views or rejects before unsafe change | 02, 04 |
| A11 | External unknown outcome is reconciled before retry; failures are not invented successes | 03 |
| A12 | Independent App works through UI, HTTP, resident and MCP with identical business enforcement | 08 |
| A13 | Upgrade retains customization; pause/uninstall revoke capabilities and fence pending work | 04, 08 |
| A14 | Restore reproduces data, relationships, files, package identity and recoverable work | 09 |
| A15 | Active documentation is coherent, linked, executable where applicable and free of superseded directives | all, 09 |
| A16 | Live-model journeys meet fixed success, intervention, latency and token budgets | 00, 06, 09 |

Mandatory live/browser scenarios: rental operations (field/status collision), service requests (workflow and assignments), and project/delivery tracking (relations and milestones). Each covers initial authoring, dashboard, record update, exact query, schema evolution and a correction. Add two-user permissions and interruption variants. Use Asset Register independently to prove extension behavior. Hold out at least one request variant per domain until qualification to discourage prompt overfitting.

Deterministic safety tests require zero violations. Live-model task success is measured over a predeclared repeated-run sample, not one successful demo; WP-00 freezes sample size and numerical thresholds. Unsupported requests must produce an honest capability boundary rather than fabricated implementation. No threshold may permit cross-workspace disclosure or false verified-completion claims.

## Migration and rollback strategy

Use one end-to-end slice at a time: read and update a typed record → execute a protected command → publish/evolve an application → full resident delivery. Avoid maintaining two writable sources of truth. Temporary routes call the canonical implementation; shadow reads may compare results, but shadow writes are forbidden.

Before schema/data cutover: take a verified snapshot, record version mapping, run migration on a representative copy and validate graph reachability/access. Rollback is supported only where reverse migration is proven; otherwise stop writes and restore the compatible snapshot/artifact pair. Never describe lossy reverse migration as safe rollback. External effects require reconciliation/compensation independent of database restore.

## Risks and control points

| Risk | Control |
|---|---|
| Framework cannot atomically persist all required effects | WP-00 transaction spike blocks dependent implementation |
| Directory moves disguise continued coupling | Public interface/dependency tests precede relocation |
| Rewrite expands into new product features | Finish-line acceptance matrix governs scope; marketplace/commerce/multi-worker remain deferred |
| Prompt tuning masks runtime defects | Deterministic command tests and saved-state/browser assertions precede live-model evaluation |
| Documentation deletion loses useful constraints | Per-file disposition and invariant-to-test mapping before removal |
| Old clients/packages rely on internal APIs | Compatibility matrix, bounded adapters and independently built reference package |
| Better UX hides partial failures | Receipt/evidence state drives UI and narration |

## Tracking and effort

Track each package with: owner, reviewer, dependency status, approved contract version, changed modules, migration impact, verification command/artifact, documentation rows closed and unresolved risks. Status vocabulary: planned / ready / in progress / blocked / verified. Do not use 'done' for a design-only artifact.

This is a multi-package architectural adaptation, not a short cleanup sprint. Calendar estimates should be set after WP-00 measures coupling and validates transaction support. The first implementation commitment is WP-00 plus the WP-01/02 typed-record slice; subsequent estimates use its observed throughput. This prevents an invented schedule from driving unsafe shortcuts.
