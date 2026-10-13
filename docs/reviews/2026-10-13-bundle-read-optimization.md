# Bundle read optimization — 2026-10-13

## Change and invariants

`ToolContext.find_entries` now uses Core's existing bounded canonical Entry audience selector. `find_entries_in_track_type` selects raw candidates by Track ID through that facade instead of hydrating every linked Entry and executing a complete point authorization cascade for each one.

The selector batches raw ancestry, direct grants and exclusions in windows of at most 500 candidates. It reuses a parent's role only inside that window; no allow or graph facts persist into the next facade invocation. Candidate predicates narrow discovery before Entry hydration. Entry-type interpretation still uses the canonical type manifest key/name and remains local to the facade call.

The facade explicitly requires **active** Apps. Existing generic data projections retain their active/paused default; a new internal `allowed_app_states` parameter carries the stricter facade requirement into canonical admission. The facade binds ordinary authority and live isolated permission reads, preventing workspace-owner stewardship or inherited process permission caches from widening bundle access.

Preserved invariants: I-GRAPH-01 canonical rooted containment, I-ROLE-01/02 ordinary role and inheritance rules, explicit workspace/App scope, private-resource boundaries, identity admission, membership revocation, inherited exclusions versus direct grants, subtree blocks, and fail-closed authority reads. I-SUBSTRATE-01 / I-EXT-01 are preserved: no domain keys, App imports or application-specific branches were introduced in Core. No indexes, pool limits or release metadata changed. As with existing canonical bulk projections, a window is a best-effort multi-read admission, not an atomic snapshot of concurrent ACL changes. New windows and facade calls read fresh facts; declared App operations retain their current function-authority checks at result release. Concurrent resource revocation inside a window still requires separate race qualification.

## Experiment ledger

| Attempt | Verdict | Reason |
|---|---|---|
| Reuse bounded canonical Entry admission in the bundle facade | Kept | Approximately 89–90% fewer SQL calls on report reads; repeated equivalent domain outputs |
| Global/operation-wide permission caching | Not implemented | Without complete authority epochs/invalidation, reuse would weaken revocation boundaries |
| Drop indexes or increase pool size | Not implemented | The trace identified repeated authorization/containment reads; these alternatives do not address that measured cause |

## Matched PostgreSQL comparison

Core baseline: `a7c20388bff2cd99224a485134c3213698700f4f`; optimized source is the implementation in `entry_access_projection.py` and `hooks/registry.py`. Adapter: **jvspatial 0.2.2**. Business Finance package: **2.2.0**, source commit `47f37e1b6dc665d1783b59be867c9e4394d1922a`.

Each run starts from the same disposable PostgreSQL 16 dump, with the copied 134 object indexes and installed synthetic Finance workspace. A controller binding is issued through the normal service. Bundle registrations are rehydrated before timing. The sequence reads two trial balances, creates/opens one USD 100 invoice, then reads its trial balance and books health. Both optimized runs restore the initial dump rather than accumulating transactions.

Times are seconds; counts cover SQL through `PostgresDB._acquire_conn`. SQL issued directly by a held transaction handle is excluded, so command SQL counts are partial. This is a small diagnostic sample, not a p95/SLA study. The full backend suite was running concurrently; CPU contention varied, so wall times should not be presented as an isolated throughput benchmark. The read query-count reduction is deterministic across repeats.

| Operation, in sequence | Baseline | Optimized | Optimized repeat | SQL calls |
|---|---:|---:|---:|---:|
| trial_balance | 10.604 | 2.026 | 2.657 | 5921 → 573 / 573 |
| trial_balance | 13.558 | 1.733 | 1.965 | 5919 → 571 / 571 |
| create_invoice | 21.930 | 9.995 | 9.594 | 205 → 205 / 205 |
| invoice_issue | 25.862 | 18.871 | 19.927 | 226 → 226 / 226 |
| trial_balance | 23.652 | 2.328 | 1.910 | 6321 → 665 / 665 |
| books_health | 87.539 | 13.164 | 11.850 | 25558 → 3032 / 3032 |

Report outputs match across all three runs. The final trial balance is balanced at USD 100 debit / USD 100 credit. Both creations return a one-line USD 100 draft; both issuances return open status and a journal reference. The final run's returned invoice and journal records were read back from the disposable primary database.

## Regression coverage and qualification

New `test_tool_context_bulk_reads.py` checks:

- inherited exclusion with an explicit direct grant, followed by membership revocation;
- ordinary reads cannot borrow workspace-owner governance;
- paused Apps and missing containment refuse reads;
- 31 entries sharing a Track resolve its parent role once, with a bounded read-call budget;
- membership revoked between windows is checked in later windows;
- live facade reads bypass inherited process permission caches.

Focused authorization/containment/projection tests, the new tests, all 14 PostgreSQL receipt/transaction tests, formatting/lint/type hooks and substrate guards passed. The full default backend suite passed with four xdist workers; the configured `not domain_app and not slow` exclusions and infrastructure skips apply. The latest six new tests also passed in a separate final run. All 43 facade/scope tests passed on PostgreSQL. This qualifies the source behavior, not a rebuilt wheel or deployed browser stack.

Evidence is local at `/private/tmp/jvspatial-022-optimization-eval`: `compare.py`, `finance_callsites.py`, `baseline.json`, `optimized.json`, `optimized-repeat.json`, `domain-comparison.json`, `finance-readback.json` and `postgres-contracts.log`. Scripts require an isolated DSN and the specified source snapshots; they restore databases and must never target live data. Only synthetic data was written. Business source and the local Business deployment were not changed.

## Remaining work

- Creation/issuance still take seconds; the receipt upsert remains milliseconds. Profile all SQL inside the transaction and the field validation/effect path before choosing the next optimization.
- Add operation-correlated phase metrics and test realistic concurrency on the original VM. Do not infer production/storage performance from this local host.
- The original Finance verifier lacks the required controller binding; its initial refusal is fixture setup, not a permission check to bypass.

## Read comparison after the backend suite stopped

The same initial dump was restored for each run. These runs contain no financial postings: three trial-balance reads and books health. The whole backend suite had stopped before measurement; ordinary host/background activity remains uncontrolled. This is still a small local diagnostic sample, not a production p95 claim.

| Operation, in sequence | Baseline seconds | Optimized seconds | Repeat seconds | SQL calls |
|---|---:|---:|---:|---:|
| trial_balance | 15.412 | 1.290 | 1.013 | 5921 → 573 / 573 |
| trial_balance | 10.372 | 0.964 | 1.042 | 5919 → 571 / 571 |
| trial_balance | 11.048 | 1.061 | 0.947 | 5919 → 571 / 571 |
| books_health | 40.113 | 5.269 | 5.015 | 24024 → 2632 / 2632 |

All report outputs match across the three runs. Baseline trial-balance median was **11.048 seconds**; the optimized medians were **1.061** and **1.013 seconds** (about **90–91% lower**). Books health fell from **40.113** to **5.269 / 5.015 seconds** (about **87% lower**). Read SQL calls decreased by approximately **89–90%**. Evidence: `compare_quiet.py`, `finance_reads.py`, `quiet-baseline.json`, `quiet-optimized.json`, `quiet-optimized-repeat.json`.

The disposable PostgreSQL container and credentials were removed after the runs. Benchmark scripts and synthetic results remain available locally. Runtime data and the running Business stack were preserved.
