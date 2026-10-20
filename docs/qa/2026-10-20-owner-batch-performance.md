# Owner configuration, collection updates and populated workspace performance

## Release scope

- An App's current owner can configure that installed or custom App's own Tracks
  through the ordinary model tools. The App-owner configuration rule is separate
  from inherited roles: collaborators remain capped at editor, and App admins
  do not acquire this exception. Sharing, moderation, deletion and ownership
  transfer retain their exact-resource authority.
- Current workspace membership, canonical containment, exclusions and subtree
  blocks remain required. Model changes still require every affected parent's
  configuration authority; installed copies do not grant edits to their shared
  library source. Existing draft/publication and migration validation remain.
- `integral_bulk_update_entries` prepares up to 500 selected records as canonical,
  revision-bound `update_entry` commands under one itemized review. It checks
  every record before staging, removes duplicate IDs, preserves field-key
  resolution and shows the actual per-record diff. Requested type changes require
  a current exact type ID, key or normalized name; an unresolved type is refused
  before mutation rather than silently dropped. A surrounding open batch
  receives the complete selection in one durable append.
- PostgreSQL execution admits the complete unresolved selection again, checks
  saved record revisions before the first write and retains each command's
  own live policy, validation, hooks and immutable receipt. Retry recovers
  completed children rather than rewriting them. Later validation, permission
  races or external effects can still stop a partially applied collection;
  report applied and unresolved results, never claim all-or-nothing behavior.
- Large collections require complete paged selection and bounded reviews. A
  capped first result page is never the whole collection. No background grant,
  arbitrary executable code, library publication or cross-workspace authority
  is introduced by these changes.

## Performance investigation and experiment ledger

Measured the real authenticated Apps, Tracks and Entries API handlers against
an isolated native PostgreSQL 14 instance and synthetic, rooted workspaces with
1/4/8 Apps, 4/16/32 Tracks and 40/160/320 records. Runtime is the rc27 locked
jvspatial 0.2.2 dependency; compare baseline source e0b83606 to this branch.
Each point is three fresh request measurements with the same seeded shape.
Permission process caching is disabled under TESTING, as is required for live
authority testing. ASGI transport excludes browser rendering, TLS and network
latency; these are local server measurements, not a claimed dev SLA. PostgreSQL
16, populated commercial package execution and production concurrency still
need deployment qualification.

The initial JSON-store profile identified repeated per-Track record counting;
the larger profile was stopped after establishing the hotspot and replaced by
PostgreSQL measurements. Docker could not create another container because its
VM storage was full; no existing container, data or cache was deleted. Native
PostgreSQL uses its own directory and loopback-only port.

| Experiment | Baseline | Result | Decision |
| --- | --- | --- | --- |
| Count a Track page through shared bounded Entry admission windows | 131 / 371 / 691 DB operations | 76 / 100 / 132 DB operations | Keep: exact same canonical audience admission, 81% fewer operations at 32 Tracks |
| App detail Track-list variant (4 Tracks) | 217 DB operations; quiet medians 87.91 / 86.11 / 92.38 ms | 174 DB operations; quiet medians 105.96 / 97.05 / 90.43 ms | Reverted: no consistent latency improvement despite less DB work |
| Repeated PostgreSQL Track-page median, 4 Tracks | 112.64 ms | 86.91 ms | 23% lower local median |
| Repeated PostgreSQL Track-page median, 16 Tracks | 252.51 ms | 215.06 ms | 15% lower local median |
| Repeated PostgreSQL Track-page median, 32 Tracks | 484.19 ms | 360.29 ms | 26% lower local median; baseline samples 471.94–691.37, changed samples 355.06–375.19 |

Apps and single-Track Entries query counts stayed at 30 and 35 respectively.
The separate App detail Track-list variant was reverted after quiet-host
measurements. Its existing concurrent counts remain. App detail also requests
the workspace Track list, which benefits from the retained optimization.
No caches, permission shortcuts or indexes were added. The existing canonical
projection still checks each record and advances in bounded windows. A focused
regression requires one four-record admission window for a populated four-Track
page and verifies the counts; a separate audience case preserves exclusions.

Reproduce the optional populated-workspace probe with an explicitly disposable
PostgreSQL test service (the test fixture recreates its named test database):

```sh
cd backend
TESTING=1 INTEGRAL_RUN_SLOW_TESTS=1 INTEGRAL_TEST_DB=postgres \
INTEGRAL_TEST_POSTGRES_DSN=postgresql://integral@127.0.0.1:55462/postgres \
PYTEST_XDIST_WORKER=loadedprobe \
INTEGRAL_PROBE_OUTPUT=/tmp/loaded-workspace.json \
.venv/bin/python -m pytest tests/test_loaded_workspace_probe.py -o addopts= -q -s
```

Set `INTEGRAL_PROFILE_PROBE=1` only for a separate CPU profile; profiled timings
are not interchangeable with the unprofiled measurements above.

## Validation evidence

Focused authority, draft-edit, selection, review assembly, count, and current
permission suites accompany the change. PostgreSQL tests exercise actual
canonical record writes, stale selection refusal, access revocation and recovery
after a worker is lost between commit and progress reporting. Existing batch and
individual-command recovery contracts run alongside the collection tests.
Final validation:

- Full backend: **7,028 passed, 966 skipped**, zero failures (8 workers, 1,318 s).
- CI-style smoke: **2,005 passed, 26 skipped**, zero failures (TESTING=1,
  DEBUG=false, no .env, four workers, loadfile scheduling).
- Focused native PostgreSQL regressions: **145 passed**.
- Final agent/collection contracts: **7 passed**, including ordinary editor
  writes, actual App-owner tool dispatch/approval/schema readback, stale and
  revoked selections, partial-result reporting and interrupted-worker recovery.
- App reads and visibility: **46 passed, 6 skipped**. Core-only: **9 passed**.
- Source review assemblies and update/type dispatch: **130 passed**.
- Final focused owner, collection, type and query-budget rerun: **41 passed**.
- Pinned Black 24.8.0, isort 6.0.0, flake8, mypy, attribution hooks and staged
  repository guards passed. Generated capability documentation was refreshed.

These lanes overlap; their counts must not be summed as unique tests. The
broad backend run was followed by focused reruns for the final changes. No
frontend code changed. Browser/deployment smoke, PostgreSQL 16 CI, release
artifacts and Business dependency promotion belong to the subsequent release
qualification; no remote deployment is claimed by this local evidence.
