# Integral Core: finish line and completion plan

**Date:** 2026-09-19
**Target confirmed by Eldon:** “An independently usable open-source Core, with reliable agent-guided app building and a proven public extension contract.”
**Assessment baseline:** `e987626`, advancing to `4515f18fffa82ff00d955ce9272ee084abc9ebe1` during inspection. The latter merges the durable work kernel. Concurrent installation/onboarding changes were visible in the working tree; they were not edited or certified by this assessment.
**Scope:** Current source, selected executable checks, and comparison of acceptance claims with their actual tests. This is a completion assessment, not a full security audit or release certification.

## 1. What finished means

A person can install Integral Core without the commercial repository, describe an operational need, refine a design with the resident, authorize it, and receive a usable application on the governed substrate. They can operate and evolve that application through both the UI and the resident. Failures leave an explainable, recoverable state rather than silent partial success.

An independent developer can ship an App containing schemas, views, operations, skills and scheduled behavior using a documented, versioned SDK. It installs and operates against the distributed Core without source patches or private imports. Upgrades, permission changes, restart, pause and uninstall preserve the platform's guarantees.

These are two authoring paths over the same platform:

| Path | Required result |
| --- | --- |
| End user → resident → declarative app | A complete operational workflow using supported schemas, relations, views, skills and routines; verified against the user's requirements |
| Developer → packaged extension | Additional domain behavior and custom UI through supported APIs; independently built, installed and tested |

An agent-authored skill is not itself an enforced business invariant. Requirements such as concurrent booking exclusion must use a supported transactional operation, an appropriate installed extension, or a clearly explained limit. “Finished” does not mean arbitrary natural-language requests can produce arbitrary software without limitations. It means reliable delivery within a published capability envelope, with honest refusal and recovery outside it.

### Outside this finish line

Stripe/billing, commercial packaging, a public marketplace, a broad portfolio of vertical Apps, every external connector, and enterprise-scale multi-worker deployment. Preserve extension points for them; do not make them prerequisites for usable Core. One documented, enforced deployment topology is sufficient for the first release.

## 2. Current position

**Core is a substantial developer-preview platform. Its next phase is contract completion, integration and proof, rather than another broad feature expansion.**

Already present:

- Independent Core repository, generic graph/schema/access surfaces, package discovery and Core-only guards.
- Profile introspection and authoring, UI view palette, resident skills, staged writes, chat-confirmed greenfield building, and conversation artifacts.
- Typed App operation routing, a capability broker with run receipts, a public SDK source tree, custom iframe view hosting, and package signature machinery.
- An external-path Asset Register example with domain operations and skills.
- Durable work items, leases, approval/outbox handling, recovery logic and Postgres-specific tests.

These are assets to finish and integrate. Their presence does not establish the stronger release guarantees below.

## 3. Findings that determine the remaining work

### A. Distribution and independent installation are not yet proven

**Verified:** `.ci/verify_artifact_baseline.sh` changes into `backend/` and imports modules using the checkout's virtual environment. It does not build a wheel/image, install into a clean environment, start an installed runtime, or exercise the browser.

`backend/pyproject.toml` discovers `app*`; the public SDK lives outside that tree under `sdk/python/integral_sdk/`, with no separate `sdk/python/pyproject.toml` found. The resident descriptor/skills live in the top-level `agent/` tree. The Dockerfile copies the agent separately, so Docker and Python distribution need explicitly distinct, tested installation contracts.

**Needed:** Select the supported release unit(s), package all required runtime resources and SDK dependencies, and prove a clean installation without editable installs, source-path injection, local harness checkouts or commercial trees. Make the README and developer quickstart follow that same path. Concurrent onboarding changes address parts of this area; assess their final diff before assigning duplicate work.

### B. The advertised extension facade does not match the reference App

**Verified:** `examples/asset-register/tools/helpers.py::create_track_entry` requires `ctx.create_entry`; runtime `OperationContext` inherits `ToolContext`, which has no such method. A runtime introspection check returned `create_entry=False`. The narrower `create_entry_in_own_bundle_track` exists, but targets the principal's own personal workspace and is not an equivalent general App-instance write API.

