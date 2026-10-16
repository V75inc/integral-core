# Core hardening completion ledger

This is the acceptance ledger for the full 2026-10-14 Core review. It preserves
that scope. A qualified increment, green CI or deployment health does not close
unrelated requirements. The execution log and individual review records retain
historical source and test evidence; their dated "current" statements must not
be read as the present checkout state.

## Completion requirements

| Requirement | Implementation present | Remaining proof or implementation |
| --- | --- | --- |
| R1: shared approval ownership | Native task/fence claims, current proposal checks and integrated canonical resource commands | Complete all 69 registered kinds, including the 43 remaining classified kinds, the separately integrated App-operation path and conditional batch. Preserve immutable original identity/workspace/review and current approval state. Independent workers, stale owners, cancellation, expiry, revocation and restart must not create a second effect. |
| R2: atomic effects and recoverable completion | Canonical graph/result/event receipts, recorded-result observation and current disclosure controls | The local staged App-operation path now shares its canonical receipt and exact original review proof; preserve policy Approval's existing transaction owner and complete external handler contracts. Finish remaining local families, per-child mixed batches, external dispatch intent/readback and unknown-outcome reconciliation. Move external I/O out of held authority transactions. |
| R3: stream release authorization | Fresh per-connection/frame and identity/workspace admission | Complete the multi-connection/revocation-ordering matrix on final source. Document the exact transport boundary; bytes already handed to transport cannot be recalled. Include sandboxed extension reply scope/frame replacement. |
| R4: request-bound history | Validated selected workspace, canonical containment and live per-resource gates; HTTP and native dispatcher workspace/Personal boundaries qualified | Reconcile these consumer checks with the final source and aggregate authorization/system gates. This is not a general instantaneous-revocation guarantee. |
| R5: bounded history | Database timestamp/id keysets, candidate budget, opaque denied-row cursors and unavailable errors | Final populated PostgreSQL query/index plan and latency probe, both directions/ties/deleted boundaries, withheld windows and honest continuation. This is not a production latency SLA. |
| R6: execution scope tool | Dedicated dispatcher-bound scope service and matching tool manifest; focused execution-scope qualification passed | Reconcile the qualified tool/service/capability-map selection with final source and aggregate system gates. |
| Result and event privacy | Current nested reference checks, whole-output withholding, parent containment and unsupported object refusal | Complete declared-reference, deleted-history audience and safe author/type/model/public-identity contracts. Conservative withholding prevents exposure but does not prove usable metadata projection. |
| Durable chat and restore | Native durable turns, interrupted-run recovery and disposable encrypted checkpoint/credential restore evidence | Complete attachment bytes, session invalidation, migration, independent-process restore and ordinary-owner/member recovery journeys on final source. A backup manifest alone is insufficient. |
| Deployment architecture | Trusted-proxy client identity and per-process admission limits | Shared admission limits across replicas and explicit scoped directory/privacy policy. Do not silently treat multiplied process limits as a shared guarantee. |
| Legacy relation identity | Stable declared identities and bounded unambiguous creation-evidence lookup | Reviewed migration for missing/ambiguous retained creation evidence, default dry run and preserved original IDs/data. Never infer identity from the relation the caller wants to create. |
| Maintainability | Shared canonical paths and isolated result/status contracts | Finish remaining command consolidation, targeted module extraction, classified error contracts and current documentation reconciliation. All runtime components remain reusable and domain-neutral. |
| System qualification | Focused source, native fault/concurrency and clean artifact gates for individual increments | Reconcile final source, run full default backend, CI-faithful selections, frontend, native concurrency/fault/multi-principal/load/restore matrix and independent Core/SDK/external-App artifacts. Inspect actual remote CI at the pushed head. Skips and unqualified journeys stay explicit. |

## 2026-10-16 stream-release ordering follow-through

The transport contract is admission ordering, not recall: for PostgreSQL-backed
requests, current identity and workspace authorization are read while holding
the shared admission fences. The protected interval ends before `yield` or
`send_text`, so no graph transaction waits on a slow client. A frame admitted
before a revocation commits may still be delivered afterward; once bytes are
handed to the transport they cannot be recalled. Every later SSE frame and each
later WebSocket connection obtains fresh admission, and a frame whose admission
starts after the revocation commit is denied. Local JSON mode cannot claim this
cross-worker ordering guarantee.

Added an integrated committed-chat SSE contract that uses the native PostgreSQL
admission path, yields one frame, commits workspace membership revocation, and
proves the next frame is withheld without replaying the page. The complete
release-admission PostgreSQL contract file passed **3/3**. The sandboxed iframe
host's stale operation/query/capabilities reply checks were also rerun: its
scope-switch/frame-replacement suite passed **10/10**. These focused checks
close the previously missing integrated SSE revocation-ordering and extension
reply/frame replacement probes. They do not qualify all ASGI disconnect and
replay paths, every provider stream, or the full multi-principal/load matrix;
the overall R3 and system qualification rows remain open until the final source
and aggregate gates are reconciled.

