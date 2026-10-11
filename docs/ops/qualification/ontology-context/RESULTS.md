# Workspace graph context qualification

Latest richer-context qualification: [RICH-CONTEXT.md](RICH-CONTEXT.md).
The source pin and gate totals below describe the earlier topology-only integration.

Qualification date: 2026-10-11. Core base: `origin/main`
`9d13b44eb7425b3371f1f2a21f799bdd41dfe02a`. Integration branch:
`codex/ontology-context`. JV Spatial source: `dev` commit
`621b77998c1a459dbb1158791e28e57c2fb438fa`.

## Isolation and implementation

Core runs in its own managed worktree and venv. JV Spatial was cloned beside
it and installed locally into that venv; the lockfile pins the same commit.
No shared runtime database, original-checkout files, release versions or
production services were changed. No commit, push or PR was made. This report
covers the initial integration gates. Subsequent isolated local deployment,
browser tests and privacy fixes are recorded in [BROWSER-SMOKE.md](BROWSER-SMOKE.md).
The later [plain-language trials](PLAIN-LANGUAGE.md) preserve failed iterations
and two successive passing fresh conversations using the user's exact question.
The earlier topology-only source and artifact gate totals are in BROWSER-SMOKE.md; historical
initial-gate numbers below remain separately attributed.

`integral_get_graph_context` and `POST /api/graph-context` expose bounded,
read-only relationship, hierarchy and ontology evidence. The resident insights
skill and MCP catalogue advertise the capability. Node/edge IDs, discovery
paths, declared meanings, typed relationship metadata and capture/budget
metadata support model reasoning. Model execution remains downstream.

I-CONTEXT-01 fixes scope to the authenticated active workspace. Authorization
runs before expansion using current policy, bypassing inherited request and
process permission grants. Other-workspace links remain excluded when the
requester can read both endpoints. Denied nodes cannot act as bridges; private
account/harness records are outside the permitted types. Installed-App records
require declared App queries. Captures expose no host candidate statistics.

## Checks

- Staged substrate guards, pre-commit, pinned formatting and whitespace checks
  passed. Frontend types passed; ESLint reported zero errors and 453 existing
  warnings. No frontend source changes were needed.
- Full backend suite (`make test-backend`, four xdist workers): 4,985 passed,
  286 skipped, zero failures or errors; 258.532 seconds. Skips cover unavailable
  infrastructure/provider integrations, PostgreSQL-only and deferred lanes.
- CI-faithful smoke (`make verify-ci`, `TESTING=1`, `DEBUG=false`, no
  `.env`, xdist): 661 passed, seven skipped, zero failures or errors.
- Frontend suite: 1,583 tests passed across 279 files.
- Focused context, database adoption, manifest and MCP tests: 64 passed.
  Context API/schema/service statement coverage: 97.01% (134 statements,
  four uncovered fail-closed branches).
- PostgreSQL 16: all 26 context and database adoption tests passed against
  an exclusive disposable container/database. No skipped tests or failures.
  Ephemeral credential-encryption keys were generated only in process; the
  container was stopped and removed afterward.
- Core artifact gate passed, including reproducible build and wheel import
  outside the source tree. Wheel SHA-256:
  `54392256fdc4044d5093628c9165083a1a029eb604951b28537513761abffd33`.

All `make verify` component gates passed. After fixing manifest counts during
the initial combined run, final backend, CI, static and artifact components
were rerun independently; the full backend used four xdist workers.

The graph tests exercise a real rooted fixture, exclusions, hidden bridges,
readable foreign-workspace links/roots, packaged and paused Apps, permission
revocation, stale request/process grants, all projection views, budgets,
typed Anchors edges, authenticated HTTP and canonical tool/MCP dispatch.
A native SQLite probe instruments bounded adjacency pages and rejects
unscoped graph scans. Schema tests reject caller-selected scope, principal,
fields and unsupported types. Old public-wheel behavior fails explicitly.

## Database adoption evidence

Core's auth-principal fallback now uses `User.find_one`; parent-track hydration
is batched per projection page; parent-App classification is reused only within
the capture. Existing Core batch saves inherit JV Spatial's native bounded
writes without replacing structural wiring or entity save hooks.

The [reproducible SQLite probe](sqlite-probe.json) recorded:

| Workload | Previous read/write pattern | Adopted JV Spatial pattern |
| --- | --- | --- |
| 5,000 matching auth mappings, warm median | 164.713 ms; 5,000 hydrated | 2.249 ms; one hydrated |
| 200 records, separate fresh collections | 518.878 ms serial | 18.316 ms native batch |

Both lookups returned the same first identity and both write paths saved all
200 records. Run `backend/.venv/bin/python backend/scripts/qualify_ontology_database.py`
from the Core root to repeat. These are local diagnostics on a busy host,
not production throughput/latency guarantees or PostgreSQL timings.

## Limits and release boundary

Captures are best-effort multi-read evidence, not atomic historical snapshots.
Native graph pages bound transferred rows, not physical database work or raw
record size; compatibility adapters can scan. Core policy adds its own reads.
The three-second budget is cooperative; canonical byte limits are not token
limits. Truncation and inaccessible absence cannot support exact counts or
causal claims. Use authorized exact queries for decision-critical facts.

The full PostgreSQL suite, production load/concurrency, live model quality and
remote deployment were not qualified by these initial gates. Public JV Spatial 0.1.1 lacks projection
APIs; release metadata remains unchanged and the Git pin is an integration
lane. Qualify a published version before shipping this capability in a release.
