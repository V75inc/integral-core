# Core performance changes: what changed, why, and what is still open

Branch: `dh-main-perf-core-team` (based on rc45). Companion business-app branch: `dh-dev-perf-business-team`.
Last updated 2026-11-05. All numbers were measured on local benchmark stacks (see "How it was measured").

## Status at a glance

| # | Change | Where | State |
| --- | --- | --- | --- |
| 1 | Shared Workspace lock for non-authority writes | `services/identity_admission.py`, `services/authorization_scope.py` | Implemented, measured |
| 2 | Job-scoped authority and location fact caches | `middleware/permissions_cache.py`, `services/resource_location.py`, `services/permissions.py` | Implemented, measured on rc41 |
| 3 | Batched event delivery | `services/app_operations/event_outbox.py`, `agentive/services/work_worker.py` | Implemented, not measured alone |
| 4 | Indexer yields to app jobs | `services/retrieval/indexing.py` | Implemented, not measured alone |
| 5 | Tool-dispatch validator cache | `services/hooks/tool_dispatch.py` | Implemented, not measured alone |
| 6 | Navigation snapshots for the tracks and apps lists, with per-track entry counts | `services/permissions_process_cache.py`, `services/permissions.py`, `api/tracks.py`, `api/apps.py` | Implemented, off by default, measured |
| 7 | Unused indexes on the `object` table | `main.py` (`ensure_indexes`), jvspatial `create_index` | Measured by a manual drop, not implemented |
| 8 | Hook dispatch cost for hooks that return "skipped" | `services/hooks/` | Measured, not implemented |
| 9 | Cross-app reads from screens | `services/hooks/registry.py` (`_resource_in_scope`) | Analysed, not implemented |

## 1. Shared Workspace lock (implemented)

**What Core did.** Every write went through `authorization_scope()`, which took `SELECT ... FOR UPDATE` on the Workspace row to
prove the workspace's authorization state could not change underneath the write. A workspace is one lock domain, so every
write in it serialised behind every other write, including writes that had nothing to do with authority.

**Measured effect.** On an unpatched 500-employee Payroll finalize there were 1,914 `FOR UPDATE` reads averaging 2.1 s
(about 68 minutes of summed waiting). With the change: about 220 reads averaging 0.7 s. Finalize went from 23.9 minutes to
5.4 to 6.0 minutes for the whole patch set.

**What changed.** Non-authority writes take `FOR SHARE` instead. Authority writes (membership, collaborators, exclusions,
revokes) still take the exclusive lock, so they wait for in-flight readers and block new ones.

**Not yet safe to merge.** Needs starvation and deadlock tests: a steady stream of shared holders must not starve an authority
write, and lock ordering with the per-entry locks must be documented. Tests so far: `tests/test_identity_admission_shared_lock.py`.

## 2. Job-scoped authority and location caches (implemented)

**What Core did.** One write runs several independent checks, each reading the database again: `resolve_role`, `get_user_node`,
`policy_engine.evaluate`, `permits_mutation`, `locate_resource` (graph walks through `parent_rows`, `read_node`,
`check_workspace_root`), `authority_facts.primary_node_record` and `primary_auth_record`, and the relation checks
`canonical_relation_app` and `assert_relation_entry_containment`. A Payroll finalize made about 290 of these per employee.

**What changed.** Inside one job (`app_job_fact_scope`) the results of these lookups are remembered for a short TTL.

**Measured effect (rc41).** SQL statements 445,495 to 325,914 (-27%), finalize 10 to 21% faster.

**Not yet safe to merge.** The TTL keys must become the authorization revision (`WorkspaceAuthorizationState.revision`), so a
revoke is visible to the next lookup rather than after the TTL.

## 3. to 5. Smaller write-path changes (implemented)

- **Batched event delivery.** `deliver_operation_events` and `defer_job_event` collect the events of a job and flush them in
  groups instead of one transaction per event.
- **Indexer yield.** Retrieval indexing (`retrieval/indexing.py`) yields to app jobs, so the single Python process does not
  alternate between a user's job and a backlog of index work.
- **Validator cache.** `hooks/tool_dispatch.py` caches the compiled JSON-schema validators for tool input and output.

None of these were measured on their own. They are part of the patched runs.

## 6. Navigation snapshots (implemented, off by default)

### The problem

Opening an app or a menu in the UI is slow. Measured on the 9106 stack, opening the Guyana Settings track took 21 s before
the title rendered. The page makes 68 API calls. A few of them are heavy when called alone and much worse when they run
together in the single Python process:

