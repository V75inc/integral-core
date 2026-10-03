# Integral Core main review for TestPyPI

Reviewed 2026-10-02, America/Guyana. Source: **`340093fe10f8597e7de504ccb0e8795da3f401fa`**, clean `main` at review start. Scope clarified by the Product Owner: TestPyPI publication.

**Recommendation: hold the next publication while repairing R01–R06 and closing the dependency-audit disposition.** The current source and independent packaging gates pass, but focused probes found gaps in plugin trust, extension write governance, conditional updates, and audit atomicity. Those gaps are material to the platform's public contract. Passing the existing suites does not close them.

The version remains `0.1.1rc11`. TestPyPI already contains that version; the current-main publishing run skipped building and uploading distributions. `0.1.1rc12` was absent at review time and is a possible next candidate, subject to a fresh index check when cutting it. A version bump should come after repairs and qualification, because merging one to main automatically triggers publication.

## Scope and evidence

This is a broad, risk-based review of the existing state, not merely the latest diff. Inventory covered `backend/app/` and `frontend/src/` (1,440 files), repository invariants, current release status, dependency metadata, workflow definitions, packaging scripts, and relevant tests. Direct source inspection concentrated on authentication, workspace scope, permission caching, public sharing, the App compiler, SDK/facade, operation/query dispatch, transactions, receipts/outbox, plugin trust, resident/broker integration, extension UI, and frontend session/request handling. Full-tree automated checks and suites complement that inspection. This is not a claim that every source line received a manual audit or that every possible defect was found.

No application fixes, dependency upgrades, version bumps, commits, pushes, publications, or remote workflow dispatches were performed. Disposable probes used synthetic principals and mocked authorization; one probe used actual persisted records in an isolated temporary PostgreSQL cluster. No provider credentials or billable model calls were used. The temporary cluster was stopped after testing.

Raw local evidence is retained in the ignored directory `.qualification-evidence/core-review-340093fe/`. It includes gate logs, audit output, collection manifests, and reproducible probes. Keep those logs internal; publish this report and selected reviewed evidence separately.

