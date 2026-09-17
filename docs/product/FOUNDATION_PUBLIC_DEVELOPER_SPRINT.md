# Sprint: Public developer foundation, proven by Asset Register

**Prepared:** 2026-09-17
**Status:** Implementation-ready planning baseline; no implementation or publication authorized by this document alone
**Execution:** Coding agents, bounded work packages, dependency-based waves
**Roadmap home:** F0 and F2, with the F1 recovery/release work required to make their guarantees credible; Asset Register is the limited F4 proof
**Product owner / architect:** Eldon Marks

## 1. Sprint goal

A public developer can build, package, install, use, and upgrade Asset Register—including app-owned data definitions, native app-surface views, APIs, operations, skills, and a scheduled warranty review—against a released Integral Foundation without editing Core or importing unsupported internals.

The implementation sprint produces release candidates and evidence for that statement. Actual pushes, PR creation, repository publication, and package publication require the explicit consent prescribed by `CLAUDE.md`. Until publication occurs, report the result as a locally verified release candidate, not a released platform.

The product is a foundation for complete applications. Asset Register is the proof of the extension contract, not a reason to embed inventory concepts in Core.

## 2. Decisions and planning assumptions

### Confirmed with Eldon

- Public developers are the first independent audience.
- Apps must be fully modular extensions of Integral's app surface: views, APIs, skills, and substrate, as well as business operations.
- A fixed asset inventory app is the preferred reference domain: cataloging company assets, warranties, check-in, and check-out.
- Coding agents will implement the sprint. Human engineer-day estimates are not the planning unit.

### Proposed implementation defaults

These are specific starting decisions for architecture review, not claims that the interfaces already exist.

1. **Reference app:** Asset Register, slug `asset-register`, distributed separately from Core; proposed public source license Apache-2.0. A separate source tree and independently built artifact are mandatory for the proof; creating the remote repository is a later publication action.
2. **Supported deployment:** Postgres is the authoritative persistence and concurrency acceptance environment. Preserve existing supported lightweight development paths, but do not claim concurrency guarantees verified only on an in-memory fixture.
3. **Backend extensions:** public authors can write executable packages, but production installation of executable Python requires explicit deployment-operator trust. A workspace admin cannot elevate arbitrary Python to trusted execution. The facade is a supported API boundary, not a Python sandbox.
4. **Frontend extensions:** support app-owned custom views in native app navigation through a constrained view host. Preferred first implementation is a sandboxed iframe with a typed, capability-limited bridge and Core theme tokens. Do not load untrusted modules into the main SPA JavaScript context. Validate the host choice in WP-00 before implementation; record the decision and its UX/security tradeoffs.
5. **APIs:** app endpoints are namespaced typed operation routes exposed through a generic Core dispatcher. Apps do not mount arbitrary FastAPI routers or middleware into the host.
6. **Data extensibility:** manifests and supported graph operations extend Core's data model. No app-owned parallel persistence, permission engine, or raw database access.
7. **Release posture:** developer preview of the documented contract, with explicit trust limitations. General untrusted backend execution and a public package marketplace are outside this sprint.

## 3. Current baseline and gaps

Evidence is from the local checkout, not a claim about production or the public registry. The preceding assessment ran `make verify-core-only verify-contract` successfully. Full verification must run again on the implementation candidate.

| Area | Existing foundation | Sprint gap |
| --- | --- | --- |
| Core extraction | Separate repo, Apache-2.0, package paths, core-only guards | Prove clean installation and operation from built artifacts outside the checkout |
| Extension contract | `docs/platform/extension-contract-v1.md`, ToolContext, hooks, manifests | Stable import namespace, complete typed operations, app API and view-host contracts |
| Reference apps | External-path Hello and commercial Hello fixtures | Independent app build, meaningful domain workflow, browser proof and failure scenarios |
| Views | Palette composites and registry; plugin initialization fetches substrate metadata | App-owned executable view host, bridge, package assets, lifecycle isolation |
| Lifecycle | Install attempts, install/upgrade/pause/resume/uninstall | Interruption, recovery, partial state, compatible schema upgrade and customization preservation |
| Migrations | Existing single migration dispatcher and async runner | I-MIG-02 documents non-resumption after process restart |
| Domain boundary | No-app-import guards and package separation | `ToolContext.get_employee_compensation` still interprets `base_salary` / `effective_date` |
| Seed hooks | Dynamic `seeds/post_install.py` runner | Trust enforcement, supported context, explicit error status and retry rather than silent success |
| Package verification | Existing bundle and plugin signature paths | Verify all executable/asset bytes against the artifact identity before loading; legacy plugin verifier requires review |
| CI/release | Smoke, contract, frontend gates; package publish workflows | Full Core-owned substrate gate, clean artifact tests, CI-success prerequisite for publishing |
| Developer experience | Authoring docs and contribution guide | One accurate quickstart, scaffold/build/validate/install workflow, independent developer trial |