| Call | Alone | During page load |
| --- | --- | --- |
| `GET /api/tracks` | 2.9 s (8 to 11 s on the benchmark stack with 289 tracks) | 3 concurrent calls, up to 17 s each |
| `GET /api/tracks/:id/detail` | 1.1 s | 17.8 s |
| `GET /api/events` | 1.8 s (6 to 7 s on the benchmark stack) | 1.5 s |
| `GET /api/apps` | 0.14 s | 4 calls, up to 5 s |
| `POST /api/auth/ws-ticket` | 0.05 s | 15 calls |

### What Core is doing

`GET /api/tracks` calls `get_user_accessible_tracks(user_id)`, which recomputes the user's full track inventory on every call:
for each of the 289 tracks it resolves the role (parent app, workspace, collaborators, exclusions), checks that the parent
app is listable, and runs a fresh canonical admission (`_filter_canonical_resource_inventory`). One request is about 82,000 SQL
statements (database time only 1.7 s; the rest is Python and round trips), for example 16,260 `pg_advisory_unlock_all`,
3,015 edge lookups by target, 2,446 node-by-id reads and 1,600 reads by `user_id`.

A process-level cache for exactly this exists (`permissions_process_cache`, 20 s TTL, per-user invalidation on every grant
and revoke, generation fence against racing recomputes). It never serves this call. `get_user_accessible_tracks`,
`get_user_accessible_apps` and the other functions that use the same wrapper (eight call sites in `permissions.py`) wrap their work in `isolated_permissions_cache()`, which sets
`live_permissions_required()`; `permissions_process_cache.enabled()` returns False while that flag is set, so the cache is
skipped on the call that was meant to use it. The wrappers came in with `e0405cb2` ("enforce canonical workspace owner
governance"), which is a correct security change for work items and brokers but is also applied to HTTP listing calls.

The page then multiplies the cost: the SPA issues the same list several times, concurrently, and the single process runs them
one after another.

Two further costs sit behind the inventory:
- `count_user_accessible_entries_for_tracks` computes each track's `entry_count` by streaming every admitted entry of every
  track on the page through the audience projection (batches of 2,500). The benchmark workspace has a track with 18,424
  entries. With the inventory cached, `/api/tracks` still took 1.3 s because of this.
- `GET /api/tracks?limit=1` took 7.2 s against 8.1 s for `limit=20` and 20 s for `limit=100`: the inventory is a fixed
  cost of about 7 s, enrichment adds about 0.05 s per track.

### What changed

A separate, opt-in cache for LISTING endpoints only. Set `INTEGRAL_NAV_SNAPSHOT_TTL` (seconds). Default 0 means off and the
behaviour is unchanged.

- `permissions_process_cache`: `get_nav_snapshot`, `set_nav_snapshot`, `clear_nav_snapshots`, `single_flight`.
- `permissions.get_user_accessible_tracks_for_navigation` and `get_user_accessible_apps_for_navigation`; used by
  `GET /api/tracks` and `GET /api/apps`. Per-track entry counts use the same snapshot.
- `invalidate_user` clears the user's snapshots and bumps the generation, so every grant, revoke, membership and
  exclusion path that already invalidates the process cache also drops the snapshot.
- A computation that started before an invalidation is not stored (generation fence).
- `single_flight`: concurrent identical computations share one task, so four simultaneous `/api/tracks` calls cost one.

It never grants access. Opening a record, writing, `resolve_role` and every other authorization check stay live. The
cost is bounded staleness of a listing (at most the TTL) for changes that do not go through `invalidate_user`.

#### Stale-while-revalidate (`INTEGRAL_NAV_SNAPSHOT_STALE`, off by default)

With a positive value (seconds past the TTL), an expired snapshot is returned at once and refreshed in the background
(`refresh_in_background`), so a listing is only slow the first time. `invalidate_user` still removes the expired copy, so
revokes that go through it are not affected.

**What it trades.** A change that does NOT go through `invalidate_user` (for example a revoke on a path nobody hooked) can stay
visible in the track and app lists for TTL plus the stale window. The lists show titles and counts only; opening a record,
writing and `resolve_role` are live. This works against the intent of `e0405cb2`, so treat it as a stopgap until the cold
computation is cheap or the snapshot is keyed on the authorization revision (see "Still open").

### Measured effect (benchmark stack, TTL 10 s)

| | Before | After |
| --- | --- | --- |
| `GET /api/tracks`, first call | 9.2 s | 9.2 s (computes the inventory) |
| `GET /api/tracks`, repeat within TTL | 8 to 9 s | 0.22 s |
| 4 concurrent calls after expiry | 4 computations | 1 computation, all four return in 11.3 s |

### Cold computation: per-computation fact scope (implemented, on by default)

The profile of a cold `GET /api/tracks` shows the main event loop 100% busy, with about half of its busy time in
`locate_resource` and its `ResourceLocationWalker` helpers (`check_workspace_catalog`, `on_track`, `read_node`,
`parent_rows`, `on_app`), 13% in `resolve_role`, 8% in `get_user_node` and 12% in asyncpg. The inventory asks the same
questions for every track, several times each (role, listable check, admission), and each `locate_resource` call builds a new
walker with empty caches.

`app_job_fact_*` already memoizes location and role facts inside an App job. `computation_fact_scope()`
(`middleware/permissions_cache.py`) opens the same kind of memo for ONE computation and discards it when the computation ends:
`get_user_accessible_tracks` and `get_user_accessible_apps` run inside it. Facts live at most `_APP_JOB_FACT_TTL` seconds and
only until the next ACL invalidation (`note_definition_changed`, called by `invalidate_user`). Nothing is shared across requests
or work items, so the result is as fresh as without the scope. An enclosing job scope is kept. `get_user_node` is also
memoized inside a scope (found users only).

| Benchmark stack, snapshot off | Before | After |
| --- | --- | --- |
| `GET /api/tracks` cold, 289 tracks | 8.4 to 9.1 s | 4.7 to 5.5 s |
| SQL statements for that request | 82,800 to 85,800 | 42,600 to 46,600 |
| One user with 112 tracks (in process) | 3.9 s | 1.5 s |

Equivalence check (`equiv.py`, in-process): for all 40 users in the benchmark database the track and app id sets were
identical with and without the scope. One user showed a transient `AuthorizationUnavailableError` on the first run WITHOUT
the scope ("Access could not be established; please retry"); repeated runs gave 5 identical results of 112 tracks.
Not covered: a user with an exclusion and a plain member were not constructed. Tests: `test_computation_fact_scope.py`.

Tried and not kept: the same scope around `GET /api/events` made no difference (about 1.2 s and 7,000 statements before and after).

Also tried and not kept (each measured on the same benchmark stack, snapshot off):

| Idea | Result | Why it did not help |
| --- | --- | --- |
| Lean asyncpg pool reset (skip `pg_advisory_unlock_all`, `CLOSE ALL`, `UNLISTEN *`, `RESET ALL` on release, full reset only after a session-level lock) | SQL statements for a cold `GET /api/tracks` 45,000 to 9,500 (-79%); wall time 4.6 to 4.8 s, unchanged | The reset is cheap on the wire; the cost is Python work per query, not statement count. Core and jvspatial use no session state that needs the reset, so it is safe, but it buys nothing for latency. |
| Seed the fact memo with one batched `locate_resources("track", ids)` per candidate group | No change (5.3 to 6.3 s, about 45,000 statements) | The role and admission paths look locations up under other keys (other app-state sets), so the seeded entries were not hit. |
| Fact scope around `GET /api/events` | No change (about 1.2 s, 7,000 statements) | The cost there is per candidate event, not repeated facts. |
| Database instrumentation wrapper (`ObservableDatabase._instrument`) | Measured, not the cost: 7 microseconds per call, 0.3 s per 45,000 calls | It shows as 28% of busy time only because the query itself runs inside it. |

What the profile still shows for a cold `GET /api/tracks` (about 5 s, main loop 100% busy): about a quarter of the busy time in
`locate_resource`; about 20% in `Walker.spawn` / `Walker.run` / `_execute_visit_hooks` (the graph walker machinery itself);
11% in `resolve_role`; 35% as unattributed interpreter time in the event loop. The remaining cost is the number of walker runs
and per-query Python overhead, so the next real step is to avoid the walk, for example by an inventory keyed on the
authorization revision (so most requests do no walk at all), not to shave constant factors.

### Still open for this item

- The cold computation is still about 5 s (see above). It needs the inventory itself made cheaper (per-track role resolution and
  canonical admission issue about 80,000 statements). Options: batch the role resolution per parent app, serve a stale
  snapshot while refreshing in the background, or key a longer-lived cache on the authorization revision.
- `GET /api/events` (6 to 7 s on the benchmark stack) and `GET /api/tracks/:id/detail` are not changed yet.
- The 15 `ws-ticket` calls per page load are not changed.

### Remaining cost of a cold `GET /api/tracks` (measured, not changed)

- 7,282 connection acquire and release cycles per request. Each release runs asyncpg's reset (`pg_advisory_unlock_all`,
  `UNLISTEN *`, `RESET ALL`, `CLOSE ALL`), so 29,128 of the roughly 45,000 statements are connection resets. Holding one
  connection per computation (jvspatial) would remove most of them. Not tried.