| Check | Result and boundary |
| --- | --- |
| `make verify` | **Pass**: guards, all-files pre-commit, pinned format/lint, frontend lint/types, artifact baseline, CI-style smoke, frontend suite, default backend suite. Index-only guards had no staged files; their success is not whole-tree proof. Other guards, including the graph AST and import boundaries, scan actual source. |
| Default backend suite | **Pass**, with 3,897 selected of 4,360 collected in a matching collection run. PostgreSQL/environment-dependent tests skip in the default lane; `domain_app` and `slow` are excluded. Do not interpret 3,897 selected as 3,897 executed passes. |
| Frontend suite | **217 files / 1,292 tests passed**. |
| `make verify-core-only verify-contract` | **Pass**, with PostgreSQL contracts skipped in that invocation. |
| `make verify-independent-artifacts` | **Pass**: reproducible Core wheel/import, fresh dependency-resolved ASGI import, SDK wheel/import, signed extracted reference App loading. Core wheel SHA-256 from this local gate: `f1972d58936ba0dece1b894ac982fc226e3bbee403b33d74a30788b50c730fc0`. This wheel was not the publication workflow's frontend-bundled distribution. |
| `npm run build` | **Pass**, with a 1,202.93 kB main JS chunk (334.26 kB gzip) and size warnings. |
| Fresh local `make test-postgres-ci` | Spike passes; **28 of 29 contract tests pass**. The restore test fails after 180 seconds because the local Docker daemon does not answer. No successful local Docker restore claim is made. |
| Current-head GitHub CI | [Run 37061441541](https://github.com/V75inc/integral-core/actions/runs/37061441541) succeeds for backend, frontend, independent artifacts, PostgreSQL, and both Docker builds. This is remote evidence separate from the local Docker limitation. |
| Current-head TestPyPI workflow | [Run 37061441494](https://github.com/V75inc/integral-core/actions/runs/37061441494) succeeds as a workflow, but publish and distribution-building steps **skip** because rc11 already exists. |
| Dependency audit | Frontend gate **fails** on the braces dependency chain. Backend normal audit command fails in the local `ensurepip` subprocess; a pinned `--no-deps --disable-pip` audit succeeds with two ignored matches under the repository's existing exception ID. Retain both results; do not call the original `make audit` successful. |
| Browser/deployed/model acceptance | No new browser journey, deployed-image qualification, live-model exam, or independent author trial was performed in this review. Unit tests and builds do not replace these. |

Prior release qualification informed the gate separation, but all readiness statements above use current source or current retrieved evidence. The existing C6 packet is for `eff58c4`, not reviewed head `340093fe`. Its A15 independent author trial and new Architecture/Product Owner decisions remain pending in `CORE_ACCEPTANCE_LEDGER.md`; this review does not silently close those rows.

## Findings

P1 means repair before the next generally usable release candidate. P2 means a concrete correctness, contract, or assurance gap requiring scheduled remediation or an explicit candidate disposition. P3 means improvement backlog. These priorities describe release risk; they do not imply that an unauthenticated attacker can reach every affected path.

### R01 — P1: Plugin signatures do not authenticate the code being loaded

**Evidence:** `backend/app/services/operational_model_plugins.py:98` calls `verify_key.verify(signature_bytes)` and discards the recovered message. It never compares that message with a digest of the plugin files. A disposable probe signed unrelated bytes, placed them beside different `__init__.py` contents, and `_verify_signature` returned `True`. This differs from `operational_model_signature.py`, which computes a directory payload for bundles.

The entry-point path at `operational_model_plugins.py:194` also calls `ep.load()` before signature policy is evaluated. With a public key configured, unsigned entry points can therefore execute import-time code before being rejected for registration.

**Impact:** the advertised signed-code gate can accept modified or unrelated plugin content. This assumes an untrusted or altered plugin artifact is present on a discovery path; it is not evidence of a remote upload exploit.

**Remedy:** authenticate a canonical file manifest/digest against the actual artifact before importing it. Define entry-point artifact verification before `load()`, or disable that discovery path under required-signature posture until it can verify. Use explicit development trust configuration and report `unsigned/development` separately from `verified`.

**Acceptance:** changing any executable file or helper invalidates the signature; swapping a signature from a different artifact fails; an unsigned entry point's import side effect never executes in required-signature mode. Keep signed external-App loading green.

### R02 — P1: Facade field updates bypass the governed Entry write path

**Evidence:** `backend/app/services/hooks/registry.py:526` checks the principal's editing role, merges `custom_fields`, and saves the Entry directly. It does not bind the target to `workspace_id` or the current App, run Entry field/relation validation, apply protected-field restrictions, check the migration write fence, or update the schema revision. `OperationContext` inherits this method. In contrast, `backend/app/api/entries.py:543`, `:620`, `:640`, and `:711` enforce these checks on HTTP updates.

A mocked probe gave the principal an editor role on an Entry outside the context workspace. The facade returned `True`, saved its arbitrary field value, and emitted an audit event. This does not show access without a role; it shows that having access elsewhere defeats the active-context boundary. The reference App actually calls this facade (`examples/asset-register/tools/custody.py`, `service.py`), so it is a live public surface.

**Impact:** an extension can write a resource outside its execution workspace or bypass the schema/protected-state contract while using the published facade. Normal HTTP writes and extension writes have different guarantees.

**Remedy:** route facade mutations through a shared, complete Entry mutation service with explicit `ExecutionScope`, App/Track ownership checks where applicable, policy, schema/relation materialization, protected-state authority, migration fencing, revision checks, and audit sink. Do not merely redirect to `entry_writer.update_entry_internal`, which currently documents deferred custom-field validation itself.

**Acceptance:** facade and HTTP paths reject the same invalid fields, stale schemas, protected generic writes, fenced migrations, and out-of-scope targets. Verify both legitimate cross-App contracts and forbidden cross-workspace targets.

### R03 — P1: The conditional-update helper uses a noncanonical persisted shape

**Evidence:** `backend/app/services/entry_conditional_update.py:8` selects collection `n`; lines 47–49 query and update top-level `custom_fields`. jvspatial 0.1.0 persists Nodes in `node`, with attributes under `context`. An actual PostgreSQL probe saved the canonical shape and called this helper: result was `(False, "write_denied")`, and `state` remained `available`.

The existing `test_entry_conditional_update_concurrent_one_wins` in `backend/tests/contract/test_custody_concurrency_postgres.py:69` seeds exactly the helper's artificial `n`/top-level shape and mocks `Entry.get`. That test passes while missing the real storage mismatch.

Additionally, `type(db).__name__ == "PostgresDB"` silently falls back to read-modify-write for wrapped databases, and the helper obtains the prime database instead of the enclosing graph transaction. Its atomic path therefore lacks a reliable capability/transaction contract.

**Remedy:** use jvspatial's actual collection and field representation through a transaction-bound, public CAS API. Replace class-name branching with capability checks and fail closed where atomicity is required. Do not issue a separate prime-database write from inside a durable operation.

**Acceptance:** create a real rooted Entry through normal persistence, race two claims, and prove exactly one wins. Run with the production wrappers and inside an operation that later raises; the claim and receipt must roll back together.

### R04 — P1: Declared reads receive mutation authority

**Evidence:** `app_operations/dispatch.py:190` treats `kind: read` as nondurable, but `run_handler` at line 220 always sets `operation_write_active(True)` and supplies mutable `OperationContext`. A focused read-operation probe observed that flag as `True` and found the update facade available. `app_queries/dispatch.py:155` also supplies `OperationContext`, including `create_entry` and inherited updates, to query handlers.

**Impact:** a mistaken trusted query/read handler can produce writes through the public facade while Core classifies it as a read, omits durable command enforcement, and evaluates the read policy. Facade role checks limit which users can edit; they do not enforce a read-only operation class.

**Remedy:** provide a read-only query context and enforce execution class at mutation boundaries. Grant protected write authority only to an authorized command within its governed execution unit. Trusted Python is not a process sandbox, but the supported facade must reject this misuse.

**Acceptance:** queries and read operations attempting create/update/conditional-update/notification effects fail before any effect, audit emission, or successful query receipt. Ordinary reads still work across all supported transports.

### R05 — P1: Inherited mutation audit events bypass the command outbox

**Evidence:** `OperationContext.create_entry` at `context.py:163` supports `deferred_change_events`. Inherited `update_entry_fields` calls `emit_change_event` directly at `hooks/registry.py:563`; `entry_conditional_update.py:74` does likewise. The probe used an `OperationContext` with an empty deferred sink: it observed one immediate audit emission and zero deferred events.

**Impact:** an App command may emit an externally persisted/broadcast update event before its graph transaction commits. If a later handler step fails, the graph and receipt can roll back while the audit event survives. Conversely, process failure around that immediate emission lacks the intended committed-outbox recovery. The reference App's update-heavy operations take this path.

**Remedy:** carry the operation event sink through every supported mutation facade, including updates, CAS, attachment changes, and notifications as appropriate. Persist event facts with the graph effect and receipt, then emit only after commit.

**Acceptance:** a real operation updates a real Entry then throws: graph, receipt, and audit all show no committed update. A successful operation interrupted after commit produces its audit event through recovery without rerunning the graph effect.

### R06 — P1: Publication runs independently of source CI

**Evidence:** `.github/workflows/publish-testpypi.yml:32` defines its own build job. Its publish job depends only on that build, not the current SHA's backend/frontend/PostgreSQL/advisory checks or the full local gate. `RELEASING.md` explicitly lists a release-gate wait on CI as out of scope. PyPI has the same structure.

**Impact:** merging an unused version can publish before source CI finishes, or despite source CI failure, provided the narrower artifact checks pass. Current successful rc11 runs demonstrate a skip, not qualification of a new publication.

**Remedy:** make candidate qualification a reusable workflow that publication must successfully await for the exact checkout SHA. Carry that SHA and tested wheel digest into the upload job; fail closed on missing or mismatched results. Include advisory checks and the repaired critical contracts. Keep OIDC publishing and digest verification.

**Acceptance:** inject a test or advisory failure in a disposable workflow fixture and prove upload cannot run. A successful qualification publishes only its verified artifact. Check both push and dispatch ref handling.

### R07 — P2: Advertised operation metadata is only partly enforced

**Evidence:** the compiler retains `staging_level`, `timeout_seconds`, and `output_schema` (`operational_model_compile.py:2274`). Operation dispatch neither enforces the declaration's timeout nor validates the operation output. Direct query handlers likewise omit output validation. Tool-bound dispatch validates a tool schema, which need not equal the operation's declared output schema. `operation_bridge.py:102` generates `staging_level: required`, but the broker/dispatcher do not enforce that declaration; they execute and only recognize a staged-change response after the handler returns.

Probes supplied an output schema requiring a missing property. Both operation and query dispatch returned the invalid output; the existing shared validator rejected the same output. A handler delayed longer than its declared timeout still returned normally.

**Remedy:** define and implement the declaration semantics consistently. Validate final declared outputs before transaction commit; enforce or explicitly reject unsupported timeout/staging values at compile time. Do not present `required` staging as a guarantee until execution enforces it. Timeouts around external effects must preserve unknown-outcome recovery rather than blindly retry.

**Acceptance:** invalid command output rolls back its effect; invalid query output fails clearly; a required-staging operation creates an approval without executing its effect; timeouts have bounded, documented receipts and recovery behavior.

### R08 — P2: At-least-once operation events lack the promised stable dedupe identity

**Evidence:** `event_outbox.py:141` emits the stored event unchanged. It computes a stable outbox ID for reconciliation but does not add that ID to the delivered envelope's metadata. The module says consumers must deduplicate by outbox ID, yet the probe showed no such ID in emission arguments. Status is saved after emission, and concurrent consumers can both observe pending state before emitting.

**Impact:** crash/replay or simultaneous sweeps can generate duplicate audit/feed events that downstream consumers cannot reliably identify as the same fact. One poison pending record also aborts the current sweep because per-record exceptions are not isolated.

**Remedy:** propagate a stable event fact ID into persisted/broadcast metadata, define consumer dedupe, and use an appropriate claim/lease where required. Bound and isolate per-record failures with observable retry/dead-letter state. Preserve the distinction between at-least-once delivery and exactly-once local effects.

**Acceptance:** crash after emission and concurrent delivery preserve the same visible fact ID; a consumer applies one logical fact; one malformed event does not starve other pending events.

### R09 — P2: `notify_once` is neither atomic nor App-scoped

**Evidence:** `app_operations/context.py:40` scans existing notifications, then creates one with no storage uniqueness claim. Two concurrent mocked calls using the same key produced IDs `1` and `2`. Lookup matches only user and `dedupe_key`, although metadata includes App and operation IDs. A different App using the same key can therefore receive the first App's notification ID instead of creating its own notice. The `Notification.find` field query itself is valid: jvspatial normalizes ordinary field names into `context` paths. The defects are the identity and non-atomic check/create sequence.

**Remedy:** define a durable identity including principal, workspace/App, and logical notice key; claim it atomically in the graph unit of work while retaining notification graph reachability. Use indexed canonical queries instead of hydrating all notices.

**Acceptance:** same logical notice across concurrent workers/restarts yields one rooted Notification; the same local key in two Apps yields independent notices; test actual persistence rather than mocked lists.

### R10 — P2: Dependency-audit status needs a current disposition and reliable failure reporting

**Evidence:** the current frontend audit fails on high findings propagated from `braces` through `micromatch`, `chokidar`, `fast-glob`, and `tailwindcss`. These are a dependency chain, not five independently reproduced vulnerabilities. npm proposes Tailwind 4.3.3, a major migration. The [reviewed braces advisory](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm), updated October 2, lists no patched braces version. This is build-tool exposure; this review did not demonstrate public browser/API exploitability.

`.ci/dependency_audit.sh:127` discards npm's exit code. Empty/unparseable output is skipped, and a valid JSON error envelope lacking `vulnerabilities` can be reported clean. A controlled fake-npm probe returned an audit error envelope and exit code 1; the repository gate still reported clean and exited 0. Backend stderr is suppressed; this local run mislabeled an `ensurepip` SIGABRT as a fixable advisory failure. A pinned no-resolve backend audit found no unsuppressed advisories.

**Remedy:** distinguish successful audit, findings, and infrastructure failure; missing/error payloads must not pass. Preserve useful error diagnostics. Assess braces reachability and either migrate the build chain with rendered QA or retain a specific, reviewed temporary advisory disposition. Do not blindly force a major Tailwind upgrade. Track lower-severity runtime findings such as the current DOMPurify advisory even if the blocking gate filters them out.

**Acceptance:** registry outages, npm JSON error envelopes, and malformed output fail the assurance check. A live successful audit produces explicit accepted/actionable findings and a timestamped candidate disposition.

### R11 — P2: The scheduled “full” CI run still runs smoke only

**Evidence:** `.github/workflows/ci.yml:40` promises nightly coverage plus slow/integration tests. The only backend test command at line 132 unconditionally selects `smoke and not domain_app and not slow`; no scheduled-event full-suite branch or `INTEGRAL_RUN_SLOW_TESTS=1` exists. `make verify` also intentionally excludes slow tests under the default pytest configuration.

**Impact:** the claimed nightly safety net does not execute, and a green scheduled run can leave non-smoke regressions untested. The passing local default suite in this review closes today's default-lane check, not future scheduled coverage.

**Remedy:** add a distinct scheduled full-Core lane with explicit marker/env selection, PostgreSQL where needed, coverage, and retained results. Keep domain-specific examinations external to Core. Make the workflow's description match its actual commands.

**Acceptance:** retained schedule results show non-smoke and intended slow tests collected and executed, with exclusions explained.

### R12 — P2: Domain knowledge still leaks into the Core facade

**Evidence:** `hooks/registry.py:426` exposes `get_employee_compensation` and hardcodes `effective_date` and `base_salary`. Operation result shaping at `app_operations/dispatch.py:254` special-cases `asset`, `custody`, and their IDs. These APIs work, but domain-specific semantics remain in independently distributed Core.

**Impact:** Core's facade and receipt logic depend on particular application concepts, conflicting with the substrate/App separation objective. New domains either add more special cases or receive incomplete object references. Automatic drift guards do not fully catch this semantic coupling.

**Remedy:** move compensation resolution into its App; expose a generic authorized relation/projection API if needed. Define generic declared output object-reference mapping or a typed result envelope rather than inferring assets/custody. Provide a bounded compatibility/deprecation path for existing SDK consumers.

**Acceptance:** an independent non-asset App receives correct references without Core edits; Core-only boot and public SDK contracts pass; domain helpers disappear from Core after consumers migrate.

### R13 — P2: Single-use and bounded-access workflows need transaction-level race coverage

**Evidence from source inspection:** `password_reset.py:267` reads and validates the reset slot, saves the new AuthUser password at line 303, revokes sessions, then clears the slot at line 313. Consumption is not an atomic claim. `share_links.py:325` checks a redemption count, grants membership/collaboration, then increments/saves at line 377 without a transactional reservation. Two overlapping requests can pass the same prior state. These concurrency outcomes were not exercised against real accounts in this review.

**Impact:** single-use reset and max-redemption contracts depend on timing. Partial failure can change a password without consuming its token, or grant access without a consistent redemption record. Session revocation also remains best-effort after a reset.

**Remedy:** atomically claim/reset tokens and reserve bounded redemptions; couple the relevant graph/auth effects with durable recovery where one transaction is unavailable. Define successful reset semantics for failed token revocation. Review the same pattern in email verification and invitations.

**Acceptance:** two resets using one token yield one successful consumption; two distinct principals racing for one remaining share redemption yield one grant. Faults between claim/effect/finalization converge without reusable credentials or extra access.

## Additional improvement backlog

These observations should not distract from the P1 repair work:

- Frontend lint has **392 warnings and zero errors**. Pay down stale-hook/dependency and state-in-effect warnings by affected user flow; introduce a decreasing baseline rather than globally suppressing them. Unit-test console warnings also obscure meaningful failures.
- The main frontend bundle exceeds 1.2 MB minified. Measure initial load on the intended test-client network and split infrequently used views/editors where evidence supports it. Build success alone does not establish load-time quality.
- Process caches and registries are extensive. Permission cache invalidation is process-local with a 20-second TTL and tests disable it by default. Verify the supported single-worker topology, then require cross-worker revoke/reload tests before expanding worker count. This review does not assert a current supported-topology isolation exploit.
- Guidance has drifted: supplied AGENTS scope instructions describe fallback for a bare workspace ID, whereas current `request_scope.py` correctly rejects an explicitly malformed header. The acceptance ledger also retains an older packet labelled “current” below its authoritative top section. Reconcile operational instructions and status headings so reviewers follow one candidate.
- Keep `main` source checks, frontend-bundled wheel checks, installed CLI/server checks, and deployed browser checks separate. The clean-install lane imports ASGI with `DEBUG=true`; add an exact published-wheel lifespan/readiness and CLI journey under normal auth posture. Existing image/browser evidence belongs to its recorded SHA and digest.

## Ordered remedial work

| Package | Ownership and scope | Dependencies | Exit evidence |
| --- | --- | --- | --- |
| A — Plugin trust | Extension/trust owner; plugin verifier and entry-point admission, R01 | First; independent of Entry work | Artifact tamper and import-before-verification tests; signed App/plugin loading |
| B — Governed facade | Substrate owner; shared Entry mutation service, scoped read/write contexts, R02/R04 | Establish the common boundary before CAS/audit repairs | Real Entry parity, scope denial, protected fields, migration fencing, read-effect denial; enumerate preserved I-CRUD-01, I-APP-05, I-EXT-01, graph and work invariants |
| C — Atomic updates and events | Execution/storage owner; transaction-bound CAS, deferred update events, R03/R05/R08/R09 | B | Real PostgreSQL concurrent claims, rollback after multi-effect failure, post-commit replay, event dedupe, singular scoped notice |
| D — Declaration semantics | Extension/SDK owner; schemas, staging/timeout admission, generic results, R07/R12 | B/C for rollback guarantees | Invalid outputs roll back; required staging has no preapproval effects; bounded timeout recovery; domain-neutral external App proof |
| E — Release assurance | Release/CI owner; exact-SHA dependency between qualification and upload, audit failure handling, nightly selection, R06/R10/R11 | Can prepare alongside A–D; publication waits for closure | Failing candidate cannot upload; successful audit or documented specific disposition; actual full-Core scheduled lane |
| F — Auth/share races | Authentication/access owner; reset consumption and share redemption, R13 | Transaction/recovery pattern from C where applicable | Race and partial-failure tests using real stored principals/tokens in disposable databases |
| G — Candidate cut | Release owner plus Architecture/Product Owner | Required repairs and candidate dispositions | Unused rc version; refreshed lock; exact-SHA source gates; bundled wheel hashes; exact wheel installed outside checkout; ledger/A15 and acceptance decisions reconciled |

Do not turn these into one large refactor. Each package should expose a bounded change and acceptance evidence; retain public SDK compatibility deliberately. Re-run the full substrate suite for substrate changes and full frontend checks for changed UI behavior. Regenerate the capability map if the manifest, skill, binding, or example-App surface changes.

## TestPyPI cut criteria

1. Repair R01–R06, decide/close the dependency-audit result, and record the remaining P2 dispositions. Include R07 staging semantics before claiming an approval guarantee.
2. Freeze the repaired candidate, choose an unused rc version (rc12 was available during this review), and refresh version/lock-dependent checks and release documentation.
3. Pass source, Core-only, contract, fresh PostgreSQL, frontend build, advisory, and independent artifact gates on that candidate. Complete intended slow/recovery checks separately from default `make verify`.
4. Build frontend and resident harness into the exact distributable wheel. Install that exact wheel outside the checkout, check dependencies, run CLI/server startup under normal authentication, and retain its source identity and SHA-256. Test the public SDK/extracted App against it.
5. Reconcile existing acceptance requirements against the new SHA. If using the C6 finish declaration, close A15 and obtain the pending independent Architecture/Product Owner decisions; do not carry `eff58c4` image results forward as new-candidate proof.
6. Publish only after the exact candidate's required checks succeed. Then download the uploaded files, compare hashes, and repeat the scoped clean installation using only Core/jvagent wheels from TestPyPI and ordinary dependencies from PyPI.

The result of this review is a repair and qualification recommendation. It is not a TestPyPI release authorization or a declaration that current Core is C6 complete.
