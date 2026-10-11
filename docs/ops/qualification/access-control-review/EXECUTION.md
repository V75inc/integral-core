# Access-control implementation execution

The authorized goal is full closure of the review and its 76 use cases. All implementation, tests, migrations and UX changes land on **`codex/access-control-review`**. The isolated checkout is based on `9d13b44e`; unrelated App/UI changes remain in the original checkout. User authorization includes milestone commits and pushes. It does not imply merge, release publishing or production deployment.

[Closure ledger](closure.json) records each finding and case without treating observation probes as secure acceptance. [Plan](../../../product/ACCESS_CONTROL_PLAN.md) defines the contract and invariants.

| Milestone | Deliverable | Completion gate |
|---|---|---|
| M0 | Preserve assessment, branch, closure ledger and clean baseline | Hooks, build/type checks and baseline evidence |
| M1 | Immediate perimeter defects and safe response replay | Paired allow/deny tests for invitation proof, events/cache/failure gates and replay |
| M2 | Canonical scope, live authorization and structural lifecycle | Foreign facade rejection, corruption denial, multiprocess revocation fences |
| M3 | Owner governance, membership/delegation, durable grants and transfers | Owner/private-resource matrix, safe self/peer administration, atomic invite/link/offboard races |
| M4 | Versioned App actions, groups and conditions | Independent reference Apps, protected mutations and equivalent execution surfaces |
| M5 | Owner administration UX and migration/recovery | Independent-account browser readback, audience migration diffs and restore proof |
| M6 | Complete qualification and residual audit | Every ledger row has actual allowed/denied evidence, full gates and all findings closed |

Before each commit, stage only scoped files, run hooks and required clean build/tests; substrate runtime milestones require the full suite. Push tested milestones to the same branch. Qualification failures remain open and are repaired before committing. Never rewrite failed evidence as successful.

## Progress

Goal active. M0 preserves the assessment, authorized plan, single branch and complete closure ledger. No runtime fixes are claimed at this milestone.

Clean-checkout characterization reproduced all 23 observations (32.60 seconds). The baseline guards, pinned format/lint, TypeScript, reproducible wheel/import and no-env smoke/xdist checks passed. The initial frontend run on unsupported Node 23.10 failed one streaming test (1,582 passed); the same test passed on bundled supported Node 24.19. The complete Node 24 frontend suite passed all 1,583 tests (279 files); the optional baseline-only backend run was stopped before runtime integration; no full-suite baseline pass is claimed. The mandatory full runtime milestone gate follows the repair slice. These checks qualify the starting state, not remediation or release readiness.


### M1 perimeter slice qualified

115 targeted regressions passed in 26.91 seconds, including the new secure requirements. Mailbox proof is now bound to the current identity email and the OTP delivery address; legacy bare verification booleans require reverification. Ordinary login remains available. By-ID actions are distinct from invitation-token acceptance, which proves no global mailbox claim.

Event release now uses isolated read-only admission for the actual Entry/App/Track/View, and comments/attachments require access to their parent Entry. Nested OTP/reset/password fields are scrubbed from user wire snapshots, while internal persisted evidence is unchanged. Unknown resource snapshots are withheld until their audience contract is implemented; other deleted-resource snapshots remain withheld pending governed tombstones. This slice does not claim full event/audit parity or cross-worker graph-fact freshness.

Complete resource inputs distinguish memoized reads; payload/effect evaluation is never memoized. Exclusion lookup failures return unavailable. Avatar reads compare the workspace role explicitly. Read and durable-command replay recheck stored Entry dependencies without re-running the prior effect. Durable epoch fencing, arbitrary-output dependency completeness, PostgreSQL replay qualification and the remaining review findings stay open.

The initial new requirement run exposed expected security failures and two event test setup defects (an unlinked synthetic auth ID), corrected to use the actual graph principal. Existing invitation happy-path fixtures now perform real verification using a test email delivery fixture. The user-scope event unit test now represents a User event; a user scope cannot authorize an inaccessible Entry event.

A disposable PostgreSQL 16 instance qualified 33 tests in 16.96 seconds: the existing operation execution receipt and graph transaction contracts, plus the new perimeter and mailbox proof cases. This establishes real-store behavior and rollback/receipt compatibility for that selection. It does not establish independent-worker revocation ordering, invitation/ownership races, or durable-command replay disclosure after revocation; those have separate remaining gates. The instance is isolated from application databases.

The first full runtime gate caught a legitimate Connector event regression: the stricter audience filter withheld the caller's own event. The repair resolves the actual Connector and checks its owner against the caller's graph/auth identity. The next run passed no-env smoke/xdist and all 1,583 frontend tests, then found a deletion-history regression. That failed run was stopped after independent reproduction. Deleted Connector history now requires its server-recorded owner, the same acting principal and exact account scope; legacy snapshots lacking owner proof remain withheld. The targeted Connector/redaction/perimeter selection passed 31 cases. General resource tombstones remain unimplemented. The full gate is rerunning with four workers for the complete backend collection.

The scope/cache audit also identified a graph entity cache beneath permission memoization. Fresh mailbox/visibility/lifecycle reads, primary-store authorization epochs and shared transaction fencing are required in M2; this slice does not claim cross-worker freshness.

The four-worker full backend run exposed four additional audit-history regressions. The repair adds administrative, snapshot-free denial forensics (including a denied create with no resulting Entry), creator/actor/account-bound Policy history, and OperationalModel history bound to its actual attached App/Track and administrative role. It does not authorize arbitrary event snapshots via account scope. The relevant 67-test selection passed, including new paired allow/deny cases. The complete runtime gate must pass after this repair before committing.

The supplementary lanes passed 9 Core-only tests, 52 non-PostgreSQL extension-contract tests, and 221 PostgreSQL spike/contract tests. The PostgreSQL restore rehearsal skipped because Docker's backup client could not reach the host-published disposable port; no restore proof is claimed. The other PostgreSQL selection passed 33 cases as recorded above. Migration/restore and independent-worker authorization ordering remain separate open gates.

The final repair selection passed all 90 cases on disposable PostgreSQL in 39.77 seconds. The subsequent `make verify` completed with exit 0: all staged guards, pinned format/lint and types, reproducible wheel/import, no-env smoke/xdist, all 1,583 frontend tests and the full backend suite. The wheel reproducibility SHA-256 was `fb5e2338b37d6cdd17c27c31dc3e9fb0725a06c66fef27873b3686e86f90ea97`. These are local and disposable-store results, not GitHub CI, browser, migration or release evidence. M1 perimeter slice is qualified for commit/push; the broader findings and all use-case rows retain their remaining gates.

### M2 scope and ordering foundation qualified

M1 perimeter slice was committed and pushed as `4d5af896` on the same designated branch. The active M2 slice adds a bounded Walker for canonical Entry/Track/App containment, rejects foreign-workspace and foreign-App point reads through ToolContext/OperationContext, and checks registry/rooted links and active App state. This is not yet the cutover of every generic resolver/facade method; corrupt legacy containment and deletion disposition remain separate migration gates.

HTTP authorization now bypasses the process TTL role/inventory cache. Authority-bearing resource/principal reads use primary queries at live boundaries; direct-grant, membership, parent and anchor lookup failures cannot silently activate a fallback grant. Current AuthUser mailbox reads use the Object query contract: an ID predicate inside an operator avoids incorrectly searching `context.id`. The first targeted run exposed that mapping error; the repaired 24 invitation/email/shared-surface cases passed. Complete event resource audiences and nested output dependencies still require their declared contracts.

Effective roles now pool direct and inherited candidates before choosing the strongest complete role. Exclusions retain only direct candidates. The author exception requires commenter and normalizes graph/Auth principal forms; viewer is read-only even for authored Entries. Existing inherited-role capping remains in force pending the separately planned owner-governance contract.

A workspace-attached WorkspaceAuthorizationState and typed HasAuthorizationState edge carry a revision separate from settings. The PostgreSQL authorization scope holds a shared row lock, reads current facts after admission and bumps ACL revision in the same graph transaction. Nested scopes join the held transaction, reject foreign-store/task inheritance, and poison the outer transaction after a caught nested failure. Nontransactional stores cannot silently claim this strong seam. The helper is not yet wired into every mutation/release surface; cross-worker goal-wide revocation remains open. Callers reload graph nodes after entering the transaction, rather than retaining a node bound to the outer database.

After correcting two test-fixture transaction errors, the real-store scope/transaction/location selection passed 22 tests in 32.66 seconds. A further four-test PostgreSQL seam selection passed in 4.12 seconds, including an independent Python process with a warmed editor cache that waits behind revocation, then reads revision 1 and no role. This proves ordering for participants using the seam, not all existing ACL writers. The compatibility selection passed 102 tests in 11.09 seconds. Targeted new fault tests found a remaining direct-role exception default, which was repaired rather than weakening the denial requirement. Full slice qualification is pending.

Affected invariants: I-GRAPH-01 preserves same-unit Workspace-to-state attachment and rooted containment; I-GRAPH-02 retains this permission/cascade participant as Node; I-ROLE-01 restores strongest-role and viewer/commenter authority semantics; I-ROLE-02 keeps the inherited editor cap; I-ACCESS-01 enforces resource modeling and bound execution scope; I-CRUD-01 keeps changes in services. Core/App import and typed-edge conventions remain preserved. No invariant is weakened by this slice.

The repaired final targeted selection passed 71 cases (one PostgreSQL-only skip) in 28.26 seconds. The final real-store selection passed 61 cases in 46.18 seconds, including canonical scope, role/failure gates, invitations, response perimeter and the cross-process fence. Pinned formatters and type checks passed; two missing public-function docstrings from the initial lint run were added. The full local gate follows before commit.

The first complete gate passed guards, pinned lint/types, reproducible wheel/import, no-env smoke/xdist and 1,583 frontend tests. The supplementary extension-contract lane independently reproduced a reference-App failure: its shared fixture created an owner and organization workspace without cataloging either into the rooted graph. The fixture now uses the production catalog helpers. The two affected qualification runs were stopped before editing that fixture; no full backend/contract pass is claimed for those runs. A secure regression retains denial for an uncataloged workspace even when its branch metadata looks valid.

A separate temporary-store probe confirmed an additional AC-04 case: an exclusion with malformed optional `excluded_at` metadata is skipped by edge hydration; the inherited role changes from None to editor even though `EXCLUDED_FROM.count` still returns 1. Deny existence must use the typed relationship-presence primitive, and the secure fault tests must inject failure into that actual read. This reproduction was retained as open while the fact repair below was prepared; the foundation is not claimed to close every AC-04 authority-helper case. The probe used an owned temporary JsonDB, removed afterward.

The repaired complete gate finished with one backend failure: the Tracks list used 74 database round trips against its unchanged budget of 55. Core-only and extension-contract lanes passed; the PostgreSQL contract lane exposed concurrent primary authorization I/O on the connector sync runtime's raw transaction connection. Neither run is claimed as a completed milestone gate. Connector effects now use the serialized, joinable seam and retain a durable outbox gate based on transactional capability. Transaction cleanup waits for active I/O and refuses queued or late reads after closing.

The additional fact slice checks exclusions by relationship presence, rechecks a graph profile's linked AuthUser at live boundaries, and supports explicit held-handle nesting. Secure cases cover malformed optional deny metadata through point, list, page and count reads, both aliases of missing/disabled identities, and caught nested write failure on PostgreSQL. The first fact selection passed 60 cases with one PostgreSQL-only skip, but the Tracks query budget still measured 62. The count helper now defers redundant Track/principal reloads until an already-authorized collection is known to contain Entries. The budget remains 55; qualification is pending.

The final focused fact/compatibility selection passed 86 cases (one PostgreSQL-only skip) in 111.91 seconds. All five existing query-budget assertions passed with their original limits. The PostgreSQL selection passed 36 cases in 102.31 seconds, including all six connector sync contracts, five authorization-scope proofs and 25 location/fact cases. This repairs the two exposed full-gate failures without raising budgets or allowing concurrent I/O on a held connection. Final required gates run against this source before commit.

The final `make verify` gate passed against this source: all staged guards, pre-commit checks, pinned formatting/lint/types, clean reproducible wheel/import, no-env smoke/xdist, 1,583 frontend cases in 279 files, and the complete backend suite. The wheel SHA-256 is `12d5b2ab12d0bd4b8508b8eb8e74f413e4976d0aa65f53fc1498c062417a919e`. Supplementary Core-only, extension-contract and PostgreSQL lanes passed. The PostgreSQL lane has one explicit restore-rehearsal skip caused by the Docker client/macOS network boundary; restore and independent current-authorization checkpoint qualification remain open. This is a qualified foundation slice, not closure of all M2, all AC-04/06 or any unqualified surface.

A reproducible passive source index now records 428 declarations (384 REST and 44 facade declarations, including overrides), source hashes, and explicitly unmapped evidence. It is not a runtime route/reachability or security-acceptance claim. The 76 use-case rows remain open until their complete permitted/prohibited surface evidence is mapped.

### M2 facade scope slice under qualification

System reads, Entry discovery, relation reads, App settings, Track identity and file reads now use canonical execution containment and fresh role checks. Bound-App governed queries apply their server-side target constraint before selection, pagination, counts, ambiguity responses and declared handler invocation. Generic conditional updates use the typed schema/policy facade; exact App action permits remain a separate outstanding repair. File access requires the actual HAS_ATTACHMENT relationship, with a second Entry/relationship check after storage read. This fresh check is not yet the shared release-ordering fence. Attachment writes receive the same canonical read/edit gate; object-store rollback/dispatch intent remains open.

Rollups refuse unreadable dependencies rather than allowing partial values to be persisted as shared computed mirrors. Adversarial tests revealed that jvspatial Walker hooks record exceptions and continue; canonical scope and rollup callers now reject hook-error reports before returning any result. Parent-read and relation-read fault injection pin this behavior. An unrooted Workspace cannot create authorization revision state. Additional foreign catalog and Workspace parent edges invalidate App/Track containment.

The initial selections failed four JSON cases and two PostgreSQL cases: one settings test incorrectly passed a graph ID to a package-key API; two older tests used unrooted fake records and patched retired cached reads; and the rollup denied-reference test exposed Walker exception suppression. Corrected the settings input and replaced the fake scope/relationship fixtures with rooted real graphs without weakening the runtime gate. Added explicit hook-report failure handling. The revised selections passed 90 JSON and 43 PostgreSQL cases. Full repository qualification is running; no full pass is claimed yet.

The passive source inventory now has 429 declarations (384 REST, 45 facade, including overrides/private methods). It remains an index, not runtime reachability, surface parity or use-case closure evidence. Remaining facade administration/notification/personal-vault/provider paths and complete dependency, identity, action and release contracts are still open.

The complete `make verify` gate subsequently passed for the frozen runtime source, including all staged guards/hooks, pinned format/lint/types, clean reproducible wheel/import, no-env smoke/xdist, all 1,583 frontend tests and the full backend suite. Wheel SHA-256: `ed7489912fcfbc042f946c344f62b37ecf96f5e589ffbfc319d50fdd7fd7cbee`. The broader PostgreSQL spike/contract lane passed 227 cases with one explicit restore-rehearsal infrastructure skip. Core-only and extension-contract lanes are pending before commit. The first full-gate attempt stopped at a lint rule suggesting an async generator passed to synchronous all(); documented that invalid suggestion locally and reran the complete gate successfully. No test or runtime behavior was bypassed.

Source inspection of the remaining conditional-write path found that OperationContext discards the normalized validation result and sends raw updates to a state-only CAS; record-revision concurrency, materialized-relation rollback and exact action permits remain open. This scope slice does not claim those write guarantees. Six use-case rows now link partial named allow/deny evidence while preserving their unfinished cross-surface limits.

Supplementary qualification finished successfully: 9 Core-only cases and 52 extension-contract cases passed. The 227 PostgreSQL-only cases skipped in the JSON extension lane are not claimed as JSON passes; the dedicated PostgreSQL lane above exercises those contracts, retaining its single restore infrastructure skip. This facade scope repair slice is qualified for commit/push on the same authorized branch. The complete M2 contract, all finding closures and remaining use-case surfaces stay open.


### M2 authority facts and legacy disposition under qualification

The preceding facade scope slice was committed and pushed as `d521eb5b`. Current workspace authority helpers reload the principal and membership facts, reject ambiguous owners and unknown roles, and page the complete workspace inventory without returning partial success after a later page fault. Complete canonical containment, owner governance, protected transfer and shared revision fencing remain separate open requirements.

AC-19 disposition removes the three inactive legacy document generation/template lifecycle modules. Whole-repository call-site inspection found no supported API, CLI, test, App manifest or example caller; the lifecycle module already referenced retired model/edge definitions. GeneratedDocument registration, persisted records, the document field API and supported renderer remain. New source and clean-wheel checks pin absence of the retired modules and availability of supported contracts. No persisted record was deleted or migrated. Closure requires the full artifact gate below.

The final targeted selection passed 55 JSON cases and 52 PostgreSQL cases, including live disabled-identity checks, ambiguous-owner denial, partial-inventory failure and legacy module disposition. Earlier attempts exposed query-budget regressions (feed 39, tracks 60; then feed 33). Fresh caller/workspace facts are reused within the already admitted operation and the membership/owner candidates are read together; the final run passed unchanged feed/track budgets. Full repository and supplementary qualification follow against frozen source.


The first supplementary extension run reproduced five failures caused by reference-App fixtures attaching a second owner after the shared helper had already created one. That run and the concurrent incomplete full gate were stopped before editing source. The fixture now accepts the intended canonical owner directly; all seven affected HTTP/resident/MCP/transactional journey fixtures use it. The deliberate ambiguous-owner regression remains unchanged. No completed full-gate pass is claimed for the interrupted runs.