The public contract lists `create_entry` as supported. The SDK protocol omits several methods the reference App uses. Custody tests provide a custom context containing the missing method, hiding this mismatch.

**Needed:** Define and implement a least-privilege, App-instance-scoped write contract; align SDK protocols, runtime, examples and documentation. Test the actual injected context. Remove or relocate Core's remaining compensation interpretation (`get_employee_compensation`, `base_salary`, `effective_date`) with a deliberate downstream migration.

### C. Atomic operations and retries need a stronger contract

**Verified:** Asset checkout conditionally changes availability, then creates custody, then updates the custody reference. These are separate awaited writes with compensating behavior, not one demonstrated transaction. The operation idempotency implementation looks up a result before execution and stores it afterwards; durable storage failures are logged and may fall back to process memory.

The broker adds run receipts and replay handling, and the new work kernel adds leases. Those are useful layers, but they do not by themselves make the domain writes and idempotency receipt one atomic operation.

**Evidence gap:** Postgres custody tests prove a conditional claim and an entry-state update, not the entire real custody workflow across independent callers/processes. Transport parity uses a Hello echo and mocked authority; it does not establish mutation parity for an installed reference App.

**Needed:** A public transaction/conditional-operation mechanism, durable contention-safe idempotency, structured business-failure propagation, and an end-to-end proof that exactly one checkout succeeds, exactly one custody record exists, and retry/crash cannot strand the asset. Exercise UI, HTTP, resident and external MCP against the same operation.

### D. Resident app delivery is improved but not acceptance-proven

**Verified:** Current scaffold skill owns design through verification, includes relations, procedures and routines, and uses chat affirmation to authorize greenfield commit. There are checks for missing track setup and unresolved batch references.

**Verified test gap:** `test_car_rental_build_through_tools_and_approval` is a non-strict expected failure. With xfail disabled it fails at `design["token"]`: the test still assumes the former design-token protocol. This is evidence of a stale acceptance harness, not proof that the current live build fails at that point.

**Remaining design weakness:** Open build batches still live in the process-local `_open_batches` dictionary. Conversation artifacts and durable work items do not automatically persist an uncommitted batch or bind its operations to a versioned approved blueprint.

**Needed:**

- One versioned blueprint/checklist: requested capability → schema/operation/view/routine → verification evidence.
- Bind confirmation to the current design and supported effects; handle correction, cancellation and later expansion explicitly while preserving the intended single-confirmation experience.
- Persist build progress and recovery state, including pre-commit work; resume after interruption without duplicating entities.
- Verify operational behavior after writes, not just existence/counts. Check relations, forms, view mappings, business-state changes and active reminders.
- Replace the xfail with a current deterministic acceptance test, then add bounded live-model evaluations across materially different domains and correction/recovery variants.
- Declare supported model/provider configurations and measurable success, latency and cost budgets. Never use “no tool errors” as the sole success criterion.

### E. Recovery and lifecycle are uneven across subsystems

**Verified:** The durable work kernel exists and focused recovery tests pass. Conversely, `services/migrations/runner.py` still launches process-local tasks and documents orphaned pending/running work after restart with manual retry deferred. Chat turn admission and WebSocket fan-out remain process-local; the accepted deployment posture is one worker.

**Evidence gaps:** The reference upgrade test preserves an App settings marker while changing version/description; it does not migrate populated data, preserve a customer-added schema field, or inject interruption. The warranty tests cover materialization, pause/resume and mocked scheduler dispatch, not a real restarted execution producing one notification. The backup test checks successful dump/drill output, not a fixture's records, relationships, attachments and package identity.

**Needed:** Bring migrations and builds under a durable lifecycle or provide explicit recovery mechanisms. Test restart, expired approvals, permission revocation, pause/uninstall and interrupted upgrade against populated applications. Restore a known fixture into a separate environment and compare data, relationships, files and package versions. Keep multi-worker support deferred until admission and event delivery are both shared; enforce the supported single-worker posture.