Starting files: `backend/app/services/hooks/registry.py`, `bundle_post_seed.py`, `content_profile_plugins.py`, `app_lifecycle.py`, `services/migrations/runner.py`, `frontend/src/views/plugins/auto.ts`, `frontend/src/views/registry.tsx`, `.github/workflows/ci.yml`, and `backend/tests/contract/`.

## 4. Scope and completion contract

### Must deliver

- Versioned public backend and frontend extension interfaces, compatibility rules, and developer documentation.
- A discoverable package owning data schemas, one custom view, declarative views, typed operations/API routes, skills, and a durable scheduled warranty review.
- Consistent authorization, staging, validation, idempotency, audit, and error semantics across app UI, HTTP API, resident agent, and external MCP clients.
- Safe lifecycle registration and removal across restart; app-disabled behavior cannot remain callable through a cached route, tool, view bridge, or schedule.
- A real Postgres proof of conflicting checkouts and retry safety.
- A supported migration/recovery slice and backup/restore rehearsal covering the reference app's data, relations, documents, and package version.
- Core-owned test/release gates, built-artifact verification, and a public-developer onboarding exercise.

### Explicitly outside this sprint

- Depreciation, tax, purchasing, stock replenishment, financial accounting, and HR/payroll integration.
- Barcode scanning, mobile camera flows, bulk import, asset reservations, and external inventory connectors.
- Stripe, billing meters, a marketplace, automated publisher onboarding, and disconnected commercial licensing.
- General sandboxed execution of arbitrary third-party Python, arbitrary server middleware, or direct database plugins.
- Comprehensive custom field-widget hosting, every native UI slot, and arbitrary microfrontend frameworks. Ship the app detail/view slot and operation actions first, with an extensible slot contract.
- A platform-wide event-stream redesign, retrieval overhaul, global ontology, and general schema-downgrade engine.

These exclusions limit the first implementation. They do not weaken the requirement that the shipped Asset Register package owns its complete delivered surface.

### Non-negotiable exit tests

| ID | Observable result |
| --- | --- |
| AC-01 | Built Core boots from a clean environment with no commercial source tree, creates a user/workspace, and serves generic views and authenticated APIs |
| AC-02 | Independently built Asset Register installs using documented configuration/interfaces, with no Core source edits or private imports |
| AC-03 | Package-owned custom asset detail view and declarative table/board/calendar appear inside native app navigation; another workspace cannot access their data |
| AC-04 | Check-out/check-in through UI, HTTP, resident, and external MCP reach the same app operation implementation and enforce equivalent policy/state rules |
| AC-05 | Two independent concurrent requests to check out one available asset yield one success and one conflict, with one custody record; retrying a successful request does not duplicate it |
| AC-06 | Denied reads/writes, expired approvals, changed permissions, unavailable apps, and forged workspace/app identifiers fail closed |
| AC-07 | Compatible v1.0 → v1.1 upgrade preserves assets and user customizations; injected interruption has a supported diagnostic and recovery path |
| AC-08 | Pause/uninstall cancels or suppresses jobs, routes, tools, and bridges; restart does not reactivate them; retained records remain permission-checkable/exportable |
| AC-09 | Warranty review runs under an explicit principal and scope, survives restart, deduplicates its notification, and obeys pause/revocation |
| AC-10 | Administrator traces an approved checkout from request/proposal through authorization to operation result and resulting audit events |
| AC-11 | Modified artifact bytes, unsupported contract versions, and untrusted executable packages are rejected before code execution |
| AC-12 | Fresh environment restores the test backup with expected record, relation, attachment, and package-version integrity |
| AC-13 | A developer/agent unfamiliar with implementation follows only public documentation to scaffold, build, install, and modify a small app |
| AC-14 | Release artifact is gated on the exact candidate's required checks; evidence records commit, artifact digest, environment, results, and limitations |

## 5. Asset Register product slice

### Users and authorization

- **Asset administrator:** configures the app, manages catalog data and custodians, checks assets in/out, and reviews history.
- **Asset operator:** reads allowed assets and performs explicitly granted custody operations.
- **Reader:** views permitted catalog/history; cannot invoke mutations.
- **Agent:** acts under a resolved human or scheduled principal, with no extra authority inferred from being an agent.

