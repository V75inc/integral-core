# Integral Core hardening execution

This continues the recommendations in `2026-10-14-core-state-access-stability-review.md`.
The active goal is completion of implementation and qualification. A partial
change or a passing targeted suite does not close the goal. No new runtime
deployment or PR merge is part of this work.

## Work and acceptance criteria

| Item | Current state | Evidence required for closure |
| --- | --- | --- |
| R1 shared approval ownership | PostgreSQL claim/fence primitive and named App/Track/Entry creation, metadata updates, deletion, App uninstall, identified comments, saved Views and Dashboard create/update/delete integrated; other executor families outstanding | Independent workers, stale worker, restart, current proposal authority, all executor families |
| R2 atomic effects and recovery | Named resource mutation receipts, hook intent/outcome handling and supported mixed batch journals implemented; remaining command families and recovery qualification outstanding | Real effect/result rollback, post-commit reconciliation without replay, cancellation, mixed/partial batch, external unknown outcome |
| R3 stream release | Per-connection/frame checks plus short native identity/workspace revision admission implemented | Multi-connection and within-page revocation tests pass; qualify admission/revocation ordering and remaining transport limitation |
| R4 history workspace scope | Bound request scope plus current canonical resource containment implemented | Same principal authorized in two workspaces; other workspace excluded despite resource permission; invalid/revoked scope refused |
| R5 bounded history | Timestamp/id database keyset windows, 1000-candidate request budget, honest continuation/outage implemented | Both directions, ties, deleted cursor row, denied windows, backend index/query behavior and realistic latency probe |
| R6 scope tool | Dedicated execution-scope service implemented | Bound versus saved preference, selective creation flags, missing/revoked binding, manifest and capability map |
| Privacy projection | Bounded per-recipient event snapshot projection implemented; declared-reference and deleted-history work outstanding | Nested references projected under current authority; explicit governed audience for deleted history |
| Durable chat stability | Durable native turns now default on; refresh/restart and terminal-run recovery qualified at `b832e592`; broader restore/session qualification remains open | PostgreSQL checkpoints/restore, session invalidation, migration, owner/member recovery journeys |
| Deployment architecture | Outstanding | Rate limits across replicas, directory privacy contract, dependency exceptions reconciled with actual current advisories |
| Legacy relation migration | Outstanding | Missing/ambiguous creation-audit identity migration; preserve existing records and stable relationship types |
| Maintainability | Outstanding | Consolidate command paths; targeted harness/module extraction; classified exception contracts; current docs and closure ledger reconciliation |
| Restore and session recovery | Disposable PostgreSQL checkpoint/manifest/synthetic-credential restore and independent-process readback qualified; attachment, session and complete user recovery journeys remain open | Disposable PostgreSQL backup/restore, independent encrypted checkpoint readback, credentials and session invalidation; no live runtime migration |
| Artifact/system qualification | Outstanding | Clean artifact, full source/CI suites, fault/concurrency/multi-principal/load matrix; preserve source-versus-deployment distinction |

## Implemented boundaries

### PostgreSQL command primitive

`staging_execution.py` binds a token-derived receipt identity to an immutable
principal/workspace/kind/payload hash. A held scalar CAS serializes ownership.
Expired undispatched claims can be replaced with a new fence; stale workers
cannot dispatch or commit. Dispatched/applied outcomes cannot be reclaimed by
waiting for a lease to expire. External unknown outcomes require reconciliation.

Local effects must enter the same task-owned graph transaction as their encrypted
result receipt. Exiting without the receipt aborts the transaction. This does not
grant resource authority or silently replace the existing staging dispatcher.
Entry create/update now bind the proposal claim into the canonical command's
existing transaction. The durable approved decision is locked and checked in that
transaction, along with the ordinary current resource authority. Minimal encrypted
record evidence commits with the graph and its event outbox. Token completion and
API notifications remain after the canonical graph commit. Recovery uses the
current read API and withholds content after current exclusion; it never replays
creation or increments the revision again.

Integration for other commands must preserve the same proposal/user/policy checks
and committed event outboxes, including partial batches and non-transactional
effects. Commands with registered Entry hooks now commit dispatch intent before entering
the API path. Actual hook invocations are tracked independently of the local graph
receipt. Returned hooks preserve normal behavior; failed/interrupted outcomes
remain held for reconciliation and cannot invoke the hook or recreate the Entry
again. Trusted nested graph writes join the outer transaction; only the root
writes its receipt. This does not claim that a hook response proves its business
outcome or that arbitrary privileged Python is sandboxed.

The current hook runner still permits extension I/O while the canonical graph
transaction is open. Moving external I/O out of held authority transactions, with
explicit graph/validation/external hook contracts, remains required.

Supported canonical batches now derive a stable command identity for every operation,
validate each step against the current approved parent plan, and recover through
immutable step receipts. They do not rely on a potentially missing progress
cursor. Named references retain the creation label even after later renames.
App/Track creation, metadata updates and deletion, Entry mutations, saved Views,
and identified comment creation/edit/deletion use this journal. Other batch command families
remain to be migrated; the current `supports_canonical_batch` implementation is
the authoritative command inventory.

New proposals retain a private execution-contract discriminator. An older blessed
proposal without qualifying result evidence is not replayed automatically. A new
explicit approval of a still-pending proposal can enter the current contract.
The staging store now locks existing PostgreSQL rows before updating, refuses to
recreate missing approvals from stale bookkeeping, preserves newer decisions and
retains terminal markers through the proposal's original expiry window.

### History and execution scope

Audit and polling routes now bind the validated request workspace and use bounded
database reads. Indexed event attribution is server-derived candidate metadata;
it does not replace current containment or resource authorization. Legacy rows
without attribution remain bounded candidates and undergo the same live gates.
Account-level events are limited to the caller's Personal workspace.

The history continuation may advance through denied candidates. `has_more` is a
conservative continuation indication, not a promise of another visible event.
No full-history total is invented. Logging-store absence/error returns an
unavailable error rather than an empty history.

Continuations through withheld candidates encrypt the underlying row position
and bind it to the principal, workspace, direction and filters. A base64-only
cursor would disclose a denied row's identifier. These are transient tokens
derived with a separate purpose from the server signing secret; rotating that
secret invalidates old continuations. They are never authorization grants or
the stored-data encryption format. Older visible-row cursors remain accepted.

`integral_get_scope` reads only the dispatcher's scope, reports current role and
creation flags, and refuses an absent/revoked scope. The UI preference endpoint
retains its separate contract.

### Streaming

Each socket and SSE frame gets a fresh admission check. A blocked first socket or
prior SSE yield cannot admit a later release after revocation. Bytes already
offered to a transport cannot be recalled. These tests do not establish atomic
network delivery ordered against every concurrent database revocation.

## Qualification record

- Previous pushed checkpoint `ac34fcba`: GitHub backend, frontend, PostgreSQL and
  independent artifacts passed; Docker lane skipped by workflow policy.
- Current targeted JSON selection passed; log:
  `/private/tmp/integral-browser-latency/hardening-targeted.log`.
- Current scope/history/staging/stream PostgreSQL selection passed; log:
  `/private/tmp/integral-browser-latency/hardening-postgres.log`.
- PostgreSQL command primitive tests passed initially. Receipt-write fault test
  was then strengthened to inject failure in the actual result update; final
  retained log: `hardening-staging-fences-postgres-final.log`.
- Final current-scope, private-cursor and command-primitive PostgreSQL selection passed; log: `hardening-postgres-final.log`. Configured formatting/lint/type checks pass.
- The broad backend run (`hardening-full-backend-cwd.log`, four workers) completed
  with two failures in tests patching the retired unbounded-reader seam. Their
  fixtures now use the bounded reader and an explicit validated workspace; all
  21 perimeter cases passed in `hardening-perimeter-final.log`. The broad run
  must be repeated on the final implementation before committing.