### F. Trust needs a release-mode boundary

**Verified:** Bundle signatures cover file payloads when verification is enabled. `verify_bundle_signature` treats a missing key as successful development mode and bypasses signature verification for Python-free packages. Custom frontend assets are executable content too. The view host resolves filesystem package assets.

**Needed:** Specify and enforce an explicit development-versus-release trust policy; decide whether immutable installed artifacts or verification at load/use binds actual code/assets to the installed digest. Test assets and seed handlers as well as Python tools, mutation after validation, lifecycle revocation and cross-workspace view access. This assessment identifies the closure work; it is not an exploit assessment.

### G. Release evidence overstates some acceptance results

**Verified:** `RELEASE_CANDIDATE.md` labels several broad ACs Done using narrower tests. Exact candidate/digest recording is deferred. The independent quickstart trial used internal lifecycle/registration helpers and did not exercise custom views; it is useful development evidence but not a clean public-only onboarding proof.

The publish workflows depend on a build job, not successful candidate verification. CI has smoke/contract/Core-only/frontend/Postgres lanes, but its scheduled trigger still selects the same smoke backend suite despite a comment describing a full nightly run. `extension-contract-v1.md` still describes custom view loading as deferred even though a view host exists.

**Needed:** Make Core own its full release gate. Tie publication to the exact tested artifact and commit. Convert the AC map into evidence with explicit pass/partial/unproven states. Update the public contract and onboarding instructions from tested behavior.

## 4. Ordered completion work

Order follows substrate/contracts → operating services → extension proof → end-user experience → release. Product acceptance criteria should be established immediately and exercised throughout.

| Package | Priority / dependency | Deliverable | Exit evidence |
| --- | --- | --- | --- |
| C0 — Freeze the finish line and candidate | First | Confirm scope above; pin candidate; reconcile AC claims; identify concurrent work | One acceptance matrix with owners, commands, results and known limitations; no ambiguous Done labels |
| C1 — Distributable Core and SDK | P0; after C0 | Supported install artifact, complete resources/dependencies, public SDK/runtime parity, reliable setup | Clean environment installs and boots, signs in, creates workspace, opens generic UI and starts resident; independently built App loads without source edits |
| C2 — Governed atomic operation contract | P0; after C0, coordinate with C1 | Scoped writes, transactions/state transitions, durable idempotency, consistent receipts/errors and approval semantics | Real Postgres conflict/retry/crash tests across separate callers; no partial custody or duplicate effect; denied/changed authority fails closed |
| C3 — Durable lifecycle and trust | P0; after C1/C2 contracts | Recoverable build/migration state, installed artifact identity, lifecycle revocation, explicit release trust | Kill/restart, upgrade, pause/uninstall, tamper and restore drills against populated Apps; documented single-worker deployment works |
| C4 — Independent reference App proof | P0; after C1/C2, completion after C3 | Asset Register built separately using public APIs; actual custom view, operations, skills and scheduled notification | Real user workflow through UI/HTTP/resident/MCP, upgrade with customization, denied access, one restarted warranty notice, restore integrity |
| C5 — Reliable resident app delivery | P0; contract alongside C0, runtime after C1/C2 | Approved blueprint → complete verified app → routine operation/evolution; recoverable orchestration and bounded evaluations | Current car-rental acceptance is mandatory and green; additional domains, corrections, empty builds, partial failures and restart pass; live-model results report actual requirement coverage |
| C6 — Public release qualification | P0; after C1–C5 | Accurate docs, docs-only trial, complete CI/release gate, evidence manifest and operational runbooks | Unfamiliar developer succeeds from released artifacts/docs; exact digests pass required lanes; human acceptance recorded |

These packages should remain bounded implementation units with explicit module ownership and acceptance evidence. Do not estimate completion by counting existing files or by turning this into a second broad feature roadmap.

### First implementation tranche

1. Reconcile the actual SDK/OperationContext and the reference App, including the missing create capability.
2. Replace source-import “artifact verification” with a genuine clean install/boot test.
3. Repair the stale resident acceptance harness for the current chat-confirmation flow and make it required.
4. Establish a real full-operation Postgres test for custody atomicity and retry safety before claiming AC-05.