An isolated characterization against pinned jvspatial 0.1.1 also confirmed an authentication-runtime revocation issue. After warming token validation, a primary-store disable was persisted (`is_active=false`); a second token validation accepted the cached identity and wrote the whole AuthUser back with `is_active=true` while updating last_accessed. The owned temporary JsonDB was removed after the probe. [Recorded characterization](auth_identity_characterization.json) is unsafe-behavior evidence, not secure acceptance or a production/PostgreSQL exploit claim. AC-06 remains open and now includes primary auth reads, limited atomic identity updates and actual HTTP/session admission qualification. This does not change the narrower authority-helper assertions already under qualification.


The repaired complete qualification finished with exit 0: `make verify` passed staged guards/hooks, pinned format/lint/types, clean reproducible wheel/import, no-env smoke/xdist, all 1,583 frontend cases (279 files) and the full backend suite. The working-tree wheel SHA-256 is `ff6830d547639b1482664f493d08d7ee97089d49935976f33d088cc52a97e1cc`. Supplementary lanes passed 9 Core-only, 52 extension-contract and 227 PostgreSQL cases; the one restore rehearsal retained its explicit Docker/macOS network skip. This closes AC-19 by removing the unsupported modules and qualifying the shipped absence while preserving supported document contracts and persisted records. It qualifies the narrower authority-facts slice without closing AC-04/06, general canonical containment or owner governance.


### M2 current authentication slice under qualification

The authority-facts/disposition milestone was committed and pushed as `3b9f855e`. Integral now extends the pinned jvspatial authentication service and Server configurator so built-in auth route closures and middleware use the same adapter. AuthUser and RefreshToken reads bypass cached entities; missing active-state facts cannot default to active. Observed writes patch only changed fields and compare identity/token security facts. Token-blacklist admission queries primary presence, avoiding stale process-local misses. Password reset reloads and writes the current identity within the held transaction.

The first PostgreSQL race selection failed one of 35 cases: an independent warmed worker waited behind account disable, then accepted and reactivated the account. The pinned store cannot translate array guards and falls back from its CAS method to read/modify/save. Overriding `find_one_and_update` alone therefore does not establish atomicity for a particular query. The repair acquires the actual identity row using a scalar-only no-op through the shared serialized transaction, compares the complete snapshot under that lock, then patches limited fields on the same connection. The held-disable assertion was preserved. A subsequent attempt stopped on a circular import, repaired by deferring the operation-package transaction import until writes execute; another attempt used an incorrect test DSN environment key and failed bootstrap without exercising runtime behavior.

The corrected final PostgreSQL selection passed all 35 cases in 14.34 seconds. It includes current disable/role/profile facts, blacklist invalidation, actual HTTP login/middleware/refresh without the test bypass, an independent worker waiting behind held disable, and password change rollback. Logs: `/tmp/integral-access-auth-current-pg.log` (initial race failure), `/tmp/integral-access-auth-current-pg-2.log` (collection failure), `/tmp/integral-access-auth-current-pg-3.log` (bootstrap configuration failure), and `/tmp/integral-access-auth-current-pg-4.log` (passed). These owned test-store proofs do not close AC-06: all identity writers, account/session epochs, common identity-before-workspace ordering, effect/release fences and live provider behavior still require completion. Full and supplementary gates follow against frozen source.

The broader JSON selection passed 104 cases in 28.68 seconds, including signup, rate-limit compatibility, session revocation, bootstrap and MCP OAuth/consent contracts. Three existing Authlib deprecation warnings remain. U61 records named partial allow/deny evidence and preserves browser/socket/MCP release and session-epoch limits. The clean working-tree wheel reproduced SHA-256 `40a88313a5f33c7277da5f8fbacf839f36dff3280768c39aadd0537c93ef431e` and passed isolated imports. Complete source qualification is still running.

Supplementary qualification completed with exit 0: 9 Core-only, 52 extension-contract and 230 PostgreSQL spike/contract cases passed. PostgreSQL collection independently confirmed 230 contract cases plus one spike; one restore rehearsal skipped because the Docker client could not reach the macOS host-published disposable port. No restore proof is claimed. The full `make verify` gate is still running; all 1,583 frontend cases in 279 files and CI-faithful smoke/xdist have passed.

The complete `make verify` gate finished with exit 0 against frozen runtime/test source: all staged guards/hooks, pinned format/lint/types, reproducible clean wheel/import, no-env smoke/xdist, all 1,583 frontend cases (279 files) and the full backend suite passed. The supplementary lanes above also passed. This current-authentication slice is qualified for commit/push on the designated branch; AC-06, U61 and the goal-wide session/effect/release requirements remain in progress. No GitHub CI, shipped-browser, provider, migration/restore or deployment proof is claimed by these local results.


### M2 raw authority facts under qualification

The current-authentication milestone was committed and pushed as `2cb066fb`. This slice shares an explicit primary AuthUser lifecycle reader with authentication and permissions; both login-ID and linked graph-profile aliases reject missing/coerced active state and ambiguous raw profile mappings. Existing graph-only actors remain supported pending their explicit trusted-service contract. Direct OWNS/COLLABORATES_ON candidates are read together before hydration, require exact persisted role literals, and reject duplicate or conflicting candidates. Optional audit metadata cannot erase a complete grant. No inherited role ceiling or ordinary role meaning is changed.

Workspace membership roles reject malformed canonical values and conflicting legacy representations; a legacy literal is used only when the canonical field is absent. Selective creation rights require actual stored booleans, validate a unique membership, and default absent optional grants to false. Creation/publication helpers and workspace flag/member projections use these primary facts. A personal workspace guest is no longer advertised both creation rights by the caller projection. Older membership mutation/recreation paths remain pending their canonical lifecycle service.

The initial selection passed 115 JSON cases, including existing query-budget checks. The expanded selection passed 150 in 59.67 seconds. The PostgreSQL selection passed 89 but two duplicate-insertion fixtures failed before permission evaluation: the existing `edge_source_target_entity_uniq` constraint rejected those writes. The tests now separately verify PostgreSQL constraint rejection and JsonDB corrupt-state cardinality denial; no constraint is dropped and the runtime denial assertion remains. Final targeted reruns and complete qualification follow against frozen source. Failed log: `/tmp/integral-access-raw-facts-pg.log`.

Affected invariants: I-ROLE-01 retains strongest complete roles and direct/inherited precedence; I-ROLE-02 retains inherited capping; I-ACCESS-01 requires actual unambiguous authority facts; I-CRUD-01 keeps authority reading in services; I-GRAPH-01/02 retain the existing rooted graph and identity Object contracts. No Core/App or typed-edge boundary is weakened. Strict canonical resolver cutover, raw structural/inventory completeness, migration and shared fence adoption are still open.

The corrected final selections passed 151 JSON cases in 59.12 seconds and 92 PostgreSQL cases in 51.89 seconds. The existing query budgets passed unchanged. PostgreSQL rejects duplicate membership/collaboration insertion through its existing unique edge index; JsonDB tests retain explicit denial of an already corrupt duplicate set. Primary identity/profile, member/grant/flag, canonical scope and independent auth-worker regression cases all passed. Full `make verify` and supplementary lanes are running against frozen runtime/test source before commit.


The first complete gate passed guards/hooks, pinned format/lint/types, reproducible wheel/import, no-env smoke/xdist and 1,583 frontend cases, but failed the full backend gate on a WhatsApp fixture. That fixture used an uncataloged fake login ID and a global `User.get` mock, which concealed the missing principal mapping used by notification persistence. It now uses the rooted actual test principal, a graph-ID channel target and primary persisted preference readback without that mock. No product principal check was weakened. The first full PostgreSQL supplementary selection failed three genuine entry-command paths (populated Asset Register upgrade and two connector command/rollback contracts): their raw PostgresTransaction lacks `find_one`, exposed by fresh principal lookup.

The canonical entry scope now opens/joins the shared serialized transaction seam and keeps fresh reads on its held connection. New PostgreSQL proofs cover concurrent primary principal reads, rollback of a mutation and rollback of the outer transaction after a caught nested entry failure. Existing raw external transactions cannot silently escape to the pool. Profile and membership uniqueness reads use two candidates; the combined direct-grant read uses three because a valid set has at most one ownership and one collaborator. Returned row identity/discriminators are validated before granting. Failed logs: `/tmp/integral-access-raw-facts-verify.log` and `/tmp/integral-access-raw-facts-supplementary.log`; no full pass is claimed for this source until the repaired gate reruns.

The repaired targeted reruns passed 129 JSON cases with two PostgreSQL-only skips in 64.14 seconds and 100 PostgreSQL cases in 94.86 seconds. They retain unchanged query budgets and cover the prior three PostgreSQL command failures, the corrected rooted WhatsApp fixture and the new shared-entry rollback proofs. Logs: `/tmp/integral-access-raw-facts-repair-json.log` and `/tmp/integral-access-raw-facts-repair-pg.log`. Full `make verify` and supplementary gates are now rerunning against frozen runtime/test source; their completion remains required before committing this slice.

The repaired supplementary gates finished with exit 0: 9 Core-only cases, 52 JSON extension-contract cases and 232 PostgreSQL spike/contract cases passed. The JSON extension selection skipped 232 PostgreSQL-only cases; those were exercised in the separate store lane, which has one explicit restore-rehearsal network skip. Log: `/tmp/integral-access-raw-facts-supplementary-repaired.log`. The restored-backup/current-authorization checkpoint is not qualified by this skip. The full gate has passed its frontend suite (1,583 cases / 279 files) and remains in its backend suite.

The repaired complete `make verify` gate finished with exit 0 against frozen runtime/test source. Staged guards/hooks, pinned formatting/lint/types, reproducible wheel/import, no-env smoke/xdist, all 1,583 frontend cases in 279 files and the complete backend suite passed. Working-tree wheel SHA-256: `1e160fb1b99634af433695cee8d780f1e108aebfdced14caaa554ee7a0b70c76`. Full log: `/tmp/integral-access-raw-facts-verify-repaired.log`. The supplementary run above also passed. M2 raw-authority-facts/entry-transaction slice is qualified for commit/push on the designated branch. AC-04/06/12 and all broader owner/governance, service-actor, session, mutation/release and migration/restore requirements retain their open gates. These local proofs do not establish GitHub CI, browser, provider, release or deployment readiness.

### M2/M3 canonical containment and protected governance integration — unqualified

The qualified raw-fact/entry-transaction slice was committed and pushed as
`3f0122de` on the same branch. The current integration introduces the normative
[access-control contract](../../../product/ACCESS_CONTROL_CONTRACT.md), separate
workspace-owner capabilities, and primary raw containment for generic resource
roles. Protected governance does not manufacture a resource `OWNS` or
`COLLABORATES_ON` edge or alter ordinary inherited roles. Ordinary administrators
retain their direct-grant ceiling. Unknown human-default actions are denied
before generic scope delegation; declared App action enforcement remains open.

The first raw-containment selection passed 86 JSON cases. The first integration
selection failed collection because a new test imported a nonexistent snapshot
helper; it was corrected to exercise the existing `sharing.list_access` service.
The next integration selection passed 70 and failed 38 cases in 78.98 seconds.
A real PostgreSQL selection passed 88 and failed two cases in 70.70 seconds.
All four new owner-governance scenarios passed on both stores. Two failures were
adversarial setup assumptions that the new always-live resolver would write a
process TTL grant; the repaired tests explicitly plant a stale legacy grant and
retain their post-revocation denial assertions. Remaining integration failures
included unrooted normal fixtures and actual query-budget regressions (28/59/
154/21/122 round trips against 22/32/55/15/45 ceilings). The ceilings remain
unchanged. Root proofs now batch their bounded raw reads without caching an
allow across invocations; repaired qualification is in progress.

Positive sharing and staff-inventory fixtures now use production catalog
helpers. Explicit missing-catalog/corrupt-state tests remain denial cases.
Workspace-owner governance of a rooted ownerless App is tested separately from
its ordinary role, paired with denial of an uncatalogued App. Scalar legacy
inventory discovery cannot authorize the latter. Full Entry page/count/query
parity, distributed effect/release fencing, ownership/membership races,
migration, browser/MCP and all use-case closure gates remain open. This working
package has not passed the full gate and must not be committed yet.

The repaired positive-fixture run resolved the invitation failures and reduced
query overruns to three. The bounded raw ancestor/grant batch selection retained
all 24 point/batch containment requirements; its complete targeted qualification
is still pending. Successive unchanged-budget measurements were feed/Tracks/
multi-Track 50/89/56, then 46/85/52. The next run contained a helper-argument
mismatch introduced during optimization; it was repaired, with four of five
budget checks then passing (feed still 39). A temporary read-only call-stack
profiler identified repeated direct-grant traversal, duplicate workspace-role
reads and automatic scope-preference writes. The profiler stored callable names
and counts only and was removed after the measured run.

The next selection passed **all five unchanged query-budget checks**, with one
remaining legacy positive expectation for an uncatalogued contained Track. That
case now pairs denial before cataloging with inclusion after canonical catalog
repair. Raw direct-grant discovery uses bounded keyset pages; malformed role
facts cannot disappear through edge hydration. Automatic scope defaults are
recomputed without writing a profile. Explicit preference choices patch only
the two scope fields, preserving concurrent OTP/credential state; their writes
still do not establish authority. New tests inject a concurrent profile-slot
update and check both protected owner identity aliases after disablement.
Expanded JSON and PostgreSQL qualification against this frozen source is in
progress. No full-suite pass or closure is claimed for this package.

The expanded repaired selection completed with exit 0: **195 JSON cases and
106 PostgreSQL cases passed**. These include the unchanged query budgets,
point/batch raw containment pairs, both linked owner identity forms after
primary disablement, the injected concurrent profile-slot update, canonical
catalog repair readback and current sharing/scope/access compatibility. A
read-only lint check initially omitted the repository configuration and emitted
baseline style noise; the configured check found one actual simplification
finding. Its equivalent short-circuit expression was corrected. Configured lint
is now clean for all touched runtime files. The next broader existing permission,
visibility, comment, cache, ownership, departure and invitation selection is
running against frozen source. Full `make verify` and supplementary qualification
remain prerequisites for committing/pushing this package.

The broader compatibility attempt exposed 45 failures before reaching an older
process-cache test that hung: its fake `SlowUser` did not implement the current
raw inventory seams, so the task failed before setting the event the test
awaited. After confirming that exact failure, the owned pytest process was
interrupted (exit 2); this is not a completed suite. Its failed log is retained
at `/tmp/integral-access-governance-compatibility-10.log`.

Normal resource-role tests now receive an explicit rooted scenario with an
independent workspace owner, catalogued resources and member actors. The
resource steward is deliberately distinct from the protected workspace owner,
so ordinary exclusion/delegation assertions cannot pass because of governance.
Visibility/comment fixtures now use canonical catalogs. The hanging fake-cache
case is replaced by a real rooted inventory read paused at raw discovery, with
a planted stale aggregate and bounded waits; invalidation must leave no reusable
allow. Corrupt containment tests remain independently constructed. The repaired
broader selection is running with source frozen. Full-suite qualification
remains open, and this package is still uncommitted.

The repaired broader attempt completed: **104 existing/owner golden cases
passed**. The remaining reaction failure revealed that `reaction.create` had
never been declared in `PolicyAction`, despite its existing commenter-tier
policy test. It is now an exact built-in gate action; unknown business actions
remain denied. The reaction handler delegates to that gate, including protected
owner governance, rather than using an ordinary-role-only check. A new golden
case posts a reaction to excluded private content through the real handler as a
linked workspace owner with no ordinary Entry role. Existing viewer denial and
self-removal-after-demotion coverage still passes. The two resource-invitation
revoke fixtures now use a real rooted personal workspace and catalogued Track.
A complete configured runtime lint scan and full local gate follow. No complete
finding/use-case closure or commit qualification is claimed from focused tests.

The first full gate passed all 16 staged substrate guards and pre-commit YAML/
JSON/whitespace/black/isort/pinned-flake8 checks, then failed mypy's `no-redef`
check for the chunk-aggregation `result` variable in the batch locator. It exited
2 before artifact or suites; no complete gate pass is claimed. The aggregation
variable is now separately named and typed, preserving its behavior. The full
gate is rerunning; failed log:
`/tmp/integral-access-canonical-governance-verify-1.log`.


The second full attempt passed the staged guards, pinned pre-commit checks,
mypy, frontend types and reproducible wheel/import verification (wheel SHA-256
`0dbee041b5fac7fb336ad8d386e9c26f8633be30456d26f2f89ac1abfce10ca7`),
then failed **39 no-env smoke cases** under xdist. Full backend and frontend
suites were not reached. The supplementary attempt passed **9 Core-only cases**
and failed **2 extension-contract cases**; the final PostgreSQL selections did
not run. Both jobs ended with exit 2. Retained logs:
`/tmp/integral-access-canonical-governance-verify-2.log` and
`/tmp/integral-access-canonical-governance-supplementary-1.log`.

The remaining normal role/moderation/attachment/public-materialization fixtures
now use production workspace/resource catalogs. App fixtures use the canonical
`private` default instead of the invalid App `inherit` literal; malformed-state
tests remain unchanged. The stale-install inventory test explicitly plants a
legacy empty aggregate instead of assuming current authorization writes a TTL
allow.