- Nine rooted PostgreSQL Entry create/update integration cases passed in
  `hardening-entry-cru-canonical-fixtures.log`. Cases include actual record/receipt
  rollback, post-commit projection failure and cancellation, separate worker
  cache ownership, completion failure/reload recovery, current exclusion, and
  update revision preservation. A tenth case verifies the graph-profile principal
  alias maps to the same unique current authentication identity; it passed with
  the other nine in `hardening-entry-alias-postgres.log`. Receipt reads now use
  the native transaction store, and a read/claim race reconciles a winner's
  committed result. The refreshed primitive plus integration log is
  `hardening-entry-current-postgres.log`. Initial update fixtures without a valid Entry
  type could fail before the injected receipt boundary; they now create through
  the canonical service and assert the receipt fault was actually reached.
- Current integration formatting, lint and backend typing are retained in
  `hardening-entry-current-hooks-final.log`. The refreshed primitive and Entry
  integration selection passed all 17 cases in `hardening-entry-current-postgres.log`.
- The local changes are not yet
  represented by a new artifact, CI result or runtime deployment.

## Integration hazards to resolve next

1. The existing API-backed entry executors also send watcher and mention
   notifications. Wrapping that HTTP coroutine in a graph transaction cannot
   roll those deliveries back. Notification delivery must join a committed
   outbox; it must not send while the graph/receipt transaction is open.
2. Entry-save hooks and generic App/tool/connector calls may perform external
   I/O. A local kind name alone is insufficient evidence of graph-only effects.
   Unknown external outcomes must retain dispatch intent and require recovery.
3. The batch executor mutates its in-memory/durable progress after each step.
   A new outer transaction must not leave the memory cursor ahead of a rolled
   back graph. Preserve already-committed legacy progress and qualify mixed
   local/external batches using stable step command identities.
4. Decision/application ordering needs additional cross-worker coverage: a revoke
   after graph commit but before legacy token consumption must not be mistaken
   for undoing the effect. Legacy decision helpers still mutate cached proposal
   instances and persist best-effort. Fence these transitions and keep decision
   and application outcomes distinct; do not close R1/R2 from Entry receipts alone.
5. Both current resource authorization and current proposal decision must be
   checked inside the effect fence. A command claim proves worker ownership;
   it cannot substitute for either approval or permission.
6. Existing operation and entry outboxes must remain post-commit and recoverable
   when joining an outer transaction. The logging database does not share the
   graph commit. A raw snapshot broadcast before commit is unacceptable.

Next active work: external hook intent/outcome handling and stable per-step batch recovery, then expand canonical command coverage and finish output-boundary qualification. The full original goal remains outstanding.

## Hook and batch continuation evidence

- Hook runtime behavior and uncertain-outcome tests passed in
  `hardening-entry-hooks-postgres.log`: 13 Entry integration cases including
  returned, failed and cancelled hooks, without repeated invocation.
- Entry batch cases passed in `hardening-entry-batch-postgres.log`: completion
  recovery, loss immediately after step commit with no progress cursor, named
  reference stability and refusal to replay an older unjournaled approval.
- Stale bookkeeping and contract-version cases passed in
  `hardening-staging-store-guards-final.log` on JSON. Time-aging tests now inject
  timestamps into the backing store rather than rely on mutating immutable
  proposal intent through ordinary persistence.
- Current combined refresh logs: `hardening-staging-progress-postgres.log`,
  `hardening-staging-progress-json.log`, `hardening-staging-progress-hooks.log`.

The complete original objective is still active. Required next work includes
remaining command and batch families, atomic decision transitions and ordering,
external I/O outside held transactions, output admission/projection, durable chat
and restore/migration qualification, maintainability and exact final artifact
and source gates. No PR merge or runtime redeployment is qualified by this slice.

## Atomic decision continuation

PostgreSQL bless/revoke/consume transitions now lock and read the shared proposal
row instead of trusting a worker cache. Terminal state-only markers commit in the
same transaction. Rejection after a local effect receipt is refused; it cannot
claim to undo an applied change. The linked WorkApproval rejection now follows
a successful authoritative staging rejection, rather than preceding it.
Malformed expiry records are refused with an explicit unavailable decision.

Six PostgreSQL decision tests passed in `hardening-decision-races-final.log`:
one winner for concurrent consumption, stale pending-cache rejection, rejection
after the real Entry graph commit (without a false linked rejection), and three
malformed-expiry cases. All 41 selected JSON decision compatibility tests passed in
`hardening-decision-logical-final.log`. Configured formatting, lint, convention
guard and backend types passed in `hardening-decision-hooks-final.log`. These
are targeted gates; the final full-suite/artifact qualification remains open.

The apply service is the only production caller of `consume_token`; no separate
consume-before-write caller remains in the current source inventory. This does
not close effect coverage: many dispatched command families still lack the
native command receipt integration.

Review identified the next required correctness issue: lazy expiry and terminal
closure can still originate from stale in-memory facts. They must transition
through the same shared decision fence and preserve committed or uncertain
effect evidence. In particular, an approval deadline must not make a committed
change appear unapplied or let stale cleanup remove another worker's approval.
This remains open and takes priority before broader executor migration.

## Expiry and partial-batch continuation

Native expiry now locks and checks current proposal and execution facts. Worker
cache sweeps queue candidates without publishing an assumed terminal state.
Cold-store reads use the same transition. Shared row deletion requires a matching
terminal marker and terminal row; a stale cleanup call cannot remove a live
approval. A current owner, committed result or unknown dispatched outcome retains
its decision row and reports reconciliation instead of a false expiry closure.
An expired undispatched owner with no local effect can still be finalized.

A child Entry receipt now writes the parent's partial-effect evidence in the same
graph transaction. Worker loss before any progress cursor no longer makes the
parent look effect-free. A partial batch past its approval deadline cannot apply
remaining steps automatically. A completed Entry or completed Entry batch can
reconcile its exact saved results after the deadline without creating records or
incrementing revisions again.

Initial combined PostgreSQL primitive, Entry, batch and decision selection passed
36 cases in `hardening-expiry-combined.log`; JSON compatibility passed 87 cases
in `hardening-expiry-json.log`. The refreshed PostgreSQL selection adds live-owner
versus lost-owner expiry and store/terminal compatibility cases in
`hardening-expiry-combined-final.log`. Formatting/type refresh is retained in
`hardening-expiry-hooks-final.log`; inspect final exits before claiming those
refreshed gates.

Remaining approval work includes current facts for replacement/partial repair,
explicit partial cancellation readback, durable downstream ledger/notification
recovery, other command families, and external I/O outside held graph/authority
transactions. Targeted expiry tests do not close those requirements.

### Expiry final targeted evidence

- `hardening-expiry-combined-final.log`: 55 PostgreSQL primitive, decision, Entry,
  batch, persistence and terminal cases passed. The initial refresh exposed a test
  that aged only the worker cache and expected immediate synchronous expiry. Its
  fixture now ages the durable proposal and drains the asynchronous transition;
  production continues to require current shared facts.
- `hardening-expiry-status-final.log`: 18 refreshed PostgreSQL decision and batch
  cases passed, including preserved terminal resolution timestamps from another
  worker and explicit applied/partial outcome indicators.
- `hardening-expiry-json-final.log`: 24 store/terminal/readback compatibility cases
  passed after the timestamp projection changes. Earlier broader JSON selection
  passed 87 cases.
- Formatting, lint, convention guard and backend types passed on the expiry
  implementation. The final docstring cleanup hook refresh is retained at
  `hardening-expiry-hooks-final.log`. Full backend/artifact/system gates remain
  outstanding; no source commit, CI publication or deployment is implied.

Next: migrate creation and other command families onto the shared receipt
contract, including the complete semantic effect of inline Track types/taxonomy;
finish partial-repair and downstream recovery, then output and system gates.

## Canonical creation continuation

App and Track creation now use the same staged command ownership, immutable
approval check and atomic graph/result fence as Entry commands. Track inline
schema and taxonomy setup occurs inside that canonical transaction, rather than
after an already-committed base Track. Failure after a first tag rolls back the
Track, schema, tags, command result and staged event outboxes together.

