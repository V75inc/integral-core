# Integral Core access-control gap-closure plan

Date: 10 October 2026; refined after a second source/probe review. Status: authorized implementation in progress on `codex/access-control-review`; individual gates remain open until evidenced. Baseline: `9d13b44e`. [Assessment and use cases](ACCESS_CONTROL_REVIEW.md). [Evidence](../ops/qualification/access-control-review/README.md).

## Intended outcome

A workspace owner can govern all canonical workspace resources. Users and groups can receive narrowly scoped resource and App-function authority. Human UI, REST, resident AI, MCP, App operations, public surfaces and deferred work enforce the same contract. Revocation and failure cannot restore stale authority. Every access change has an explainable, durable outcome.

This is substrate work. Keep domain-specific role names, business transitions and commercial entitlements outside Core. Core supplies typed declarations, bounded conditions, rooted grants/groups, authorization and lifecycle machinery. Do not replace the current graph with a separate account/ACL store or make a second policy engine for Apps.

## Execution structure and dependencies

| Package | Ownership / deliverable | Depends on | Required completion evidence |
|---|---|---|---|
| W0 Contract and fixtures | Access specification, invariants, golden scenario fixture, endpoint/action inventory | None | Signed-off owner/governance/privacy contract; U01–U76 mapped to named assertions and surfaces |
| W1 Immediate perimeter fixes | Invitation mailbox proof, event payload authorization, policy memoization, denial lookup failure, avatar/legacy-directory gates, replay-result disclosure and legacy-module disposition | W0 authority-neutral subset can start immediately | AC-01–04, AC-15–16, AC-20 paired allow/deny tests; AC-19 removed/unreachable or repaired disposition; HTTP by-ID invite proof; excluded Entry withheld from live event, polling and audit |
| W2 Canonical scope and live authority | Resolver, all ToolContext point/read/write methods, process/request cache fencing, typed scope/resource contracts | W0; integrates W1 | AC-05–06, AC-12; all-worker revocation/effect tests; foreign workspace facade rejected; malformed containment denied |
| W3 Governance and membership lifecycle | Workspace owner governance, delegation ceilings, canonical membership services, owner recovery/transfer | W0, W2 | AC-08–09, AC-18; full owner/member/private-resource matrix; self-grant/peer-owner changes denied; atomic ownership proof |
| W4 Durable grants/invitations/shares | Versioned grant sources, atomic invite/link lifecycle, suspension/offboarding, canonical ownership transfers and receipts | W0, W2; integrates W3 | AC-07, AC-10–11, AC-17, AC-21; real PostgreSQL races and fault/replay tests; no unscoped resources or revived suspended membership |
| W5 Declarative App actions and groups | Versioned action schema/compiler, principal/group bindings, bounded conditions, function-level perimeter | W0, W2, W3 | AC-13–14; two independent reference Apps; contributor cannot approve; generic PATCH cannot bypass protected transition |
| W6 Collaboration UX and administration | Owner access center, invite flow, effective-permission preview, authoritative capabilities, audit/review UI | W1–W5 contracts | Browser acceptance with independent accounts; accurate enabled/disabled actions and explanations; paginated complete inventory |
| W7 Migration and qualification | Access diff/migration, clean artifact checks, cross-surface security acceptance and operator runbook | W1–W6 | Current `make verify`, CI reproduction, PostgreSQL multiprocess lane, clean wheel/Core-only/contract gates, browser/MCP receipts |

Give each coding work package explicit file/module ownership. Packages may be implemented in dependency waves, but resolver/policy/schema/manifest changes must have one integrating owner; avoid simultaneous divergent definitions of action precedence. Close W1, W2 and the invitation/concurrency security slice of W4 before G0; do not defer P0 invitation repairs behind the owner-governance rollout. Non-expanding repairs can proceed while migration semantics are ratified. W1 should land before expanding authority. No automatic merge, push or deployment is implied by this plan.