Map these app capabilities onto Core policy and collaboration roles. Do not introduce a second role engine. The initial custody scope is app-wide; finer custodian-specific visibility is deferred and must not be implied by the UI.

### Data model

Use existing App → Track → Entry containment and profile-owned EntryTypes. Declare relations in the manifest; materialize named relationship edges through supported Core services. Every created graph participant must satisfy I-GRAPH-01.

| Track / type | Fields and relationships |
| --- | --- |
| Assets / asset | Unique normalized asset tag per app instance; title; category; serial number; purchase date; optional purchase amount/currency; lifecycle state; location reference; current custodian/custody reference; warranty start/end/provider; photos and documents as attachments |
| Locations / location | Name, site/address notes; no country-specific logic |
| Custodians / custodian | Display name, optional contact and optional Core member reference; no dependency on an HR app; personal data inherits normal access controls |
| Custody / custody record | Asset and custodian relations; checkout time, expected return, returned time, condition notes; actor and operation correlation; correction history |
| Service history / service record | Asset relation, service date, summary, provider, optional next-service date, attachments |

Custody records are ordinary governed domain records, not a substitute for Core audit events. Do not embed an unbounded history array in an asset. Define one authoritative source for current custody; if asset fields cache the active record, update both atomically and verify reconciliation.

Asset state: `available → checked_out → available`; `available → maintenance → available`; `available → retired`. Initial retirement of a checked-out asset is refused until check-in. Missing/lost and reservation workflows are deferred. All paths that edit protected state—including generic CRUD/profile writes—must preserve these rules or reject the write. A custom UI cannot be the only enforcement point.

### Operations and APIs

Proposed route shape: `/api/extensions/{app_instance_id}/operations/{operation_key}`. Final route and schema names are frozen in WP-00. Route implementation uses jvspatial `@endpoint`, schemas in `app/schemas/`, and canonical errors.

| Operation | Class | Behavior |
| --- | --- | --- |
| `list_available_assets` | Read | Paginated, permission-filtered availability with location/category filters |
| `register_asset` | Mutation | Validate tag uniqueness, references, dates, and authority; create governed record |
| `check_out_asset` | Mutation | Validate current availability and custodian; atomically open custody and change availability |
| `check_in_asset` | Mutation | Close active custody once; set available and record return condition |
| `record_service` | Mutation | Add service record through canonical writes |
| `review_warranties` | Read + notification | Query upcoming expiry, return grounded references, optionally create one authorized in-app notification per scheduled window |

Every mutation declares input/output schema, policy/capabilities, staging requirements, timeout, idempotency semantics, conflict codes, and emitted events. UI forms submit explicit user actions; agent-initiated custody changes require the existing prepare/bless/execute flow. API callers cannot bypass an operation's required approval by selecting another transport. Approval authorizes a particular operation/input/version; execute rechecks permissions and current asset state.

Scope idempotency by workspace, app instance, operation, principal and key; persist a request hash and result. Same key/different payload is a conflict. A database-backed transaction or conditional update must make custody changes and the idempotency result consistent. No process-local lock as the correctness guarantee; no direct database-driver imports to work around a missing jvspatial primitive.

### Interfaces, skills, and schedule

- App home: availability counts and links, derived through permission-filtered queries.
- Inventory table and availability board: app-owned declarative definitions using generic Core widgets.
- Warranty calendar: existing palette, app-defined mapping.
- Custom asset detail view: app-owned build output, condition/history/documents, check-in/out action forms, pending/conflict/error states, and keyboard navigation.
- Skills: `register_asset`, `find_available_asset`, `prepare_asset_checkout`, `review_warranties`; follow I-SKILL format and grounding rules. No invented IDs or claims of successful mutation before a result.
- Daily warranty review: workspace timezone, configurable horizon (default 30 days), opted-in recipient, explicit scheduling principal; produces an in-app notice. No outbound email or external connector required.
- Scratch-to-domain example: an agent may collect draft asset details in supported scratch memory, but final registration goes through the app operation and its controls.

## 6. Public extension architecture to implement

### Package identity and compatibility

An immutable package artifact contains manifest, schemas/migrations, declarative views, custom view assets, operation handlers, skills, and documentation. The build records all content hashes, Core/contract compatibility ranges, requested capabilities, and trust requirements. Workspace installation records the exact version and fingerprint.