Canonical staged creation events are captured into the existing graph outbox
and offered after commit. Ordinary unstaged creation retains its original
transaction and event path. Current data/resource authority remains enforced by
the canonical services; the proposal and command receipt do not replace it.

The new private helpers were renamed from `staging_entry_apply` /
`staging_entry_batch` to `staging_command_apply` / `staging_command_batch`, and
the task binding is now `StagingCommandExecution`. All current Python source
and test consumers were inventoried and updated. The Entry-specific target
adapter remains at its canonical Entry boundary. Earlier logs and report
paragraphs retain the historical test/module names from their source checkpoint.

Mixed App/Track/Entry batches now journal every completed step with the parent
partial-effect fact. Recovery rebuilds named references and returns current
authorized records instead of rerunning creation, inline schema, or taxonomy.
Concurrent completion that finds the same effect already consumed now returns
its owned terminal marker as success, rather than reporting unknown application.
Consumption of a dispatched/partial/unreconciled native command is refused.

Qualification so far: `hardening-creation-final-postgres.log` passed 40 creation,
Entry, mixed-batch, decision and creation-context cases;
`hardening-creation-json-final.log` passed 69 selected JSON compatibility cases.
The initial taxonomy fault test expected an exception, but the existing HTTP
executor returns an error envelope. It now asserts that refusal, two actual tag
calls, and complete graph/result/outbox rollback.

The six final immutable-input snapshot, competing completion, and unknown-outcome
consumption cases passed in `hardening-creation-snapshot-final.log`. Formatting,
lint, convention guard and backend types passed in `hardening-creation-hooks-final.log`. A first JSON command referenced
a nonexistent batch-resume filename and collected no tests; the corrected
selection uses the actual `test_staging_resume.py` and is the evidence above.

Remaining: library/package creation side-effect qualification, all remaining
mutation families, external hook I/O outside graph/authority transactions,
partial repair and durable downstream delivery, followed by output and system
qualification. No full milestone, artifact, CI or deployed-stack claim follows
from this targeted source integration.

Review follow-up: native public proposal reads still need current-store refresh
when a worker cache retains a proposal another worker consumed or revoked.
Dispatch/effect checks are authoritative, but stale cached UI state is not an
acceptable final state. Qualify get-token, inbox and session listing after an
independent worker's terminal transition before closing the approval contract.

## Shared proposal read continuation

Native get-token, inbox and session lists now refresh from the current graph
store rather than merging cache-only approvals into the response. A terminal
marker from another worker updates the known token's terminal state and actual
resolution timestamp. Current approval changes are also visible without clearing
the worker cache. The native inbox reads pending/blessed rows as current facts;
terminal rows are excluded.

Native store and terminal-marker read outages surface unavailable errors. They
do not turn a warm pending card into successful readback, absence or an empty
inbox. JSON retains its earlier single-process read contract. A read is current
at its database admission/snapshot boundary; this is not a claim that bytes can
be recalled after later revocation. Every effect still performs its own live
approval/identity/resource checks.

Initial read-focused PostgreSQL selection passed 33 cases in
`hardening-shared-reads-postgres.log`; JSON continuity selection passed 38 cases
in `hardening-shared-reads-json.log`. The refreshed native selection passed 59 cases, including three 503 outage
cases and current creation/Entry/mixed-batch and store/terminal regressions, in
`hardening-shared-reads-integration.log`. Formatting, lint, convention guard and
backend types passed in `hardening-shared-reads-hooks-final.log`.

The next command integration targets App/Track metadata update: the current
HTTP bodies still own those graph writes. Move the complete canonical mutation
into service admission/transaction/receipt boundaries while preserving scope,
name uniqueness, stable relation type identity, Home validation and event
semantics. Other mutation families, partial repair, external I/O and full
output/system qualification remain outstanding.

## Metadata update continuation

App/Track update HTTP handlers now delegate to canonical metadata services.
The service admits the current identity, holds the workspace authority fence,
rechecks canonical location, explicit request scope and current policy, then
commits metadata with the staged result receipt and graph event outbox.
Ordinary human metadata updates use the same admission/service path and offer
committed events after leaving the transaction. The existing name uniqueness,
visibility, accent/icon, Home validation and stable Track relation identity
logic were preserved in the service extraction.

The shared command reader and mixed-batch journal now include App/Track updates.
Recovery returns current authorized records without repeating metadata writes
or changing their update timestamps again. Event delivery was renamed to
`deliver_staging_command_events` to reflect creation and metadata coverage.

Qualification:
- Initial four native metadata tests passed in `hardening-metadata-postgres.log`:
  completion recovery and actual receipt-write rollback for both resource types.
- The five-case update selection passed in `hardening-metadata-six-postgres.log`,
  including six existing-record updates, a typed expense-to-business relationship
  after the Businesses rename, retained stable type identity and no duplicates.
- `hardening-metadata-integration-final.log` passed 30 native metadata, creation,
  Entry and mixed-batch cases, including current-permission readback withholding.
  The first negative fixtures added exclusions while retaining direct admin
  grants. Core's explicit contract preserves direct grants across inheritance
  exclusions. The fixtures now remove the direct grant too; they verify actual
  revocation without changing permission semantics.
- Initial Home/relation/event-bypass JSON selection passed 13 cases in
  `hardening-metadata-json.log`. Broader ordinary CRUD/visibility cases are
  passed 72 cases with 7 explicit library-fixture skips in
  `hardening-metadata-crud-json.log`. Package-specific creation was not qualified
  by those skips and remains part of the library side-effect gate.
- Formatting, lint, convention guard and backend types passed in
  `hardening-metadata-hooks-final.log`.

Remaining command families include deletion/cascade, comments, views/dashboard,
taxonomy, sharing/ACL/link/invitation, operational-model authoring and generic
tool/operation invocation. Deletion must preserve graph and external cleanup
boundaries and explicit tombstone history, not wrap arbitrary HTTP/connector
I/O in a graph transaction. Library side effects, partial repair and full
output/system qualification remain open.

## Entry deletion continuation

Approved Entry deletion now joins the existing canonical deletion authority and
graph transaction with an immutable command receipt. Cleanup intent for upload
chunks and embeddings remains in `EntryCleanupFact`; file/provider work stays
after graph commit. The staged deletion event commits in the graph outbox, and
the HTTP wrapper offers it after commit instead of creating a second event.
Nested anchored deletion events can retain their validated before-snapshot
workspace witness when the resource has already disappeared. This is trusted
canonical event evidence, not a client-supplied authorization grant.

Deletion proposals now capture observed record/effective-schema revisions when
available. The effect compares both under its fence and refuses changed data
with a conflict. Completion recovery reports the saved graph-deletion fact; it
does not imply that every physical cleanup job has already finished.

Batch reference reconstruction now uses exact encrypted child receipts privately,
not the current UI projection. A completed create-then-delete sequence can
therefore reconcile all steps after the created record is gone. The response
still withholds the deleted record and its identifier; private command handles
never replace current mutation/resource authority.

Qualification:
- `hardening-delete-integration.log` passed 21 deletion, mixed-batch, creation
  and metadata cases.
- `hardening-delete-final-postgres.log` passed five final cases: completion
  recovery with cleanup fact retention; receipt failure rolling back the Entry,
  cleanup and event; record revision conflict; effective-schema conflict; and
  create-then-delete batch reconciliation without disclosure/recreation.
  The first schema fixture attempted to create a second pending deletion for
  the same target and was correctly refused. It now snapshots the schema on the
  original proposal, matching the real approval flow.
- `hardening-delete-final-json.log` passed 63 selected entry CRUD, staging,
  resume, event-bypass and generated capability-map cases.
- Capability map regenerated; formatting, lint, convention guard and backend
  types passed in `hardening-delete-entry-hooks-final.log`.

Remaining deletion work: App/Track cascades and lifecycle/library uninstall,
post-commit bundle/skill registry recovery, explicit tombstone-history audience
and broader external-cleanup/restore fault qualification. The other command
families and full output/harness/system gates remain part of the active goal.