- About 5,000 real queries remain: edge lookups by target (1,857), node by id (1,590), edge lookups by source (1,327).
- `GET /api/events`: about 1.2 s and 7,000 statements for one page, because it proves access to up to 1,000 candidate events.

### Tests

`backend/tests/test_nav_snapshot.py` (11 tests): off by default, second call served from the snapshot, tracks and apps keyed
separately, `invalidate_user` drops it, other users unaffected, a computation that raced an invalidation is not stored,
concurrent callers share one computation, TTL expiry.

## 7. Unused indexes on the `object` table (measured, not implemented)

The `object` table has 117 indexes (9.8 GB) on 1 GB of data; 86 of 109 non-unique ones have never been scanned. They are
generic `(entity, expression)` b-trees created at boot by `ensure_indexes` (`main.py`) through jvspatial `create_index`
(which also creates sort-signature indexes for keyset pagination). They are not partial, so an insert of any entity updates
all of them. Dropping 93 unused ones took finalize from 5.4 to 6.0 minutes to 4.2 minutes and inserts from 6 to 8 ms to
2.5 ms. Core recreated all 93 at the next boot, so the fix has to be in `ensure_indexes` or in jvspatial: make non-unique
indexes partial per entity (`WHERE entity = '...'`) and do not recreate indexes with no scans.