## W0 — Ratify one access contract

Resolve the deliberate change to I-ROLE-02: inherited App owner/admin remains capped for ordinary delegation; protected workspace-owner governance is a separate path. Update I-ROLE-01 author/viewer behavior and I-ROLE-03 wording (“commenter default” is not strict default-deny). Preserve I-ACCESS-01's resource privacy contract unless field-level access becomes a separately reviewed project.

Define owner stewardship versus collaborator roles; protected single workspace owner; ordinary administrator ceilings; direct/inherited allow precedence; inherited exclusion versus absolute block; voluntary leave versus administrative removal/suspension; guest directory; anonymous sharing policy and its inability to enforce identity-specific suspension; canonical workspace data versus privately addressed sessions; link revoke/expiry semantics. Enumerate each affected invariant and its before/after examples.

Create a golden rooted graph: W-A/W-B, workspace owner, two creators, delegated workspace admin with no creation flags, App admin, Track admin, editor/commenter/viewer, guest, outsider, disabled principal, verified/unverified invitees, both principal ID forms. Include private/workspace/public/inherit resources, standalone/contained Tracks, Entry exclusions, authorized/restricted relations, source-provenance grants and two App versions. Record the complete HTTP/tool/action/exposure inventory and map U01–U76 to it.

Specify the permission-composition table in the review as executable examples: App roles intersect resource audiences, named transitions can write only their declared state without granting generic edit, owner governance remains separate from business-action execution, and accepted grants have explicit durable/dependent lifetimes. Start with flat workspace groups. Personal workspace ownership is not transferable through the collaborative workspace transfer flow. Define how security attributes are protected from generic update/import/bulk APIs.

Exit: the matrix defines expected outcomes; no implementation silently chooses a different role meaning. Annotate each scenario as current defect, existing intended behavior, target change or unqualified surface. A changed expectation needs a contract decision, not a test renamed until it passes.

### Invariant impact to carry into implementation plans

| Invariant | Disposition and required preservation |
|---|---|
| I-ROLE-01/02/03 | Deliberately amend viewer/author behavior, owner governance and precedence; preserve ordinary inherited-role limits unless an explicit scoped governance grant applies; name creation defaults accurately |
| I-ACCESS-01 | Preserve resource privacy; named App actions and protected state are not field-level read ACL |
| I-GRAPH-01/02 | Root new groups/grants beneath their workspace/App in the same unit of work; keep genuinely non-graph receipts as Objects; use walkers for multihop resolution or document measured bulk-query deviations |
| I-CRUD-01 | One canonical service per persisted mutation; HTTP, tools, imports and named actions use it |
| I-APP-DEF-01, I-APP-01/02/04/05/06/07 | Extend the existing active ApplicationDefinition and compiler/install path; preserve compensation, dependency gates, same-workspace references and signup-created personal workspaces |
| I-SUBSTRATE-01, I-EXT-01, I-HOOK-01/02 | Domain-neutral Core, no domain imports, declared hook dispatch and scoped facade; reference Apps stay outside Core |
| I-APPROVAL-01/02/03 | Preserve narrowly guarded re-entry, original actor and actual outcome; reviewed execution never becomes blanket authorization; integrate policy/audit action declarations |
| I-WORK-01–06, I-HARNESS-01 | Preserve lease fences, deterministic effect identity, transactional work/outbox, current-grant admission, production-store posture and tenant-bound resident sessions; a lease fence does not replace an authorization epoch |
| I-RET-01/02, I-APP-03 | Current permission filtering survives retrieval/index/cache paths; restricted relation stubs reveal no unauthorized label/content |
| I-TEST-01 | Every changed boundary includes success, denied and invalid cases; concurrent paths additionally require ordered race/fault evidence |

These are proposed impacts, not amendments to `docs/INVARIANTS.md` in this review. W0 records exact normative before/after text before implementing semantic changes. Extend existing contract tests rather than creating a competing interpretation in the review probes.