Separate deployment-wide executable code availability from workspace-local app activation. All resolution uses workspace + app instance + package version; no global slug-only registrations that cross-serve tenants. Detect route/view/tool collisions before activation. For the preview, one executable version per slug per deployment is acceptable only if enforced and documented; attempts to install an incompatible second version must fail explicitly.

Signature verification must bind to the manifest and actual files loaded, including frontend assets and seed handlers. Test modification after signing and after validation. Missing production trust configuration fails closed; an explicit local development mode is visibly separate. Reuse existing signing primitives after reviewing them rather than building an unrelated signing stack.

### Public SDK and operation context

Choose a stable public namespace in WP-00 (proposed `integral_sdk`) and provide typed examples. Internally delegate to existing services. Apps may not import `app.models`, `app.services`, jvspatial persistence internals, or Core frontend source files.

Context provides scoped reads/relations, bounded pagination, canonical writes, attachments, settings, operation identity, audit correlation, and access to the supported unit of work. Callers cannot override the verified principal/workspace by supplying payload fields. Relation targets and attachment IDs are authorized individually. Multi-hop behavior uses walkers behind the facade.

Remove compensation interpretation from the Core facade through a documented compatibility transition; do not silently break downstream commercial callers. If a consumer cannot be changed in this sprint, retain a clearly deprecated adapter only under an explicitly documented temporary exception, with the release limitation visible. Completion cannot claim a fully domain-neutral facade while that exception remains.

### View host and frontend SDK

The package registers a view descriptor and immutable asset entry point. Core owns navigation, slot placement, loading/error fallback, generic record fallback, and lifecycle. The view owns its UI and requests data/actions through the supported bridge.

For the proposed iframe host: restrictive sandbox/CSP, no Core cookies/tokens exposed to the child, deny arbitrary network egress by default, validate message source and schema, and use a per-mount handshake bound to workspace/app/version. Opaque-origin frames require source-window/channel validation rather than reliance on origin alone. Server authorization remains authoritative for every bridge call. Revoke the bridge on unmount, logout, workspace switch, pause and package replacement. Define allowed theme/layout/navigation messages; prevent arbitrary parent DOM access.

Ship an SDK example that looks and behaves coherently inside Integral, including theme, accessible labels, focus return, loading, errors, and route restoration. Do not quietly replace the required custom-view proof with a declarative view if the host is unfinished.

### Lifecycle and recovery

Inventory routes, tools, hooks, views, schedules, and seed handlers as one app registration set. Activation occurs only after validation/materialization succeeds; rollback removes partial registrations. Persist sufficient state to reconcile after restart.

Upgrade tests use a real second artifact with an additive field/default migration and a view change. Preserve customer-added fields/views; preview collisions and refuse incompatible changes. Serialize app mutations during migration or use a defined version-aware write rule. Existing records must never be served as successfully migrated if only the manifest changed.

Bound this sprint's recovery implementation to supported declarative migrations and the reference workflow. Provide status, failed-item diagnostics, durable progress, restart reconciliation, and an authorized retry. Reuse the single migration dispatcher. Non-reversible changes need backup/restore or refusal, not a misleading “rollback” button. External effects are not undone by restoring a manifest.

## 7. Coding-agent execution model

Do not estimate delivery by number of agents or assume generated code eliminates integration effort. Work packages are the unit of execution; acceptance evidence determines completion. No fixed calendar commitment is made until WP-00 validates the critical primitives.

- One integration agent owns dependency sequencing, shared contracts, integration commits, evidence, and sprint status.
- Up to three implementation/review agents may run alongside it where the execution environment supports four concurrent agents. Reduce concurrency when paths or contracts overlap.
- Each assignment names files/modules it owns, inputs, forbidden imports, acceptance tests, and required output. Agents must not revert others' changes.
- Prefer isolated branches/worktrees with sequential integration of reviewed changes. If sharing one tree, enforce disjoint ownership and avoid concurrent staged-index operations.
- App agent owns an external source tree; it does not modify Core to make its app pass. Contract gaps return to the owning Core work package.
- Reviewers inspect behavior and counterexamples, not just implementation-mirroring tests. Review can be performed by a fresh agent after an implementation package completes.
- Every package returns a handoff: changed files, contract changes, checks and environment, known limitations, commit/artifact identifiers, and next dependencies.
- No agent may weaken tests, skip required gates, remove failed cases, or add domain exceptions to finish its assignment.

Complexity labels: **M** bounded work in an existing subsystem; **L** new public surface or cross-subsystem behavior; **XL** architecture uncertainty or persistence/security boundary. Labels are relative risk, not time estimates. Split XL packages into reviewed increments before dispatch.