## Track cascade and App teardown continuation

Track deletion now binds its staged command into the existing exact named
cascade authority scope. The complete subtree deletion, Entry cleanup facts,
Track event outbox and result receipt commit together. Receipt failure preserves
the Track and its contained Entry. Recovery reports the prior deletion without
replaying cascade or exposing the absent Track. The HTTP wrapper offers the
committed staged event instead of emitting a duplicate.

App hard-deletion's canonical cascade now also has a receipt integration seam
and records `BundleCleanupFact` while its bundle identity is still available.
This survives graph commit/worker loss; a receipt fault rolls it back with the
App. The hard-delete primitive is qualified directly. **The general delete-App
dispatch selector is not yet switched**, because its library branch archives
through `uninstall_app` and needs the complete lifecycle and registration
recovery protocol. This is ongoing integration, not refusal or narrowing of the
requested final command coverage.

Qualification: seven Entry/Track deletion cases passed in
`hardening-track-delete-postgres.log`; the refreshed primitive and Entry cleanup
selection passed ten cases in `hardening-track-app-delete-postgres.log`, including
App hard-delete bundle identity commit/rollback. Ordinary App/Track CRUD and
event checks passed in `hardening-track-app-delete-json.log` with seven explicit
library-fixture skips. Final hook/type status is retained in
`hardening-track-delete-hooks-final.log`.

Next lifecycle work must address more than one graph receipt: archive/uninstall
reference changes, strict graph agent/skill removal, local uplink and bundle
registrations after commit, recovery on each replica, restore/reinstall lineage
so old cleanup cannot disable a current install, and tombstone-event admission.
The retained cleanup fact is the beginning of that protocol, not a claim that
registry cleanup is delivered. Other command and output/system requirements
remain in the full active goal.

## Uninstall and registry recovery continuation

Native staged delete-App dispatch now includes the library archive/uninstall
branch. The wrapper enters the named cascade authority and shared command fence,
retains bundle/agent cleanup identity, applies lifecycle and strict graph
agent/skill removal, captures the uninstall event and commits the result.
Registration and local uplink cleanup happen after graph commit. Uninstall
recovery preserves archive semantics and returns its saved outcome without
repeating lifecycle writes. Legacy ordinary/compensation entry points retain
their existing behavior; further unification is still required.

Bundle cleanup is integrated into the existing recovery sweep. It reads bounded
keyset windows and checks current restore/reinstall identity under the workspace
authority fence before synchronous registry changes. Lookup failure keeps the
work pending. Each process handles a shared delivered fact independently, since
another replica's acknowledgement cannot clean this process's RAM. Cleanup
generations distinguish a later uninstall of the same App from an older
acknowledged command; nested phases share their command generation.

Qualification:
- The initial bundle/Entry cleanup and deletion selection passed 14 cases in
  `hardening-bundle-cleanup-final-postgres.log`. Actual registry tests preserve
  sibling registrations and model another replica's independent state.
- `hardening-uninstall-final-postgres.log` passed 16 cleanup-generation, library
  archive and deletion cases.
- `hardening-uninstall-agent-final-postgres.log` passed three final library
  cases: completion recovery, graph/cleanup rollback, and local agent retention
  after receipt failure followed by successful post-commit removal on retry.
- `hardening-uninstall-json.log` passed 48 existing lifecycle/recoverability and
  hook registry cases. Earlier ordinary bundle/App selection passed 31 cases
  with six explicit seeded-package skips.
- Formatting, lint, convention guard and backend types passed in
  `hardening-uninstall-hooks-final.log`.

Still required: concurrent install/restore ordering for every legacy writer,
strict declaration/permission evidence for cross-App uninstall reference changes,
full mixed uninstall/purge qualification, deleted-workspace maintenance, broader
replica/load/restore fault cases, and the remaining command/output/harness/system
requirements. Targeted simulated-replica tests are not deployment evidence.

## Ordered release admission continuation

`release_admission` performs a short native transaction with live identity and
workspace authorization ordering, ending before any transport send/yield. Event
release revalidates canonical workspace containment inside that interval. The
actual chat API's per-frame callback now enters the same admission rather than
relying on request-cached workspace selection alone. Current workspace denial
is the literal `"none"`; the first native test caught the initial `None` mismatch,
and that condition was corrected.

The defined ordering is admission before transport handoff. Revocation waits for
that short admission; it does not wait for a stalled socket. A frame admitted
before revocation may still be delivered, and cannot be recalled. A later frame
or another connection must obtain fresh admission and is refused after the
revocation commits. This does not claim atomic physical network delivery or
complete ordering for legacy writers not participating in the shared fence.

Fresh-process test collection also exposed an eager import cycle: importing
transaction helpers loaded the operation dispatcher, which registered HTTP
routes and imported the still-initializing membership helper. The operation
package facade now loads dispatcher exports lazily while retaining typed
exports for static checking. Fresh-process import tests preserve this boundary.

Qualification: nine current release/history PostgreSQL cases passed in
`hardening-release-final-postgres.log`; 27 event/history/stream JSON cases passed
in `hardening-release-json.log`; 46 import/facade/staging/operation/stream cases
passed in `hardening-release-facade-json.log`. Formatting, lint, convention guard
and backend types passed in `hardening-release-hooks-final.log`.

Remaining output work: nested reference and App-contract projection, explicit
tombstone audience, byte/admission/session contract documentation and full
transport/backend fault qualification. All remaining approval, harness, restore,
performance, maintainability and artifact/system requirements remain in scope.


### Recipient-specific nested event snapshot admission

Event history and each WebSocket connection now use `prepare_event_payload`.
Primary-resource authority and snapshot preparation run within the same native
identity/workspace release admission; transport sends happen after its exit.
A snapshot block (`before`, `after`, or `details`) with an inaccessible, missing,
foreign-workspace, undeclared or uninspectable embedded resource reference is
withheld as a whole. This also removes neighboring labels that could disclose
the denied reference. The stored event envelope and server-only hooks retain
original data; no recipient projection is cached or shared between users.

Inspection is bounded to 1000 values, depth 20, 64 KiB per string, 128 KiB total
text per block, and 100 distinct authority evaluations across the payload.
Only recognized canonical node references are eligible. Parent-owned nested
resources without a canonical resolver remain conservatively withheld, except
an already-admitted primary resource. Core's current contract has no field-level
visibility primitive; this does not introduce one. Unstructured prose without
record references is not a natural-language privacy filter. Declared App-output
contracts and an explicit deleted-history audience remain outstanding.

Qualification: `hardening-event-projection-json.log` completed successfully with
45 unit/perimeter/history/socket cases. `hardening-event-projection-postgres-final.log`
completed successfully with five real database cases: current private-reference
allow/deny, recipient-distinct broadcasts, foreign/missing reference withholding,
and admission/revocation ordering including a stalled socket. The initial native
fixture incorrectly used profile-only identities; the gate refused them, and
fixtures were corrected to actual authenticated identities. Configured format,
lint, convention and mypy hooks passed in `hardening-event-projection-hooks-complete.log`.
Final aggregate-text bounds are separately requalified below. The broader default
backend suite is running against this dirty source; no final suite result or
production deployment qualification is claimed yet.

The final aggregate-text bound and boolean-only admission change passed 46 JSON
cases (`hardening-event-projection-json-final.log`) and five native cases
(`hardening-event-projection-postgres-last.log`). Final source hooks passed in
`hardening-event-projection-last-hooks.log`; new test hooks passed separately in
`hardening-event-projection-final-test-hooks.log`. The broader default run began
before the last small bound change, so it is diagnostic evidence only and will
not substitute for an exact final-tree gate. An integrated native staging,
cleanup and event-admission regression run is now started on the final snapshot
projection source in `hardening-native-integrated-current.log`.