The warranty-schedule failure exposed a behavior regression: the new active-only
canonical execution gate also removed retained-data and recovery access to
paused Apps. Exact internal lifecycle sets now separate active execution,
active/paused ordinary data access, and stewardship recovery for known retained
installation states. Uninstalled Apps remain denied. Workspace governance does
not manufacture an ordinary role; direct config authority remains non-inherited.
Paired lifecycle point/batch, inventory, data, direct steward and owner recovery
cases, plus an actual HTTP resume enqueue, are in focused qualification. Resume
admission is not a completed transition or authorization fence proof. These
repairs remain uncommitted pending the complete gate.


Focused repair 13 ended with two failures: staff inventory discovery still
pruned paused Apps before canonical admission, and the new HTTP enqueue test
imported WorkItem from the wrong module. Discovery now leaves lifecycle
admission to the raw final filter, and the test reads the actual durable
`app.agentive.work_models.WorkItem`. Existing extension installation and warranty
pause/resume cases passed in that run. Repair 14 named a nonexistent performance
test file and exited 4 without running tests; the command was corrected to
`test_perf_query_budget.py`. Repair 15 is running against frozen source.


Repair 15 passed **88 cases**, including all unchanged query-budget ceilings,
and failed only the HTTP resume scenario: its graph stub lacked the active
application definition required for durable lifecycle work. The test now uses
production App creation, then explicitly changes resource stewardship so the
workspace owner still has no ordinary App role. The active-definition gate is
preserved. The actual HTTP enqueue is being rerun before broader qualification.


The repaired HTTP resume case passed (1 case, 5.11 seconds). Full attempt 3
passed the staged/pinned checks, mypy, frontend types, reproducible wheel/import
(SHA-256 `7505243f56b7ec55e11ec0b07828b55e2a3a1ef5898188afa3d78a1ce051d358`)
and the no-env smoke lane. Frontend/full backend remain in progress.

Consumer review identified another boundary to qualify before committing this
package: callable skill discovery uses the data-access inventory, which now
includes protected owner governance with no ordinary App role. The execution
and declared-query paths already check ordinary App role, but resident overlay
and App-scoped workspace-authored skill discovery need the same distinction.
Source remains frozen for the current full run. A separate active/ordinary-role
function audience and paired govern/read, explicit delegation, revoke and pause
cases are planned after it finishes; no function-security closure is claimed.


Full attempt 3 completed with exit 2: the no-env smoke lane and **1,583 frontend
cases passed**, but the full backend suite failed **52 cases**. Source is now
unfrozen. Most failures are older normal fixtures without canonical catalogs;
connector-bound type discovery has the same fixture problem. The filing privacy
case also uses a workspace owner for an ordinary audience assertion, which must
be distinguished under the new protected governance contract. The full failed
log is retained; no complete backend qualification is claimed.

Function-audience refinement is in focused qualification. Callable and overlay
App audiences now require a current ordinary App role plus active canonical
containment. Protected generic governance is suppressed when the server-created
policy-module scope denotes an App operation/declaration. The memo identity
includes that distinction so a cached protected read cannot become a function
allow. Paired ordinary commenter/generic write, explicit editor delegation,
revocation, private App cached read, active/paused callable and workspace-authored
overlay scenarios are included. This does not complete declared action grants,
exact effect permits or release fencing; those planned gates remain open.


Function-audience attempt 17 passed **55 cases** and failed four setup cases.
The new workspace-authored skill used a disk-style hyphenated key instead of the
runtime workspace snake_case key. Three existing profile positives depended on
an external package/library cache for their skill body; they now persist an
explicit fixture body through the real registration service. These repairs do
not weaken the function audience check. The governance evaluation mode also
reflects an inherited ordinary-only context in its memo identity.

Full-failure fixture repairs use production catalogs without monkeypatching
framework APIs. Connector binding catalogs its Track after assigning the real
personal workspace. Public share, relations, transform, precompute, watcher and
view fixtures now have canonical workspace/App/Track ancestry. Sharing-overview
resources use the actual source owner's rooted personal workspace; its foreign
resource tests retain distinct source owners. The attachment scope test now
uses an actually accessible foreign workspace, proving scope filtering rather
than denial of a fake parent. Filing uses an independent organization owner and
explicitly delegated creation rights so ordinary resource privacy remains a
meaningful assertion. The broader attempt 18 includes every full-failure file,
function/overlay/policy cases and unchanged query budgets, with source frozen
and a JUnit receipt. Full and PostgreSQL qualification remain prerequisites.

Attempt 18 completed with **211 passed, 1 failed and 1 skipped** (101.10
seconds). The remaining assignee-notification fixture created Entries with only
scalar track IDs. It now wires actual Track containment, entry-type and
OperationalModel type relationships. The targeted watcher rerun passed all
three cases; canonical admission remains enforced. The complete gate is being
repeated on the staged package before any milestone commit.

Full attempt 4 stopped after the pinned Black hook formatted the anchor fixture;
that formatting was staged. Full attempt 5 passed all guards and pinned checks,
types, **795 smoke cases (7 skipped)**, **1,583 frontend cases**, and **5,122
backend cases (297 skipped)**. Its reproducible wheel/import SHA-256 is
`bb2f078939d7401573dc8b8a4993485416696b7d3e5094cc45b30478b5bc8c05`.
Supplement 5 passed 9 Core-only cases, 52 extension-contract cases and 165
focused PostgreSQL cases, then failed two PostgreSQL contract cases. The restore
rehearsal remains skipped because the dump process cannot reach the host's
published port; it is not restore qualification.

The cross-process regression assumed that public authorization populates TTL
allow caches; it now explicitly plants a legacy allow in the second process and
retains the committed revocation/lock assertions. The transactional count test
now uses canonical catalogs and an independent workspace owner, preserving its
ordinary direct-grant/exclusion assertion. Source review also found the actual
declared-query origin `app_query`; it now joins operation/declaration origins in
ordinary function authority evaluation. Repair 20 qualifies these changes on
PostgreSQL before another complete gate. Attempt 5 proves the prior source,
not these subsequent repairs; the package is still uncommitted.

Repair 20 passed all **29 PostgreSQL cases**, including the real second-process
revocation and transactional count assertions. The staged refined package is
entering complete gate attempt 6. Source will stay frozen through all final
local and supplementary qualification lanes.

Attempt 6 passed the complete local gate on the refined source: **796 smoke
cases (7 skipped)**, **1,583 frontend cases**, and **5,123 backend cases (297
skipped)**, with guards, pinned checks, types and reproducible wheel/import
SHA-256 `17304562803d83c6ce018af1a37d64e7d8903c3b52c425b269faae5ed5310a90`.
Its Core-only lane also passed; extension and PostgreSQL lanes are still
running. No milestone commit/push has occurred for this package.

The next projection repair includes U24 minimal parent navigation: current
Entry enrichment exports full Track/App objects without a caller audience
parameter. Direct Entry discovery, canonical audience filtering and exact
totals must agree across point/list/feed/page/count/stream/query. Function
admission and callable/overlay audiences do not establish nested handler effect
authority: exact permits, target scope and release fences remain open. Complete
owner recovery inventory/metadata and sharing across retained installation
states also require actual API/browser qualification. These are recorded
remaining requirements, not claims of closure from the current point tests.

Final attempt 6 completed successfully across every scheduled lane: the full
local gate, **9 Core-only**, **52 extension-contract**, **166 focused PostgreSQL**
and **232 PostgreSQL spike/contract cases**. The known backup transport failure
still skips the restore rehearsal; it is explicitly outside this milestone's
qualification and remains a required W7 repair. GitHub CI, browser/MCP,
migration and complete effect/release-fence evidence are not established by
these local results. The source package is now qualified for its partial
canonical-governance milestone commit and push on the designated branch.

That qualified package was committed and pushed as
`6c48cb6021020b4ab26ffc3a3851fff45c29afc2` on `codex/access-control-review`.
Remote/local equality and a clean worktree were verified before the next slice.

Entry projection baseline 1 introduced paired point/projection tests across
list, keyset page, workspace page, count, offset page and stream, both human ID
forms where relevant, plus an actual HTTP parent projection. Against the pushed
source it reproduced **44 failures and 11 passes** (57.31 seconds). The tests
cover inherited exclusions, protected owner data access, missing/foreign extra
containment, direct Entry grants without parent access, malformed raw grants
and injected candidate-storage outages. These are baseline failures awaiting
runtime repair; no new closure or qualified source is claimed from them.

### Entry projections: bounded shared admission (working; unqualified)

The 55-case baseline against pushed `6c48cb60` reproduced 44 failures. The
shared reader now admits raw Entry windows before exposing list/page/count/
stream results, retains direct Entry discovery without parent read authority,
refills rejected windows and propagates source failure as unavailable. Parent
exports expose navigation fields only. Navigation payloads remain private
in-memory response metadata and never establish an authorization decision.

Focused JSON 3 passed **97** cases, including all five unchanged query budgets.
Extended JSON 5 passed **141** cases. A deterministic compound-sort baseline 7
then reproduced omitted authorized rows; regression run 8 passed both sort
directions, null tails, multi-window refill, between-request revocation and
private-payload nonpersistence. These are source tests, not browser, wheel,
MCP, durable migration or release-fence proof. Full local/PostgreSQL qualification
remains required; no finding or use-case closure status changed.

### Entry projection refinements before commit

Final run 11 passed the full local gate (855 smoke/7 skips; 1,583 frontend;
5,182 backend/297 skips), Core-only and extension contracts. Its focused
PostgreSQL lane then reproduced two explicit-null cursor omissions; this
snapshot was not committed. PostgreSQL plain-null equality and explicit JSON
null differ in jvspatial 0.1.1; cursor predicates now use the adapter-shared
exists-false semantics. Native date cursors cover both explicit-null and missing
tails in either direction.

A separate regression reproduced saving a selected Entry retaining its temporary
parent projection in the framework's object cache. Entry persistence clears this
response-only payload; a warm-cache/canonical-workspace-move regression confirms
that a subsequent selection contains only the new parents. This payload never
establishes authority.

Eight PostgreSQL numeric-sort failures were reproduced. Custom/label ordering now
uses a portable comparator over admitted raw windows and a heap sized by offset,
page and one lookahead. ID/date ordering retains the storage keyset path. The
portable path scans candidates to determine the actual top page; typed indexed
ordering and whole-query work/byte budgets remain qualification work. The memory
regression observes eight retained records for offset four/page three over 25
candidates, including exclusions and a direct grant that defeats an exclusion.

Ordinary-mode App/Track inventories no longer consume the protected-owner fast
path. Tests retain the published active/paused data gate; installing,
awaiting-settings and failed states stay in recovery controls rather than
ordinary data projections. Recovery metadata/inventories/UI remain open.

Extended run 20 passed **127 cases on each adapter**, including the five unchanged
query budgets. Full final qualification is still required. Finding/U-case closure
statuses remain unchanged; this is not response-fence, complete related-resource,
migration, restore, browser, MCP or release qualification.

### Entry final-21 PostgreSQL contract correction

The final-21 snapshot passed all 16 guards, pinned formatting/lint/types, reproducible wheel qualification (`9348064eeb2f02d9023b93c2f6565fe2085ab9967d03a4bc53710ebdf697d052`), 899 smoke tests (7 skips), 1583 frontend tests, 5226 backend tests (297 skips), Core-only, extension, and 229 focused PostgreSQL tests. The broader PostgreSQL lane failed the existing transactional permission-fanout test: it expected an inherited Entry exclusion to hide a record from the canonical workspace owner. That expectation contradicted I-GOVERN-01. The test now checks protected stewardship (all five canonical records) and ordinary App authority (four records, with the excluded record absent), outside and inside the actual PostgreSQL transaction. Runtime source was not changed for this correction.

Running that test alone also exposed its implicit dependency on another test having created the Integral graph shell in the raw PostgreSQL store. The test now provisions the rooted shell explicitly. The standalone strengthened test passes (`/tmp/integral-access-entry-projection-transaction-23.xml`); the prior unrooted standalone attempt is retained as fixture evidence (`...-transaction-22.log`). Final qualification must be rerun before commit. The restore drill remains skipped because the Docker backup helper cannot reach the host-only owned database mapping; restore/current-authorization recovery remains open.

### Independent next-milestone baselines during frozen Entry qualification

Read-only probes outside the repository test tree, using the repository pytest configuration and graph fixtures, reproduced two open boundaries without changing the frozen runtime/test source:

- `/tmp/integral-access-function-audience-baseline-2.xml`: 10 assertion failures, no setup errors. An explicitly admitted App viewer who is also the workspace owner regains stewardship mode inside the actual operation/query handler. Excluded Entry point reads, nested policy evaluation and current list selection disclose the record. Direct Entry grants remain readable but execution mode is still incorrect. Exception/cancellation/concurrent-Core cases and a declared-template adapter fixture also reproduce the missing invocation-wide restriction. Template adapter replacement is explicit; this is not complete real template/transport qualification. The first external probe omitted the pytest config and had fixture setup errors; it is not security evidence.
- `/tmp/integral-access-policy-scope-baseline-3.xml`: positive actual PolicyModule authorization under canonical workspace B succeeds, then the same App/grant is incorrectly allowed under immutable execution scope A. The scope mismatch survives an inherited request memo. This remains a separate module target/parent binding requirement; a header-only check does not close it.

These are next-slice baselines, not new passing acceptance, and do not change any finding/use-case closure status. Dispatcher/module sources were unchanged from `6c48cb60`; selection used the pending Entry snapshot.

### Qualified Entry projection milestone — final-24

The final-24 coordinator completed all five lanes successfully with frozen runtime and test diffs. All 16 guards, pinned formatting/lint/type checks and reproducible wheel/import checks pass. The wheel SHA-256 is `9348064eeb2f02d9023b93c2f6565fe2085ab9967d03a4bc53710ebdf697d052`. Final automated receipts: 899 smoke tests (7 skips), 1583 frontend tests across 279 files, 5226 full backend tests (297 skips), Core-only, extension-contract, 229 focused PostgreSQL tests, and the broader PostgreSQL spike/contract lane. The restore rehearsal is still explicitly skipped and does not qualify current-authorization restore. `/tmp/integral-access-entry-projection-final-24.status.json` records terminal exit zero for every lane; adjacent XML/log receipts retain the named scenarios and exact test counts.

Frozen patch SHA-256: runtime `ed6c6378a21ca2e27028ef336860a03aeb600e623133e3743d8308e591e79864`; tests `24e2dc5f255d4a8969ed84bdea446bb6236453e92bedb58d2af11aa62d2e7fbf`. Runtime/tests were compared again after terminal qualification and before committing. This milestone establishes current canonical Entry selection, admitted counts/refill/cursors, portable numeric/compound ordering, and narrow parent navigation. It is partial remediation, not full closure of a finding or U-case: whole-release fences, saved View authority, related-data disclosure, App function permits/effects, budgets, migration/restore and independent browser acceptance remain open. No GitHub CI, deployment or release is inferred from the local branch gates.

### Pushed Entry milestone and App invocation working slice

Entry milestone `c22c1fb7e56a81cd2d41dff0a49c3d830e64d7ad` was committed with all hooks passing and pushed to the sole `codex/access-control-review` branch. Local/remote revision equality and a clean tree were verified before starting the next slice. Final-24 PostgreSQL receipts cover 229 focused tests and 232 spike/contract tests, with the restore rehearsal explicitly skipped.

The working App invocation slice now binds ordinary authority around the entire operation/query dispatcher call. Its ContextVar guard covers admission, actual handlers and registered tools, nested policy decisions/projections, result construction, receipt replay and awaited effects; cleanup preserves exception/cancellation and concurrent Core stewardship semantics. Focused-4 passes 80 tests on JSON and 80 on PostgreSQL; focused-5 expands registered-tool paths and passes 88 on each adapter. The test sources include the 18 new direct/tool/template, positive-direct-grant, error and cancellation/concurrency cases. The template adapter is explicitly replaced by a fixture, while its underlying current authority/projection checks are real.

This is unqualified working runtime until the exact full gates pass. It does not implement exact action permits, declarative group/condition bindings, durable dependency epochs/release fences, retained-context leases or canonical PolicyModule target/workspace binding. The independent scope baseline remains failing/open; no finding or U-case closure is claimed.

### Qualified App invocation milestone — final-6

The full final-6 coordinator completed every lane with exit zero. All 16 guards, pinned formatting/lint/types, reproducible wheel/import, CI-faithful smoke, frontend, full backend, Core-only, extension and both PostgreSQL lanes pass. Wheel SHA-256: `eed10f95c4a4a94179c998b0a01b99f70d208aa4c880a4ade31ffaadc540b1c3`. Automated receipts cover 917 smoke tests (7 skips), 1583 frontend tests / 279 files, 5244 backend tests (297 skips), the combined focused PostgreSQL set, and 232 broader PostgreSQL spike/contract tests (one explicit restore skip). Terminal receipt: `/tmp/integral-access-function-audience-final-6.status.json`; adjacent XML/logs retain exact case counts.

Runtime/test patch SHA-256 values were checked after terminal qualification: `085468f08d29d82432324e0315347e8221479e443c49637e92fb963e9362b0ce` and `9094158e9466b9a3eef698222c9eed1f3bea8dfcb6037b9dcfdf33f6cfcdd40d`. This qualifies the complete awaited invocation's ordinary-authority mode and its cleanup/isolation; it does not qualify exact per-function grants, retained contexts, definition/dependency epochs or the ASGI/stream release point. All finding/U-case closure statuses remain unchanged. Restore/current-authority recovery and GitHub CI/deployment remain unverified.

Combined focused PostgreSQL: 298 passed, 0 skipped.


### Pushed App invocation milestone and canonical policy scope working slice

App invocation milestone `b50dbf3cdefc3ac7a169fd16b334b12bf2f4efd4` was committed with all hooks passing and pushed to `codex/access-control-review`. Local/remote revision equality and a clean tree were verified before this slice.