## 8. Work packages

| ID | Package | Size | Depends on | Primary ownership | Acceptance |
| --- | --- | --- | --- | --- | --- |
| WP-00 | Contract decisions and feasibility | L | None | ADRs, contract schema drafts, architecture findings | Approved design and demonstrated required persistence/view-host primitives |
| WP-01 | Core artifact and verification baseline | L | WP-00 packaging decisions | Build config, CI, release workflows, artifact harness | AC-01, AC-14 baseline; exact candidate/digest tested |
| WP-02 | Backend SDK and typed app operations | XL | WP-00 | SDK, operation registry/dispatcher, Core schemas and adapters | AC-04/06; shared semantics, namespace isolation, concurrency primitive |
| WP-03 | App view host and frontend SDK | XL | WP-00; WP-02 schema for integration | FE view host, bridge, native slots, asset delivery contract | AC-03/06/11 browser tests |
| WP-04 | Artifact trust and lifecycle recovery | XL | WP-00, WP-02 registration contract | Package validation, lifecycle, seed hooks, migration reconciliation | AC-07/08/11 |
| WP-05 | Asset Register package and operations | L | WP-02 contracts; WP-04 for installation | External app manifest, handlers, fixtures, migrations | AC-02/05, data model and state invariants |
| WP-06 | Asset Register interfaces | L | WP-03 and WP-05 | External app custom view and declarative view assets | AC-03; no Core registry edit |
| WP-07 | Agent skills and durable warranty schedule | L | WP-02/04/05 | App skills; Core schedule adapters only by designated runtime owner | AC-04/06/09/10 |
| WP-08 | Adversarial integration and restore | L | WP-01 through WP-07 | Cross-surface/browser/Postgres tests, restore rehearsal | AC-01 through AC-12 |
| WP-09 | Public developer kit and independent trial | M | WP-02/03; final trial after WP-08 | Docs, scaffold/validate/build commands, SDK examples | AC-13 |
| WP-10 | Release-candidate closure | M | WP-08/09 | Evidence, release notes, compatibility matrix, status | AC-14 and all must-deliver gates |

### WP-00 — Resolve uncertainty before broad implementation

1. Read `CLAUDE.md`, relevant invariants, current public contracts, lifecycle, signing, scheduling, and jvspatial transaction facilities.
2. Produce one ADR covering operation routing, API namespace, public SDK imports, view isolation, installation trust, package version resolution, and lifecycle registration ownership.
3. Demonstrate a database-backed conditional state transition through jvspatial in a small fixture. If unsupported, identify the upstream change/version dependency; do not introduce raw SQL or claim a local lock solves it.
4. Prototype one package-owned view mounted with theme and one typed read request. Verify the sandbox/bridge behavior before committing to the host design.
5. Specify manifest additions as JSON/Pydantic schemas; do not silently overload the existing schema-version field. Decide contract versioning and migration of existing reference manifests.
6. Create an acceptance-to-test map, fixture shapes, and shared response/error examples before implementation branches diverge.

**Checkpoint:** Eldon reviews architectural choices and any invariant amendments; implementers resolve routine details within them. An unresolved persistence primitive or view-host design blocks dependent packages, not unrelated documentation/build work.

### WP-01 — Artifact-first baseline

- Inventory runtime resources: seed manifests, agent descriptors, skills, templates, contracts, migrations, static assets. Confirm wheel/sdist/container include and resolve everything required.
- Test from a fresh working directory with no source checkout on `PYTHONPATH`, no sibling commercial repo, and no developer `.env`.
- Verify dependency installation against intended indexes; make the jvagent pre-release/TestPyPI dependency explicit until a stable dependency is available. A successful editable install is insufficient evidence.
- Establish separate Core-only and Core-plus-app configurations. Resolve/document the distinction between the Core distribution and `INTEGRAL_CORE_ONLY`, which intentionally filters non-Core packages.
- Expand CI so Core owns full substrate verification, with a real Postgres integration lane and final full gate. Ensure scheduled runs actually select the full intended suite.
- Publish workflows depend on required success for the exact commit/artifact. `twine check` alone is not an application test. Record how manual-dispatch and tag paths enforce the same rule.

### WP-02 — Backend contract and shared operations

