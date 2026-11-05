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

### Measured effect (benchmark stack, TTL 10 s)

| | Before | After |
| --- | --- | --- |
| `GET /api/tracks`, first call | 9.2 s | 9.2 s (computes the inventory) |
| `GET /api/tracks`, repeat within TTL | 8 to 9 s | 0.22 s |
| 4 concurrent calls after expiry | 4 computations | 1 computation, all four return in 11.3 s |

### Still open for this item

- The cold computation is still about 7 to 9 s. It needs the inventory itself made cheaper (per-track role resolution and
  canonical admission issue about 80,000 statements). Options: batch the role resolution per parent app, serve a stale
  snapshot while refreshing in the background, or key a longer-lived cache on the authorization revision.
- `GET /api/events` (6 to 7 s on the benchmark stack) and `GET /api/tracks/:id/detail` are not changed yet.
- The 15 `ws-ticket` calls per page load are not changed.

### Tests

`backend/tests/test_nav_snapshot.py` (8 tests): off by default, second call served from the snapshot, tracks and apps keyed
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
