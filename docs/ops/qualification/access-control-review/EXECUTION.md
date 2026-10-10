# Access-control implementation execution

The authorized goal is full closure of the review and its 76 use cases. All implementation, tests, migrations and UX changes land on **`codex/access-control-review`**. The isolated checkout is based on `9d13b44e`; unrelated App/UI changes remain in the original checkout. User authorization includes milestone commits and pushes. It does not imply merge, release publishing or production deployment.

[Closure ledger](closure.json) records each finding and case without treating observation probes as secure acceptance. [Plan](../../../product/ACCESS_CONTROL_PLAN.md) defines the contract and invariants.

| Milestone | Deliverable | Completion gate |
|---|---|---|
| M0 | Preserve assessment, branch, closure ledger and clean baseline | Hooks, build/type checks and baseline evidence |
| M1 | Immediate perimeter defects and safe response replay | Paired allow/deny tests for invitation proof, events/cache/failure gates and replay |
| M2 | Canonical scope, live authorization and structural lifecycle | Foreign facade rejection, corruption denial, multiprocess revocation fences |
| M3 | Owner governance, membership/delegation, durable grants and transfers | Owner/private-resource matrix, safe self/peer administration, atomic invite/link/offboard races |
| M4 | Versioned App actions, groups and conditions | Independent reference Apps, protected mutations and equivalent execution surfaces |
| M5 | Owner administration UX and migration/recovery | Independent-account browser readback, audience migration diffs and restore proof |
| M6 | Complete qualification and residual audit | Every ledger row has actual allowed/denied evidence, full gates and all findings closed |

Before each commit, stage only scoped files, run hooks and required clean build/tests; substrate runtime milestones require the full suite. Push tested milestones to the same branch. Qualification failures remain open and are repaired before committing. Never rewrite failed evidence as successful.

## Progress

Goal active. M0 preserves the assessment, authorized plan, single branch and complete closure ledger. No runtime fixes are claimed at this milestone.

Clean-checkout characterization reproduced all 23 observations (32.60 seconds). The baseline guards, pinned format/lint, TypeScript, reproducible wheel/import and no-env smoke/xdist checks passed. The initial frontend run on unsupported Node 23.10 failed one streaming test (1,582 passed); the same test passed on bundled supported Node 24.19. The complete Node 24 frontend suite passed all 1,583 tests (279 files); the full backend baseline remains in progress. These checks qualify the starting state, not remediation or release readiness.