- Implement the SDK as adapters to canonical policy/write/graph services, including the transaction or conditional-write facility proven in WP-00.
- Add typed, namespaced discovery and invocation; HTTP, MCP and frontend actions bind to one operation definition. Generated operation discovery includes schemas and approval rules.
- Validate lifecycle, workspace scope, user grants, trust/capabilities, and approval at execution time. Audit denied and failed attempts with appropriate redaction.
- Persist idempotency records and define retention/retry windows. Reject payload mismatch; recover ambiguous outcomes by checking the durable result.
- Enforce protected domain state rules on every mutation path using supported hooks/validators; propose an invariant amendment if a new hook point is required.
- Strengthen no-private-import checks across external handler, seed, migration, and frontend code. Include planted-violation tests so guards cannot pass vacuously.

### WP-03 — Frontend host

- Build generic view descriptors, mount/unmount, bridge schemas, theme/action APIs, asset verification/delivery, and loading/error/generic fallback.
- Wire native app navigation once in Core; no `asset-register` branch, slug mapping, or hardcoded component in Core.
- Test workspace switches, two app instances, package replacement, stale messages, forged operation IDs, invalid payloads, and expired bridges.
- Browser-check keyboard navigation, focus management, screen-reader labels, empty/error/conflict states, and narrow layouts. Existing unit tests do not establish iframe/CSP behavior.

### WP-04 — Trust, lifecycle and migration recovery

- Reconcile existing bundle/plugin signing and post-seed paths into the declared trust policy. Validate before import; reviewed trust is deployment-controlled.
- Pass a public installation context to seed hooks. Required seed failure marks install failed/recoverable; it must not report a healthy app with missing prerequisites.
- Build durable registration reconciliation for pause/uninstall/restart and failed activation; drain or cancel in-flight work according to documented semantics.
- Implement supported migration status/retry/restart behavior, bounded batches and idempotent checkpoints; update I-MIG-02 accurately.
- Test a real compatible version upgrade, user customizations, corrupted artifact, failed seed, failed migration, restart, and restoration. Explicitly refuse unsupported downgrade/destructive cases.
- Preserve existing entitlement-loss behavior and run commercial Hello as a regression fixture; no billing expansion.

### WP-05 / WP-06 — Independent Asset Register

- Create the app in an external source tree using the SDK/scaffold; retain Hello as a cheap minimal compatibility fixture.
- Supply deterministic synthetic records for two workspaces, two app instances, different permissions, available/checked-out/retired assets, service history, attachments and expiry boundaries.
- Implement uniqueness and transition rules in the package using generic Core enforcement facilities. Validate date ranges and condition fields; do not use title-based lookups as identity.
- Exercise duplicate HTTP requests, retries after lost responses, and concurrent checkout from separate sessions/processes.
- Build declarative views and the custom asset detail asset bundle; build tooling emits a single installable artifact with a content digest.
- Ship a second artifact version that changes actual schema/view behavior and provides the supported migration.

### WP-07 — Agentive and scheduled proof

- Publish discoverable app skills and tool bindings scoped to accessible active app instances. Keep the singular resident harness; no new peer-agent service.
- Reuse staging and the routine scheduler. Define explicit owner/grant handling, revocation checks, retry limits, misfire behavior, timezone and notification deduplication.
- Deterministic tool-level tests cover approval binding, argument validation and scope; a real configured-model run demonstrates the intended dialogue. Record provider/model for the live run, but do not let nondeterministic wording be the main correctness gate.
- Verify the resident and external MCP client cannot see or invoke another workspace's app skills/operations, including concurrent turns.
- Audit warranty review scheduling and execution; notification generation must not require an unauthorized broad system identity.

### WP-08 — Integration and recovery evidence

- Run the acceptance matrix in section 10 against built artifacts and Postgres.
- Exercise both normal and fault paths in the browser and through API/MCP clients.
- Rehearse backup/restore into a disposable environment, including attachment storage and required key/config handling; never overwrite a developer or production database.
- Ensure snapshots are consistent or taken under an explicit quiesce procedure. A successful SQL restore alone is not a complete app restore.
- Report findings by severity, fix release blockers, and rerun affected tests plus required final gates.

### WP-09 / WP-10 — Developer and release closure

- Document clean prerequisites, local development trust, production trust, SDK support boundary, package lifecycle, error diagnosis, and uninstall/export behavior.
- Provide scaffold → validate → build → install → test commands; test the commands verbatim in a fresh environment.
- Independent trial: an agent with only the public docs and released/local-built SDK adds a field and a view/action to a new app. Log every missing instruction/private dependency. Target first successful install within 60 minutes excluding dependency download; record actual duration rather than treating an estimate as a result.
- Reconcile README, extension contracts, extraction guide, roadmap and release notes, including the `personal-context` discrepancy and historical monorepo language.
- Add public security-reporting/support guidance and known preview limitations. Publication metadata must not promise isolation or compatibility that was not tested.
- Produce a release candidate with exact revisions, artifact hashes, tests, compatibility matrix, unresolved issues and the final demo recording/screenshots. No success claims for skipped checks.