## 2026-10-16 execution-scope tool qualification

The dispatcher-bound `integral_get_scope` projection is independently tested
against a different saved workspace preference, owner/admin/guest creation
rights, absent and revoked bindings, and attempts to pass model-controlled
identity arguments. Its declared result and generated capability map are
checked in the same selection. The focused run passed **38/38** across the scope
service, resident-skill alignment, and generated capability-map suites. R6 is
qualified on this source; this does not close the distinct workspace-policy,
authorization-revocation, or overall system qualification rows.

## 2026-10-16 request-bound history follow-through

Added an authenticated HTTP regression that gives one principal live owner
membership in two organization workspaces, creates track and entry events in
both, and reads both `/api/audit-log` and `/api/events` under each selected
workspace. Each response contains its own workspace's track event and excludes
the other workspace's track. An authenticated continuation from the first
workspace is rejected when presented under the second workspace. The new
regression also persists a user-scoped profile event for the same principal:
both APIs return it under the default Personal scope and withhold it under an
organization scope. Together with the existing audit-log and polling suites,
these cases passed **19 tests** on the JSON-backed HTTP harness. This closes
same-principal/two-workspace and Personal account-event boundary coverage for
the two HTTP consumers. It does not cover the native event-history consumer or
every invalid/revoked scope variant. R4 and system qualification remain open.

The 20,000-row native PostgreSQL probe also ran successfully after explicitly
overriding the repository's default `not slow` marker filter. It confirms the
attributed and legacy paths use index scans and no sort/sequence scan for the
sample plans. However, the attributed plan removed 3,592 rows by residual filter
to return 100 rows, and three timed two-page runs reported 732-841 ms ascending
versus 17-20 ms descending. Index catalog inspection confirms a workspace
composite exists, but the planner selected the generic DBLog timestamp index.
An experiment with an entity-partial composite selected the workspace index,
but reintroduced a top-N sort and failed the current plan contract; that
unqualified index change was reverted. The timings and plan are from one
disposable fixture shape, not a stable latency claim. Query/index expression
alignment, bounded work under a much larger workspace history and the direction
asymmetry need investigation before R5 can close.

## 2026-10-16 native audit consumer and filter repair

The native `integral_query_audit_log` contract advertised `resource_id` and
`resource_type`, but its route binding discarded both. A PostgreSQL regression
against the actual dispatcher reproduced a Track-only query returning the
neighboring Entry event. The binding now forwards these filters together with
actor kind, exact event action, cursor and limit. The model's published schema
now exposes the supported confidential continuation instead of the previously
advertised, unimplemented `since` argument. An undocumented `scope` argument is
removed from the binding; the authenticated execution workspace remains the
invoker's separate scope input.

The native PostgreSQL selection passed **8/8**, with no skips or errors. Each
filter is checked independently through the actual dispatcher, route, logger,
policy and database. The same principal owns two workspaces: queries and
continuation remain bound to the selected one, while a continued query in the
other is refused. Revoked and nonexistent bindings return errors without event
data. Account events appear in the caller's Personal workspace and are withheld
under an organization workspace. The catalogue schema is checked for the
model-visible filters and cursor. History endpoint documentation now describes
the actual bounded reader and confidential continuation.

The focused HTTP/history, manifest/reconciliation and capability-map selection
passed **43/43**. All 16 staged substrate guards and the configured changed-file
pre-commit checks passed, including pinned formatting, lint and backend typing.
The generated capability map was regenerated and remained unchanged.
`make verify-ci` also passed: **1,797 passed / 22 skipped**, with zero failures
or errors. Skips retain their stated PostgreSQL, optional Atlas and slow-probe
requirements; this smoke result does not replace those separate lanes or the
full default backend suite.

The separate index investigation confirmed that the pinned adapter compares
string timestamp ranges using text extraction while its ordered companion uses
JSONB extraction. Exact scalar workspace predicates improve selectivity but
cause an unbounded top-N workspace sort in the populated fixture. Splitting
timestamp and equal-timestamp/id branches still sorted 906 matching workspace
rows for a 100-row page. Both experiments were reverted; no dependency or
runtime index change is included here. R5 remains open and requires a qualified
query/index alignment solution, rather than relaxing the plan assertion to
accept the full-workspace sort.

## 2026-10-16 history qualification matrix correction

