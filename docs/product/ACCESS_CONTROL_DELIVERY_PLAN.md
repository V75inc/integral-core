# Access-control delivery reset

Date: 11 October 2026. Status: authorized execution resumed; R1 primary-proof/policy/directory checkpoint qualified; R2/R3 parallel preparation. Branch: `codex/access-control-review`. Last committed milestone: `cb8670df`. This document governs sequencing and checkpoints; ACCESS_CONTROL_CONTRACT.md remains normative and ACCESS_CONTROL_PLAN.md retains the detailed requirements. No requirement is silently removed.

## Reassessment

The branch contains substantial implementation, but its closure ledger is stale and mixes historical failures with current qualification. At reassessment the ledger had 1/22 findings closed and 0/76 use cases closed; these are recorded closure counts, not estimates of code completion or effort remaining. AC-02 contains unrelated member-provision evidence; AC-21 and AC-22 still include qualification language predating subsequent repairs/gates. Reconcile evidence before reporting a new completion percentage. Do not bulk-close rows from test totals.

The notification cutover is staged and uncommitted. Its focused PostgreSQL and designated DeepSeek smoke passed. Its comprehensive run was interrupted on user instruction and is incomplete, not passed or a product failure. Preserve it and the prior failed characterization receipts. Member provisioning has an import-broken HTTP route and separately reproduced direct-service mailbox/delegation defects. Both require repair; restoring the route alone would expose an unsafe service.

The execution failure was excessive subdivision and repeated qualification without requirement closure. Existing services and tests are the starting point. Avoid replacing the authorization architecture, adding a second engine, repeating the original audit, or expanding the contract during ordinary implementation.

## Fixed delivery sequence

| Package | Concrete deliverable | Finding ownership | Exit |
|---|---|---|---|
| R0 Reconcile once | Map all findings and 76 scenarios to current code, named assertions, source revisions, remaining gaps and primary package. Correct stale or misplaced ledger text while preserving historical evidence. Review the pending notification diff. | All reporting; preserve AC-19 disposition | One finite backlog; each gap says exactly what behavior/test is missing. No broad test run or runtime changes. |
| R1 Secure collaboration baseline | Finish pending notification flow and member provisioning; converge invitation and membership producers on existing canonical services. Complete owner governance and admin ceilings. Close current-identity, scope, memoization, fail-closed, replay and output-release defects across supported consumers. | AC-01–09, AC-12, AC-15–16, AC-20, AC-22 | Owner invites verified user; delegate sees exactly granted resources; admin cannot expand authority; revoked/disabled/foreign actors cannot mutate or receive restricted output. Paired positive/negative assertions and relevant independent-worker/fault proofs close each assigned finding. |
| R2 Complete authority lifecycle | Adopt shared grant history/fences for all remaining grant writers; source-specific sharing, suspension/resumption, safe departure, atomic App/Track/workspace transfer, canonical containment lifecycle and truthful receipts. Finish role/author precedence and workspace governance audit. | AC-10–11, AC-17–18, AC-21 | Link disablement and sourced-grant revocation differ explicitly; suspension blocks re-entry; transfers preserve one owner through faults/retries; no unscoped survivors or stale grant resurrection. |
| R3 Deliver declarative delegation | Extend existing App definition/compiler and policy dispatcher with namespaced actions, flat workspace groups, grant ceilings and bounded conditions. Intersect agent/connector authority with current delegator rights. Guard protected transitions through all mutation paths. | AC-13–14 | Two reference Apps delegate editor/approver/configuration separately; generic CRUD cannot bypass a protected transition; group/declaration revocation works across UI/API/tools/deferred execution. |
| R4 Ship a qualified owner experience | Complete access management, effective-access explanations and audit UI. Implement and qualify migration/access diff, quarantined restore, operator recovery and cross-surface acceptance against a clean artifact. | All final integration and U-case acceptance | All 22 findings have evidence-backed disposition; all 76 use cases have applicable allow/deny evidence; browser/API/MCP readback, PostgreSQL concurrency and repository gates pass. No deployment implied. |

R1 is internally dependency ordered: (1) notification + provisioning + membership/invitation admission; (2) shared identity/workspace fence order and writer contract; (3) remaining scope/output consumers and owner inventory. Preserve the working repairs. Inspect the direct service and every supported caller together. Do not begin by inventing a new universal authorization framework.

