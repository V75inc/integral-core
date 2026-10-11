# Workspace graph context integration

Base: Integral Core origin/main 9d13b44e. Dependency: JV Spatial dev
621b77998c1a459dbb1158791e28e57c2fb438fa, cloned beside this isolated
worktree, installed locally, and pinned by commit in uv.lock for reproduction.
No release version change, push, deployment, or migration is part of this work.

## Contract and invariant preservation

- Read-only, on-demand integral_get_graph_context for resident and MCP models;
  the same authenticated HTTP endpoint supplies both surfaces.
- I-GRAPH-01/02: use existing rooted nodes; persist no projection nodes.
  JV Spatial owns bounded extraction; Core adds no procedural graph traversal.
- I-ROLE-01/02, I-ACCESS-01, I-RET-01, I-CONTEXT-01: canonical policy evaluation on every
  candidate before expansion; deny intermediates, private runtime/account
  records, other workspaces, and unavailable roots. No field privacy claim.
- I-APP-DEF-01, I-APP-05, I-EXT-01, I-SUBSTRATE-01: installed-App entry
  records stay behind declared App queries. No App-specific imports or rules.
- I-CONV-01/02/03: endpoint decorator, canonical exceptions, separate schemas.
- I-SKILL-01/02: extend the canonical tool manifest and generated capability
  map, with schema and discovery verification.
- I-TEST-01/02: real graph tests for visible paths, exclusions, denied bridges,
  cross-workspace relations even when both endpoints are readable, App
  boundaries, revocation, budgets and dispatch.

## Database adoption

Use native graph adjacency paging with explicit node/edge/candidate/byte/time
bounds. Keep compatibility mode explicit in capture metadata for JSON and other
scan-based adapters. Do not load the authorized workspace universe or compute
exact degrees just to render context. Adopt first-match reads where only one
result is consumed. Preserve existing uniqueness/collision behavior.
Do not replace entity save hooks or structural-edge writes with blind bulk
saves. Qualify inherited optimizations through the full Core gate plus a
reproducible SQLite workload; timings are local evidence only.

## Evidence and rollout

Run staged guards, pre-commit, formatting, CI-faithful smoke and full backend
suite through make verify, plus projection-specific integration tests. Report
PostgreSQL, live-provider and clean public-wheel limits separately. The public
0.1.1 wheel lacks projections; the new endpoint must report unavailable rather
than silently reverting to unbounded export. Remove the dev source pin only
when a released JV Spatial version is qualified.