The populated PostgreSQL probe previously timed both directions but explained
only the final descending page, and all 20,000 fixture events shared a timestamp.
It now checks exact merged-stream page IDs against an independently ordered
fixture for both tied and varying (non-ID-monotonic) timestamps. Both directions
continue correctly after the boundary record is deleted. Every first/continued
workspace/legacy query is explained, and JUnit stores explicit sort, residual-row
and workspace-index findings. The original descending/tied no-sort/no-sequence-
scan assertion is retained; the new diagnostic shapes do not silently expand
that narrow qualification into an R5 completion claim.

Three independent disposable database runs each passed both fixture shapes
(six case executions, two distinct cases). Two-page logger timings were:

| Shape | Ascending milliseconds (three runs) | Descending milliseconds (three runs) |
| --- | --- | --- |
| Tied timestamps | 1337.49, 1136.75, 694.31 | 27.02, 55.57, 30.44 |
| Varying timestamps | 58.26, 49.25, 73.81 | 31.17, 37.31, 42.20 |

These include query/hydration/connection effects and must not be attributed solely
to an EXPLAIN node or reported as a stable latency SLA. Every sampled ascending
stream plan includes a sort. The tied descending workspace continuation removes
3,592 rows by filter for a 100-row query; the varying continuation removes 3,489.
The descending workspace plans lack a workspace index condition. R5 remains
open: bounded returned rows are not bounded database scan work. No production
index, adapter monkeypatch or query workaround is included in this increment.
On the final source, the two expanded probe cases plus eight native audit-tool
contracts passed **10/10** with zero skips, failures or errors
(`/tmp/core-history-expanded-final.xml`). EXPLAIN binds the same page-limit
parameter as the adapter. Configured changed-file formatting, typing and drift
checks passed. These are test/documentation changes; no runtime behavior changed.
The probe requires both `INTEGRAL_RUN_SLOW_TESTS=1` and an explicit marker
selection overriding the default slow exclusion; a skipped run is not evidence.

## Rules for recording closure

For each row, record the exact source commit or file manifest and the terminal
results of the relevant commands. Confirm that a test reaches the claimed
boundary using real current identity/containment or an explicitly named synthetic
fault. Do not add overlapping test counts. Do not turn timeout, unavailable
storage, incomplete journals or unknown external effects into absence or a fresh
execution permit. A prior receipt proves a past invocation/local commit, not the
present business state or arbitrary provider outcome.

Push qualified increments under the user's current authorization. Keep remaining
candidates isolated and preserve unrelated checkouts and runtime data. Merging,
publication and redeployment are separate actions and are not authorized by this
hardening goal.


## Qualified consumer checkpoint

The recorded-result HTTP/view/SDK increment is committed at `03e1a4611f9906ffa9161562d61bc032e9a395ce` on top of
`edd7817d`. Its unchanged runtime/test files match the sealed qualification
manifest. Full default backend: 6,527 passed / 781 skipped; CI-faithful smoke:
1,778 passed / 22 skipped; full frontend: 1,829 passed. All had zero failures or
errors. TypeScript, build, pinned hooks, staged guards and all four independent
artifact checks passed. The operation and graph PostgreSQL component evidence
is recorded separately in the MCP remediation and consumer reports.

This closes that consumer increment, not R1/R2 or the full goal. Independent
operation contract and terminal-proposal intent work remains isolated and needs
combined qualification and actual approval-consumption integration. A stronger
history plan probe also exposed remaining index-planning work despite the old
load test passing; R5 is not closed by that older probe. Shared replica limits,
directory privacy, metadata/deleted-history contracts, legacy relation migration,
external effects and complete restore/session journeys remain explicit rows above.

Remote CI must be read at the pushed head. No merge, deployment, live-provider
acceptance or publication is part of this checkpoint.


## Qualified local staged App-operation increment

On the `c7381289` baseline, staged App operations now bind their exact original
review and declaration to the existing canonical receipt. Local graph effect,
receipt/event intent and consumed decision commit together. Recovery observes
that same completion under current authorization without resolving a handler.
See the [atomicity report](2026-10-15-staged-app-operation-atomicity.md) for scope,
faults, immutable proof and source identity.

Full backend: 6,588 passed / 819 skipped; CI-faithful smoke: 1,797 passed / 22
skipped; combined native PostgreSQL: 224 passed / zero skipped; supplementary
expiry/revocation: three passed / zero skipped. All have zero failures/errors.
The supplementary file was added after full-suite collection; its result is
separate. Pinned hooks, staged guards and all four independent artifacts passed.
Remote CI must still be inspected at the new pushed head.

This qualifies one local integration. The other 43 executor kinds, conditional
batches, arbitrary external App effects, history query plans, replica admission
limits, privacy and restore/session journeys remain within the original goal.
There has been no merge, deployment or live-provider acceptance for this increment.
