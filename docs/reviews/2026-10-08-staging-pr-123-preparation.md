# Staging integration and PR 123 preparation

PR 123 targets `codex/pr-113-staging` and remains open for review. Its original
description omitted later ownership/privacy changes and duplicated the already
integrated chat-version/Home/model-admission work. The final description must
describe the actual remaining diff against staging.

## Review repairs

- Enforce private-library workspace policy on existing App/Track merges and
  validate the complete dependency chain before the first merge write.
- Move private library visibility/install policy into a service. App lifecycle
  must not depend on an API implementation for this policy.
- Do not swallow ownership/grant lookup failures during membership removal.
  Create replacement ownership before deleting the departing ownership.
- Use a walker for scoped resource-grant inventory, including Entry parents.
  Inspect its public report because jvspatial records hook failures there.
- Join PostgreSQL ownership, grant, active-workspace, and membership mutations
  in one graph transaction. Reload graph entities inside the held transaction.
  JSON/SQLite retain ordered development behavior: membership is removed last,
  with no claim of atomic rollback on these stores.
- Durable native chat keeps approved effects behind worker acceptance, lease
  and effect fences. Synchronous host follow-through may execute approved
  staging; the durable HTTP preparation path must not.
- Bind optimistic App rows and asynchronous lists to their workspace. Discard
  stale responses after a workspace switch and retain multiple acknowledged
  creations until the scoped list includes them.
- Preserve both scaffold-error diagnostics and durable conflict diagnostics,
  ordered receipt-note persistence, pending Prompt Sheet resumption, and the
  asynchronous Home observer assertion when combining PRs 122 and 123.

## Invariants preserved

- I-CONV-01..03 / I-CRUD-01: decorated API routes delegate mutation to services;
  no new raw FastAPI routes, inline request schemas, or database drivers.
- I-GRAPH-01 / I-GRAPH-02: ownership remains an explicit graph edge, Entry grant
  scope follows its structural Track edge, and no persistence type is changed.
- I-SUBSTRATE-01 / I-EXT-01: policy remains independent of App identity.
- I-ACCESS-01: privacy remains per resource; no field visibility is introduced.
- I-WORK-01 / I-WORK-02 / I-WORK-05 / I-HARNESS-02: durable effects retain current
  lease, exact effect identity, approval and accepted-input authority.
- Tenant scope: departure changes only the selected workspace; private library
  templates remain inaccessible from another active workspace.

## Targeted evidence

Five departure regressions pass on the default test store: complete ownership
transfer and Entry grant cleanup, cleanup failure preserving membership, missing
replacement owner preserving the old ownership, walker failure propagating, and
other-workspace ownership/grants remaining unchanged.

Two actual PostgreSQL regressions pass: complete cleanup commits, and an injected
failure after ownership transfer and grant deletion restores all original node
records and ownership/grant/membership edges on direct store readback.

Existing library lifecycle fixtures now select the workspace where their private
package was published. Cross-workspace read/install/merge denial remains covered.
Prompt Sheet tests distinguish proposals from execution, and ownership tests
cover both private and workspace-visible resources.

All 16 repository guards also pass over the complete proposed diff against
current staging using an isolated temporary Git index and HEAD, preserving the
review worktree and original checkout. The parallel full backend regression run
passes.

Two frontend regressions cover workspace switching during an in-flight list and
multiple acknowledged App creations surviving a stale list.

The full source/build/CI gates are recorded in the PR description after the final
candidate is verified. Provider/BYOK, independent-process recovery, durable
design-build continuation, public mandate budgets and broader human journey
acceptance remain separate qualification work. Durable chat stays disabled by
default; no production deployment is part of this preparation.