Integrated native regression completed successfully: 78 cases covering shared
staging claims/decisions/effects, creation/update/deletion/uninstall batches,
cleanup receipts, event projection and ordered release
(`hardening-native-integrated-current.log`, exit 0). This qualifies only the
implemented command families, not the remaining legacy families. A separate
20,000-row event-history probe is now running with both-direction keysets,
deleted cursor rows, multi-workspace and legacy attribution plus an unforced
EXPLAIN ANALYZE plan (`hardening-event-history-load.log`).

The enabled load probe passed after correcting a missing `cursor=None` fixture
argument (`hardening-event-history-load-final.log`). Both directions returned
100 distinct rows per page across tied timestamps, even after cursor-row deletion.
PostgreSQL used attribution indexes with a bitmap heap scan and top-N sort; the
unforced second descending page took 11.128 ms execution plus 1.079 ms planning
on this local 20,000-row dataset. Two-page elapsed measurements were 1013.0 ms
ascending (cold initialization included) and 34.18 ms descending. These are local
diagnostic timings under concurrent test load, not production latency limits.
The OR between current-workspace and legacy-attribution rows still requires
merging/sorting the combined candidates; independently bounded indexed streams
and a legacy-only partial ordered index remain a worthwhile optimization. The
probe does not validate higher dataset sizes or authorization throughput.


### Separately bounded history streams and broad regression findings

`find_page` now fetches at most one limited current-workspace page and one
limited unattributed legacy page, then merges at most 400 candidates by the
same timestamp/id ordering before returning at most 200. Missing/null legacy
attribution uses the shared exists-false predicate. A DBLog-only legacy partial
ordered index was added through jvspatial's public index API. Corrupt timestamp
representations fail unavailable rather than inventing a continuation.
The legacy descending probe used an index scan without a sort (0.419 ms local
execution in `hardening-event-history-load-merged.log`). A strengthened probe
also requires the workspace stream to avoid a sort; that assertion currently
fails in `hardening-event-history-load-two-streams.log`. This is an open
optimization/qualification gap, not a closed performance claim.

The broader diagnostic default backend run finished with exit 1
(`hardening-default-backend-current.log`): connector-binding snapshots and own
avatar metadata were over-withheld, and a staging documentation assertion no
longer matched the refactor. The source now derives own-account Connector event
release workspace from its current workspace binding and verifies that binding
inside admission. Avatar reference access requires the caller's own unique
User avatar edge plus caller upload attribution; uploaded-by alone is not
sufficient. The staging docstring now explicitly identifies staging_store and
its hydrated _open_batches cache. Focused regressions are being requalified;
the full final-tree gate remains required. Runtime and PR state remain untouched.


Personal event corrections passed 62 focused JSON cases
(`hardening-personal-events-regression.log`) and eight native cases
(`hardening-personal-events-postgres-final.log`): permitted/denied connector
references, own versus ambiguous avatar ownership, mismatched Connector scalar
versus current workspace edge, and the existing private/foreign/revocation cases.
Connector admission now reads the primary row rather than a cached entity and
requires one matching current Workspace parent; mismatches fail unavailable.
Final configured source hooks passed (`hardening-event-handoff-hooks.log`).
The corrected full default backend run is active in
`hardening-default-backend-corrected.log` (pytest session 98562, four workers).
An external reporting-only pytest hook prints failures as soon as they occur;
its source is outside the checkout and does not change markers, assertions,
fixtures or application behavior. The stronger workspace-stream query-plan
assertion remains failing: bitmap scans plus a top-N sort examine roughly 906
matching rows on the tied-timestamp second page. A disposable-only additional
partial-index experiment did not remove that sort and was not promoted to Core.
The next native test resets that experimental database state.

Final corrected-source targeted run passed 26 cases
(`hardening-event-final-source-targeted.log`, exit 0): the three broad-run failures,
merged history stream ordering in both directions, perimeter denials and bounded
snapshot inspection. The full corrected-source default suite is confirmed live
as session 98562; no failure has been emitted at this checkpoint. Do not restart
that suite solely because a polling interval expires. Wait for its terminal
result before claiming broad qualification. The strengthened query-plan probe
remains red; it must be improved and requalified before the performance item is
closed or a commit gate can be declared clean.


### Comment creation and recovery qualification

Identified human comment creation now uses current membership/policy admission,
canonical Entry containment, immutable approved body/parent inputs, and a graph
transaction that includes the Comment, author/reply relationships, encrypted
execution receipt and event outbox. `add_comment` participates in the durable
command and batch dispatch infrastructure. Mention notifications remain
best-effort callbacks; edit/delete comments and public-share comments are not
qualified by this change. The internal comment writer now writes the model's
`text` field; historical `body` records have not been migrated.

Per-recipient Comment snapshots retain authorized literal text and supported
public author metadata while still bounding inspection and checking typed parent
references. This does not qualify arbitrary declared App output or deleted-record
audiences.

Comment receipt recovery preserves the applied fact after workspace revocation
and withholds current content and identifiers. It still propagates authorization
availability failures and restores request scope. Native PostgreSQL qualification
passed seven cases (exit 0, `hardening-comment-revoked-readback.log`), including
completion failure/restart without duplication, receipt rollback, foreign reply
parent denial, immutable body enforcement, authorized reply event projection,
revoked readback and authorization outage. Configured hooks passed on the changed
recovery source and native tests (`hardening-comment-recovery-hooks.log`). Earlier
comment/metadata native qualification passed 12 cases; JSON event/CRUD/perimeter
qualification passed 59 cases, with terminal exit 0 confirmed.

The corrected default backend run (`hardening-default-backend-corrected.log`,
previous session 98562) terminated with exit 0. It started before the comment
changes, so it is not a final full-tree gate. The history workspace query-plan
probe remains red. No source commit, merge or deployment is claimed here.


### Identified comment mutation integration

`edit_comment` and `delete_comment` now enter the same durable command claim and
batch dispatch infrastructure as creation. The API delegates to a canonical
mutation service with a current live identity, workspace admission, unique Entry
parent, current scope/read policy, a locked current Comment and immutable approved
inputs. Editing remains author-only; removal of another author's or public
visitor's comment requires current moderator authority. Both supported author
identity forms are matched to the currently admitted profile, rather than trusting
an arbitrary caller-provided profile. The existing `text`/`body` executor aliases
are compared using the same input normalization at the effect boundary.

The comment change, event outbox and encrypted receipt commit together. Recovery
reads current content after edits and returns only the applied deletion fact after
removal; it does not repeat a write or reveal the removed identifier. This does
not yet qualify mixed comment batches or durable downstream notifications.

Fault tests first exposed two defects in this increment: lazy entity-save index
DDL stalled while a row lock was held, and retaining a preflight graph context
allowed mutation outside the transaction. The corrected service rebinds the
transaction context before hydration/writes and uses its public `save` method.
Primary-storage rollback assertions prove that injected receipt failures leave
both original text and parent relationships intact, including after attempted
deletion. These failures are retained in the earlier logs rather than represented
as passing qualification.

Corrected native PostgreSQL qualification passed all 11 cases, terminal exit 0
(`hardening-comment-mutations-postgres-corrected.log`). The 35 existing JSON
comment CRUD, permissions and public moderation cases passed, terminal exit 0
(`hardening-comment-mutations-json-final.log`); the moderation audit test now
captures the canonical event service. Configured hooks passed on the mutation
source/tests and the final context correction. This remains targeted source
qualification: full-tree/backend/CI/artifact gates, other command families,
deleted-history audiences and runtime validation remain outstanding. No commit,
push, merge or deployment occurred.


## User-requested pause checkpoint: bounded model retries

The retry increment is source-qualified and the goal is paused at the user's
request. Default `INTEGRAL_NATIVE_MODEL_MAX_ATTEMPTS=3` means three total physical
model attempts, with bounded backoff and inline `Retrying · 2 of 3` / `3 of 3`
status. Successful retry restores the normal Thinking status. Only transient
SDK/network/rate-limit/provider failures and empty replies before output qualify.
No whole-agent run is restarted and completed tools are not replayed. Each
physical request gets a fresh ID, numbered attempt and dispatch/outcome evidence;
Work admission is obtained afresh for every attempt. Unknown provider billing
remains unknown rather than being represented as free retries.