## 9. Dependency waves and integration checkpoints

| Wave | Agent work | Integration checkpoint |
| --- | --- | --- |
| 0 | WP-00; read-only artifact/doc inventory | Freeze architecture and acceptance schemas; validate persistence/view feasibility |
| 1 | WP-01, WP-02 first slice, WP-03 first slice | Empty external package builds/loads; one typed operation and one custom view work |
| 2 | Finish WP-02; WP-04; WP-03 integration | Trusted activation, policy/staging, bridge and durable state primitives converge |
| 3 | WP-05, WP-06, WP-07 with dependency-aware handoffs; WP-09 drafting when a slot is free | Asset checkout/check-in works across all surfaces; warranty schedule and upgraded artifact available |
| 4 | WP-08 plus independent WP-09 trial | Fault, tenant, concurrency, artifact, browser and restore evidence passes |
| 5 | WP-10 | Eldon acceptance, then separately authorized release/publication |

Limit simultaneous implementers to available slots; a wave is not permission to run every named package concurrently. Shared manifest/SDK edits are owned by WP-02's integrator; shared CI edits by WP-01; lifecycle changes by WP-04. Agree interfaces before consumer agents start. Merge/integrate in dependency order and rerun impacted gates after integration.

Reserve roughly one quarter of execution capacity for integration, defect correction, and independent review. This is a scheduling policy, not a promised number of days or tokens. Re-estimate after Waves 0 and 2. If an upstream jvspatial dependency or view isolation design materially expands scope, split the sprint at an explicit acceptance boundary instead of labeling incomplete modularity “done.”

## 10. Verification matrix

| Scenario | Required evidence |
| --- | --- |
| Empty foundation | Fresh installed artifact, boot logs, signup/workspace/API/browser smoke, no commercial sources mounted |
| Independent app | Build/install log and package import scan; Core source hash unchanged by app installation |
| Policy | Reader denied mutation; outsider denied read; app/workspace mismatch; revoked actor; cross-app relation/attachment target checks |
| Shared semantics | Same valid/invalid operation payload through UI, HTTP, resident tool and external MCP; equivalent decisions/errors |
| Approval | Changed arguments, changed record state and changed permissions after approval rechecked; no stale-approval bypass |
| Concurrency | Separate clients/processes, real Postgres, one active custody; same-key retry returns prior outcome; different payload conflicts |
| Generic write bypass | Direct record edit/import/profile write cannot forge protected custody state or duplicate unique asset tags |
| Package trust | Modified backend/UI/seed bytes, missing key, unsupported version, untrusted code and namespace collision rejected |
| UI isolation | Forged messages, old bridge, workspace switch, revoked app, unauthorized navigation/data/operation, package crash fallback |
| Lifecycle | Failure at each activation boundary; pause/restart/resume; uninstall retains/exports as declared; no stale callable registration |
| Upgrade | Real v1.0/v1.1 pair, additive migration, customer customization, failure mid-batch, restart and authorized retry |
| Schedule | Timezone and expiry-date boundaries, retry deduplication, missed run, owner revocation and disabled app |
| Audit | Correlated allow/deny/failure/retry, no secrets or unnecessary custodian data in events; no duplicate successful effects |
| Restore | Counts, representative field values, graph reachability, relation integrity, attachment hashes, package identity |
| Public developer trial | Instructions followed without Core internals or implementation assistance; gaps fixed and rerun |

Performance is a bounded regression check, not a scalability claim: use a documented fixture of 10,000 assets with custody history, specify machine/Postgres/cache conditions, and measure paginated list p95 against the existing 300 ms roadmap budget and draft/publish p95 against the existing 1 s budget where applicable. Measure small-operation API latency separately from LLM latency. Investigate failures; do not silently change targets or hydrate whole collections to make the demo work.

For every substrate change preserve applicable I-GRAPH-01/02, I-CRUD-01, I-SUBSTRATE-01, I-EXT-01/02, I-HOOK-01/02, I-APP-01..07, I-MIG-01..04, I-SKILL-SCOPE-01, I-SKILL-01..04 and access/staging invariants. List exact invariants in each implementation handoff; any amendment has an ADR and regression test.