## 8. Hook dispatch for skipped hooks (measured, not implemented)

Payroll finalize sets a batch flag so its own hooks step aside, but Core still dispatches them: it builds a tool context,
begins and finishes a staging step and runs the lookups behind it, then the hook returns "skipped". One 500-person finalize
dispatched 12,016 hooks, each about 21 ms (about 250 s summed). A trusted write should be able to suppress named hooks, or the
dispatch should be cheap enough that a no-op costs microseconds.

## 9. Cross-app reads from screens (analysed, not implemented)

`ToolContext._resource_in_scope` requires the record to be in the same workspace and, when the context carries an app id,
`location.app_id == app_id`. `OperationContext` (screens) always carries the calling app's id; tools do not. Screens therefore
cannot read another app, even a required one, which forces Payroll to keep a copy of every HR employee. The change is to
accept records of apps the calling app requires (installed in the workspace) for the read calls only, keeping writes fenced
to the app's own tracks and role checks unchanged.

## How it was measured

- Stack: one API process (`WEB_CONCURRENCY=1`), Postgres in a separate container, 28-core host. Benchmark workspace with 500
  employees and prepared pay runs; the database is large because it was reused across many runs.
- Payroll finalize: `fin_only.py` against a freshly prepared run, totals checked after every run (133,140,000 gross,
  112,550,360 net, 500 people). SQL from `pg_stat_statements`, Python from py-spy at 50 to 100 Hz.
- UI: a headless browser recorded every `/api` call and its timing while opening a track page and each embedded view.
- Variation between repeated runs is about 10%; most results are single runs.

## Frontend: shared list requests and lazy entry counts (commits d2c244df..a00abc4e)

One page load of a track page made the same list calls several times, and every
`GET /tracks` counted entries for every track even for name-only callers.

| Change | Where | Effect on a track page load (9106, headless Chromium) |
|---|---|---|
| Share identical in-flight list requests for 2 s; any successful write clears them | `frontend/src/api/singleFlight.ts`, `client.ts`, `tracks.ts`, `apps.ts`, `aiChat.ts`, `approvals.ts` | `/tracks`, `/apps`, `/approvals` 1 call each (was 1-3 each), `/chat/threads` 4 to 2 (the rest differ by params) |
| `include_entry_counts=false` for lookup-only callers | `backend/app/api/tracks.py` + 11 callers | skips the per-track count query on those lists; default unchanged |
| `POST /auth/ws-ticket` is NOT deduplicated | n/a | tickets are single-use per socket by design; it also must not clear the shared requests |

Measured: API calls per load 48-49 to 39-40. Time to title is noisy (2.9-5.5 s across
runs, before and after) and did not clearly improve: it is dominated by
`GET /tracks/:id/detail` (about 1.5 s), which is a separate item.