The initial stream is inspected with a bounded 64-chunk buffer until content,
reasoning or tool output starts. An empty stream or failure before that point can
retry; a failure after released output cannot. The per-attempt deadline includes
SDK dispatch and initial stream output, and the existing overall turn deadline
still applies. Permanent errors, admission/storage failures and cancellation
stop execution. A bounded turn event queue delivers retry status promptly and
propagates producer cancellation without leaving a waiting consumer.

Final targeted qualification passed 183 cases, terminal exit 0
(`model-retry-checkpoint-tests.log`): new retry/admission/attempt identity,
exhaustion, empty reply, first-chunk recovery, partial-output no-replay and
cancellation cases plus existing LiteLLM transport, provider streaming,
provider cancellation, adaptive harness and error classification suites.
Configured hooks passed (`model-retry-checkpoint-hooks.log`) and `git diff --check`
passed. Configuration and retry limits are documented in `.env.example` and
`docs/ops/CONFIGURATION.md`.

This is an uncommitted source checkpoint. No commit, push, merge, deployment,
live-provider or browser qualification is claimed. Remaining goal work is still
the full acceptance matrix above, including other command families, mixed comment
batch qualification, external-I/O transaction boundaries, the red history query
plan probe, durable chat/session/restore tests, reference/deleted-event privacy,
legacy migrations, maintainability and final whole-tree/artifact/CI gates.
Resume from this worktree and the exact logs; preserve all unrelated checkouts,
private `tasks/` evidence and runtime data. Do not resume the paused release
heartbeat or deploy merely because this targeted checkpoint passes.


### Requested local conversation recovery fix

The deployed conversation reproduced `harness_model_request_unsettled` despite
its previous Core run being `succeeded`: five earlier model attempts retained
unknown billing outcomes, while the run had a complete snapshot and scoped
successful read receipts. The terminal recovery predicate admitted only failed
or cancelled runs, so a successful retry run was incorrectly blocked.

Terminal continuation now includes succeeded runs under the same exact
principal/workspace/thread/provider and original tool-receipt checks. Unknown
provider cost observations remain unchanged, and no old request or action is
replayed. The history notice accurately distinguishes completed from interrupted
runs. Scoped text-only recovery also accepts a message's descriptive
`page_context` metadata without importing that metadata as instructions; other
provider metadata and non-text attachments remain excluded.

144 targeted recovery/provider/retry cases passed (exit 0,
`/private/tmp/integral-local-hardening-deploy/recovery-fix-tests.log`), including
successful runs with uncertain attempts, failed/cancelled/succeeded receipt
validation, blocked unknown writes, and scoped text metadata. Configured hooks
passed. The broad hardening goal remains paused; this is a separately requested
bug fix and local deployment, not full goal closure.


The corrected wheel was deployed to the existing local API as
`integral-business-recovery-api:local`. Installed module hashes match source,
the API reports healthy/connected, and a scoped readback of the exact affected
session admits its 33 saved history messages. All five uncertain physical-model
observations compare unchanged before/after recovery. The previous images,
override and a fresh database dump remain available for local rollback. This
qualification does not change billing outcomes or rewrite the old run.


Browser qualification in that same conversation completed successfully: the
read-only CRM request returned the correct existing App link and `Finished · 1
tool call · deepseek-v4.1-flash:cloud`. The old historical error remains in the
transcript; it was not erased or rewritten. No new conversation was needed.


### Commit qualification corrections

The previously red populated history probe now passes on jvspatial 0.2.2 using
public query operators. Singleton membership for workspace-attributed string
filters avoids redundant text/JSONB equality predicates that distorted planner
estimates. The legacy stream retains its equality predicate and existing partial
ordered index. The 20,000-row test verifies both-direction keyset results,
deleted cursor rows and disjoint attribution; the inspected workspace/legacy
descending page plans have neither Sort nor sequential-scan nodes. This is local
candidate-query evidence, not a production throughput claim. Detailed plans are
in `/private/tmp/integral-local-hardening-deploy/commit-history-load-final.log`.

The first full commit-gate run exposed a retry-stream cancellation ordering
regression and was interrupted explicitly after source corrections, rather than
claimed passing. Source events now wait for consumer acknowledgement before the
producer advances, preserving the initial session metadata persistence boundary;
transient retry status does not impose that acknowledgement. The provider wrapper
explicitly closes the producer on cancellation. The existing no-dispatch-after-
initial-event cancellation test and all 28 targeted boundary/retry/history tests
pass (`commit-final-boundaries-corrected.log`). The final full-source suites and
native qualification are recorded separately below when terminal.


The sequential broad run identified one HTTP compatibility regression: identified
comment creation used the existing-effect scope validator (403) instead of its
established creation-scope mismatch contract (400 for an accessible but different
workspace). The canonical service now uses the existing creation-scope resolver;
foreign scope remains denied with 403. All 51 scope/comment regression cases and
11 final native comment cases pass. The latter retains transaction rollback,
immutable approved bodies, current parent containment and recovery without replay.
The corrected whole-source backend and subsequent CI smoke gates are recorded
below once terminal; previous failed/interrupted logs are not passing evidence.


### Terminal checkpoint qualification — 14 October 2026

The final default-selected backend suite passed with `TESTING=1`, `DEBUG=false`
and four xdist workers. The subsequent `make verify-ci` smoke replay also passed
with four workers; these gates ran sequentially. Native PostgreSQL selections
passed separately: 93 integrated contract cases, followed by 11 final comment
cases after restoring the creation-scope HTTP contract. The 51 scope/comment
API regressions, 28 retry/recovery boundaries, isolated graph deadline test,
and populated history load/query-plan probe passed. Configured hooks over all
staged files and staged substrate guards passed. Infrastructure/domain/slow
exclusions and skips remain; local smoke is not the entire GitHub workflow.

Earlier failed or interrupted logs remain diagnostic evidence, not passing
gates. The source snapshot does not include the later private-default visibility
amendment, which is being qualified separately. The broader goal remains paused
and the open-boundary ledger above remains authoritative.

## Resumed qualification — 15 October 2026

The goal is active again. The previous goal turn made authoritative progress:
recovery commit `b832e592ed01091bd2ddd8c0092869a6dadd7228` was pushed to PR #136.
GitHub CI run `38682149836` completed successfully for that exact commit:
backend, frontend, PostgreSQL and independent artifact jobs passed. Docker
verification was skipped. The earlier full default-selected local backend suite
also exited 0; frontend qualification passed 1,815 tests, and the separate
transaction/recovery PostgreSQL selection passed 86 cases. These results belong
to that committed source, not to every requirement in this ledger.

Further work uses `/private/tmp/core-hardening-continuation` on
`codex/hardening-continuation`, based on that commit. This separates source
qualification from the recovery checkout mounted by the running Business API.
No runtime data, container, deployment or PR merge was changed by this increment.

Mixed Entry/comment batch qualification now covers loss immediately after each
of six committed steps: Entry creation, comment creation, comment editing,
Entry update, comment deletion and a final comment creation. Clearing the local
proposal cache before recovery exercises durable reload. Assertions check stable
Entry identity/revision, exactly one final comment, and completion without
resurrecting the removed comment. A separate parent-completion outage case
reconciles a completed batch through saved child receipts even after an earlier
comment has been deleted. A workspace-membership revocation between steps keeps
the committed Entry/comment intact but refuses unfinished edits and leaves the
approval unconsumed. Recovery restores the request scope.

The final combined PostgreSQL run passed all 24 batch/comment cases with zero
skips, failures or errors (exit 0; `/tmp/core-hardening-mixed-comments-revocation.xml`
and `.log`). The new tests and ledger corrections are uncommitted. Configured
flake8, isort and whitespace checks pass for the changed test file; no new
production-code changes or new full-suite qualification are claimed.