## W1 — Close immediate security defects

1. Protect `/me/invitations` and by-ID accept/decline with current verified-email proof. A valid token can authorize that specific invitation in a separately defined ceremony; the issuer also receives the URL, so possession must not automatically mark the account email verified; mere database `invited_user_id` or matching email cannot authorize acceptance. Test preclaimed signup, changed email, missing proof, valid token and normal verified acceptance. Do not gate ordinary login on verification.
2. Authorize event/audit/poll/notification payloads against the affected resource and authorized referenced contents, not only subscription scope. Give events explicit workspace/resource/audience facts. Define creation/deletion/permission-change visibility and avoid sending before-snapshots that expose now-hidden content. Ensure internal consumers retaining broader authority do not relay it to users.
3. Replace incomplete policy cache identity; isolate every event/exposure/effect boundary. Avoid memoizing approval creation and payload-sensitive effects. Test two equal resource IDs with different scope/tags/types/payloads and multiple events in one batch. Existing event tests that mock `policy_evaluate` do not exercise the real cache.
4. Propagate indeterminate exclusion/containment failures as denied/unavailable and audit the reason. A failed read must not become “no deny.” Test failures independently in edge lookup, parent resolution and membership lookup.
5. Repair the avatar membership truthiness gate. For AC-19 first decide whether the import-broken generated-document helper is a supported surface: remove/reconcile inactive code or prove it unreachable; if retaining/exposing it, repair imports and enforce row/source authorization even without a context filter. Do not bring dead functionality online just to repair it. Route directory surfaces through the ratified administration/mention-directory contract with minimal public User projections.
6. Reauthorize idempotent read results and durable command response replay against their actual record dependencies. Preserve immutable internal receipts and exactly-once local effects; generate an authorized response projection or withhold content. Arbitrary cached handler output without sufficient provenance must be withheld conservatively after relevant authority changes. Test warm memory, durable storage, deleted/excluded records, App-definition changes and retries with both old and new keys. An unchanged App-level decision does not authorize old Entry payloads.

Exit: reproduce each defect first, convert it to a passing requirement regression after repair, and retain authorized-path tests so hardening does not disable collaboration. No production-wide security claim from green unit tests alone.

## W2 — Canonical scope, action evaluation and revocation

Resolve a resource's structural workspace through canonical services. Validate scalar workspace/parent fast paths against rooted ownership; reject missing/multiple/inconsistent anchors. Introduce typed workspace ResourceKind/scope representations instead of mixed `org:`, `workspace:`, `app:` aliases escaping schema validation. Keep canonical HTTP `ws:<id>` parsing and deliberate omitted-personal scope behavior.

Audit every facade accessor, including `get/get_entry`, attachment methods, system/hook reads, related entries, query, labels and cross-App calls. Require bound workspace and any declared cross-App reference/operation permission; check the actual resource workspace, not only the principal's union of accessible resources. A point-return must never label foreign data with the caller context's workspace.

Begin with a persisted monotonic workspace authorization epoch, read from the authoritative store; include principal lifecycle and active App-definition revisions. Every relevant grant, membership, block, containment and policy mutation advances the appropriate epoch atomically. Optimize to narrower resource/grant lineage only after dependency-completeness tests and measurements. The current decision fingerprint is not this epoch. Persisted revisions must invalidate all replicas. Local caches may store policy facts keyed by revision; effects and exposure must validate current revision. Do not rely solely on pub/sub delivery or a TTL to bound privileged revocation. Define the linearization point: revocation and an effect serialize through a shared storage fence, with authorization checked after acquiring that fence and before commit; operations committed before revocation remain audited, while later operations cannot use the old authority. External effects already dispatched need outcome reconciliation.

Until durable fencing is qualified, bypass stale caches at sensitive effect/exposure boundaries and ensure every transfer, visibility, lifecycle, parent, group, membership and permission mutation invalidates graph/auth aliases and request/process aggregates. This mitigation does not prove a distributed revocation guarantee.

