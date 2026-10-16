# Browser navigation latency qualification — 13 October 2026

## Scope and result

This is a local diagnostic record, not a release or production capacity claim.
Business was exercised with Core **0.1.1rc18**, jvspatial **0.2.2**, base commit
`25ab5ab32dec14dc6d1609c6154080d9d0915b87` and the dashboard scheduling change
in this commit. Business source and the web image were unchanged.

| Measurement | Sequential widgets | Bounded parallel widgets |
|---|---:|---:|
| Warm single-request median | 1085.6 ms | 722.7 ms |
| Warm two-request median | 1414.6 ms | 1434.0 ms |
| Recorded DB calls per warm request | 2098 | 2098 |

The single-request median fell **33.4%**. Each sample contains six requests;
the first request is excluded from warm medians. The two-request sample consists
of three pairs. All eight widget outputs had the same canonical JSON SHA256
across baseline, repeat, parallel, concurrent and final-wheel samples.

The small two-request difference is within observed local variation. This change
improves individual request latency without reducing database work. It does not
establish higher production throughput. Cumulative database time rises with
concurrent reads and cannot be interpreted as request wall time. This is a small
sample on the local Docker PostgreSQL stack, with ordinary host activity remaining
uncontrolled. The earlier contended run was excluded from the table.

## Implementation and preserved behavior

`resolve_dashboard_data` permits up to three concurrent widget reads **per request**;
it is not a process-wide admission or pool-size setting. Each child receives a
fresh live permission memo. No widget result or allow decision is reused between
children. Existing scoped queries, policy, entitlement and canonical audience
checks remain authoritative.

Declaration order, missing IDs, duplicate-ID last-value behavior and per-widget
errors are preserved. Request cancellation stops running and queued child reads.
App Home retains its separate sequential five-second budget.

Preserved invariants include explicit workspace/App scope, current ordinary read
authority, canonical containment, fail-closed authorization and domain-neutral
Core behavior (I-GRAPH-01, I-ROLE-01/02, I-SUBSTRATE-01 and I-EXT-01).

## Browser coverage

Navigation covered workspace Home, Apps, CRM Home/Tracks/Dashboards, Contacts
table/Leads/Feed, Opportunities Pipeline Kanban, Collaborators and Access details.
Contacts and Feed showed three records; Kanban showed three deals. Home and
Dashboard returned Contacts 3, Opportunities 3, Communications 4 and value 69500.
Navigating from Kanban to Apps updated the view correctly. No console errors were
captured in initial or final checks. No business writes, permission changes,
installations, deletions or model inference were exercised.

The final dashboard locator readiness check took 811 ms; the initial visit took
2106 ms. Cache state and automation overhead differ, so this is a navigation
observation, not a controlled UI performance comparison. These are not INP, LCP
or real-user telemetry. Empty initial placeholders filled after API responses;
shell readiness is not data readiness.

## Validation and artifact identity

- Widget/service/query-adapter tests: 51 passed.
- Dashboard API, Home, bindings, summaries and concurrency tests: 43 passed;
  four overlap, giving 90 distinct selected tests.
- New tests cover bounded fan-out, forwarded scope, ordering, duplicate IDs,
  sibling errors, independent live permission memos and cancellation.
- The full default backend suite passed before committing, with four xdist
  workers, `TESTING=1` and `DEBUG=false`. Default `not domain_app and not slow`
  exclusions and infrastructure skips apply; this is not the unrestricted suite.
- Black, isort, flake8, configured mypy (713 files), substrate guards, TypeScript,
  relative documentation links and diff whitespace checks passed.
- Wheel build, installation without dependency replacement and `pip check` passed.
  Runtime package metadata and dashboard source checksum matched the artifact.
- App/Track/Entry count and content digest were unchanged (59 nodes).
- Temporary Nginx timing and API perf-header instrumentation were removed.

The locally installed wheel was built before the source commit. Its SHA256 is
`70cd18cff74a7462ebae77c3725a1c93f7c6da87efa946a28b93f6f4003e3d28`;
its dashboard service SHA256 is
`e019ed18ad58d4a1076f5b683b6ebdf999c15bf1184bff5715fcf005eb778c6c`.
API image: `integral-business-dashboard-latency-api:local`.
Web image: `integral-business-optimized-web:25ab5ab3`.
These are local artifact identities, not registry publication evidence.

## Reproduction and retained evidence

For a new comparison, use the same database snapshot, principal, workspace and
saved dashboard. Authenticate normally, send `X-Integral-Scope: ws:<workspace_id>`,
and time six GETs to `/api/apps/<app_id>/dashboards/<dashboard_id>/data` for each
source variant. Compare canonical JSON output as well as elapsed time. Repeat with
two simultaneous requests, keep host contention separate, and record exact source,
artifact, adapter and pool configuration. Do not infer a production percentile
from these medians.

Local diagnostic files are retained under `/private/tmp/integral-browser-latency`:
`sample.py`, `baseline.json`, `baseline-repeat.json`, `parallel.json`,
`parallel-quiet.json`, `baseline-concurrent.json`,
`parallel-concurrent-quiet.json`, `final-wheel.json` and test/build logs.
The script reads existing local credentials without printing them; its temporary
session token was deleted. Timing logs omit query strings, credentials, headers
and response bodies. These files are local evidence and may not survive cleanup.
The ignored Business deployment folder retains the wheel, artifact metadata and
PostgreSQL recovery dump; runtime data and secrets are not repository artifacts.

## Next targets

Initial upstream browser samples showed dashboard data up to 1678 ms and Track
inventory up to 1233 ms with about 1208 recorded database calls. Track inventory
and detail reads remain candidates for reducing repeated admission work. Preserve
parent visibility, canonical scope, private access and fresh revocation checks
before replacing those reads. See the [bundle-read optimization record](2026-10-13-bundle-read-optimization.md)
for the preceding query-count improvement and its separate Finance qualification.