Each package is delivered through bounded changes, each closing named acceptance assertions. Commit only after required repository gates; push tested milestones to the same branch. A package is not complete merely because one change has landed. Any discovered P0 blocks the affected package and is repaired or the affected unsupported path is explicitly disabled with compatibility evidence; other newly discovered work is recorded without automatically expanding the active change.

## Scenario ownership

Primary ownership below is scheduling, not a waiver of cross-cutting requirements. R4 independently verifies the final integrated journeys for every scenario.

| Package | Primary scenarios |
|---|---|
| R1 | U01–U18, U46–U48, U53, U56, U58, U61, U63, U65, U67, U71, U76 |
| R2 | U19–U45, U54–U55, U57, U59–U60, U64, U66, U68, U72 |
| R3 | U49–U52, U69–U70, U73 |
| R4 | U62, U74–U75; integrated acceptance for U01–U76 |

## Implementation and validation discipline

1. Use the existing golden workspace/actor fixtures, canonical permission services, membership/invitation services, transaction scopes, grant history, outbox and active App compiler. Consolidate alternate writers into these contracts. Classify every writer-inventory candidate as authorization writer, structural-only edge, unreachable/retired, or explicit remaining gap; static inventory is not runtime coverage.
2. For each bounded change, state its finding IDs, scenarios, affected invariants, concrete before/after behavior and exit assertions before editing. Include service-level denial so an alternate caller cannot bypass an HTTP-only gate. Preserve I-GRAPH-01/02, I-CRUD-01 and Core/App separation; semantic changes must agree with the existing normative contract.
3. During development run the affected tests. Security assertions inspect stored effects and disclosed fields, not only status codes. PostgreSQL tests cover actual transactional changes, ordered races and rollback; do not multiply equivalent fixtures. Use independent processes where the requirement is cross-worker, not merely two handles in one process.
4. Freeze runtime/test source before qualification. Run the required full backend/frontend and repository gates once for the completed milestone, then actual staged guards/hooks. Reuse evidence only when the relevant source and dependencies are unchanged and the repository gate permits it. A failed or interrupted required gate must finish successfully before commit. No hook bypass, skipped required full suite or false pass from stale artifacts.
5. Use DeepSeek 4.1 Flash Cloud (`ollama_chat/deepseek-v4.1-flash:cloud`) for live smoke when changed model/tool boundaries need qualification and for the final integrated smoke. Reuse the successful unchanged notification smoke; repeat only after a relevant change, failure, or final artifact requirement. Deterministic access decisions remain backend-enforced.
6. Every evidence reference records source revision/diff, named assertions, storage/surface and applicable limits. Historical failed probes remain historical. Close a finding when its stated defect and enumerated related obligations are fixed; do not indefinitely attach every platform-wide qualification obligation to every local defect. Use cases retain their own cross-surface acceptance status.

## Fast feedback and stopping rules

R0 has one output: the reconciled backlog. It must not become another audit. Before each subsequent change, choose a small explicit set of assertions it will close. After that change, report those closures and remaining failures before opening another area.

If two consecutive changes close no planned assertions, stop implementation and explain the dependency or design problem. If a test harness fails twice before reaching the target behavior, repair or simplify the harness before launching another broad run. Do not repeatedly run full qualification against moving source. Use a compact current status table; retain verbose machine receipts without duplicating them across narrative documents.

Report two measures separately: (a) verified findings closed out of 22, and (b) fully accepted scenarios out of 76. Show implemented-but-unqualified counts separately. No invented weighted completion or ETA. Package estimates follow R0's concrete remaining backlog, not the historical volume of code or tests.

## First implementation checkpoint after resumption

Repair member-provision route/service together using current actor authority, owner-only privileged delegation, exact role inputs and current verified identity for existing accounts. New owner-provisioned identities must not acquire unearned mailbox verification. Join grant/history/audit effects in the canonical transaction and deliver credentials outside it. Cover owner success, permitted admin delegation, admin escalation denial, unverified existing account denial, malformed input and rollback. Integrate the already prepared notification cutover. Reconcile relevant ledger rows, complete the interrupted milestone's required gates on frozen source, then commit and push. Report concrete assertions closed and whether whole findings now qualify; never label this first checkpoint the whole project.

User authorized resumption after finalizing this plan. Execution continues under the original consent to commit/push tested milestones; merge, deployment and release remain separate actions.