Exit: two worker processes warm old roles, one revokes/changes scope, the other cannot perform a subsequent privileged effect or expose prohibited data. Test in-flight permission reads, session/routine resumption and duplicate approvals. Document exposure/revocation semantics for long-running streams and external requests: the server release point precedes transport delivery; already released bytes cannot be recalled. Qualify attachment URLs/CDN caching separately with an explicit maximum stale-access bound. Partitioned workers must fail closed when current authority cannot be established.

## W3 — Owner governance and safe membership administration

Implement a governance decision carrying workspace owner/delegated source, capability, target scope and revision. Owner governance is protected, live and workspace-bound; do not duplicate resource OWNS edges for it. Distinguish governance from resource curator/admin roles and from App business functions. The owner can inventory and reclaim member-created private resources without deleting the workspace or removing the member as a workaround.

Move membership writes behind canonical services. Validate caller and subject, owner/peer protections, administrative grant ceilings, creation rights, active/suspended state and version. An administrator cannot raise its own privileges merely because it can edit membership settings. Enforce membership uniqueness and canonical owner uniqueness at storage level. Detect duplicate/corrupt owner edges and fail closed into an audited repair flow; do not pick arbitrary first rows.

Implement collaborative workspace owner transfer with verified eligible destination, explicit acceptance/reauthentication, one atomic ownership transition, protected last-owner rules, audit and replayable receipt. Resource steward transfer/reclaim has separate semantics; deny transferring an auto-created personal workspace through this flow. Owner recovery uses documented operational authority, not client-selected owner fields.

Exit: owner governs data created by another member across Apps/Tracks/Entries; creator-private and exclusion paths cannot disable owner governance. Ordinary admins retain only expressly delegated capabilities. Cross-workspace and personal-credential boundaries remain intact.

## W4 — Durable grant and invitation lifecycle

Use typed associative edge metadata for grant source, issuer, effective/expiry timestamps, delegation ceiling and revision. Where multiple independently revocable sources can grant the same user/resource/action, represent them explicitly in rooted grant/relationship structures with an effective projection; do not collapse all sources into one untraceable COLLABORATES_ON row. Existing direct grants migrate as explicit legacy sources. Every new Node needs same-unit rooted attachment under I-GRAPH-01; use Object only for scoped non-graph receipts/logs under I-GRAPH-02.

Invitation acceptance atomically validates state/expiry, recipient proof, live issuer or adopted owner authority, resource/workspace identity, current policy and target membership state. It claims pending revision, creates/upgrades only the intended grant and commits the terminal receipt/audit intent. An old invitation must not overwrite a newer role. Use explicit grant outcomes (created, upgraded, unchanged, conflict) and return the actual effective result. `ensure_edge` currently returns existing attributes unchanged; replacing it with an unconditional update would trade a false receipt for a stale-role overwrite. Qualify both existing-resource collaborators and existing workspace members. Revoke/accept/concurrent accept tests use real independent PostgreSQL connections and barriers.

Bind collaborator links to intended audience and workspace sharing policy. Owner may limit external sharing, public exposure, grant ceilings, expiry and domains/email where required. Token reservation, grant, receipt and audit intent commit together; compensate/reconcile external delivery separately. Repeated redemption must not consume capacity without providing a new intended grant. Recheck live link state at the final grant boundary; a reservation alone is not authorization.

Expose separate operations: disable future link redemption; revoke grants originating from that link; revoke one resource grant; block one resource audience; suspend workspace member. Removing one source reports the remaining effective authority. Accepted ordinary collaboration grants remain workspace-owned after their issuer leaves unless explicitly marked dependent; pending invitations and service subdelegations have different live-issuer rules. Preserve grant-source history and don't remove unrelated grants. Expired tokens and expired grants are separate concepts, visible in UI and audit.