These expose the most important constraints early. The subsequent durable lifecycle work should reuse the new work kernel where appropriate, not create another scheduler or approval system.

## 5. Final demonstration

Starting with a fresh environment and built artifacts:

1. Install Core from documented instructions; no source checkout or commercial repository is required by the supported deployment path.
2. Create two users/workspaces with distinct authority.
3. Describe an operational need, refine one design, confirm, and receive a usable app. Verify forms, relationships, views, procedures and reminders from actual state.
4. Correct and extend the app without losing records or creating duplicates; interrupt a build and recover.
5. Install independently built Asset Register. Use its custom view and the same operation from HTTP, resident and MCP.
6. Race checkouts, replay a successful request, and interrupt execution; show one coherent custody history.
7. Upgrade while preserving a customer field; interrupt and recover; pause/restart/uninstall without lingering callable behavior.
8. Restore into a separate environment and verify records, relations, attachments and package identity.
9. Have an unfamiliar developer create and install a small extension using public docs only.
10. Record exact commit/artifact digests, checks and limitations. Publication is a separate authorized action.

## 6. Verification performed for this reassessment

- `make verify-core-only verify-contract`: passed on the inspected checkout; three Postgres-only contract tests were skipped in this run. This does not certify the Postgres lane.
- Focused resident/batch/design/work recovery suite: **70 passed, 1 xfailed**. The expected failure is the end-to-end car-rental acceptance test.
- The car-rental test with `--runxfail`: failed at the removed design-token assumption (`KeyError: 'token'`). Its later acceptance checks were not reached.
- Runtime facade introspection: `OperationContext.create_entry` absent; `create_entry_in_own_bundle_track`, `get_entry`, `conditional_update_entry_fields`, and `emit_audit` present.
- Attempted local wheel build without dependency installation: unavailable because this environment lacks a runnable `build` module. No built artifact was certified.
- No fresh browser, Postgres race, restore, live-model evaluation, or full release gate was run for this assessment. Earlier-task test output was not used as current release evidence.
- The API rebuild attempted during the cancelled task was not executed because automatic approval review could not complete due to a usage limit. No new deployment was performed here.

Concurrent edits and the moving HEAD mean a release qualification must rerun against a frozen candidate. This assessment changed only this report.

## 7. Evidence index

- Product target/context: `docs/product/CONCEPT.md`, `FOUNDATION_EXTENSION_SAAS.md`, `FOUNDATION_PUBLIC_DEVELOPER_SPRINT.md`.
- Public claims: `docs/platform/extension-contract-v1.md`, `docs/backend/adr/011-public-app-extension-platform.md`, `docs/product/RELEASE_CANDIDATE.md`, `docs/product/ACCEPTANCE_TEST_MAP.md`.
- Distribution: `.ci/verify_artifact_baseline.sh`, `backend/pyproject.toml`, `backend/Dockerfile`, `sdk/python/integral_sdk/`, `docs/developer/quickstart-trial-log.md`.
- Extension writes/atomicity: `backend/app/services/hooks/registry.py`, `backend/app/services/app_operations/{context,dispatch,idempotency}.py`, `examples/asset-register/tools/{helpers,custody}.py`.
- Delivery: `agent/agents/integral/integral_agent/actions/integral/embedded_integral_action/skills/integral_scaffold/SKILL.md`, `backend/app/agentive/{staging,batch_validation}.py`, `backend/app/agentive/tooling/dispatch.py`, `backend/tests/test_operational_app_build.py`.
- Recovery: `backend/app/agentive/services/work_*.py`, `backend/app/services/migrations/runner.py`, `backend/app/services/chat_turn_registry.py`, ADR-005.
- Trust/lifecycle evidence: `backend/app/services/operational_model_signature.py`, `backend/app/services/app_extension_views.py`, `backend/tests/contract/`.
- Release gates: `.github/workflows/{ci,publish-pypi,publish-testpypi}.yml`, `RELEASING.md`.
