# W3.7 query scale evidence

**Candidate:** `codex/w3-7-scale-query-completion`; **measurement date:** 2026-09-28
**Fixture:** 50,000 structurally linked Entries in one isolated PostgreSQL Track.

## Budgets and setup

The selective scalar-filter request has a 250 ms p95 budget. Broad unfiltered
queries, grouped counts, and activity digests have a 500 ms p95 budget. A
non-pushable keyword query uses a bounded 5,000-Entry scan batch and has a
2,000 ms p95 budget. Each timed path warms once and then measures 20 runs (5
for the slower keyword scan); p95 is nearest-rank. Correct totals and row ids
are asserted separately from latency.

The benchmark seeds 50,000 Entries with exactly one matching title. For the
scalar comparison, the baseline disables only persistence candidate-filter
pushdown. Both paths retain the same Track/workspace checks and batched Entry
grant/exclusion checks. Test-owned Entry nodes and `CONTAINS` edges are deleted
in teardown.

Command:

```bash
cd backend
TESTING=1 INTEGRAL_TEST_DB=postgres INTEGRAL_RUN_SLOW_TESTS=1 \
  ./.venv/bin/pytest tests/test_scale_query_benchmark.py -s --tb=short -m slow
```

## Result

| Surface | p95 | Budget |
|---|---:|---:|
| Exact scalar predicate pushed into Entry persistence query | 57.8 ms | 250 ms |
| Same scalar query without predicate pushdown | 1,569.3 ms | Comparison baseline |
| Plain unfiltered query, keyset paged | 384.6 ms | 500 ms |
| Grouped count by Track | 49.3 ms | 500 ms |
| Activity digest | 119.4 ms | 500 ms |
| Non-pushable keyword query, 5,000-row bounded batches | 1,586.4 ms | 2,000 ms |

All tested surfaces returned exact totals; the selective query returned only
the expected Entry. Prior qualification runs measured the selective request at
34.5–43.7 ms and its no-pushdown baseline at 1,618–1,619 ms. Latency varies
with the local PostgreSQL instance; every repeated workload remained within
its declared p95 budget in the final run.

## Implementation covered

- Exact stable scalar predicates are pushed into the persistence read and
  checked again by the canonical evaluator.
- Plain query results use database keyset pages and keep only the requested
  sorted window in memory. Legacy Entries without `updated_at` are read as a
  separate `created_at` partition to preserve existing order semantics.
- Complex filters, including keywords and custom fields, scan authorized
  Entries in bounded batches and retain only the requested sorted window.
- Grouped counts aggregate each authorized batch directly. The unfiltered
  Track grouping uses exact graph counts and applies Entry exclusions while
  preserving direct-grant precedence.
- Activity digests use exact visible counts and bounded recent-entry pages.
- Workspace filtering and packaged/paused App refusal run before aggregation;
  per-Entry exclusions are removed and direct Entry grants continue to win.

The full non-pushable keyword predicate cannot be narrowed safely by the
current persistence query contract, so it still reads the full Track while
keeping memory bounded. This is measured separately from the selective query
budget. Source, backend, frontend, and PostgreSQL release gates are recorded
after the final verification run.
