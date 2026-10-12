# Access-control milestone integration review — 12 October 2026

This review integrates access checkpoint `0f971e03833902a2a202f2c9a8ee2bae9c9e705f`
with main `21b56bbab51af7822cb1e61807ebcccd0a8f788f` (UI polish,
source observations and ontology context). The current user requested review
and merge of this milestone. The 11 October pause/authorization statement in
`MILESTONE_HANDOFF.md` describes that historical checkpoint. Core version
remains `0.1.1rc16`; integration is not a version bump, release or deployment.

## Review scope and invariants

The review covered current primary identity and canonical containment; workspace
membership, suspension and protected owner governance; source-specific sharing
and grant lifetimes; access groups and named App actions; independent service
identity intersected with current issuer rights; exact deferred approvals;
transactional Entry admission and post-commit cleanup; local recovery posture;
and the integration boundaries with staged operations and graph projection.

The reconciliation preserves I-GRAPH-01/02 (rooted graph participants),
I-ROLE-01/02 and I-ACCESS-01 (current resource authority and role boundaries),
I-CHAT-01 (audience-scoped label resolution), I-SUBSTRATE-01/I-EXT-01/I-HOOK-01
(generic contracts and published App hooks), and the view/model and UI scope
and clipboard invariants added on main. Protected owner governance is not
ordinary App function authority; named action execution retains the ordinary
authority context.

## Resolutions and regressions

- Retain access-control primary raw identity validation with a two-row bound:
  duplicate profiles remain unavailable instead of silently selecting a match.
- Retain canonical Entry projection for pagination and counts. Catalog-rooted
  feed and mission-control tests retain exclusion and direct-grant assertions;
  malformed direct roles now explicitly require authorization-unavailable.
  A separate Workspace owner is used for ordinary-member exclusion checks.
- Preserve authenticated human provenance separately from independent service
  authorization. Explicit human subjects must match the current caller;
  independent agent/connector relation tests use actual rooted service actors
  with current source-only delegation, and cannot borrow human target reads.
- Preserve staged App operation preparation and reviewed declaration binding
  alongside current named action and service authority. Store original and
  resolved inputs separately; include resolved targets in proposal identity;
  reject changed targets or declaration before execution.
- Reproduced a stale-handler race: a definition changed between preparation and
  effect admission could let an old handler execute under a new permit. Bind
  the admitted permit fingerprint to the prepared registered definition before
  calling the handler. The regression failed before this check and passed
  after it; the old handler is never called.
- Keep graph-context semantics and denial/size/candidate limits under current
  authority. Complete semantic fixtures copy nodes, edges and authentication
  objects into native SQLite without relaxing the cooperative deadline or
  caching permission grants. Root catalogs introduce a second valid path;
  the test verifies either valid path and every connecting evidence edge.
  JSON under concurrent disk load can return an explicitly incomplete bounded
  capture; this review does not claim complete projections on every backend.
- Reset raw PostgreSQL contract tables for each independent test. The previous
  raw fixture retained other tests' outbox rows, causing false audit-count
  evidence when these contracts ran together. The shared graph helper now
  independently creates the Root/IntegralApp shell; the logging fixture
  initializes DBLog indexes as server startup does before protected effects.
  Inferred relation tests explicitly select their intended target Entry type
  alongside the newly cataloged Track's default type. Assertions remain unchanged.
- The final App identity test uses the actual current Workspace owner, not
  a fabricated actor. The retrieve probe measured 21 operations at both one
  and thirteen Entries after main's request-workspace binding; calibrate fixed
  overhead to 22 while retaining the strict +2 growth bound and current
  identity/scope checks. No runtime authorization checks were removed.
- Regenerate the combined capability map (126 declared tools, 124 advertised).

## Qualification

Current integration qualification is recorded in `merge_qualification.json`.
The current candidate passed CI-faithful no-environment smoke, all 1,678
frontend tests, 103 targeted PostgreSQL tests on an isolated disposable
database, Core-only and independent Core/SDK/external-App artifact gates,
and the designated native DeepSeek cloud allow/deny smoke. The complete parallel backend run finished with two test-only failures:
a fabricated install actor and the pre-integration fixed retrieve ceiling.
All other cases passed, including the extracted App install/operate/deny/
pause/upgrade/uninstall journey. After correcting those two tests, the App
identity test and the complete query-budget module passed (six checks).
Runtime source was unchanged between the complete run and the repair rerun.
All `make verify` constituent gates are covered; the initial complete
invocation itself exited nonzero and is retained as failed-attempt evidence.
The first PR CI run passed backend, frontend and independent artifacts but
exposed seven failures in the broader PostgreSQL lane. Raw tests now bind
the fresh graph and preinitialized audit store; normal Workspace/library/
connector fixtures independently seed the root. The custody race uses a
real admitted owner, rooted resources and a declared Entry type instead of
mocked permissions and a fabricated actor. All 297 cases in the full
PostgreSQL CI selection then passed on a freshly recreated review database;
JSON fixture-consumer checks also passed. Assertions and real storage races
remain intact. Fresh PR CI is still required on the updated committed head.
Historical failed integration attempts are retained in local review logs; they
are not passing evidence. Original milestone receipts remain separately scoped.

## Remaining milestone limits

`MILESTONE_HANDOFF.md`, `ACCESS_CONTROL_LIFECYCLE_HANDOFF.md` and the delivery
plan retain unresolved R1 output/workspace binding, actual send/chunk release
races, nested reference disclosure, broader admin/public writer coverage;
R2/R3 group overlap/rejoin with pending approval, changed declarations and
explicit regrant, safe adoption and emergency override; and R4 deployment
PostgreSQL restore, continuously current independent checkpoints, structural
migration, per-owner recovery credentials and complete paginated owner UX.
Full browser/API/MCP journey acceptance and the 22-finding/76-scenario closure
ledgers remain independent work. Passing integration tests do not close these
items or complete the finalized access-control plan.