The PolicyModule working repair binds App/Track/Entry point targets to their current canonical workspace and validates the claimed structural parent before legacy policy evaluation. Empty-ID creation binds an existing permitted parent; it cannot invent a target location. Management lifecycle states establish location only: the existing action-specific data, execution and recovery gates retain their separate meanings. Invalid, missing or foreign location denies; unavailable primary facts propagate a typed authorization-unavailable error. Other resource kinds still need dedicated ownership adapters and are not declared solved by this helper.

Every module evaluation now isolates its permission cache, including inside a retained request context. Actual-store tests pair legitimate cross-workspace direct shares with foreign-scope denial, both principal aliases, current grant revocation, disabled linked identities, same-workspace wrong parents, Entry creation, removed named containment and primary-source outages. This does not supply atomic effect/release fencing or complete definition/dependency epochs.

Focused-1 reached all new scope/current-authority assertions without failure. Its six lifecycle cases failed setup because the fixture used an undefined field (`install_state`); the fixture now uses the actual `lifecycle_state`. No lifecycle security pass or failure is inferred from that erroneous setup. Focused rerun and exact complete gates remain required before commit. All findings/use-case closure statuses remain unchanged.

Focused-2 completed with exit zero: **154 tests passed on each adapter**, with no skips. Logs/XML are under `/tmp/integral-access-policy-scope-focused-2-*`. The complete gate now follows against staged, frozen runtime/test source. This slice preserves the Authority and access invariants, I-GOVERN-01 and existing graph/lifecycle semantics; no persisted schema or structural writer changes are introduced.

The first complete-gate attempt (`/tmp/integral-access-policy-scope-final-1.status.json`) stopped at the module-boundary guard before tests. The adapter had acquired an unapproved direct service import. The repair places scoped evaluation behind the already-approved `policy_engine` seam; the module guard and public immutable scope forwarding contract remain intact. No full-gate pass is claimed for this attempt. Frozen source is superseded, with focused/full qualification required again.

Focused-3 qualifies the repaired adapter/service boundary: 154 passed on JSON and 154 passed on PostgreSQL, with no skips. Full-final-2 follows; no broad closure is inferred.

During frozen full-final-2 qualification, an isolated external JSON-store probe established the next related-resource baseline: `/tmp/integral-access-related-view-baseline.xml` has one assertion failure and zero setup errors. A caller with guest membership and a direct Track viewer grant can read a canonical View in workspace B, then the same actual PolicyModule call wrongly allows it under workspace A execution scope. The probe source is outside the repository runtime/test trees; those remain frozen. This is unsafe-behavior evidence for the already-open related-resource ownership requirement, not current-slice acceptance or PostgreSQL/browser proof.


### Qualified canonical policy scope milestone — final-2

The final-2 coordinator completed all five lanes with exit zero. All 16 guards, commit-hook checks, pinned format/lint/types, reproducible wheel/import, CI-faithful smoke, frontend, full backend, Core-only, extension, and both PostgreSQL lanes pass. Wheel SHA-256: `5681b23c6bcee9f5a93d63e61e6750aeeb9a965011cd906b52ed28e4dd39a9b8`. Automated receipts cover 946 smoke tests (7 skips), 1583 frontend tests / 279 files, 5273 backend tests (297 skips), 327 focused PostgreSQL tests (zero skips), and 232 broader PostgreSQL spike/contract tests with one explicit restore skip. Terminal receipt: `/tmp/integral-access-policy-scope-final-2.status.json`.

Frozen runtime/test patch SHA-256 values were rechecked after terminal qualification: `4b985307760b26762a9350f669845834e41458f165664690f7e606f0cf1a279d` and `3baf3a8c42268e30cc9b59d09acab50ee6d1ddb44444394a9f780ffa79804d18`. Twenty-nine new actual-store cases qualify canonical App/Track/Entry workspace/parent binding and retained-context current admission. The isolated View baseline remains failing/open; dedicated related-resource ownership, exact action permits, definition/dependency epochs, atomic effect/release fences, migration/restore and browser acceptance remain required. Finding/U-case closure statuses remain unchanged. These local and owned-store results are not GitHub CI, deployment or release evidence.


### Pushed policy-scope milestone and saved-View working slice

Policy-scope milestone `996d68456809efb8102c8be4124533b890573972` was committed with hooks passing and pushed to the sole branch. Local/remote revisions and clean tree were verified before this slice.

Saved Views now have a typed `Track —HasView→ View` canonical scope, distinct from shared operational-model catalog membership. A raw Walker checks the complete incoming scope/catalog sets, Track/model/registry caches, named model attachment and rooted App/Workspace location before any inherited permission or payload hydration. Template/model-only Views do not become saved Track Views. Canonical View location extends the immutable policy scope binding; current View reads/configuration retain existing action-specific lifecycle and role semantics. Source failures propagate unavailable. Live policy boundaries bypass request decision reuse.

Track-bound producers adopt a common creation service. On supported stores, creation and scope/catalog attachment join one graph transaction; nontransactional adapters retain cleanup without an atomicity claim. Private model/registry hydration avoids warmed objects escaping a held graph unit. Saved-View point/list/default/reconciliation/copy/filing/staging/verification consumers use canonical raw scope rather than a scalar Track cache or a CP-wide permission OR. Model-only authoring removal validates membership in the explicitly authorized model; canonical model authority remains separately open.

The migration defaults to metadata-only dry run and keyset-paged inspection. It infers only a unique existing catalog/model/Track graph path, quarantines ambiguous shared-model/cache/topology cases, and withholds unanchored Views. Apply requires graph transactions, rechecks the plan under held workspace/View locks, and rolls back scope/revision on failure. Ambiguous owner repair and its UI remain open; this script is not whole-platform migration/restore qualification.

Targeted failures remain preserved: focused-1/2 found positive fixtures lacking the new scope; focused-3 contained a syntax error from import placement and root-incomplete structural fixtures; focused-4 found the split fixture's noncanonical mocked registry. Those fixtures now represent actual rooted saved Views, retaining independent unanchored/quarantine tests. Formatting and syntax are checked before focused-5. No full qualification or finding/U-case closure is claimed yet.

Focused-5 completed both adapters: 124 JSON passes with six adapter-specific skips; 130 PostgreSQL passes with no skips. Extended-6 stopped on the staging routing unit, whose mock targeted the retired raw View.get seam. The canonical seam now drives that unit. Inspection also exposed unresolved View IDs skipping the required scope check; the repair returns scope_violation before dispatch and adds actual-store positive/foreign/missing/corrupt/omitted target coverage. Extended-7 is running against frozen source; no full pass is claimed. The saved-View operator runbook documents unique-path apply, per-item transactions, ambiguous owner review, and separate restore/model-authority limitations.

Extended-7 failed because the missing-scope fixture passed Nodes rather than IDs to the edge lookup and therefore removed no edge. The fixture now asserts a single existing edge and deletes it by ID. Extended-8 did not collect tests because resolving the venv symlink selected the base interpreter without cryptography; the lexical venv path is preserved in extended-9. Extended-9 passed both adapters: {"json": {"total": 241, "skipped": 6, "failed": 0, "errors": 0, "passed": 235}, "postgres": {"total": 241, "skipped": 0, "failed": 0, "errors": 0, "passed": 241}}. The full five-lane final-1 gate follows against the staged, frozen source.

Full-final-1 stopped at mypy before the suites: migrate_view_scopes returned an untyped heterogeneous dictionary, producing six annotation/union errors. All 16 guards and preceding hooks passed. Added ViewScopeMigrationReport with typed mode/items/counts without changing migration behavior. The failed receipt is retained; the affected migration tests and exact full gate must qualify the replacement source.

The report typing correction passed its affected selection: three JSON cases (three PostgreSQL-only skips), and all six PostgreSQL cases. Full-final-2 follows.

Full-final-2 passed mypy but stopped at the configured pre-commit flake8 docstring plugin: get_scoped_view and text_cache lacked public-function docstrings. Added those docstrings without behavior changes. Full-final-3 requalifies the replacement source; no suite pass is inferred from the stopped attempt.

Full-final-3 stopped at the flake8 C408 rule for the new authoring fields dictionary. Replaced dict(...) with the equivalent literal; mypy/all guards passed on that stopped attempt. Full-final-4 follows. During frozen qualification an external probe reproduced the separate related-model scope gap: /tmp/integral-access-related-model-baseline-1.xml has one assertion failure and no setup errors. A legitimate Track viewer may read its actual attached OperationalModel in B under immutable execution scope A. This is existing open related-resource binding work, not a saved-View acceptance failure. The probe used repository configuration and graph fixtures, outside the runtime/test trees.

Full-final-4 was stopped intentionally after the final consumer audit found remaining scalar profile deletion, cached post-sync update receipt and scalar definition verification. The owned test/make subtree was terminated before source changes; the coordinator reports terminal -15. Prior guards/types/frontend checks and wheel/import passed, but no full suite result is claimed. The revised consumers use canonical raw point/list reads; five actual-store paired cases cover profile deletion, post-sync receipt and materialization evidence. Extended-10 passed {"json": {"total": 261, "skipped": 6, "passed": 255}, "postgres": {"total": 261, "skipped": 0, "passed": 261}}. A further direct profile-default maintenance loop was CP-wide; it now scopes to the Track and has an unmanaged-sibling regression under a direct Track admin. Extended-11 follows. Broader model reset/deletion/manifest ownership and legacy configuration migration consumers remain open; named View scope does not close those model lifecycle contracts.

Extended-11 passed both adapters: {"json": {"passed": 256, "skipped": 6, "total": 262}, "postgres": {"passed": 262, "skipped": 0, "total": 262}}. The configured preflight found an obsolete registry import before initial test launch; removed it, then configured app lint and all focused cases passed. Full-final-5 qualifies this replacement snapshot.

Full-final-5 stopped in CI-faithful smoke: {"total": 989, "skipped": 10, "failures": 1, "errors": 0, "passed": 978}. The sole failure was an older name-fold materialization fixture that created its Workspace/Track without catalogs; canonical View post-wire validation correctly refused that unrooted path. The fixture now catalogs both before model materialization. Runtime is unchanged. A broader JSON backend run checks affected setup paths before full-final-6.

Broader JSON backend-1 finished with {"total": 5606, "skipped": 300, "failures": 27, "errors": 0, "passed": 5279}. The 27 failures are in six modules with uncataloged Workspace/App scaffolds, placeholder Workspace IDs, or an unscoped Track. Fixture helpers now catalog actual Workspaces/Apps/Tracks and wire Workspace→App containment; the edge test also checks named HasView scope. Structural/mock-policy tests retain that evidence category and do not become principal/HTTP security proofs. Affected fixtures are being exercised on JSON and PostgreSQL before full-final-6; the failed broad receipt is preserved.

Fixture selection 1 left two alternate anchor setups outside the common builder. The Track helper now roots their App/Workspace via catalog and containment without creating an App model, so the missing-model negative case retains its intended precondition. Fixture selection 2 follows on both adapters. No runtime validation was relaxed.

Affected fixture selection 2 passed on both adapters: {"json": {"passed": 69, "skipped": 0}, "postgres": {"passed": 69, "skipped": 0}}. Full-final-6 requalifies the frozen snapshot, adding all affected fixtures to the dedicated PostgreSQL selection.

During frozen final-6 qualification, the expanded external model probe `/tmp/integral-access-model-binding-baseline-2.xml` reproduced four unsafe allows and zero setup errors. Each legitimate actual Track-model read first succeeds; the subsequent read still allows wrong immutable execution workspace, an unrelated claimed Track, removal of the named model attachment, or a conflicting Track model cache. This is actual PolicyModule/service characterization on an isolated JSON store, not HTTP/PostgreSQL/deployed proof. Model attachment/parent authority remains open, including dedicated draft, template, library and shared-model semantics. Runtime/test source under final-6 remained unchanged.

The paired external shared-model probe `/tmp/integral-access-model-shared-mutation-baseline-1.xml` reproduced one unsafe mutation authorization and zero setup errors. A direct Track configuration administrator can update the initially sole attached model; adding its named attachment to an unmanaged sibling preserves legitimate read but incorrectly preserves model-update permission. This is an actual PolicyModule authorization decision, not a persisted mutation or transport/PostgreSQL exploit proof. All affected-parent authority and atomic attachment/effect fencing remain open.

### Qualified saved-View scope milestone — final-6

All five lanes completed with exit zero against unchanged staged runtime/tests. All 16 guards, commit-hook checks, pinned formatting/lint/types, reproducible wheel/import, CI-faithful smoke, frontend, full backend, Core-only, extension and both PostgreSQL lanes passed. Results: 979 smoke passes (10 skips), 1583 frontend passes across 279 files, 5306 full backend passes (300 skips), 603 focused PostgreSQL passes (zero skips), and 232 broader PostgreSQL spike/contract passes (one explicit restore skip). Terminal coordinator: `/tmp/integral-access-view-scope-final-6.status.json`; adjacent XML/logs retain case receipts. The restore skip is the Docker backup client reaching Mac localhost; current-authorization restore remains unqualified.

Wheel SHA-256: `12247294d10982ec2e7e7347c2a2075abae45f60ea526f89ba442d60c3ed45ae`. Frozen staged patch SHA-256: runtime `e64417eaa06610847e6dd91c0e016a1ed92d55d1f633a4027f1b024aa4945da3`; tests `2a4c74cf894490942db605ea6681f183862acfadd1b44382f06f1964ca0438a3`. Both matched after terminal qualification, with no unstaged source changes. This qualifies named saved-View Track scope, current raw point/list/config admission, sibling-safe default maintenance, common creation and unique-path transactional migration. It is partial remediation: no review finding or use case is closed by this milestone. Canonical Model ownership, broader Model lifecycle/configuration paths, ambiguous owner repair, complete action permits/dependency epochs, effect/release fences, independent browser/MCP and full migration/restore acceptance remain open. No GitHub CI, deployment, production migration or release is inferred.

### Pushed saved-View milestone and Model authority working slice

Saved-View milestone `d9eecdcf54a2130af5cd208f8a2ab2cdc8053cd0` committed with all hooks passing and was pushed to the sole branch. Local and remote revisions matched and the worktree was clean before starting Model work. The preceding goal turn made concrete progress by completing and pushing the qualified runtime/migration milestone, and by reproducing five Model authorization cases.

The working Model Walker now checks raw complete named attachments, current rooted parents, explicit stored lifecycle/kind fields, draft and template links, and cache conflicts. Models shared by valid same-workspace parents retain legitimate reads; configuration authorization requires every affected parent. Consumed published draft rows retain current parent-based read authority and remain immutable. Module evaluation binds the actual Model to its claimed parent and immutable workspace. This preserves Core stewardship versus ordinary App authority.

Focused-1 passes the five original Model baselines on JSON. Focused-2 passes 81 tests on JSON and 81 on PostgreSQL, zero skips/errors/failures, including attached App/Track, draft/template provenance, consumed-version shape, shared grants/revocation and action-kind negatives plus existing scope/View regressions. Focused-3 adds linked identity aliases/revocation and ordinary owner-authority tests; it is running with source frozen. The consumed-version test persists the actual retained-row shape and does not claim publication-effect qualification.

This is unfinished working integration, not a qualified milestone: platform/private library publication ownership must receive a separate named contract, with writers and legacy quarantine/repair. Global catalog membership and an empty workspace cache cannot safely declare public data. The current module helper withholds unsupported library targets. Existing API/authoring helpers and child projections remain to be adopted, followed by focused and complete gates. Authority/access, I-GRAPH-01, I-ROLE-01/02 and I-GOVERN-01 are preserved by the intended complete repair. Atomic attachment/effect/release fences and full finding/use-case closure remain open.

### Model draft/library boundary refinement — working source

Focused-3 completed 84 tests on each adapter; focused-4 completed 91 tests on each adapter; focused-5 completed 95 tests on each adapter. Each JSON/PostgreSQL lane has zero skips, errors or failures. Adjacent `/tmp/integral-access-model-authority-focused-{3,4,5}-{json,postgres}.xml` files preserve the receipts. These partial snapshots qualify named library ownership/publication families, explicit pending-draft review and retained workspace revocation at the module boundary. They do not qualify production publication writers, API projections or migration.

Inspection identified a vocabulary problem in private library authority: generic `resolve_role(workspace)` collapses membership into resource roles. The working helper now reads canonical workspace roles directly, rejects `none`, preserves common published-library read for owner/admin/member, and requires explicit Model configuration authority or protected Core owner governance for unpublished reads and mutations. Workspace admin or creation flags alone do not designate a Model curator. Focused-6 adds actual library-template provenance and conflicting private/public owner paths; it is running with runtime/tests frozen.

A separate seed-path probe is checking whether platform bundle synchronization overwrites a workspace-private library sharing its slug/name. Active seed lookup currently selects by slug/name without canonical publication provenance. Trusted producer markers and seed identity selection, API visibility/edit helpers, legacy quarantine/repair and grant-generation/offboarding remain unfinished. No new creator grants, commit/push qualification, finding closure or use-case closure is claimed.

Seed ownership baseline-4 completed with **two assertion failures and zero setup errors** on JSON: valid private Models were overwritten by trusted seed upsert in both slug-collision and display-name-collision cases. The probe reads the persisted raw node after the write. Source/XML/log remain at `/tmp/integral-access-library-seed-ownership-baseline-4.*`. Earlier probe setup errors and the first noncanonical private manifest are preserved separately and are not secure acceptance evidence. The production seed writer is unchanged and the issue remains open pending named publication identity plus atomic writer integration.