Required implementation verification includes pre-commit hooks, `make verify`, `make verify-core-only`, `make verify-contract`, the configured Postgres lane, and new artifact/browser/SDK tests. Confirm actual commands in the checkout before execution. Never treat guards that scan only staged files as a whole-tree architectural audit. No bypassing real failures to commit.

## 11. Risks and decision rules

| Risk | Consequence | Mitigation / decision |
| --- | --- | --- |
| Generic atomic mutation unavailable in jvspatial | Custody safety cannot be guaranteed | Prove in Wave 0; upstream dependency is a blocker, not a raw-driver exception |
| UI host is larger than expected | Package still cannot own its interface | Prototype early; reduce UI slots and polish, retain one real custom-view proof |
| Signature mistaken for isolation | Public executable packages over-trusted | Separate identity, operator trust, tenant consent and capabilities; clearly disclose trusted Python execution |
| Migration rollback overpromised | Data loss or inconsistent schema after interruption | Supported operation subset, durable checkpoints, refusal and restore rather than fictional reversibility |
| Concurrent agents diverge on schemas | Integration churn and hidden bypass paths | Single contract owner, fixtures first, short handoffs, sequential integration |
| Reference app receives exceptions | Commercial apps cannot reuse the platform | External build; no app slug/field branches in Core; report missing contracts |
| App generic CRUD bypasses operations | Invalid custody/availability despite safe custom UI | Enforce protected rules at canonical write boundary and test every entry path |
| Fresh installs rely on local files/index config | Public quickstart fails | Built-artifact installation without checkout; verify runtime resources and indexes |
| Existing commercial callers break | Core separation harms downstream deployments | Compatibility inventory and consumer test; staged deprecation and explicit release notes |

Cut order if capacity is exhausted: cosmetic dashboard polish, advanced service-history editing, extra UI slots, and optional developer conveniences. Do not cut cross-tenant controls, concurrency, package-owned custom UI, independent installation, recovery, or full release gates. If those cannot be completed, close with an explicitly incomplete developer preview and a follow-up plan, not the sprint goal achieved.

## 12. Final demonstration and acceptance

1. Start clean Core from its candidate artifact; create two workspaces and users with distinct grants.
2. Install Asset Register from its independent package; inspect version, capabilities and trust disclosure.
3. Register a laptop with a warranty and document; browse package-owned native views.
4. Ask the resident to find an available laptop and prepare checkout; approve and inspect custody/audit history.
5. Run two competing checkouts on another asset and demonstrate one conflict; retry the successful request without duplication.
6. Show reader/outsider denial and external MCP access under the same constraints.
7. Run the warranty schedule, then retry it; demonstrate one grounded notification.
8. Upgrade to the second artifact while retaining a customer-added field; interrupt a supported migration and recover using the documented interface.
9. Pause and restart; demonstrate disabled operations/jobs/views and retained generic data access. Resume, then exercise uninstall/export in a disposable fixture.
10. Restore a backup in a separate environment and verify records, relationships, documents and package identity.

Eldon accepts product behavior and architectural fit. The integration agent supplies technical evidence, not self-certification based on completed task counts.

### Closure checklist

- [ ] Every AC-01..14 has a passing evidence link or is explicitly marked incomplete.
- [ ] Core and reference app build independently; app has no privileged imports.
- [ ] Invariants, public contract, SDK examples and compatibility rules agree with implementation.
- [ ] Required tests, lint, builds, browser and Postgres verification pass on the integrated candidate.
- [ ] No unresolved release-blocking isolation, trust, data-loss, retry or lifecycle findings.
- [ ] Developer trial succeeds; quickstart and runtime prerequisites are reproducible.
- [ ] Release notes, security-reporting route, support limitations and artifact digests are recorded.
- [ ] Human acceptance is recorded; publication status is reported separately.

## 13. Planning sources

- [Foundation-first roadmap](ROADMAP.md#21-foundation-first-priority-reframe-draft--2026-09-15)
- [Foundation, extension and SaaS direction](FOUNDATION_EXTENSION_SAAS.md)
- [Extension contract](../platform/extension-contract-v1.md) and [governance](../platform/extension-contract-governance.md)
- [Substrate invariants](../INVARIANTS.md)
- [Core extraction](INTEGRAL_CORE_EXTRACT.md) and [commercial pin](CORE_PIN.md)
- [Agent guide](../../CLAUDE.md), [release procedure](../../RELEASING.md), and [CI workflow](../../.github/workflows/ci.yml)

This sprint refines the F-series execution order; it does not reinstate historical `.planning/STATE.md` as the source of current project status.