Offboarding cancels/adopts pending grants, removes authority, reassigns canonical stewardship, fences deferred approvals/routines/connectors and rejects old credentials under a member lifecycle block. Restore suspended membership only via explicit owner action, reviewing any dependent grants. Suspension cannot identify a visitor to anonymous public content; disable that exposure or require authenticated sharing where exclusion is required. Preserve other workspaces and personal data. Distinguish suspension from voluntary leave/reinvitation.

App/Track transfers join a graph transaction with uniqueness and current-owner CAS, new-owner membership eligibility, scalar-cache synchronization, authorization revision and durable receipt/audit intent. On unsupported stores, document constrained fallback; never advertise production-strength atomicity that the store lacks.

Entry/Track moves, copies and bulk imports require source and destination action checks, protected security fields, explicit handling of grant provenance and a before/after audience diff. Moving cannot clear a block without corresponding governance authority.

Replace workspace unlink-and-clear deletion with explicit archive/delete/rehome semantics. Rehome requires authorized destination ownership and a complete access/relationship diff. Failed deletion cannot report success or leave unscoped authority.

Exit: invitation/link/transfer/offboarding races, interrupted writes, post-write audit failure, process restart and replay have one recoverable outcome; authority never floats unowned/unscoped or resurrects unexpectedly.

## W5 — Declarative functions without authority amplification

Version the App action contract: immutable installed App instance, active definition revision, operation key, namespaced action, effect classification, required resource types, role bindings and bounded condition schema. Action names are not arbitrary strings that default into a generic admin branch. Unknown actions deny. A manifest `capability` label becomes enforceable only through this contract.

Workspace owner binds users/groups to App roles; grant administration obeys the same action/ceiling contract. Root groups and membership relationships beneath their workspace and propagate membership changes into authorization revisions. Use schema-validated subject/resource/action/state attributes and a small condition language. Start with flat groups; evaluate each complete grant with its own conditions before union, then mandatory conditions/blocks. Never combine the action from one constrained grant with the scope of another. Core never imports App domain roles or hardcodes a business workflow.

For identified business execution, actor authority = live human/service delegation ∩ resource audience eligibility ∩ App action grant ∩ current typed conditions ∩ platform constraints. Public execution uses its declared token audience; owner governance uses the separate protected governance path. A transition-specific writer need not receive generic editor authority. Credential use versus secret export are separate actions, and shared workspace service delegation must be explicitly owned/adopted rather than silently tied to a departed human. Owning a connector/agent permits administering that subject, not granting it unrestricted access to foreign targets. Wildcard scope/actions require explicit authorized meaning and cannot automatically cover future sensitive actions. Human, agent and connector paths share this contract; distinguish policy administration from the act being authorized.

Extend the existing protected-field guards and transaction/receipt/outbox machinery; do not introduce parallel infrastructure. Replace the ambient operation-write bypass with a server-created, non-client-forgeable authorization context bound to the exact App definition, action, targets and allowed state mutations. Check it in canonical mutation services and nested effects. Preserve generic CRUD denial outside that scope. Test an unrelated allowed operation trying to mutate another action's protected fields, top-level state, deletion and referenced records. Return authoritative per-operation decisions to UI/tool catalogues, but recheck on invocation and every internal target effect. Owner may manage role bindings but platform safety and declared separation-of-duty requirements still apply to execution unless an explicit reviewed override action exists. Definition-change governance must be stated separately: an owner permitted to replace those rules is not constrained against malicious rule replacement by the action checker alone. In-process trusted executable Apps remain trusted host code; the facade is not a sandbox.

Compile/publish action and protected-state declarations as one versioned definition. Workers acknowledge the active definition or deny; mixed old/new workers cannot silently authorize different actions. Bind queued operations, approvals and receipts to definition identity, while checking current authorization at resume/replay. Installation, update, uninstall and rollback have explicit stale-binding/public-link dispositions.