Focused-6 finished **102 JSON passes and 102 PostgreSQL passes**, with zero skips/errors/failures. The coordinator terminated with exit zero before adding new tests. Canonical workspace membership vocabulary, pending draft privacy for members/admins, library-template provenance and conflicting ownership are covered at the module boundary. Focused-7 adds explicit outage regressions after a positive warmed authority result: raw Model, named attachment or actual parent source failure must raise `AuthorizationUnavailableError`. Runtime/tests are frozen during this selection. Full consumer/producer and migration integration remains required before any milestone commit.

Focused-7 finished **105 passes on JSON and 105 passes on PostgreSQL**, with zero skips/errors/failures. Terminal coordinator exit zero confirmed before further source edits. Primary Model, attachment and parent failures raise typed unavailable after a prior positive result. Syntax compilation also passed. Next work is production library writer/publication identity and API/authoring/dependency integration; additional repeats of this unchanged focused selection are not needed. The seed collision remains reproduced and open. Current source is not ready for full gates or a commit.

### Platform library writer integration — working source

The prior turn made concrete progress: Model boundary qualification reached 105 tests on each adapter, and the valid-manifest seed probe established two persisted private overwrites. The current seed writer now selects only a raw, uniquely rooted named platform publication, including retained inactive seed identity. Slug/name/cache/catalog facts alone do not adopt a Model. New seeds wire their publication marker and catalog in the creation unit; PostgreSQL joins the graph transaction, while nontransactional stores use cleanup without an atomicity claim. Raw candidate overflow or multiple admitted matches withholds the operation. Seed removal checks the same publication identity and never deactivates an unproven private/manual row.

Working tests add both original collisions, cleared workspace cache with retained private edge, unmarked global catalog rows, legitimate reseeding and inactive-publication identity restoration. Existing metadata tests now wire actual catalog edges; the mocked restoration test has been replaced with an actual persisted round trip. Focused library-publication-1 is running on frozen source/tests. No pass, full gate or commit is claimed yet.

Private-library creation marker wiring, API/authoring/list/dependency consumers, grant designation/generation, concurrent seed/effect fencing and reviewed legacy dispositions remain open. Existing API visibility still relies on scalar caches and must adopt canonical admission before this family is releasable. Creating a fresh marked public seed does not repair historical ambiguous rows or rewrite their references.

Platform publication focused-1 completed **58 passes per adapter**, zero skips/errors/failures, including current Model-family regressions and producer restoration/metadata cases. Terminal coordinator exit zero confirmed before refinement. Name fallback now only adopts a named, marked pre-slug seed; one package's nonempty stable slug cannot be replaced by another package with the same display name. Focused-2 completed **16 production-selection tests on JSON and 16 on PostgreSQL**, zero skips/errors/failures. New actual-store tests cover distinct same-name platform packages and failure after catalog attachment leaving no persisted Model. Both coordinators are terminal. `git diff --check` passes.

This repairs the observed private overwrite at the seed service boundary in working source. Complete marker migration, private creation/curator designation and offboard/reentry, canonical API/authoring/dependency admission, concurrent producer identity/effect fencing and full milestone gates remain open. No commit/push or finding/use-case closure is claimed for this working slice. The last pushed milestone remains `d9eecdcf54a2130af5cd208f8a2ab2cdc8053cd0`.

### Private library producer and Model handler integration — working source

The preceding goal turn changed authoritative seed source and passed paired producer tests, so it was progress. Current private library producers (HTTP publish/import/derive/author and in-process author/empty-draft) now use a common service. It validates current creator/workspace/root/catalog facts, prevents ordinary App origin from consuming implicit owner creation stewardship, wires `HAS_LIBRARY_MODEL` and catalog in the creation unit, and validates the resulting canonical private authority. PostgreSQL joins the graph unit; nontransactional stores use cleanup without an atomicity claim. No new creator configuration grants are introduced yet: explicit curator designation and membership-generation/offboarding remain open.

HTTP and authoring edit helpers now evaluate the exact Model configuration action with isolated current permission facts; scalar Track/App OR and workspace creation flags no longer designate configuration rights. The Model scope helper follows complete current named provenance. HTTP library point/list/workspace inventory and type-hint selection use canonical publication/private audience admission; actorless hint lookup only admits published platform Models. HTTP/in-process point Model captures bypass warmed Node getters. Update/delete library handlers use exact Model admission, including initial private draft update. Draft relationship/reuse and child projections still require integration.

Focused `/tmp/integral-access-model-api-focused-1` is running against frozen source/tests: actual rooted producer draft/published cases, both edit helpers on shared parents, point-handler current body and guest/revocation admission, actual named draft regression, Model-family and publication regressions. JSON lane passed, PostgreSQL pending. A lint check also identified two missing local `OperationalModel` imports in existing draft-finder helpers after getter replacement; these are pending repair after the source-reading process ends. Full-gate qualification and commit remain unclaimed.

Model API focused-1 finished **56 JSON passes and 56 PostgreSQL passes**, zero skips/errors/failures, terminal coordinator exit zero. The two missing draft-finder imports were repaired before further integration and symbol/unused-import lint passed. Focused-2 then completed **89 passes on each adapter**, zero skips/errors/failures, including existing Model draft/diff suites and new pending privacy, corrupt lineage and duplicate pending-child cases. The source was unchanged during both selections.

Named pending draft selection now follows `HAS_DRAFT_PROFILE`, distinguishes retained published history from current drafts, rejects multiple/dangling/corrupt named children, and withholds pending content/presence from published-only viewers. Both reuse surfaces admit the exact child before returning it; configuration denial cannot trigger a replacement fork. Patch/diff/publish/discard service permissions now target the exact draft, preventing its scalar parent claim from authorizing a different leaf. The service orphan-delete shortcut was removed.

Further HTTP review found the same authenticated orphan-discard shortcut and an arbitrary diff-reference read without a separate audience check. The working HTTP repair requires exact draft deletion admission, separate canonical reference read and scope admission, and reference configuration authority before entry-impact calculation. App child impact projections still need complete actor/dependency admission; this guard alone does not establish that coverage. Direct Model action boundaries now isolate current permission facts even outside PolicyModule. Focused-3 adds HTTP orphan preservation and separate reference audience/revocation/foreign-workspace cases; it is running with source frozen. Full effect/release, migration, creator curator lifecycle, complete projections and milestone gates remain open.

Model API focused-3 completed **92 JSON passes and 92 PostgreSQL passes**, zero skips/errors/failures. The coordinator terminated with exit zero before further source changes. Exact HTTP orphan admission, independently authorized diff reference, revocation recheck and foreign-workspace denial are actual-store handler regressions; existing draft/diff suites also pass. Symbol/unused-import lint and `git diff --check` pass. This does not claim deployed transport, full browser/MCP, complete child projection or effect/release acceptance.

Next integration work: canonical library dependency and installation admission, exact related child projections (including impact), initial private library draft publish/patch lifecycle, creator/curator designation with membership-generation and departure/reentry, immutable creation action/sentinel scope binding, reviewed legacy marker migration/quarantine and complete gates. No source-reading job remains live. The working slice remains uncommitted; last pushed revision is the saved-View milestone.

### Current library dependency/install admission — working source

The preceding goal turn made concrete runtime/handler progress and qualified 92 Model API cases on each adapter. Current working source replaces recursive library dependency resolution with a Walker that admits each current named library family before inspecting its declarations. Captured graph topology is sorted without further graph I/O; cycles, multiple visible aliases, dangling/unavailable sources and defensive overflow withhold the complete result. A real existing point ID cannot fall back to a different package alias. Actorless internal callers admit only named published platform sources; this is not a service-actor effect-authority contract.

Persistent library merges now resolve canonical target Model ownership, check an explicitly supplied Track belongs to the complete target parent set, and require supplied human configuration authority for every affected parent. Full dependency admission precedes merge writes. HTTP/in-process merge and install/upgrade paths carry their actor into these source checks; supplied execution workspace membership is current even for public sources. Install preflight uses the returned fresh source body. Upgrade preview carries actor through the same dependency merge. Revert preflight now admits target Model configuration and the full library chain before its destructive child cleanup. Exact effect/dependency/release fencing and complete child materialization admission remain open.

Dependency focused-1 stopped on JSON: six newly added fixtures omitted mandatory dependency versions and failed canonical validation before exercising the intended source-admission assertions. That receipt remains preserved; no security qualification is inferred. Fixture declarations were corrected to valid versioned manifests after the coordinator terminated. Focused-2 adds the existing HTTP private-library/workspace-isolation test and is running against frozen source/tests. Symbol/unused-import lint passes. Version compatibility/definition epoch binding is not qualified by supplying syntactically valid dependency versions.


Library dependency focused-2 completed **29 tests on JSON and 29 on PostgreSQL**, zero skips/errors/failures, terminal coordinator exit zero. Old scalar-only ownership/visibility helpers have been removed; their fake-node tests now exercise current rooted private ownership, fresh returned content, cache corruption and revoked workspace membership. New dependency cases cover public roots with private dependencies, actorless withholding, public-only internal reading and inaccessible exact IDs with a visible alias collision.

Consumer focused-1 preserved **14 JSON failures out of 81 tests**: one deep-copy fixture error, unrooted public seed fixtures, changed type-hint mock signatures and failed installs/list matches. Fixtures now use the trusted named publication producer; mocks carry actor arguments. Batch preflight now admits full selected source chains before compiling/returning details or invoking installation, and auto-expansion admits current source audiences before inspecting app requirements. Visible alias ambiguity is rejected. In-process library listing also admits canonical current audience. This is partial preflight coverage; the existing catalog scan is not a bounded physical-work qualification.

Consumer focused-2 preserved **one JSON failure out of 81** from an incorrectly changed fixture description; restored manifest description before rerun. Consumer focused-3 completed **83 passes per adapter**, zero skips/errors/failures, terminal exit zero: `/tmp/integral-access-library-consumers-focused-3-{json,postgres}.xml`. Source/tests remained frozen during each selection. A post-run selected lint check identified unused imports in the touched authoring test file; these were removed after terminal confirmation. No finding/use-case closure, full milestone gate, new commit or push is claimed. Initial private draft lifecycle, curator grant designation/generation/offboarding, legacy quarantine/migration, atomic source/target/definition/release fencing and complete child effects remain open.


### Initial private library draft lifecycle — working source

Initial named workspace-private library drafts now support patching, first-publication review, and first publication through the HTTP and in-process paths. Publication retains the Model ID, named private ownership and catalog attachment; it cannot turn an empty workspace cache into platform publication. Exact `operational_model.publish` is checked separately from configuration/update. First publication has no attached entry migrations; forced migration is rejected for this path. Review fingerprints bind the exact initial candidate, and a changed candidate refuses publication. PostgreSQL joins a graph transaction; concurrent revocation/effect/release fencing is not qualified by these point checks.

Initial-library focused-1 preserved two JSON failures: the new handler test attempted a nonexistent `__wrapped__` attribute and an older review test mocked the retired cached getter. Tests were corrected after terminal exit; no runtime bypass was restored. Initial-library focused-2 completed **101 JSON tests and 101 PostgreSQL tests**, zero skips/errors/failures, terminal exit zero. Actual-store coverage includes owner patch/review/publication preserving private identity, no admin curation from creation flags, stale review and cross-workspace HTTP binding, with existing draft/diff, Model authority, producer and publication-review suites.

Pinned black 24.8.0 left all 1343 Python files unchanged. Pinned isort 6.0.0 is run before the full-gate snapshot. Current surface inventory is regenerated as a passive declaration inventory, not enforcement acceptance. Full five-lane qualification is the next gate; no commit/push or new closure is yet claimed. Curator designation/generation, reviewed legacy migration/quarantine, complete child projections and effects, concurrent publication/revocation and release authority remain open.

Full Model qualification-1 stopped in `make verify` before smoke/frontend/backend suites: all substrate guards and pre-commit formatting/lint passed, but mypy required an explicit `set[str]` annotation on dependency topology's `emitted` accumulator. The coordinator is terminal (exit 2). Annotation repaired; no full-suite pass inferred. Full qualification-2 starts on the corrected staged snapshot.


Full Model qualification-2 passed substrate guards, pre-commit (including mypy), pinned format/lint, frontend lint/types, reproducible wheel import/build (`51a6256c700faf09ecc9baf34557740c1c0dc02241310c37b74dece1c551ce55`), CI-faithful smoke (**1061 passed, 10 skipped, zero failures/errors**) and frontend (**1583 tests, 279 files**). Runtime patch SHA `56dca95294851ab502387356366b9cf1a0cf422a606edb28cb42279f376a09ed`; test patch SHA `fa9bf6abee46d71298326cba55d074860cf9581990417aaf2b1b46bf50a32407`. Both staged snapshots had no unstaged source/test changes. External snapshot: `/tmp/integral-access-model-authority-full-2-snapshot.json`.

The full backend selection reported failures, so its owned pytest coordinator was gracefully interrupted to surface reports and begin repair; this was an intentional response to known failures, not a timeout restart. The coordinator and test process are terminal (exit 2). Partial backend receipt contains **269 cases, 10 failures, 37 skips, zero errors**; it is not a complete-suite result. Ten failing Asset Register contracts stop at canonical library admission. Legacy fixture helpers directly created unrooted library rows; two artifact paths also used unresolved actor placeholders. Repairs use the actual trusted publication producer and actual rooted workspace owner; no authorization bypass or scalar-public fallback is restored. Focused repair qualification follows before a fresh full gate.

Isolated external current-source probes also reproduced Model grant reentry. Baseline-1 failed setup because external-path pytest omitted repository async configuration. Corrected baseline-2 (`/tmp/integral-access-model-grant-reentry-baseline-2.xml`) has one unsafe reentry assertion failure, zero setup errors. The actual `remove_workspace_membership` probe (`/tmp/integral-access-model-departure-reentry-baseline.xml`) likewise has one reentry assertion failure and zero errors: current departure walker omits OperationalModel grants. Both are terminal; runtime/tests were unchanged during full qualification. Canonical Model cleanup and membership-generation binding remain required; these are characterizations, not acceptance. No finding/use-case closure or commit/push is claimed.


Contract repair focused-1 completed **10 JSON passes and 10 PostgreSQL passes**, zero skips/errors/failures, terminal exit zero. These are the exact ten failing Asset Register contract cases from the interrupted full backend receipt. Shared fixture publication now uses the trusted named producer and preserves loaded artifact provenance; unresolved actor placeholders use the actual graph workspace owner. Runtime permission checks remain unchanged by fixture repair.

Model departure cleanup now follows direct Model grant targets and resolves current canonical Model authority with management lifecycle admission. Only grants belonging to the departing workspace are selected; platform/foreign-workspace grants are preserved. Unresolved Model ownership stops the complete grant inventory, preventing a successful departure or deletion of already collected grants from an incomplete result. This integrates private library and attached draft reviewer targets without trusting scalar workspace caches.

Departure focused-1 completed **12 JSON passes plus 2 explicit PostgreSQL-only skips**, and **14 PostgreSQL passes**, zero failures/errors. Coordinator terminal exit zero. New actual-store cases cover the reproduced production membership-removal/reentry path, foreign grant preservation, named attached draft reviewer cleanup and unresolved ownership retaining membership and both Track/Model grants. Existing workspace departure, ownership transfer and PostgreSQL departure contracts also pass. Direct membership deletion outside this cleanup path, grant generations/issuer adoption, suspension/in-flight work and restore/replay authority remain open. This is not AC-10 closure.

A selected symbol/import lint run identified an unused import in the touched artifact contract test; it was removed after all source-reading jobs ended. Full qualification must run again after remaining affected-consumer fixture integration. No new commit or push is claimed.


### Remaining library consumers and summary privacy — working source

The broader consumer baseline preserved 71 JSON cases with 38 failures, 3 skips and zero errors. Most failures used unrooted library fixtures or unresolved actor placeholders. Repairs publish fixture libraries through the trusted canonical producer and use the actual rooted workspace owner in lifecycle/onboarding/reference/commercial contracts. An import cleanup also mistakenly removed a used artifact helper import; that import is restored and symbol lint passes. No permission bypass is added.

A runtime summary helper still enumerated scalar library rows without current audience admission. It now captures raw candidates, validates the current principal and supplied workspace membership, and admits canonical public/private source authority before returning summaries. Manifest scope supplies the package's App/Track scope rather than platform placement. Onboarding supplies its actual current principal and personal workspace, using a current User capture. Unbounded catalog scanning, complete response/release fencing and onboarding preference-write authority remain open.

Consumer integration focused-1 is terminal with **73 JSON passes and 3 skips**, and **76 PostgreSQL passes**, zero failures/errors. Tests include summary privacy, current membership revocation, unknown-principal rejection, lifecycle, onboarding and App contracts. JSON skips are retained in the receipt; this is not a full suite or transport qualification. The previous full-2 snapshot is superseded by these runtime/test repairs. A fresh full five-lane milestone gate is required before commit/push; no finding or use-case status changes.


Full Model qualification-3 passed guards/hooks, pinned format/lint/types, reproducible wheel/import (`ca60e9da3d24bab8f75c84e127eacbc06bcedc009ec3b36b60b545dc63994d7c`), CI-faithful smoke (**1067 passes, 10 skips**) and frontend (**1583 tests, 279 files**). Runtime staged patch SHA `5d7470161a6ae13720c7fe724ab26dd49d36b31cfb44ef850214df92ec80402e`; tests SHA `026a9f77ace9c327f78ca8fa6d02075e823b2485694dd8dea9a80823d0e17072`; no unstaged runtime/tests. Snapshot `/tmp/integral-access-model-authority-full-3-snapshot.json`.