This closes the named mixed comment-batch qualification gap only. Remaining
work includes other executor families, external hook I/O transaction boundaries,
durable downstream notification delivery, declared App-output/deleted-history
privacy, backup/restore and independent checkpoint/session recovery, legacy
relation migration, replica-wide rate limits/directory privacy/dependency review,
maintainability work and the final whole-goal acceptance matrix. The historical
pause and earlier checkpoint sections above remain dated evidence, not current
completion claims.

### Saved View receipt integration and readback classification

The next executor inventory audit found that `save_view` already entered
`staged_command_effect` but was absent from the durable dispatcher. Its effect
binding was therefore empty: both creation and update wrote graph data without
an execution receipt. Four native reproductions failed before the correction
(`/tmp/core-hardening-saved-view-red.log`). The command now joins the shared
claim/receipt path, including supported canonical batches. Single-command and
batch eligibility use one `CANONICAL_COMMAND_KINDS` inventory. Other Operational
Model actions, View deletion and dashboard commands are not qualified by this
addition.

The effect compares the complete submitted View payload with the immutable
approved inputs. Its encrypted receipt stores only Track/View identity, rather
than a historical content snapshot. Recovery reads the current View through
canonical containment, current policy and release admission; a moved, deleted or
currently denied View is withheld. Named batch references are rebuilt from
private receipt identity and the original approved View name, so a later rename
does not prevent continuation. The first mixed-view test exposed the prior
title-versus-name reference mismatch; that failing log is retained separately
(`/tmp/core-hardening-saved-view-extended.log`).

The new outage test also reproduced an error-classification gap: an endpoint
authorization outage was converted into an applied-but-withheld response
(`/tmp/core-hardening-saved-view-outage-red.log`). The shared current-record
readback helper now accepts only 401/403/404 as classified denial/absence.
Unavailable, rate-limited, malformed or other failed reads raise the classified
authorization-unavailable error. Comment and ordinary resource receipt recovery
use the same helper. This does not retry or replay any mutation.

Native qualification passed 57 cases, zero skips/errors/failures, covering the
new View family plus existing Entry, metadata, batch and comment recovery
(`/tmp/core-hardening-saved-view-final.xml`). View cases include create/update
completion loss, graph/manifest rollback on receipt failure, current revocation,
endpoint authorization outage, changed approved inputs, named mixed-batch
references, and an independent connection holding identity disable before the
write. The latter passes with existing admission behavior; no new identity
locking policy was added. Strengthened primary-storage graph-edge rollback
assertions passed separately in both create/update cases
(`/tmp/core-hardening-saved-view-edge-rollback.xml`).

The JSON/view regression selection passed 73 cases with zero skips/errors/failures
(`/tmp/core-hardening-view-json.xml`). The configured pre-commit framework passed
formatting, import order, lint, the convention guard and whole-backend mypy
(`/tmp/core-hardening-view-precommit-qualified.log`). Earlier tool-only attempts
could not locate mypy; running the configured hooks with the qualified backend
environment resolved that environment issue. This increment remains uncommitted
and undeployed. A final default-selected backend gate is still required; the
older green PR CI does not qualify this source delta.

The final default-selected backend gate is running as unified exec session
`91203`, with `TESTING=1`, `DEBUG=false`, four xdist workers and the repository's
`make test-backend` selection. Output is in
`/tmp/core-hardening-continuation-full.log`; JUnit will be written to
`/tmp/core-hardening-continuation-full.xml` at completion. The session was polled
and confirmed live after start; no terminal result is claimed here. Staged
structural guards passed separately (`/tmp/core-hardening-continuation-guards.log`).
Resume by polling that handle or inspecting its owning process; do not restart
the suite merely because an observation interval expires.

### Restore failure classification and independent encrypted-state recovery

The restore audit reproduced a tooling defect in all three modes: a nonzero
`pg_restore` exit could still yield script exit 0, and a drill with matching
fixture counts even reported completion. The red client-fixture run retained
three failures (`/tmp/core-hardening-restore-status-red.xml`). The script now
uses `--exit-on-error` and rejects any nonzero client exit before diagnostics or
success reporting. Drill cleanup still runs through its EXIT trap. It does not
promise rollback of an explicit target after a failed restore.

All six success/failure client-fixture cases pass after correction
(`/tmp/core-hardening-restore-status-green.xml`, zero skips/errors/failures).
The separate real PostgreSQL gate also passed with `INTEGRAL_TEST_RESTORE=1`
(`/tmp/core-hardening-checkpoint-restore-final.xml`, one case, zero skips/errors/
failures). It seeds a disposable graph, encrypted Harness message snapshot,
encrypted Core recovery manifest and synthetic provider credential; uses the
actual backup/restore scripts; then reads the separately restored database from
a fresh interpreter and GraphContext. Foreign checkpoint scope returns no data,
and a wrong key rejects both manifest and message snapshot authentication.
The actual `--drill` mode then verifies counts and identity and removes its
scratch database. The separately created readback copy is removed in test
cleanup. No live runtime database or real provider credential is used.

The gate is explicitly opt-in because it requires a disposable PostgreSQL
server, Docker clients and permission to create/drop test databases. Its
configuration and exact limits are documented in `docs/ops/DEPLOY.md`.
Configured pre-commit formatting/import checks and backend mypy passed, and
`bash -n scripts/pg_restore.sh` passed. These are component recovery results,
not attachment-byte, session-invalidation, live-provider, production RPO/RTO or
complete owner/member recovery acceptance.

The existing full backend run (session `91203`) remains live. Its collection
predates these new restore tests; the enabled restore and client-fixture runs
above qualify those additions separately. No terminal whole-tree/backend or
new CI result, commit, push, deployment or goal completion is claimed here.

The final strengthened restore rerun passed (one case, zero skips/errors/
failures; `/tmp/core-hardening-checkpoint-restore-cleanup.xml`). It additionally
checks `pg_database` directly after cleanup: neither the exact generated drill
database nor the independent readback copy remains. This replaces reliance on
a cleanup log message as proof of removal. The staged structural guards and
configured pre-commit checks passed on the restore increment; the final added
assertions also pass the pinned Black hook. The full backend session `91203`
was polled again and remains live, without a terminal result yet.

### Dashboard containment perimeter and broad-gate correction

The first full backend run (session `91203`) is now terminal with exit 2 from
Make: JUnit records one failure, zero errors, 512 skips and 6,827 executed cases
(`/tmp/core-hardening-continuation-full.xml`). The failure was the successful
frontend-audit fixture: the isolated checkout had no `frontend/node_modules`,
so the real gate correctly refused to run. The locked frontend dependencies
were installed with `npm ci`, without changing tracked dependency files.

The audit test itself also needed isolation. Its negative fixtures previously
could pass on that missing-directory precondition, without exercising their
mocked audit error documents. Frontend audit cases now copy the unchanged real
script into a temporary repository with a fixture install directory; an
invocation log proves the mocked `npm audit` command actually ran. The tests
are marked pure unit cases. The real gate still requires installed dependencies.
All six corrected audit cases pass (`/tmp/core-hardening-audit-fixture.xml`).

While preparing the next command-family migration, six Dashboard scope tests
reproduced an authorization gap (`/tmp/core-hardening-dashboard-scope-red.xml`):
single-dashboard reads trusted scalar App attribution, and listing trusted a
registry's hydrated children. A forged Dashboard App/workspace cache could make
a foreign dashboard readable to the owner of the forged App; missing or extra
catalog/registry parents also failed to deny reads.

Dashboard location now uses the existing primary-record ResourceLocationWalker:
one CATALOGS parent of type Dashboards, one matching App parent via CONTAINS,
consistent App identity caches, and the canonical App/workspace/root path.
Present workspace caches must agree; a legacy empty or absent denormalized
workspace cache does not replace the named path. The projection hydrates the
same admitted raw Dashboard record rather than a cached `Dashboard.get` result.
Single reads, lists and default-dashboard maintenance use that projection.

Listing uses 128-row storage keysets selected by scalar App attribution only
as candidate hints, with fresh named-path validation for every result. A
withheld last candidate still advances the internal window; storage outages
surface unavailable rather than an empty list. Listing no longer creates or
repairs registry/catalog nodes. Ambiguous legacy catalog links are denied;
this change neither deletes nor automatically repairs them. This is not a
dashboard latency benchmark or qualification of concurrent dashboard mutations.