Keep resource privacy in I-ACCESS-01. Relations/anchors can join differently governed data with restricted stubs; queries, summaries and exports must apply both resource and App action contracts. Implement no cosmetic field hiding as authorization.

Exit: two external reference Apps outside Core exercise different role vocabularies, author/state conditions, separation of duty, groups, protected transition fields, sibling references and version upgrades. Demonstrate equivalent results through REST, UI, resident/MCP, routine and approved execution. Reject a subject-owner wildcard policy to another workspace even when it matches syntactically.

## W6 — A collaboration system users can understand

Build one owner access center with workspace members/groups, invitations, Apps/Tracks, function roles, links/public audiences, suspensions and complete governance audit. Resource-level access dialogs consume the same effective-decision representation.

The UI must show: direct/inherited/governance/token/group grant sources; actual effective role/action; remaining access after removal; the audience effect of private/inherit/public; grant ceiling and expiry; safe disabled action reason (full grant-path details only for authorized reviewers); invite delivery/state; transfer/offboarding result. Use product wording such as “Block access,” “Stop inheriting access,” and “Disable this link” only for the exact corresponding behavior.

Creation presets should be least privilege and explicit. Do not use commenter default as a claim of no access. Do not expose inaccessible resource labels or complete member directories through pickers. Paginate inventories and audit; show unavailable state on authorization/storage failure, not empty-success or zero counts.

Exit: independent-account browser journeys cover owner, creator, delegated admin without flags, member, guest, outsider, unverified invitee and suspended member. Every advertised allowed journey completes and each disabled/forbidden journey is rejected server-side even with hand-crafted requests.

## W7 — Migration, gates and operational qualification

1. Produce a versioned, recoverable graph backup. Inventory role literals, direct/inherited grants, owners, missing/duplicate membership edges, empty/mismatched workspace anchors, active links/invitations, public capabilities and packaged App declarations. Do not silently use a permissive fallback when records are corrupt.
2. Generate a before/after authority report per user/workspace/resource/action. Preserve original grant provenance. Flag newly owner-visible private resources, viewer-author reductions, direct-viewer/parent-editor increases, inherited-block changes, legacy owner-role behavior, wildcard policies and outstanding credentials for explicit migration review.
3. Migrate via idempotent canonical graph services with durable progress/receipts; enforce graph and storage constraints. Backfill group/grant roots, revisions and source metadata; reconcile failed records into an owner/operational repair queue. Stage shadow comparison before enabling the new resolver, then cut over with a version fence and drain incompatible workers. Rollback must retain security repairs and the latest revocations; never revert blindly to an older permissive ACL snapshot.
4. Run `make verify` with repo venv PATH, test encryption-key setup and required no-env smoke/xdist behavior. Run current Core-only, extension-contract and clean built-wheel gates as defined by the repository. Regenerate capability maps only if manifests/skills/bindings/examples change. Check actual PR CI after explicitly authorized pushing.
5. Add a PostgreSQL lane with independent processes/connections and forced race barriers. Cover invite accept/revoke, link claim/revoke/cap, grant upsert/demotion, workspace suspension, ownership transfer and effect/revocation serialization. Prove database uniqueness constraints and rollback after injected mid-write failures. JsonDB tests are not a substitute.
6. Require U01–U76 surface coverage: direct read/write, lists/counts/search/vector retrieval, relations/stubs, dashboard/export, attachments, comments/reactions, notification/event/audit, App functions, tools/MCP, routines/hooks/connectors and approvals/resumes. Do not infer surface parity from package compilation.
7. Browser/API readback and runtime receipts validate the shipped artifact, with warm caches, multiple worker processes, reconnect/restart and account aliases. The live-model lane is additional assurance for model/tool routing; deterministic backend denial must hold regardless of model behavior.
8. Qualify session lifecycle and owner-operation reauthentication/MFA using the installed identity runtime. Define cookie/CSRF/CORS behavior if browser token storage changes. Confirm token expiry/revocation and WebSocket reconnection behavior; do not install a commercial provider as a Core requirement.
9. Publish the operator runbook: delegated administration, periodic effective-access review, orphan recovery, transfer/offboarding, public-link inventory, audited emergency access, session revocation, failed-effect reconciliation, evidence retention and restore verification. Restore into quarantine and reconcile an independently retained current authorization/revocation checkpoint before serving; rotate/revoke outstanding credentials when that checkpoint is unavailable, and review restored memberships before exposure. Test rollback/restore explicitly rather than assuming graph backup implies secure recovery. Audit intent should survive grant commits even if log delivery is delayed; inaccessible sinks must not fabricate a successful audit trail.