The full backend reported six failures and its verified owned coordinator was gracefully interrupted to collect repair evidence. Terminal partial receipt: **2780 cases, 6 failures, 53 skips, zero errors**. This is not full-suite qualification; later supplementary lanes were not reached. All failures are migration recovery/publication endpoint fixtures mocking the retired cached Model getter. Repairs use actual rooted attached/pending Models, current workspace owner/scope and real configuration admission. Scheduling/publication effects remain mocked for these dispatch tests; no policy or provenance bypass is introduced. A new recovery case admits a current curator once, removes actual membership, then requires denial without another enqueue. Paired focused qualification is running before a new full gate. No finding/use-case closure, commit or push is claimed.

Migration consumer focused-1 is terminal: **37 JSON passes and 37 PostgreSQL passes**, zero skips/failures/errors. Exact six previously failing cases plus current Model/API/initial-publication regressions and the recovery revocation/no-second-enqueue case pass. The fresh full-4 gate includes these migration consumers in its PostgreSQL selection. This is dispatch/current-admission coverage; durable worker claim/effect/replay authority remains a separate open requirement.


Full qualification-4 passed the repeated guards/hooks/lint/types, clean wheel/import, **1067 smoke passes plus 10 skips**, and **1583 frontend tests in 279 files**. Runtime/test snapshot `/tmp/integral-access-model-authority-full-4-snapshot.json` records unchanged runtime patch `5d7470161a6ae13720c7fe724ab26dd49d36b31cfb44ef850214df92ec80402e` and test patch `82a5a98f57b49eeeaa099b8b3f3a5cecb395529fe430831ef320e3a216c9a0bf`. A new full-backend failure led to graceful interruption of the verified owned coordinator. Terminal partial receipt: **3681 cases, 1 failure, 56 skips, zero errors**; supplementary lanes were not reached. The failing operational-layer installation fixture supplied an unresolved actor rather than its actual workspace owner.

Further fixture review found the dependency/cross-App modules excluded from default collection by the domain-App selection policy. Their positive library fixtures lacked publication provenance and used unresolved actors. Working repairs use canonical trusted publication, actual workspace/App owners and structural legacy-App attachments. The opted-in focused-1 selection was interrupted after lint exposed an overbroad fixture-binding removal; its 51-case partial XML contains 24 failures, zero errors/skips, mainly a newly introduced keyword-only helper invocation mistake, plus an absent external CRM artifact. These are fixture failures, not security acceptance. The binding and keyword calls are corrected. The CRM source-artifact check explicitly skips when that external App artifact is absent; no Core dependency/runtime assertion is skipped. Focused-2 runs both adapters with `INTEGRAL_INCLUDE_DOMAIN_APPS=1`; no full pass/commit/push or finding/use-case closure is claimed.

Install consumer focused-2 completed 71 JSON cases with 3 failures, 1 external-artifact skip and zero errors. Two fixture merges passed `actor_id` instead of the service's `user_id`; the third was a stale label-field grep declaration allowlist. Source review verified the region-system hits are manifest/schema text and the migration hit is a configuration field-name rewrite list, neither target Entry field-content access. The guard now permits these declaration files with an explicit reviewed rationale; the content reader remains unchanged.

Install consumer focused-3 is terminal: **70 JSON passes and 70 PostgreSQL passes**, each with the one explicit absent-CRM artifact skip and zero failures/errors. Domain selection was explicitly enabled, so generic requires-app and cross-App tests actually executed. Coverage includes install/uninstall dependency gates, same-workspace rejection, restricted label reads, multi-instance resolution and operational-layer update alongside lifecycle/teardown regressions. This does not qualify the absent CRM artifact or the broader durable dependency/effect journal. Fresh full-5 qualification follows before commit/push.


Full Model qualification-5 reached a **complete** backend report: **5690 cases, 5388 passes, 300 skips, 2 failures, zero errors**, coordinator terminal exit 2. The run was allowed to finish to gather the full failure set. Both failures are warranty schedule contracts supplying unresolved actor placeholders; they use the common canonical Asset Register publication fixture. Runtime was unchanged; repair selects each fixture's actual workspace owner. Guards/hooks/pinned lint/types, clean wheel/import, **1067 smoke passes with 10 skips** and **1583 frontend tests across 279 files** passed before backend. Later supplementary lanes were not reached. Snapshot `/tmp/integral-access-model-authority-full-5-snapshot.json` records runtime patch `5d7470161a6ae13720c7fe724ab26dd49d36b31cfb44ef850214df92ec80402e`, test patch `2a2bcc5211caf8ef3eebbec9a0d58d94392ee0b4e45bffe90f27c597cfe94a51`, no unstaged runtime/tests. This is a complete failing gate, not a milestone pass. Paired warranty/Asset Register qualification is running before full-6.

Warranty consumer focused-1 is terminal: **20 JSON passes plus 3 PostgreSQL-only skips**, and **23 PostgreSQL passes**, zero failures/errors. The exact two complete-full-5 failures now execute with real fixture workspace owners; scheduler pause/resume/dispatch and existing Asset Register artifact/App contracts pass. No scheduling-worker/replay/effect closure is inferred from these contract dispatch tests. Full-6 starts on the repaired staged snapshot, with supplementary lanes before the broad `make verify` lane.


### Model admission foundation — qualified partial milestone

Full qualification-6 is terminal, **all five lanes exit zero**: Core-only, extension-contract, focused PostgreSQL, PostgreSQL spikes/contracts and complete `make verify`. Full backend: **5390 passes, 300 skips, zero failures/errors** (5690 cases); CI-faithful smoke: **1067 passes, 10 skips, zero failures/errors**; frontend: **1583 tests in 279 files**; focused PostgreSQL: **807 passes, zero skips/failures/errors**. Guards/hooks, pinned format/lint/types and clean reproducible wheel/import pass. Wheel SHA-256 `ca60e9da3d24bab8f75c84e127eacbc06bcedc009ec3b36b60b545dc63994d7c`. Runtime/test staged patch fingerprints match the full-6 snapshot after the coordinator terminated, with zero unstaged source/test bytes.

[model_admission_qualification.json](model_admission_qualification.json) retains the terminal gate/receipt inventory and exact source fingerprints. The PostgreSQL contract gate retains one restore-rehearsal infrastructure skip: `pg_dump` executed in a container cannot reach the host-loopback database. No restore/current-authorization acceptance is inferred. The explicitly enabled dependency/cross-App consumer selection separately passed 70 tests per adapter with one absent external CRM artifact skip.

This qualifies the canonical named Model read/configuration foundation, selected producer/API/library/install consumers, initial private publication and production departure Model-grant cleanup for commit/push on `codex/access-control-review`. It does not finish the whole Model family or close findings/use cases. Creator curator designation and issuer/generation binding, legacy marker migration/quarantine, complete child/impact/materialization projections, creation sentinel/action execution binding, atomic effects/release and complete dependency versions/epochs, worker/replay, browser/MCP and restore remain open. The wider goal remains active.

### Private Model creator curation — working lifetime binding

The foundation was committed as `5bac17e4b50278b6695547cda76566a9ef60a526` and pushed to the same branch. Remote readback matched the full SHA; no PR, deployment or release was performed.

An actual-store baseline (`/tmp/integral-model-creator-baseline.xml`, one expected failure) reproduced that a member with `can_create_tracks=True` could create an empty private library draft but could not continue authoring it: `apply_patch_to_draft` returned `forbidden`. The working repair designates an exact `admin` curator grant on the newly created Model when the creator has explicit creation flags. The source, workspace, membership edge identity and issuer provenance are typed associative metadata. Membership IDs are generated randomly by the pinned jvspatial edge constructor and change on delete/recreation; an actual-store test requires this property.

Bound grant reads require a canonical private library target in the same workspace and the unique original current recipient membership. Missing or changed membership denies; corrupt/partial/conflicting bindings or unavailable primary facts are unavailable, not an ordinary role. App/Track/Entry bulk role selectors cannot consume Model-only binding metadata without validating it. Creation flag removal blocks later creation without deleting already issued exact curation. Protected owner creation without explicit flags does not mint ordinary App configuration authority. Ordinary accepted human grants are durable: issuer provenance is not a live issuer chain; pending invitation/adoption and service-delegation rules remain separate per W4.

Focused-1 is running with runtime/tests frozen on JSON then PostgreSQL. This slice does not migrate legacy unbound grants, implement suspension states, establish atomic creation/revocation/effect fencing, or close a finding/use case. Full staged gates and commit/push qualification remain pending.

Focused-1 completed **163 JSON / 163 PostgreSQL passes**, zero skips/errors/failures. A subsequent source review identified an ordered creation race: flags could disappear after the initial gate and before curator designation. The producer now captures role, flags and membership identity from one raw membership row, requires agreement with the current admitted role, and refuses a non-owner whose captured flags no longer authorize creation. This protects that ordered interleave without claiming a storage-level authorization fence. Focused-2 completed **164 JSON / 164 PostgreSQL passes**, zero skips/errors/failures, including a real store update between the initial gate and membership capture. Staged pre-commit formatting/lint/drift/mypy passed. Full-1 is the next frozen qualification; no commit or push is claimed for this working slice.

Full-1 Core-only passed. While extension qualification ran, an external actual-store creation-identity probe established that the common producer accepted a caller-selected existing Model ID: the persisted owner-private name changed to the creator replacement, and the newly issued creator grant could designate the requester on that existing target. Current HTTP/author producers use fixed fields; this is service-boundary evidence, not a demonstrated HTTP exploit. The first external invocation used the repository root and could not import `tests.conftest`; baseline-2 used the backend configuration and produced one assertion failure, zero setup errors (`/tmp/integral-private-model-creation-identity-baseline-2.xml`).

The exact owned full-1 coordinator tree was deliberately interrupted (terminal exit 130); every owned PID was absent before source edits. No extension/full-suite pass is claimed. The private producer now accepts an explicit content/lifecycle field allowlist, withholding caller identity, attachment, lineage, seed metadata and audit envelopes before entering any write unit. Existing producers were inspected through their AST keyword sets; they all use the admitted fields. Permanent actual-store tests assert unchanged target content, unchanged Model inventory and no new target collaboration grant across nine protected-field inputs. Focused-3 and new full qualification remain pending; all review and use-case statuses remain unchanged.

Focused-3 completed **173 JSON / 173 PostgreSQL passes**, zero skips/errors/failures, including the nine identity/provenance rejection cases. Staged pre-commit formatting/lint/drift/mypy passed after the allowlist repair. Full-2 will use the same required gates; the extension-contract selection runs under four isolated xdist workers and retains a dedicated XML report. Source and tests are frozen for qualification. No new commit/push or finding/use-case closure is claimed.

### Current invitation lifecycle recheck — AC-07 / AC-21 next integration priority

While full-2 remains live, a separate external JsonDB probe rechecked invitation behavior against the current rooted graph fixture and a real linked AuthUser recipient. All three secure assertions failed, with zero setup errors (`/tmp/integral-invitation-lifecycle-current-baseline.xml`): a pending resource invitation still grants editor after the issuing admin grant is deleted; acceptance observed before revocation can write accepted/grants after revoke has persisted; an existing viewer accepts an editor invitation but remains viewer while the returned invitation advertises editor. These are current service/ordered-interleave observations, not independent PostgreSQL concurrency proof or a deployed exploit. Runtime and repository tests stayed frozen.

The next integration must prioritize the AC-07 P0 invitation lifecycle with AC-21 effective-grant receipts, per W4/G0. It must validate actual target/workspace and current issuer (or explicit protected-owner adoption), claim the pending revision and materialize only the intended grant in the same PostgreSQL authorization/graph unit. Existing grant upgrade/unchanged/conflict outcomes must retain newer changes and report persisted effective authority. Recipient mailbox/token proof retains its separate contract; successful ordinary human grants remain durable after issuer departure. Revocation, acceptance, grant change and membership suspension need a common ordering fence, durable audit intent and real independent-connection barriers. JSON ordered probes are baseline evidence only. Legacy Model/grant migration remains required; no finding or use-case closure is claimed by this recheck.

A further authenticated-handler recheck found the invitation path still bypasses selective creation-grant ceilings: an ordinary workspace admin with both current creation flags false can call `post_create_invitation` with both flags true, and the handler creates the pending invitation instead of raising `InsufficientPermissionsError`. Outbound email was replaced with an AsyncMock; no message was sent. `/tmp/integral-invitation-admin-ceiling-baseline.xml` retains one assertion failure, zero setup errors. This rechecks AC-09/W3 through an additional invitation surface, rather than claiming the previously tested membership PATCH repair covers every grant path. Issuance and acceptance need one canonical ceiling rule. Runtime/tests remained frozen during the external JsonDB handler probe.

Full-2 partial qualification: Core-only passed; extension-contract under four isolated xdist workers passed **52 cases / 232 explicit skips**, zero failures/errors (284 XML cases, `/tmp/integral-model-creator-full-2-extension.xml`); focused PostgreSQL passed **836 cases**, zero skips/failures/errors (`/tmp/integral-model-creator-full-2-postgres.xml`). Broader PostgreSQL and `make verify` remain live/pending in that coordinator. These partial gates do not authorize a new commit/push or close a finding/use case.

### Restore namespace diagnosis and bounded route-corrected drill

The required broader PostgreSQL lane passed with its original explicit restore skip: container `pg_dump` cannot reach host-loopback `127.0.0.1:63803`. Direct `pg_isready` probes against `host.docker.internal:63803` completed exit zero under both host and default container networking. An external pytest probe then created a disposable PostgreSQL fixture with a canonical private library Model, membership-bound curator grant and an Entry exclusion, overrode only the existing scripts' client host to that gateway and ran their backup/`--drill` path. The probe passed one case, zero skips/errors/failures. Script receipts reported 33 nodes / 46 edges, counts match, Model identity match, completion and scratch database removal. `restore_gateway_qualification.json` records source/script fingerprints and exact receipts.

No runtime/repository-test edit occurred while full-2 was live. This diagnoses the local networking skip and supplies bounded logical round-trip evidence; it does not revise the original gate receipt. The scripts do not compare complete ACL payloads or reconcile an independently retained current revocation checkpoint before serving. Quarantine, post-revocation grants/tokens/credential denial and U75/W7 restore security remain open.

### Creator curation foundation — full-2 terminal qualification

All five frozen full-2 lanes completed exit zero: Core-only, extension-contract (52 passes / 232 explicit skips), focused PostgreSQL (836 passes / zero skips), broader PostgreSQL (one explicit loopback restore skip), and `make verify`. Full backend: **5419 passed / 300 skipped**, zero failures/errors; smoke: **1096 passed / 10 skipped**, zero failures/errors; frontend: **1583 tests / 279 files passed**. Pinned formatting/lint/mypy, staged guards, types and reproducible wheel/import passed. Wheel SHA-256: `d4d8e49192f802142113ea8be30ebcc70ae46bc72ca8f22c74fcb5d78b985bd0`. Frozen runtime/test staged hashes matched after terminal completion; no unstaged source bytes were present. `model_creator_qualification.json` records exact source fingerprints, reports, gate logs and unresolved limits.

This qualifies the creator curation and private creation identity/provenance repair for commit and push on the same branch. It does not close any whole review finding/use case or finish Model/membership lifecycles. In particular, the four freshly rechecked invitation defects, legacy migration, suspension, atomic effect/release, complete epochs, browser/MCP and current-revocation restore acceptance remain open.


### M3 invitation authority integration in progress

The prior creator foundation is committed and remote-verified on the singular branch as `ea3bc98041f9fe763fe8fdeebadc1dde89ade559`. The next package implements common service-level invitation authority. Workspace issuance now checks a current canonical issuer and rooted workspace before effects. Ordinary admins can issue basic member/guest invitations but cannot issue administrator membership or creation flags, even when they themselves hold creation flags; explicit scoped delegation administration is still forthcoming. Protected ownership retains those rights without requiring creation flags. App execution cannot consume protected owner authority.

Workspace issuance qualification-2 passed **55 JSON and 55 PostgreSQL cases**, zero failures/errors/skips. Initial existing-fixture qualification had two failures: unrelated overview/revoke tests issued workspace invitations without any owner membership. The repaired fixtures give their intended issuer canonical ownership and preserve their endpoint assertions. The first resource selection named a nonexistent test module and ended with exit 5 without a usable qualification result; corrected selection-4 includes the actual `test_sharing.py`.

Resource issuance now checks the exact `*.invitation_create` policy and canonical containment at the service boundary. An omitted workspace is resolved from the target; a supplied mismatch denies before writes. The canonical issuer ID is persisted. Acceptance rechecks current issuer authority before materializing grants. This broader integration is under qualification. These checks are **not** a transaction/revocation fence: pending issuer membership lifetime, protected-owner adoption, legacy migration, raw record admission, atomic lifecycle CAS/outcomes, durable audit/receipts and independent PostgreSQL races remain required. AC-07/09/21 and related use cases remain open. No full-gate pass, commit or push is claimed for the working M3 changes.


Invitation integrated qualification-4 ended with three failures, zero setup errors: canonical graph issuer ID was absent from an overview query keyed only to login ID; a cross-owner fixture's issuer was outside the target's workspace; and the now-fail-closed target/issuer check returned the generic grant failure rather than a missing-target subtype. Overview/revoke paths now recognize both current principal aliases. The negative fixture uses the actual resource owner's workspace. Missing/corrupt target acceptance remains pending and returns generic grant failure before any grant. Focused-5 passed **90 JSON / 90 PostgreSQL cases**, zero skips/errors/failures.