The initial Dashboard/API/service regression selection passed 50 cases. The
final scope/audit selection passed 17 cases with zero skips/errors/failures
(`/tmp/core-hardening-dashboard-final-targeted.xml`); nine actual graph cases
also pass on PostgreSQL (`/tmp/core-hardening-dashboard-scope-postgres.xml`).
Configured pre-commit lint/format/import checks, whole-backend mypy and staged
structural guards pass (`/tmp/core-hardening-dashboard-final-precommit.log`,
`/tmp/core-hardening-dashboard-final-guards.log`). Dashboard durable mutation
claims/receipts are still the next command-family integration, not completed
by this read-perimeter fix.

A new sequential broad gate is live as unified exec session `64666`:
`make test-backend` with four isolated workers and explicit TESTING/DEBUG
settings, then `make verify-ci` only if the first command succeeds. Logs are
`/tmp/core-hardening-continuation-corrected-full.log` and
`/tmp/core-hardening-continuation-corrected-ci.log`; the full run writes
`/tmp/core-hardening-continuation-corrected-full.xml`. This run includes the
new restore and dashboard tests in collection; opt-in PostgreSQL/restore cases
still require their separately recorded enabled gates. No terminal broad result
or CI smoke result is claimed yet. Preserve this live run when continuing.

### Dashboard command integration, event capture and effect-time View authority

The earlier sequential gate (`64666`) completed successfully. Its full backend
checkpoint has 6,845 cases, zero failures/errors and 513 skips, followed by a
successful CI-faithful smoke command. These results are checkpoint evidence:
the following dashboard receipt and stronger View authority changes require
their own final broad gate.

Dashboard create/update/delete now use the shared command inventory, claims and
batch journal. A separate generic Core dashboard-command module wraps the
existing API execution in current identity/workspace admission, canonical App
containment recheck and the exact approved payload fence. It commits graph
changes, encrypted minimal identity/deletion evidence and event outboxes
together, then offers event delivery after commit. Readback returns current
authorized content or an applied-but-withheld/deleted fact; it never reruns a
mutation. Invalid graph-only API results travel through a shared classified
rollback exception, preserving their error envelope rather than converting
them into a missing-receipt failure. This is not a contract for external I/O.

Eight initial native cases reproduced missing dashboard receipts and missing
saved-View outboxes (`/tmp/core-hardening-dashboard-receipts-red.xml`). Event
capture had its own stale command allowlist, which omitted `save_view`. It now
uses the same canonical inventory as native dispatch and batches. Saved View
and dashboard outboxes are offered after their graph/receipt transaction
exits. The actual allowed Entry hook points are expressed directly for the
four command kinds that invoke them; configuring widgets does not execute
their queries or extension tools.

The first native correction passed 21 cases. A broader 39-case diagnostic run
found one invalid-input fixture had staged a duplicate still-pending dashboard
proposal; the other 38 passed. The fixture now rejects its first proposal using
the actual revoke API before staging the invalid variant. Its corrected 400
classification/rollback case passes. These failed fixture logs remain evidence,
not passing runs. The complete staged-command PostgreSQL selection then passed
114 cases with zero skips/errors/failures
(`/tmp/core-hardening-staging-all-native.xml`). This includes Entry, metadata,
creation, deletion, uninstall, comments, saved Views and dashboards.

Named dashboard references are rebuilt from private child receipts even after
approved deletion. Three additional unit cases exposed inconsistent whitespace
normalization in reference capture (`/tmp/core-hardening-batch-name-red.xml`).
Capture now trims original approved name/title padding just as lookup does.
The padded-name dashboard batch and saved-View event rollback additions passed
their 21-case native rerun (`/tmp/core-hardening-dashboard-final-native.xml`),
and 67 JSON dispatch regressions passed
(`/tmp/core-hardening-dashboard-dispatch-json-final.xml`).

A stronger native authority test found an additional saved-View race: workspace
revocation could commit after the model-edit permission check but before the
View write. The previous held-identity test covered admission before execution,
not this interval. The new test pauses the admitted View writer and competes
with revocation on another connection, with authorization state/indexes
prewarmed to exclude incidental DDL blocking. It failed before the correction
(`/tmp/core-hardening-saved-view-mid-effect-revocation-red.xml`) and passes after
placing the entire saved-View command under the existing membership/identity
fence with a fresh canonical Track scope recheck
(`/tmp/core-hardening-saved-view-mid-effect-revocation-fixed.xml`). The admitted
write now commits before that participating revocation can finish. This does
not claim that arbitrary unguarded database edits participate in the protocol.
Both invalid create/update View configuration cases also pass, retaining 400
errors with graph and event rollback
(`/tmp/core-hardening-saved-view-invalid-config.xml`).

Final configured format/lint/import/mypy and staged structural guards pass
(`/tmp/core-hardening-dashboard-receipts-qualified-hooks.log`,
`/tmp/core-hardening-dashboard-receipts-qualified-guards.log`). The current
whole-source qualification sequence is live as unified exec session `97511`.
It runs artifact verification, the full native staging selection, the full
default backend suite, CI smoke, then frontend tests sequentially, stopping
on the first failure. Artifact verification already exited 0: both wheel builds
are identical, SHA256
`881530aa8af5a1388067a8d4040fc3a29704bc80e84ac4ae4d632ac59c3dbb6c`,
and the wheel imports outside the checkout. Logs use the prefix
`/tmp/core-hardening-final-` (`artifact`, `native`, `native-driver`, `backend`,
`ci`, `frontend`); JUnit is recorded for native and backend. Poll this live
handle before making new product-code edits or claiming the final gate passed.

The changes remain staged and uncommitted. No push, merge or deployment is
included in this continuation. Operational Model sub-actions, standalone View
deletion, other executor families, extension I/O boundaries, downstream delivery
recovery, output/deleted-history privacy and the remaining system/maintainability
matrix are still open. In particular, integrating `save_view` does not by itself
migrate `modify_operational_model.*` to durable command ownership.

At the latest poll, session `97511` reported artifact exit 0 and native exit 0.
The final native JUnit has 117 cases, zero skips/errors/failures
(`/tmp/core-hardening-final-native.xml`). The same live sequence has advanced
to the full backend stage; CI smoke and frontend follow only on success. Their
not-yet-created logs do not establish a missing or stopped process.

### Commit handoff qualification — 2026-10-15

The frozen-source sequence above completed successfully before this handoff:

- Reproducible artifact build and outside-source wheel import: exit 0; wheel
  SHA256 `881530aa8af5a1388067a8d4040fc3a29704bc80e84ac4ae4d632ac59c3dbb6c`.
- Native PostgreSQL staged-command selection: 117 passed, zero skips/failures/errors.
- Full default backend suite: 6,335 passed, 530 skipped, zero failures/errors
  (6,865 total JUnit cases, 1,099.359 seconds).
- CI-faithful smoke (`TESTING=1`, `DEBUG=false`, xdist): exit 0.
- Frontend: 319 files and 1,815 tests passed, exit 0.
- Configured pre-commit formatting, imports, lint and mypy: passed.
- All staged structural guards: passed.

The raw local evidence uses `/tmp/core-hardening-final-` as described above;
these temporary logs are qualification evidence, not durable deployment files.
The separate opt-in encrypted checkpoint/credential restore rehearsal passed
previously against the same unchanged restore implementation; the default
backend suite intentionally skips that explicit disposable-server gate.

The user authorized committing and pushing this increment to existing PR #136.
The handoff consists of the restore correction, isolated audit-test fixtures,
and durable View/Dashboard command and containment fixes. The earlier statements
that these changes were uncommitted describe intermediate checkpoints. This is
local qualification only: remote CI must be checked at the resulting pushed
head, and no merge, redeployment, browser acceptance or full-goal completion is
claimed. The remaining work listed above remains open.