## Requirement coverage and measurable evidence

W0 produces a checked-in coverage ledger for U01–U76. Each row records the exact action/resource, permitted and prohibited actors, HTTP/tool/exposure surfaces, invariant(s), storage mode, named automated assertion or browser receipt, implementation package and unresolved limits. The scenario matrix is the requirements inventory; this review does not pretend it already has 76 completed end-to-end tests.

Minimum cross-cutting acceptance assertions:

- Authorization failure produces zero unauthorized grants/effects and zero restricted payload fields, labels or references; use target snapshots and effect counters rather than response status alone.
- After a committed revocation, an independently warmed worker cannot win a later protected mutation fence or release a newly unauthorized response/chunk. Capture operation/revocation ordering and authorization epochs in the receipt.
- Each completed invitation, share redemption or transfer has one durable effective outcome; retries do not consume additional capacity, overwrite a newer role or rerun a committed effect. The returned role/action projection matches the persisted grant state.
- Group, membership, parent, App definition and identity changes each advance the relevant authorization epoch; rollback restores both graph effects and epoch state atomically. Failed epoch reads do not reuse a cached allow.
- Query pagination, result caching, signed file access and audit/event delivery are exercised after permission reduction. Test authorized empty results separately from unavailable/denied results; a result cursor is never an authorization credential.
- Establish representative workspace/resource/group sizes and agreed latency/work budgets before optimization. Bound graph/condition evaluation, detect cycles/corruption and paginate inventories; exhaustion fails unavailable/denied rather than returning a permissive partial decision.

AC-01–21 retain their evidence category and closure disposition in that ledger. G0 explicitly includes AC-20; AC-21 belongs to W4/U66; AC-19 is disposition work unless a supported route can reach the helper. Final qualification must use an identified clean artifact; the second-review working tree contains unrelated App/UI edits and is not a release artifact.

## Completion gates

| Gate | Pass condition |
|---|---|
| G0 Security baseline | AC-01–07 and AC-20 perimeter issues fixed with paired authorized/denied regressions; no known disclosure retained as “expected behavior” |
| G1 Owner governance | Owner administers every canonical resource in its workspace, including member-created private content; cannot cross tenant/credential boundaries |
| G2 Revocation and durability | Real multiprocess/PostgreSQL proof; no stale privileged effect, suspended re-entry, ambiguous owner, unscoped survivor or partial grant-success |
| G3 Declarative delegation | Two independent Apps can delegate functions and conditions without Core domain imports or generic-CRUD bypass |
| G4 User experience | Independent-account browser journeys complete with authoritative capabilities, explanations and readback |
| G5 Release qualification | Full required repository gates, artifact and runtime evidence, reviewed migration diff/runbook; deployment authorization remains separate |

Do not declare the system closed until every assessment finding has a concrete disposition (fixed with evidence, deliberate policy decision with acceptance tests, proven unsupported/unreachable, or open), every U-case has allowed and denied evidence, and residual limitations are explicit. An open P0 blocks G0; a source-only issue does not become closed merely by calling it “qualified.” Accepted product semantics must not waive a demonstrated disclosure while still claiming a secure baseline. Passing the characterization probes in their original form is an audit observation; repaired behaviors must replace those observations with requirement assertions.