New producers now persist `issuer_membership_id`, capturing the unique primary-store membership edge. Acceptance compares it with the current live issuer membership after current target/policy admission. Removing/rejoining the issuer does not reactivate the pending invitation even when its direct resource admin grant remains. Pending legacy records without that original binding fail closed and need explicit owner adoption/reissuance; no historical binding is invented on read. Integrated focused-6 passed **92 JSON / 92 PostgreSQL cases**, zero skips/errors/failures (`/tmp/integral-invitation-authority-{json,pg}-6.xml`). This is current admission proof, not atomic revocation ordering or a complete migration. The next implementation remains the invitation CAS/transaction/receipt/outbox/adoption package. No broad finding closure or milestone push is claimed.

A direct ad hoc mypy invocation followed transitive third-party imports and reported 143 errors; test-inclusive flake8 also scanned files outside the configured app-only gate. Neither is represented as the repository qualification command. The configured `.ci/mypy_backend.sh` and app-only flake8 are rerun separately; the full milestone gate remains required before commit.

Configured app-only flake8 passed. The actual `.ci/mypy_backend.sh` passed all **659 source files**; the ad hoc transitive-import result above is not a failure of that configured gate. All focused test and lint processes are terminal before the next source-edit batch. Full qualification and commit/push remain pending the lifecycle implementation.


### M3 lifecycle scope and acceptance integration

The new lifecycle scope uses the existing PostgreSQL workspace authorization fence and public held graph transaction, then locks the invitation row. It reloads primary raw status/workspace/revision, rejects missing authority fields instead of hydrating pending defaults, and prohibits nested same-task transitions. Development JSON only gets process-local serialization and cannot claim transactional rollback. Lifecycle-scope focused-2 passed **5 JSON tests with one explicit PostgreSQL-only rollback skip**, and **6 PostgreSQL tests with no skips/failures/errors**. The rollback test verifies both terminal invitation state and first-created workspace revision are absent after an injected exception.

Acceptance is now being connected to that held unit: current recipient identity/mailbox proof, pending/terminal/expiry state and issuer admission are re-evaluated inside it; a materializer error raises through the transaction before returning an error. Accepted retries use current canonical identity and recipient proof rather than an early cached status result. Terminal acceptance increments the invitation revision. Integration qualification is running; revoke/decline/expiry still need the same seam, durable audit intent and grant receipts remain unfinished, and membership/grant writers still need common source-change ordering. No full lifecycle closure or milestone commit is claimed.


Lifecycle integration-2 passed the acceptance/revoke selection on JSON and PostgreSQL. Revoke now reauthorizes current actor/target at the service boundary, reloads current state inside the same lifecycle seam, increments revision, returns idempotently for an existing revoke and refuses to replace other terminal states. Decline now rechecks current recipient proof and pending/expiry state in the same seam. Expiry now reloads each candidate inside that seam before changing state.

Integration-3 had **one expiry assertion failure on each adapter**, zero setup errors. The pinned `Node.find(..., limit=N)` interprets `limit` as an entity filter rather than a database option, so no rows matched. The repair reads bounded raw primary candidates (`database.find(..., limit=N)`) before any hydration and validates the raw identity/workspace before scope admission. The old expiry fixture also used unrooted fake workspaces; it now issues actual authorized rooted invitations before moving the expiry into the past. No permissive lifecycle fallback was added. A patch initially matched an earlier duplicate-pending search rather than expiry; it was corrected and inspected before any test run, retaining the original producer query.

Integration-4 passed **96 JSON tests with one explicit PostgreSQL-only rollback skip**, and **99 PostgreSQL tests with no skips/failures/errors**. Actual PostgreSQL tests now write both guest membership and collaboration, inject a post-grant failure and verify neither grant nor accepted state/revision survives. A second independent connection attempts acceptance behind a held revoke, remains waiting until cancellation commits, then observes revoked and no recipient grant. These tests do not prove all source membership/grant mutations use the same fence.

Further source review found the transaction helper defaults to the registered prime store. Lifecycle now explicitly joins the current scoped store's public transaction before taking the workspace fence, so an independently selected internal context stays on its intended store/connection. Integration-5 requalifies that change. Recipient identity-before-workspace fencing, durable terminal receipts/outbox, legacy adoption/migration and complete peer-writer participation remain required. The current audit emits are still in the held operation and are **not** the durable post-commit intent required for closure. No finding/use-case closure, full milestone gate, commit or push is claimed.

Lifecycle integration-5 is terminal exit 0: **99 PostgreSQL cases passed**, zero skips/failures/errors, after explicitly binding the lifecycle transaction to the current scoped store. App-only flake8 passed and configured mypy passed **660 source files**. All test/lint jobs are terminal before the next source-edit batch. Durable receipts, post-commit audit intent, adoption/migration and common identity/source-writer fencing remain the next requirements; the branch has no new milestone commit or push yet.


### M3 durable invitation audit slice

Acceptance/revoke/decline now persist audit intent using the existing `OperationEventOutbox` Object collection in their held PostgreSQL graph transaction. The shared primitive now accepts an explicit namespace, subject and revision; Entry CRUD delegates to it while retaining its exact prior `entry-command-v1` identity format. Invitation facts use `invitation-lifecycle-v1` and the invitation ID, rather than the common target resource ID or an invented default revision. This avoids collapsing distinct invitations to the same target. The invitation stores its outbox ID in the same graph commit.

Normal delivery runs after scope commit; nested callers leave delivery to their outer command/recovery owner. Sink failure is logged as pending delivery and leaves durable intent; it does not pretend the audit reached the sink or undo committed access. The existing resident startup/periodic recovery uses the same outbox consumer. This is reuse of current recovery infrastructure, not a new side-channel logging system. JSON continues immediate nontransactional audit behavior and does not claim durable atomic guarantees.

Audit-1 passed **80 JSON and 99 PostgreSQL tests**. Audit-2 passed **108 PostgreSQL tests**, zero skips/failures/errors, including an actual sink failure followed by successful recovery and idempotent second delivery, an outbox insertion fault that rolls back accepted state plus guest/collaboration grants, current Entry outbox tests and App shared-semantics regressions. Configured app-only flake8 passed and mypy passed **660 source files**. A same-target/different-invitation event-identity test is now running in audit-3. Issuance/expiry/adoption audit, actual grant outcome receipts, original-grant concurrency, complete source-writer/identity/release fencing and owner adoption/legacy migration remain required. No finding/use-case closure, full milestone gate, commit or push is claimed.

Audit-3 completed terminal exit 0: **109 PostgreSQL tests passed**, zero skips/failures/errors. The added actual-store case accepts two separate invitations to the same Track with matching action/revision, and verifies distinct durable fact IDs plus delivery of both facts. All test/lint processes are terminal. The next implementation remains actual grant baseline/version/outcome receipts and protected owner adoption/migration; milestone qualification/commit/push are still pending.


### M3 invitation grant outcomes and current response projection

Issuance now captures the exact observed recipient grant or explicit absence. Acceptance locks an existing PostgreSQL grant row before comparing its captured fingerprint; it upgrades only that unchanged baseline and retains stronger roles/creation flags. An intervening create, change or removal yields conflict without overwriting the newer state. The accepted invitation stores the created/upgraded/unchanged/conflict receipt, persisted direct role/creation flags and effective role at acceptance in the same graph/outbox transaction. Workspace upgrades preserve the membership edge identity rather than destroying creator membership lifetimes. Technical `grant_revision` is typed relationship metadata; introducing zero on a legacy row for locking does not manufacture a role. All ordinary writers still need common revision/fence integration; this is not an ABA or complete peer-write guarantee.

The acceptance endpoints project current effective access independently from that durable historical receipt, including retries after grant removal. They do not recreate removed access on an accepted retry. Existing workspace members may now receive an invitation for an intended rights upgrade. The old existing-member test's expected blanket 400 was the only grant-1 failure on both adapters. It now performs actual HTTP acceptance and verifies unchanged membership/effective role/receipt for the no-op case. This aligns with the plan's requirement to qualify existing members rather than avoiding that scenario.

Grant-2 passed **107 JSON tests with one explicit PostgreSQL rollback skip** and **149 PostgreSQL tests with no skips/failures/errors**, including creator membership binding regressions. New actual-store scenarios cover creation, intended same-edge upgrade, preservation of stronger rights, intervening role/create/removal conflicts, membership lifetime preservation, historical retry without regrant, and missing raw role/baseline/expiry rejection. Raw invitation role/subtype/baseline/expiry are now admitted before hydration defaults; creation flags are validated as stored booleans. The subsequent grant-3 selection requalifies current creation-flag receipt fields. App-only flake8 and configured mypy passed **661 source files**.

The API clients now allow an absent current role and carry the grant receipt. All three acceptance screens use outcome-aware messages and avoid workspace navigation when current access is absent. Three actual rendered invitation-page interaction tests passed for absent access, conflict returning viewer despite requested admin, and unchanged membership. Frontend type checks passed. This is component/HTTP/local-store evidence, not independent-account browser or full frontend-suite qualification. Legacy adoption/migration, complete peer grant/membership revisions, identity/source/effect/release ordering, issuance/expiry/adoption audit, declared delegation and full milestone gates remain required. No broad finding/use-case closure or new commit/push is claimed.

Grant-3 completed terminal exit 0: **107 JSON passes with one PostgreSQL-only skip** and **149 PostgreSQL passes**, zero PostgreSQL skips/failures/errors. Receipt creation flags match persisted membership readback; raw invitation flags are validated without hydration coercion. App-only flake8 passed, configured mypy passed **661 source files**, and frontend types passed after receipt/null-role integration. All processes are terminal before the next source-edit batch. The next requirements remain protected owner adoption and explicit legacy reconciliation, issuance/expiry audit, complete source-writer/identity fencing and full milestone qualification before commit/push.


### M3 explicit owner adoption and issuance durability (working)

Owner review now binds the stored pending invitation, current recipient grant,
canonical target workspace and current owner membership lifetime. Adoption
requires chosen rights and the current review digest, preserves original issuer
provenance, rotates the token, and grants nothing until acceptance. It can
explicitly reconcile missing legacy binding/baseline/revision and an empty
workspace scalar only when the named resource target proves the workspace.
Conflicting edges, terminal invitations and expired records are rejected.
Ordinary acceptance never performs this migration implicitly.

Acceptance and decline compare the supplied token hash after held-scope
admission. The owner UI requires deliberate role selection and clears a stale
review after a failed apply. Issuance now commits rooted state and audit intent
before delivery, propagates catalog failures, and expiry records a system actor.
Nodes returned from completed transactions are rebound to the caller context;
nested commands retain their still-active outer context.

Evidence: `/tmp/integral-invitation-adoption-pg-2.xml` has 161 passes, no skips,
failures or errors. JSON-2 has 122 passes and 3 explicit PostgreSQL-only skips.
Adoption-1 PostgreSQL failed setup with the owned container missing. Adoption-3
exposed closed transaction contexts in returned invitations and two old audit
assumptions; repaired before further qualification. Adoption-4 was interrupted
by Docker WAL storage exhaustion and also required a revision test to compare
against issuance's actual persisted before state. These failed/interrupted
receipts are retained and do not qualify the milestone. Only the owned
disposable database was recreated, on temporary memory storage with bounded
WAL. Adoption-5 is pending. Five frontend interaction tests and the configured
663-file mypy gate passed. No full milestone gate or new commit/push yet.

The whole-goal ledger remains open: current source changes by all peer writers,
identity ordering, ABA/version discipline, inventory/quarantine/restore and
independent-account browser qualification are still required.

Adoption-5 terminal: pg: 169 cases; 0 failures; 0 errors; 0 skips; json: 128 cases; 0 failures; 0 errors; 3 skips. Source binding, issuance fault rollback, expiry intent and partial adoption provenance executed. Current type/docstring corrections proceed to full five-lane milestone gate.

Full-1 was intentionally stopped after Core-only passed and while the extension
lane was active, to resolve I-ROLE-01 duplicated resource-role/rank declarations.
The runtime now consumes canonical sharing.VALID_ROLES and permissions.ROLE_RANK.
Full-2 adds a staged preflight (actual guards/precommit/pinned format/lint/types)
before the five qualification lanes. No pass is inferred from a stopped lane.

Full-2 failed staged UI drift before tests; full-file migration to shared Text
and Surface primitives resolved the invitation panel's pre-existing literals
and the new owner review literal. Full-3 staged guards, actual precommit,
pinned Black/isort/flake8 and configured backend mypy have passed so far;
frontend lint/types and the full five lanes remain in progress. No new commit.

Invariant review for the invitation milestone: I-GRAPH-01 is preserved by
same-unit target/catalog attachment and complete rooted identity admission;
I-GRAPH-02 is preserved by reusing Object-shaped outbox facts; I-ROLE-01 consumes
canonical VALID_ROLES/ROLE_RANK and keeps owner/admin/editor tiers distinct;
workspace target admission retains the access perimeter; I-CRUD-01 keeps graph
mutations in service functions; I-EXT-01/Core-only and the module-boundary/cycle
guards qualify domain-neutral imports. Creation grants remain separate from
protected owner delegation and do not mint ordinary App action authority.
Full-3 preflight and Core-only terminal exit zero; broader lanes remain pending.

Full-3 broad PostgreSQL lane failed only the PolicyAction strict-superset
contract: workspace.invitation_adopt was added to ChangeEventAction without
the policy twin. Corrected. A source release-boundary recheck also found that
nested issuance could deliver inside a still-open outer transaction. Issuance
now refuses that unsupported nesting before creating state or delivering a
token; a real PostgreSQL regression checks it. Composable nested issuance
requires deferred delivery integration and remains open; this constrained
behavior is not whole-goal closure. Full-4 requalification begins next.

Full-4 intentionally stopped in preflight for acceptance audit before-state refinement; the snapshot now precedes grant receipt assignment, with durable before/after regression assertions. Full-5 restarts the gate.


### M3 invitation lifecycle foundation — full-5 qualified partial milestone

All processes are terminal. Actual staged guards/precommit/pinned format/lint/types,
Core-only, extension, PostgreSQL integration, PostgreSQL spikes/contract and full
`make verify` passed. PostgreSQL integration: 976 passes,
zero failures/errors/skips. Extension: 52 passes,
244 explicit skips. Smoke: 1158
passes, 11 explicit skips. Frontend: 1,588 passes. Full
backend: 5481 passes, 313 explicit
skips, zero failures/errors. Receipts use `/tmp/integral-invitation-lifecycle-full-5`.
Wheel SHA-256: `5eb7745d64d8a98ef8fa2281cf80235738e21e8d7a526e3514f6ceb9cb5a17ed`.
Frozen staged runtime/test diff SHA-256: `5f306f1d4ea7319d67878dc91cd50ad07e3b1190a11421e84a9b6d22c07baa46`.

This closes no whole finding or use case. All peer identity/membership/grant
writers need common fences and version discipline; offboarding/reentry blocks,
complete source/audit history and migration/quarantine/current-auth restore
remain open. Nested issuance now denies before effects until deferred outer
commit delivery exists. The owner UI has rendered test coverage, not
independent-account browser qualification. No GitHub CI, deployment or release
claim. Commit/push proceeds on the single authorized branch.


### M3 invitation milestone pushed; member-source foundation in progress

Invitation foundation commit `3b49c29bf8fc24ae5269599d82a78bb3cf6981be` is
pushed on the single authorized branch; a fresh remote read returned that SHA.

Direct membership management now delegates to a current service boundary.
Ordinary administrators cannot assign administrator/creation rights, change
themselves, or edit/remove protected administrator/creator peers. Generic
member APIs cannot change the owner. Updates retain the membership ID and join
time, advance a technical grant revision, and preserve unrelated edge facts.
Duplicate additions fail without replacing existing rights. Self departure
remains distinct and cleans current grants within the shared workspace fence.
The UI matches these ceilings; two rendered interaction cases pass.

PostgreSQL writes and before/after audit intent commit together; postcommit
sink failures retain recoverable intent. Fault-injection covers add, update
and remove rollback. Independent pools qualify demotion versus invitation
acceptance and another member addition. The first race run exposed the pinned
adapter's cold collection bootstrap deadlock on a one-connection pool. The
common transaction seam now prepares Core collections through bounded public
reads before acquisition, once per live adapter. A separate cold-pool test
qualifies this fix; race pools are preinitialized to isolate the authority lock.

HTTP probes showed the endpoint binder coercing strings/numbers despite a
StrictBool annotation. Original JSON is now admitted with a schema; query
rights and supplied nulls are rejected. Service kwargs independently require
actual bools. Creator-curator grants survive legitimate membership edits,
while role round trips advance the captured invitation baseline and conflict.
Invitation upgrades mirror legacy revisions; invitation audit snapshots now
include requested flags, original/adopted membership binding and grant baseline.

Retained focused receipts: focused-1 JSON124/PG128 passes plus two bootstrap
timeouts; focused-2 UI2 passes and JSON188 passes plus eight coercion failures;
focused-3 JSON196/PG204 passes plus one incorrect test return-shape expectation.
Focused-4 passed JSON202/PG211 cases without skips/failures/errors and UI2;
its formatter lane requested one contract-test reformat. Source jobs are terminal
before edits. Full staged/Core-only/extension/PostgreSQL/full-suite gates remain
pending. No new member-source commit or finding/use-case closure yet.

Invariant review: I-GRAPH-01 keeps rooted workspace/resource cleanup; I-GRAPH-02
reuses Object-shaped outbox facts; membership vocabulary remains separate from
I-ROLE-01's canonical resource ladder. I-CRUD-01 removes membership graph writes
from API handlers; I-EXT-01 remains domain-neutral and receives Core-only and
module-boundary qualification. Complete identity/source/release fences, all peer
writers, offboarding blocks, migration/restore and independent browser evidence
remain part of the active whole-goal ledger.


### User-designated live smoke model

