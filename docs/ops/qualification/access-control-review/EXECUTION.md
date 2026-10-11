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
