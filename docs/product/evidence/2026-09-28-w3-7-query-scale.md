# W3.7 query scale evidence

**Candidate:** `codex/w3-7-scale-query-pushdown`; **measurement date:** 2026-09-28
**Evidence scope:** `query_entries` with one exact scalar equality filter on a
50,000-entry PostgreSQL Track.

## Frozen budget and setup

The selective scalar-filter journey has a frozen p95 latency budget of 250 ms.
The benchmark seeds 50,000 structurally linked Entry nodes in the isolated
PostgreSQL test database, with exactly one matching title. It warms each path
once, then measures 20 identical requests and reports the nearest-rank p95.
The filter is evaluated again by the canonical in-process filter contract on
every returned row. The comparison baseline disables only persistence
candidate-filter pushdown; both paths use the same workspace/Track checks and
batched Entry-level grant/exclusion checks.

Command:

```bash
cd backend
INTEGRAL_TEST_DB=postgres INTEGRAL_RUN_SLOW_TESTS=1 TESTING=1 \
  JVSPATIAL_PG_GIN_INDEX=off JVSPATIAL_POSTGRES_MAX_POOL_SIZE=3 \
  ./.venv/bin/pytest tests/test_scale_query_benchmark.py -s -q --tb=short -m slow
```

## Result

| Path | Run 1 p95 | Run 2 p95 | Run 2 median |
|---|---:|---:|---:|
| Exact scalar predicate pushed into `Entry.find` | 43.7 ms | 34.5 ms | 27.3 ms |
| Same query with candidate hydration before in-process filtering | 1,618.4 ms | 1,619.0 ms | Not retained |

Both runs returned the same sole matching Entry and exact total of 1. The
measured pushed path was below the 250 ms p95 budget in both runs. The fixture and benchmark
were run successfully against the PostgreSQL backend; test-owned Entry nodes
and `CONTAINS` edges were deleted in teardown.

Regression gates also passed on the staged candidate: `make verify` and
`make test-postgres`. The former included 16 substrate guards, pinned
format/lint/type checks, CI-faithful backend smoke, all 1,254 frontend tests,
and the full backend suite. The latter ran the full non-slow backend suite on
PostgreSQL. Targeted permission, role-resolution, workspace-scope, result-set,
sort, and filter-contract tests passed as well.

## Implementation covered

- `query_entries` pushes exact equality, inequality, membership, and ordered
  comparisons for stable scalar Entry fields into the permission-aware Entry
  read. Unsupported field aliases, custom fields, relative date objects, and
  ambiguous predicates stay on the canonical in-process path.
- Every returned candidate still passes the canonical filter evaluator.
- Once a candidate's Track access is established, Entry-level direct grants
  and exclusions are read in batches. A direct grant continues to override an
  Entry exclusion.
- The benchmark compares filtering pushdown independently of the ACL batching
  change, so the 1,618.4 ms figure is not presented as a before/after measure
  for all W3.7 changes.

## Remaining W3.7 qualification

This evidence closes only the selective scalar-filter journey. Unfiltered and
non-pushable `query_entries` still hydrate all candidate Entries before
sorting/paging. Grouped counts still materialize rows, and `activity_digest`
still scans Entry objects to compute totals and recent results. These paths
need persistence-backed pagination/aggregation and their own 50k measurements
before W3.7 can be marked complete. No claim is made here for broad workspace
latency or production capacity.