The user requires DeepSeek 4.1 Flash Cloud for live smoke testing. The current
local Ollama inventory reports `deepseek-v4.1-flash:cloud`, remote model
`deepseek-v4.1-flash` at `https://ollama.com`. An actual request through the
current Core LiteLLM/Pydantic model transport returned `READY` on
`ollama_chat/deepseek-v4.1-flash:cloud`; its physical request observations are
retained at `/tmp/integral-access-deepseek-transport-smoke.json`. No workspace
data or tool was supplied in that transport check. It does not establish native
conversation persistence, workspace authorization, browser behavior or complete
ACL qualification. A synthetic native tool/readback smoke is prepared as a
separate opt-in lane. Deterministic pytest smoke contracts continue alongside
real cloud smoke; their passing counts cannot be attributed to model inference.

The separate native smoke now passed on isolated PostgreSQL: **1 passed,
zero skips/failures/errors**. Real DeepSeek Flash Cloud calls completed capability
search and `integral_get_app`, returned the exact owned synthetic App name,
finished the native turn and persisted an API-readable transcript. Physical
model steps identify the requested route. The first JSON attempt correctly
refused nontransactional session activation; the first PostgreSQL attempt passed
its body but failed teardown on LiteLLM tokenizer environment mutation. The
repaired fixture contained that setting and the complete rerun passed.
[Sanitized receipt](deepseek-native-smoke.json) records source attribution,
model steps and evidence digests. This in-process authenticated fixture does
not qualify independent-account browser behavior, all ACL cases or migrations.
The membership full repository gate remains live; no new commit is claimed.


### Next producer convergence: grant history remains open

Two current rooted JSON probes independently confirm remaining AC-21 stale
baseline defects. `viewer → editor → viewer` preserves the collaborator edge
identity, leaves `grant_revision=0`, and restores the exact baseline fingerprint.
Workspace membership `absent → member → absent` returns the identical
`absent-v1` baseline after actual canonical add/remove calls. Failed security
assertions are retained at `/tmp/integral-collaborator-revision-gap-2.xml` and
`/tmp/integral-absent-membership-baseline-gap.xml`; neither is counted as a pass.
The current membership milestone preserves existing edge revisions but does
not yet preserve negative history after deletion. All invitation, membership,
collaboration, share, departure and ownership writers need a common versioned
source history and fence. Suspension and re-entry policy remain separate
explicit controls; history alone does not manufacture a permanent deny.

The next relationship-clock helper was prepared outside the frozen checkout
at `/tmp/integral-grant-history-next/` with candidate typed membership and
collaboration history edges. Independent candidate tests passed **13 JSON
cases with one explicit PostgreSQL-only skip** and **14 PostgreSQL cases with
zero skips/failures/errors**, including joint grant/history rollback. They
check absent/create/remove/recreate, role roundtrips, no-op stability, malformed
clock denial, recipient/target binding and untracked-write denial. Logs/XML:
`/tmp/integral-grant-history-candidate-json-2.{log,xml}` and
`/tmp/integral-grant-history-candidate-pg.{log,xml}`. These are helper candidate
proofs, not integrated writer or complete invitation qualification. Apply and
qualify the helper and all producers after the current frozen membership gate
and milestone commit; preserve the failing current-source probes above.


### M3 membership source foundation qualified for milestone commit

The frozen membership source completed all six lanes with exit 0. Preflight
staged guards, actual precommit, pinned formatting/lint and types passed;
Core-only passed; extension passed 52 with 244 explicit PostgreSQL-only skips;
the focused real PostgreSQL selection passed all 1,098 cases with no skips;
and broader PostgreSQL spikes/contracts passed. `make verify` passed the
CI-faithful smoke (1,211 passed / 11 skipped), all 1,590 frontend cases in 282
files and full backend suite (5,534 passed / 320 skipped), with no failures or
errors. The reproducible wheel/import gate passed with SHA-256
`b7339a64439a1ca2062b627d0b348b57f56aa9b18463ded6730c30333a47f732`.
Logs, terminal coordinator status and XML remain under
`/tmp/integral-membership-full-2*`. Full-1 stopped on an unused import after
passing guards; that import was removed before this complete passing run.
The failed preflight record remains retained.

The separate designated DeepSeek native smoke passed as recorded above.
These results qualify this participating source-writer foundation for the
authorized milestone commit/push. They do not close AC-09/21, other findings
or U01–U76: negative source history, complete producer convergence, explicit
delegation, identity/release fencing, suspension, migration/restore and
independent-account browser/MCP remain required. The next implementation is
the qualified relationship-clock candidate and all producer cutovers, including
legacy notification acceptance and provision/account lifecycle paths found in
the producer inventory. No PR, release or deployment is authorized here.


The membership source foundation was committed and pushed as
`05a8d0280e34fa65e9de354865d5f430c5cd349c`. Remote branch readback verified
that exact commit on `codex/access-control-review`; final staged guards and
the actual commit hook passed. No additional finding or use case was closed.

### Integrated relationship history under qualification

Typed membership/collaboration history edges now retain a clock after actual
grant deletion. Versioned invitation baselines bind the recipient, target,
grant family, complete current grant and persistent clock. Legacy baselines
cannot prove unchanged history and require explicit adoption/reissue.
Participating membership, invitation, collaboration and departure writers
advance these clocks; collaboration mutations join the workspace fence and
record PostgreSQL audit intent alongside the actual effect. Existing adds and
exact role no-ops report stored rights without replacing or inventing rights.
Untracked mutations or malformed clocks deny until governed recovery.

Source-1 exposed a matching legacy technical revision mirror changing a grant
fingerprint; normalization now accepts only an exact typed matching mirror.
Source-1 recorded 153 passes, two explicit skips and one failure. Source-2
passed its narrower membership/invitation/helper selection: 157 JSON passes
with two explicit PostgreSQL-only skips, and all 180 PostgreSQL cases. Source-3 stopped with a collection import cycle introduced by the
sharing scope. Delaying the membership scope import until admission removes
that cycle. Source-3 retained 235 passes, one explicit skip and one collection
error. Source-4 passed 255 JSON cases with two explicit PostgreSQL-only skips;
all 278 PostgreSQL cases also passed, with no skips, failures or errors. Failed logs/XML remain retained under their
original `/tmp/integral-grant-history-source-*` prefixes. No complete current
source qualification or new milestone commit is claimed.

All producer convergence, independently revocable source provenance, identity
fences, suspension/reentry, full history audit payloads, migration/restore and
independent-account browser/MCP remain open. Share-link redemption, legacy
notification acceptance and provisioning still need canonical cutovers. The
relationship clocks themselves never grant access. JSON provides local
ordering only, with no distributed or transactional rollback guarantee.


The additional PostgreSQL producer lane passed **35 cases with zero skips,
failures or errors** (`/tmp/integral-grant-history-producer-pg-1.{log,xml}`).
Actual add/update/remove failures at durable audit intent roll back the grant,
its history clock, guest membership and notification; sink failure preserves
committed rights with a pending fact. Independent database pools prove a
collaborator-administrator revocation orders subsequent peer grant admission.
Departure now verifies raw grant deletion before advancing negative history;
full malformed-target inventory/cascade reconciliation remains open.

The designated DeepSeek native smoke is rerunning with an allowed owned-App
read and a private foreign-App denial, plus transcript readback. Full current
source qualification remains pending; no new milestone commit is claimed.


### AC-22 discovered by designated native cloud smoke

Both actual cloud smoke attempts completed the owned read and denied the foreign
App tool read, then failed the secure assertion because SSE disclosed the exact
private name. Transport inspection found no private marker in the model request
bodies. Host ID humanization, including streamed prose and persistence, loaded
labels globally without recipient admission. AC-22 is now included in the review,
plan and closure ledger; its sanitized failed evidence is in
[output-label disclosure](output_label_disclosure.json).

The repair binds current identity/workspace/read authority to known label
families and leaves unsupported or denied metadata opaque. Model labels use
only their admitted own metadata. The first compatibility run recorded six
interruption failures because its synthetic context has no identity attributes;
that known-failed lane was interrupted, with its partial receipt retained.
Unknown identity now produces an opaque fallback. Revised JSON/PostgreSQL
contracts and real DeepSeek allow/deny smoke are running; no current full
qualification or additional finding closure is claimed.


The revised focused label/stream/persistence/staging selection passed **56 JSON
and 56 PostgreSQL cases**, with no skips, failures or errors. Actual designated
DeepSeek native smoke passed **one complete case**, including the owned read,
foreign private-App forbidden result, absence of the private marker in SSE and
persisted transcript API readback. [Fresh sanitized cloud receipt](deepseek-output-label-smoke.json)
identifies the current runtime diff and physical model steps. This establishes
the in-process fixture result, not independent-account browser/production or
complete output release fencing. Full staged repository qualification is now
running under `/tmp/integral-grant-history-full-1*`; source is frozen.


The Full-1 focused PostgreSQL selection passed with zero skips/failures/errors;
exact XML counts are retained in the milestone ledger. Broader PostgreSQL
spikes/contracts and `make verify` remain pending/running.

A separate authenticated HTTP security probe confirms the unchanged legacy
notification response path: a synthetic delivered `type=invite` notification
containing workspace/role metadata but no canonical invitation returns 200 and
creates `IS_MEMBER_OF{role=admin}`. The required no-grant assertion failed;
[retained receipt](legacy_notification_grant_gap.json) is characterization, not
a pass. HTTP notification creation is platform-admin-only; no ordinary-user
notification forgery is claimed. This strengthens the existing all-producer
convergence obligation. Current `create_notification` already normalizes a
linked recipient to its AuthUser subject; preserve that behavior rather than
assuming an inbox alias defect. Canonical invitation notifications use
`type=invitation` with the ID nested in `metadata.payload`, so response handling
needs the actual canonical shape and verified current invitation service.
No full finding closure is claimed by the pending foundation milestone.

The second legacy-notification HTTP characterization explicitly checked `verified_current_email(recipient)` returned no proof before acceptance. The route still returned 200 and materialized workspace-admin membership without any canonical Invitation. Both failed probes and their source digests are retained in `legacy_notification_grant_gap.json`; this strengthens the mailbox-bypass evidence without asserting that ordinary users can forge privileged notifications.

Retained the stronger legacy notification HTTP probe under `probes/test_legacy_notification_grant_gap.py`, so the regression scenario is reviewable beyond the temporary-run files. The runtime/test source under full qualification remains unchanged. The passive surface inventory was refreshed to 386 REST and 45 facade declarations; all surface qualification fields remain unmapped.

The current-source CI-faithful smoke receipt contains 1,235 passes and 12 explicit skips, with zero failures/errors (`/tmp/integral-grant-history-full-1-smoke.xml`). The frontend suite passed 1,590 tests across 282 files. The owned full qualification coordinator remains live in its full backend lane; neither smoke nor frontend completion substitutes for that remaining gate.

Full-1 is terminal and passed all six lanes. Exact current-source results: backend 5,557 passed /326 skipped, CI-faithful smoke 1,235 passed /12 skipped, frontend 1,590 passed across 282 files, focused PostgreSQL 1,180 passed with no skips, extension 52 passed /244 explicit PostgreSQL skips; all failure/error counts zero. Core-only and broader PostgreSQL spikes/contracts also passed. Reproducible and verified wheel SHA-256: `8b0e48968ca5bc2cd4150f09554fb4e1576a7fe4e3cbfb8287a98cf19366dc03`. Ledger records exact receipt digests and this milestone as qualified_partial; all stated whole-goal gaps remain open.

Milestone committed as `c7c51216aa0278f61e102f9ec9995bf382dfb866` after final staged guards/precommit and the actual commit hook passed. Push to the singular `codex/access-control-review` branch exited 0; independent `git ls-remote` readback matched that exact SHA. No PR, merge, deployment or release was performed. All 22 findings and 76 use cases retain their individual closure states.

Canonical invitation notification cutover is now implemented in the working tree, unqualified. It preserves I-GRAPH-01 rooted Notification recipients (no new Node type), I-GRAPH-02 durable audit Objects, I-CRUD-01 service-owned writes, and I-SUBSTRATE-01/I-EXT-01 generic Core behavior. The endpoint requires explicit JSON action/revision and calls the existing canonical invitation materializer under lifecycle admission. PostgreSQL includes notification image admission and response audit intent in the grant unit; JSON remains weaker. Source-1 collected zero tests due a nonexistent test filename (exit5); corrected source-2 selection is live.

Source-2 notification selection passed 138 JSON tests /1 explicit PostgreSQL-only skip and all 139 PostgreSQL tests with zero skips/errors/failures. Expanded source-3 retained four PostgreSQL setup failures: the raw-store fixture skipped ordinary graph shell seeding, so saved View admission failed before notification response ran. The new contract fixture now explicitly ensures the rooted shell rather than depending on order. Lint-1 retained an unused removed-route import and missing public service docstring; both fixed only after terminal checks. Source-4 is running.

Source-4 root seeding resolved saved-View setup, then four producer cases stopped on a fixture AuthUser by-id lookup returning None. The fixture now uses canonical `current_account_email(recipient)` and verifies it matches the issued invitation. Producer PostgreSQL-1 passed all four cases with zero skips/errors/failures: delivery-save and response-outbox faults roll back grant/history/terminal/notification; a sink failure retains committed rights plus pending response fact; another connection held in revoke orders the response, which denies after revoke commits. Raw-store fixture has no registered logging DB; these are durable-intent and injected-sink proofs, not independent persisted-log qualification. Pinned format/lint-2 passed. Source-5 adds paired corrupt-recipient-topology HTTP cases and remains running.

Expanded source-5 passed JSON; PostgreSQL retained one fixture insertion failure: its source/target/entity unique constraint prevents duplicate HAS_NOTIFICATION edges. Corrected negative coverage asserts this actual PostgreSQL prevention without weakening the index, and still constructs a legacy duplicate in JSON to prove HTTP fail-closed admission. Newly adopted delivery actor identity now uses the adopting owner rather than the obsolete original issuer. Source-6 is running.

Source-6 passed 144 JSON tests /5 explicit PostgreSQL-only skips and all 149 PostgreSQL tests, zero errors/failures. Designated DeepSeek collaboration smoke-2 passed one real cloud/native PostgreSQL test: registered recipient proved current email through the OTP HTTP flow, accepted a private App viewer invitation through its canonical notification, then read the private App with integral_get_app; the same recipient was denied a foreign private App without name leakage in SSE/transcript. Five distinct physical model receipts use only ollama_chat/deepseek-v4.1-flash:cloud. Sanitized receipt: `deepseek-notification-collaboration-smoke.json`. Smoke-1 stopped before inference because external test configuration lacked async fixture mode; repository pytest configuration fixes it. Focused qualification is not a complete milestone or finding closure.

Full notification-cutover qualification coordinator started under `/tmp/integral-notification-cutover-full-1.status.json`; exact owned session 89074. Runtime/tests frozen. A conservative static writer inventory now records 45 candidates and exposes remaining source-birth, member provision, model creation and ownership/Track writers for the next convergence work. Dynamic families (including share-link helper dispatch) and raw lifecycle/import/restore paths still need semantic expansion; no exhaustive writer or closure claim.

Next-producer characterization retained: actual member-provision HTTP route fails before grants on an import of removed `_validate_member_role`. First probe stopped on client reuse; corrected probe reached that runtime import failure. Direct-service probe after actual owner/admin/account HTTP setup produced two secure-assertion failures with zero setup errors: owner email-based provisioning binds an existing unverified account and grants admin; an authenticated workspace admin can confer admin to a separately OTP-verified recipient. This is service evidence, not a functioning HTTP privilege bypass. `member_provision_characterization.json` and retained probe source distinguish the boundaries. Repair must converge route and service together, not merely restore the obsolete helper. Full notification gate remains live; runtime/tests still frozen.


### Delivery reset resumed — 11 October 2026

Saved ACCESS_CONTROL_DELIVERY_PLAN.md and delivery_backlog.json: primary ownership covers all 76 use cases exactly once. Corrected unrelated provision evidence in AC-02 and stale qualification wording in AC-07/AC-22; historical failure receipts remain historical. Closure counts remain 1/22 and 0/76, not an estimate of implemented code.

Provision route and service now share canonical membership admission. Existing email-matched accounts require exact current proof; admin delegation cannot confer admin; new profiles remain unverified. PostgreSQL account/profile/personal-workspace/grant/audit effects join one unit; credentials follow successful commit. Focused selection: 98 JSON passes/1 PostgreSQL-only skip; 99 PostgreSQL passes/zero skips/failures/errors. Retained first fixture failure; corrected AsyncClient lifecycle before acceptance. Independent disable ordering and full milestone qualification remain pending.


R1 first checkpoint qualified: make verify passed (5597 backend passes/332 explicit skips; 1275 CI-faithful smoke passes/13 skips; frontend 1590 passes/282 files). Core-only passed; extension 52 passes/249 explicit PG skips. Wheel SHA-256 1fdadcd7c65e2c0304ef9519816510bcedec206456bac85beafdbf7be24cb369. Independent PostgreSQL recipient-disable ordering passed; supplemental rollback asserts absence of identity/profile/recipient edges/personal workspace/audit intent and no credential delivery. Runtime remained unchanged after full qualification; only assertions in the PostgreSQL-only skipped test body were strengthened and rerun on PostgreSQL. Existing designated DeepSeek notification/private-App smoke reused for its unchanged native boundary; not a fresh wheel/provider smoke. Whole project remains open.

AC-16 closed by exact committed-source regression and prior full-suite receipt; total finding closure is 2/22. No use-case closure inferred from suite counts. The first failed provision run was a client lifecycle fixture error; the first ordering probe lacked explicit raw-store rooted seeding. Both failures are retained separately from acceptance.
