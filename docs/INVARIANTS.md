# Integral substrate invariants

These are the constraints every substrate change must preserve. This edition removes repeated milestone history and obsolete architecture while retaining active invariant IDs and the detailed authority/admission clauses. It does not grant new runtime authority or qualify a feature for release.

Read the root [agent instructions](../AGENTS.md) and [architecture](product/ARCHITECTURE.md). A substrate-touching plan must enumerate affected invariants. A new constraint needs its rule and gate here in the same change. Weakening a constraint requires a dedicated justified architecture change and relevant migration/behavior tests; an editorial cleanup cannot silently weaken it.

## Canonical literals and graph conventions

Define ActorKind, PolicyAction, ChangeEventAction, and AgentType once in their canonical source modules. Extend existing literals additively instead of making another definition. Every non-audit-only change-event action has a policy-action counterpart; the audit-only exemption whitelist in `test_policy_audit_emit.py` is authoritative, including denial/expiry events.

New edge types use a PascalCase persisted class and ALL_CAPS alias. Existing all-caps persisted Core classes retain compatibility. Traversals pass the actual class or stored entity name, never an alias string for a differently named class. Typed edge fields carry relationship metadata.

Single-hop reads are bounded; pages/counts use their respective APIs. Multi-hop behavior normally uses a Walker. Permitted procedural/denormalized deviations require measured evidence, an inline explanation, and preserved invariants. Endpoint, exception, schema, structural-edge, and Core/App boundaries have no performance escape.

### I-GRAPH-01 — All-Nodes-Reachable-From-Root (`all-nodes-reachable-from-root`)

Every persisted Node has a named structural path from Root through IntegralApp. Creation wires its structural edge in the same transaction/unit of work. Scalar IDs are lookup caches, never substitutes. A participant in an App-bound subsystem extends from that subsystem's App anchor directly or through its valid registry/branch; hanging an App sidecar only from User or IntegralApp is forbidden. There is no orphan-Node exemption.

Use static create/edge checks and runtime reachability audits in `test_graph_contiguousness.py`. Any legacy allowlist must remain drained, not become a permanent exemption. Existing structural attachments include uploads/files/conflicts beneath records, drafts beneath published models, sessions beneath threads, and notifications beneath users through their canonical helpers.

### I-GRAPH-02 — Object-For-Non-Graph-Records (`object-for-non-graph-records`)

Use jvspatial Object for log-shaped/scalar-keyed records that have no meaningful graph relationship, permission resolution, cascade, traversal, or Walker computation. Graph participants remain Nodes. Conversion between these primitives is a substrate change. ChangeEvent persistence uses DBLog(Object) through the canonical logger; it does not need a graph migration. Private execution records can use scoped encrypted Objects while their owning sessions remain rooted Nodes.

## Authority and access

Use canonical policy evaluation. Agents without applicable policy fail closed. Policy administration authorizes itself through the same perimeter. No API-level legacy access helper or client-selected scope can bypass authority.

Explicit scope is `X-Integral-Scope: ws:<workspace_id>`; malformed supplied scope is rejected. Private workspace access is validated server-side. Ordinary omitted scope uses the personal default; specialized stored-active-scope paths are explicit. Bind chat and approval execution to authenticated scope. Public-read/share exceptions are narrow contracts, not general writes or private enumeration.

### I-ROLE-01 — Five-Tier Role Ladder (`role-ladder-five-tier`)

**Statement.** `COLLABORATES_ON.role` accepts exactly five values, ordered
by `ROLE_RANK` in `backend/app/services/permissions.py`:

```
owner (5) > admin (4) > editor (3) > commenter (2) > viewer (1)
```

Source-of-truth literal: `VALID_ROLES` in `backend/app/services/sharing.py`.
Every consumer (invitation validators, share-link minting, collaborator
add/update, role-cap math) MUST resolve through the single tuple — no
inline `("owner", "editor", ...)` re-declarations.

**Tier authority split:**

| Tier | Track config (schema/views/tags/library/anchors/migrations) | Entry CRUD | Comment + react | Read | Delete / ownership transfer | Sharing mgmt (collab, exclusion, share-link, invite) |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| owner | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| admin | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ |
| editor | ✗ | ✓ | ✓ | ✓ | ✗ | ✗ |
| commenter | ✗ | author-only (own entries) | ✓ | ✓ | ✗ | ✗ |
| viewer | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ |

The admin/editor split is the substrate boundary: **editors do entry
CRUD; admins do entry CRUD + track-config curation**. Conflating the
two (the pre-split `editor` semantics) is the bug this invariant prevents
from regressing.

**Gating helpers:**

- `can_edit_track` / `can_edit_app` / `can_edit_entry` — accept
  `owner | admin | editor` (entry-CRUD authority).
- `can_admin_track` / `can_admin_app` — accept `owner | admin` only
  (track-config authority).
- `can_edit_view` delegates to `can_admin_track` (views are config).
- `comment.create` + `add_reaction` gate via direct `resolve_role >=
  commenter` check (closes viewer-can-comment latent gap — both
  endpoints previously gated on `entry.read` only).

**Routing in `policy_engine._evaluate_human_default`:**

- `track.update` / `app.update` → `can_admin_track` / `can_admin_app`.
- Scope-fallback (resources without their own `rk` branch), three tiers, not
  two: `comment.` / `reaction.` → `resolve_role >= commenter`; `entry.` →
  `can_edit_*`; everything else (tags, operational_models, entry_types, anchors,
  migrations, views, attachments) → `can_admin_*`. Comments sit one tier BELOW
  entry mutation — gating them on `can_edit_*` would make the `commenter` role
  unable to comment, which is the whole point of it.
- **`comment.moderate` is the exception inside that namespace** — deleting
  SOMEONE ELSE'S comment, so it lands on `can_admin_*`, not the commenter tier
  its `comment.` prefix would otherwise select. `_MODERATION_ACTIONS` is
  subtracted inside `_is()` before the prefix test, because
  `"comment.moderate".startswith("comment.")` is true; a new governance verb
  added to this namespace without that subtraction silently inherits
  `commenter`, which would let every commenter delete every other person's
  words. Same tier as `track.share_link.mint`, deliberately: whoever can turn
  public commenting ON is whoever can clean up after it. `DELETE
  /api/comments/{id}` falls through to it when the caller is not the author,
  and `GET /entries/{id}/comments` reports the same evaluation as
  `can_moderate` so the UI's delete affordance cannot drift from the gate.
  `PUT /api/comments/{id}` stays author-only — rewriting another person's
  words is not moderation. Pinned by
  `backend/tests/test_public_comment_moderation.py`.
- **Entry-targeted comment / reaction actions resolve on the ENTRY**, not on
  the track named in the scope. The scope names the parent because that is how
  the cascade is expressed, but resolving there skips per-entry
  `EXCLUDED_FROM`: an excluded user resolved as the track's editor and could
  post on an entry whose reads already refused them. Callers must therefore
  pass `Resource(kind="entry", id=<entry_id>, scope=f"track:{...}")` — an
  empty id falls back to the track and re-opens the hole. Pinned by
  `backend/tests/test_comment_permission_matrix.py`, including the
  `create_comment_internal` approval path.
- `anchor.create` / `anchor.cascade` / `anchor.delete` → `can_admin_track`.

**Owner-only carve-outs** (never delegable to admin):

- `track.delete` / `app.delete` (via `can_delete_track` / `can_delete_app`).
- Ownership transfer (`/transfer-ownership` endpoints).

**Owner-or-admin carve-outs** (resource `resolve_role` ∈ {owner, admin}):

- Collaborator add / remove / role-update
  (`_require_collaborator_manage_authority` in `services/sharing.py`).
- Exclusion add / remove (`_require_share_authority` with
  `{type}.exclusion_add` / `{type}.exclusion_remove`).
- Share-link mint / revoke (`services/share_links.py`).
- Resource-level invitation create (`services/invitations.py`).

### I-ROLE-02 — Inherited-Role Cap At Editor (`inherited-role-caps-to-editor`)

**Statement.** When `resolve_role` recurses up the parent chain
(Entry → Track → App), any inherited role above `editor` (i.e. `owner`
or `admin`) is demoted to `editor` on the child. Roles at or below
`editor` (`editor`, `commenter`, `viewer`) pass through unchanged.
Implementation: `_cap_inherited_role` in
`backend/app/services/permissions.py`.

**Rationale.** Track-config authority (schema, views, tags, library,
anchors, migrations) is a **per-resource curation decision** and MUST
be granted directly on the resource — never via parent cascade. An App
owner / admin inheriting onto contained Tracks gets entry-CRUD authority
(`editor`) but cannot reshape per-Track substrate without an explicit
direct grant. Deletion and ownership transfer require ownership of the
specific resource. Sharing management requires a direct owner or admin
role there, as specified in I-ROLE-01; those rights never cascade.

If inherited owner / admin preserved track-config rights, the `admin`
tier would be meaningless at the App-cascade boundary: anyone added to
the App at admin (or owner) would silently curate every Track in the
App, defeating the per-Track delegation model the role split exists to
support.

**Direct-grant exception.** A direct `OWNS` or `COLLABORATES_ON{role:
"admin"}` edge on the resource itself ALWAYS resolves to its raw role
(no demotion) — only the *inherited* path caps. A user owning both the
App and a contained Track is `owner` on both. To grant track-config
rights to a non-owner, the resource owner must add them as a direct
`admin` collaborator on that specific Track (or App).

**Mirror in collaborator-listing aggregation.** The App→Track inherited
display row in `api/tracks.py:list_collaborators` follows the same map:

```
App role  → Track display role
owner     → editor    (capped — track-config requires direct grant)
admin     → editor    (capped — same)
editor    → editor
commenter → commenter (passes through — comment + react on cascade)
viewer    → viewer
```

The `services/sharing.py:_inherited_collaborators` cap variable
(`cap = "editor"`, applied to both `owner` and `admin`) and
`permissions._cap_inherited_role` MUST stay synchronized — single
demotion rule, two consumers.

### I-ROLE-03 — Default-Deny For New Collaborators (`new-collab-defaults-to-commenter`)

**Statement.** When the frontend invites a new collaborator via the
collaborators modal (Track or App), the default role is `commenter` —
NOT `editor`. Promotion is per-row through the explicit role pills.
Implementation: `addTrackCollaborator` in
`frontend/src/pages/TrackDetailPage.tsx`, `addCollaborator` in
`frontend/src/pages/AppDetailPage.tsx`.

**Rationale.** Least-privilege onboarding. Under the pre-split model,
`editor` default silently granted track-config rights to every invitee;
under the split model the same default would silently grant entry-CRUD
to everyone the owner shares with. `commenter` makes the privilege
intentional — owner promotes via "Make admin" / "Make editor" pills.

Backend `add_collaborator` still accepts any valid role; this invariant
governs the frontend's *default* selection only. CLI / API callers may
specify any tier explicitly.

### I-ACCESS-01 — Privacy through resource modeling

Access is per App/Track/Entry. There is no field-level visibility primitive. Differently governed data must be in a separate resource, linked through an authorized relation/anchor. An excluded audience sees only the permitted restricted stub. A hidden field, conditional renderer, or one-off API redaction cannot supply privacy for a readable Entry.

### I-FIELD-MEMBER-01 — Member references bind graph Users

Use `type: member` to refer to an account. Materialize HAS_MEMBER_REF and validate the workspace member pool. `relation target: user` is invalid. Scalar user/member IDs cannot replace a semantic graph account binding.

### I-FIELD-RESOURCE-01 — Resource references render as named destinations

**Scope:** OperationalModel scaffolding and shared entry field presentation, including detail pages and cards.

**Rule:** Declare references to entries/tracks as `relation`, members as `member`, and attachments as `file` / `files`. Persist canonical IDs for execution, but resolve them through authorized resource APIs for display. A resolved value shows its human label with navigation to the entry/track or an authenticated file preview/download. Never render the raw stored ID as the normal field value, including loading, denied, missing, or deleted states. Those states show a readable unavailable/loading message. Reference controls use the same label placement and alignment as adjacent fields.

**Boundary:** Do not guess resource references from ordinary text, fingerprints, hashes, or arbitrary JSON. Opaque diagnostic values remain diagnostic text. Resolution must preserve workspace permissions; it must not substitute a label from another scope or manufacture a destination for an inaccessible resource.

**Verification:** Shared `EntryMetaFields` uses `RelationValue`, `MemberValue`, and `FileValue`; typed file presentation tests cover named preview controls and unavailable values. Browser qualification covers relation-control alignment and authenticated PDF preview.

### I-CHAT-01 — No raw node id surfaces in human-facing chat or staging text

**Scope:** `backend/app/services/id_resolver.py` (the resolver), and every surface that renders agent-authored or staging text to a person: `backend/app/services/chat_streaming.py`, `backend/app/api/ai_chat.py`, `backend/app/providers/jvagent_streaming.py`, `backend/app/agentive/staging.py`, `backend/app/agentive/api/staging.py`.

**Rule:** Text a person reads — assistant chat prose (live stream + persisted transcript) and staging-card `summary`/`diff_human` (including error messages and batch op previews) — MUST NOT contain a raw node id (`n.<Type>.<hex>`, `o.<Type>.<hex>`). Every such surface routes its text through `id_resolver.humanize_ids`, the single canonical pass that replaces each id with the node's human label. Resolution is schema-agnostic (any node type, via the discriminator-dispatching `Node.get`/`Object.get`), batched (`resolve_id_labels`, one `$in` query per collection), and best-effort (an unresolvable id degrades to `"<Type> …<last4>"`, never a raw id).

**Labeling:** the label priority chain is `title → name → display_name → filename → text-snippet`; `User`/`AuthUser` use `display_name|email` (resolved through `batch_resolve_users_by_principal_ids`, which covers both `n.User` and `o.User` principal forms); a `OperationalModel` is named by the Track/App it shapes ("the Opportunities profile"), resolving via the published parent for a draft. Batch placeholders (`{{app.id}}`, `{{step_2.id}}`) render as readable phrases ("the new app", "step 2's result").

**Where ids are kept (NOT humanized):** machine inputs the agent/executor act on — tool-call arguments, `StagedChange.payload`, `StagedChange.diff_machine`, and the raw-JSON inspector — retain ids verbatim. Humanizing is for human-facing TEXT only. Structured FE fields (relation/member values) resolve their own labels client-side and are out of scope.

**Verification:** `backend/tests/test_id_resolver.py` (resolution, batching, OperationalModel/user labeling, placeholder prettify, the streamed-delta buffer that releases only whole words so an id split across tokens is humanized before it reaches the browser).

## Events, audit, and review

All change emission uses `emit_change_event`; no direct competing logger writes. Internal actor recursion bypass stays in process and must not leak into stored audit or observability. High-fan-out denial/cascade actions can suppress websocket push while retaining audit. Introspection extends additively without changing existing key types.

### I-APPROVAL-01 — Reviewed re-entry is narrowly guarded

Approval execution threads the defined system internal actor through its canonical helper calls to avoid recursively staging the same write. The bypass exists only in the policy engine, forwarding change helper, and approval executor. A new producer requires an explicit invariant amendment and bypass test; it cannot become general authorization.

### I-APPROVAL-02 — Preserve original actor and actual outcome

Rejection emits policy.deny with approval/original-action/agent/reason detail, never the proposed mutation. Approval executes the original action and attributes its change event to the original actor, preserving the approving human separately. Approval is not proof the effect succeeded. Current-grant and durable-work admission checks still apply at their specified boundaries.

### I-APPROVAL-03 — Audit-only exemptions are explicit

Every new ChangeEventAction appears in PolicyAction unless the canonical test whitelist identifies it as audit-only. Policy-only approval, migration, authoring, and conflict gates need no fabricated change-event twin. Update the existing definitions and tests together.

### I-UX-01 — One provenance presentation

ProvenanceBadge is the sole frontend consumer choosing source styling/popovers. Human, agent, connector, and system variants stay aligned with ActorKind. All relevant surfaces reuse it. Legacy default provenance does not establish a confirmed origin. Add a variant only with backend/frontend type and presentation updates.

### I-UX-02 — One event-shape normalizer

`normalizeChangeEvent` is the only frontend adapter for nested websocket actors and flat polling actors. Every event consumer uses it. Add another wire shape in lockstep; do not duplicate per-surface normalization.

## Operational Models and composition

Authoring YAML v3 and compiled runtime schema v2 are separate interfaces. Package, library artifact, attached model, installed App, and active definition retain distinct identities.

Anchor edges are written only through `_sync_anchor_edges`. Anchored Tracks stay in the source workspace and share the template model by reference in the current contract. Attached-model scalars and HAS_OPERATIONAL_MODEL targets agree within the same transaction. Only `materialize_anchor_track` writes TEMPLATED_FROM; USES_TEMPLATE retains Track-to-Track provenance.

ANCHORS is a relationship, not Entry containment. Entries do not contain Entries/Tracks; Tracks do not contain Tracks. Operational collections are explicit records, not growing nested JSON entities. Current anchor deletion is policy-governed hard cascade; denial preserves the target and emits denial, not an invented soft-delete grace period.

### I-PROFILE-01 — Deterministic authoring service

`integral_author_model` uses deterministic catalog metadata matching/template fill; no hidden inline LLM call. Core cannot hardcode domain keyword/package maps. Authorized explicit workspace is required; policy authoring and workspace publish checks both apply. Drafts do not become published catalog artifacts until publication. Preserve structured fallback and explicit package selection.

### I-PROFILE-02 — One patch and publication path

Model modifications use the allowlisted patch service and canonical draft/publish path, never direct arbitrary manifest mutation, eval, or exec. Attached-instance publication handles applicable migrations; library publication does not automatically migrate installed instances. Forced publication uses its separate policy gate.

Type hints and explicit pickers cannot conflict. The type-hint resolver must surface ambiguous top matches rather than silently picking one. MCP naming overrides and substrate introspection retain one canonical definition.

### I-MIG-01 — One migration dispatcher

Only the canonical `_OP_HANDLERS` catalog dispatches supported declarative operations. Workers/retries use it rather than another catalog. Declared but manual/pending operations must not be represented as completed automatic transforms.

### I-MIG-02 — Durable progress and safe retry

Enqueue idempotent migration work before responding and bind it to the exact manifest fingerprint. Recover under the normal lease path. Legacy orphan reconciliation cannot race active workers. Authorized status/retry exposes bounded failed-item diagnostics and the same supported dispatcher. Entry create/update/internal writers reject mutation while the applicable model is queued/in progress with `migration_in_progress`.

### I-MIG-03 — Reject unhandled breaks

Reject breaking changes lacking a matching migration with unhandled-break details. Force is a separately gated destructive escape and does not migrate old Entries. Do not equate force with repair.

### I-MIG-04 — Declarative supported operations only

No inline Python or arbitrary migration classes in a manifest. Use supported authoring/patch/publication mechanisms, with reviewed migration work where necessary.

### I-LIB-01 — Discovery tags belong to the package

Refined seeded library artifacts carry their required discovery tags (at least three under the current refinement test). The legacy exempt seed set stays defined by `test_seeded_library_packages.py`; do not silently widen it. Metadata must survive compilation.

### I-LIB-02 — Tags guide discovery, not permission

Package tags are domain-discovery metadata sourced from the package. They can improve authorized catalog matching but cannot authorize a read. Preserve compatible metadata hydration and fingerprint-based refresh. Core has no domain-specific keyword routing table.

### I-LIB-03 — Derived package provenance

Deriving a reusable package stamps origin kind/identity, time, and deriving principal before persistence. Preserve round-trip install/merge behavior and authorized source reads. Current public terminology uses App rather than obsolete Space routes.

### I-LIB-04 — Detach/revert are non-cascading

An App detach/revert affects its directly attached model. Defined child track models retain their identity and structure; do not silently cascade a library replacement into independently attached child models.

### I-LIB-05 — Revert checks data impact

Compute existing-record validation impact before destructive reapplication. Blocking impacts reject unless the supported force path is explicitly selected. Emit canonical operational-model update detail, including revert/force/impacts.

## Packages and extension boundaries

### I-APP-DEF-01 — Active-Definition-Authority

Every App materialized through the package lifecycle has one active
`ApplicationDefinition` revision. The definition is attached with
`App —HAS_APPLICATION_DEFINITION→ ApplicationDefinition`, and
`App.active_definition_id` / `App.active_definition_revision` are only
denormalized pointers to that edge target. A revision's canonical manifest,
requirement ledger and provenance are immutable after compilation; a changed
contract appends a revision and marks the prior active revision `superseded`.

Operational Models remain the schema/composition component. They are not the
sole authorization or execution authority for an installed App: an operation
that needs the effective contract resolves the active definition first. This
preserves package provenance and gives upgrades a stable base for a later
three-way merge.

### I-APP-01 — Canonical runtime schema v2

The runtime compiler requires schema version 2 and rejects version 1. Disk authoring v3 compiles into this shape; compatibility input handling does not authorize obsolete runtime manifests. Keep seeded-package compile/round-trip tests.

### I-APP-02 — Compensated lifecycle

Install steps record reverse-order compensation, and one failed compensation cannot prevent earlier cleanup. Lifecycle state reflects the actual result. Required-setting completion uses the signed expiring token and abandoned-install cleanup. Normal and force uninstall emit their distinct actions; forced removal must preserve override diagnostics.

### I-APP-03 — One cross-App label resolver

Authorized relation labels pass through `relation_runtime.read_cross_app_label`. A restricted stub contains only kind, relation field key, and target kind, never source field contents. The compile/schema/edge declaration whitelist in `test_app_bundles_invariants.py` constrains other label-field references.

### I-APP-04 — Dependency and reference gates

Required absent/unsatisfied dependencies block install. Inbound hard dependencies and blocking REFERENCES prevent normal uninstall. Force overrides are explicit, audited, and leave references to render safely rather than leak data. Preserve structured blocker/override results.

### I-APP-05 — Same-workspace App references

Cross-App resolution and explicit instance pins stay inside the source workspace. A manifest cannot publish data through a cross-workspace target pin. Resolve ambiguous instances through the supported declaration contract.

### I-CRUD-01 — Service-layer canonical mutations

Routes delegate persisted writes to named services; direct graph creation/connection in API modules is forbidden. Internal operations use the appropriate shared writer, not a second mutation implementation. Staged-index guards and behavior tests enforce this boundary.

### I-APP-06 — One library installation path

Library Apps install through `app_lifecycle.install_app` or its canonical HTTP route. Partial create-and-merge cannot omit settings, definitions, seeds, skills, tools, or lifecycle. Seed/provisioning callers delegate rather than reconstructing install.

### I-APP-07 — Personal workspace at signup

Provision the personal workspace idempotently at signup. Listing and scope reads do not lazy-create missing workspaces. Accessible workspace reads enumerate already linked resources.

### I-SUBSTRATE-01 — No domain identity in Core

Core services/APIs/models/schemas contain no domain slug/type/Track names except a specifically justified structural allowlist entry. Domain side effects belong to bundle tools/hooks. Drift guards and architecture review enforce this.

### I-EXT-01 — No App-specific Core branching or imports

Core does not branch on App identity or import domain packages/plugins. Handoff aliases and routing metadata belong in declared packages. Apps reach Core through the facade; Core-only boot proves independence.

### I-EXT-02 — Core defaults are Core packages

Generic defaults use `package.class: core_package` and the generic installer. Core-only loading filters to that class. Commercial/community defaults must not be hardcoded into provisioning.

### I-PC-01 — Unstaged writes are bounded to the observation stream and the attention log

**Scope:** `backend/app/agentive/unstaged_targets.py`, `backend/app/agentive/tooling/dispatch.py`, `backend/app/services/operational_model_compile.py`, `backend/app/services/operational_model_merge.py`, bundle manifests declaring `app.unstaged_tracks`.

**Rule:** An agent write MAY bypass staging only when ALL of the following hold: the staging kind is `create_entry` or `update_entry`; the target Track carries a manifest key (`Track.template_id`) listed in its App's attached-manifest `app.unstaged_tracks`; that App is `lifecycle_state=active`; the App's Workspace is `kind="personal"`; and the acting principal resolves to that Workspace's `IS_MEMBER_OF{role:"owner"}` User. Every other write stages — including every other track in the same App. The exempt keys are declared in the bundle manifest and gated at compile on `package.trust_tier ∈ {trusted, audited}` with a cap of `MAX_UNSTAGED_TRACKS_PER_APP`; the substrate MUST NOT name a bundle or a track key (I-SUBSTRATE-01). The gate fails closed: any error, missing link or ambiguity resolves to "stage it".

**Rationale:** The approval surface exists so a person can refuse a change to their substrate. An App that records observations about the person would fill that surface with items where refusal means only "do not remember that", draining the signal from every other card. Observations are not changes to what the person owns; beliefs are, and beliefs stage.

**Consequence for merge:** `app.unstaged_tracks` is read off the ATTACHED manifest at write time, so `merge_library_manifest_into_operational_model` MUST carry it through the rebuilt `app` block — the same failure mode I-HOOK-02 §1 documents for `hooks`/`tools`, where a dropped key silently disables the behavior.

**Verification:**
- `backend/tests/test_personal_context_unstaged.py` — 14 tests: exempt tracks mint no token; every fact track and the compiled pages track do; a hand-made Track with no manifest key is never exempt; another user's personal workspace is not exempt while its owner's is; an organization workspace the acting user OWNS is not exempt; only row-write kinds are exemptible; the exempt set is read from the manifest and the substrate names no bundle.
- Each of the four gate clauses (kind, manifest list, personal-workspace, ownership) is mutation-verified — removing it fails a named test.

**Extended by ADR-007:** the decision ledger writes into a third exempt track, `decisions`. Recording that a person approved something cannot itself require approval. `Decision` was moved OUT of the `commitments` track to make this possible without exempting beliefs — the exemption is a whole-track property, and `commitments` holds `Commitment`, which is a belief and stages. Verified by `backend/tests/test_decision_ledger.py`.

### I-HOOK-01 — Frozen hook catalog

The current catalog is entry.transform, entry.public_share, entry.precompute, entry.create, entry.validate, entry.update, connector.dedup, connector.auto_link, and email.sent. Adding/removing a point requires review and an invariant amendment; removal first migrates dependent packages. Domain behavior is dispatched generically.

### I-HOOK-02 — Carry and scope the operational layer

Merges preserve hooks/tools and unstaged declarations where applicable. Startup rehydrates active package registrations and heals missing declared operational blocks through the canonical source path. Handler resolution uses verified package/module identity, including external packages, rather than a hardcoded domain import.

Entry hooks match type keys. ToolContext reads validate entries through their owning Tracks and active workspace. Only the explicit own-personal-workspace facade contract can select that narrow alternate target; mutating context is forbidden. Tools cannot import private Core services/models. Registration collisions fail; same-package idempotent registration does not replace another package.

### I-BUNDLE-01 — Package manifest required

Each catalog package directory contains operational-model.yaml. Missing manifests produce skip/issue diagnostics rather than an invented package.

### I-BUNDLE-02 — Declared skills exist on disk

Every declared skill has the required skills/{key}/SKILL.md file or the validated explicit standard path. Missing files are excluded with diagnostics, never silently granted instructions.

### I-BUNDLE-03 — Production Python signature gate

When the public verification key is configured, Python-shipping bundles require successful signature verification for admission. Record identity and rejection reason without key disclosure.

### I-BUNDLE-04 — Directory/slug agreement

The package directory name equals package.slug. File identity and verified content must agree during loading.

### I-BUNDLE-05 — Overlay namespace

Runtime App skills use `{app_slug}__{skill_key}`; keys are unique within an App and global names retain the slug namespace. Disk-standard names remain distinct.

### I-SKILL-SCOPE-01 — Accessible active Apps only

The resident overlay contains bundled skills only from active installed Apps in the active workspace on which the acting user has an effective role. Registered tools/hooks still check current permissions through the facade.

### I-SKILL-01 — Standard frontmatter

Follow [Agent Skills](https://agentskills.io/specification). Require matching lowercase/hyphen directory name (1–64 characters) and non-empty description (at most 1024 characters). Standard optional fields only; compatibility is 1–500 characters, metadata string-valued, allowed-tools a space-separated string. Vendor activation/extends/requires-actions fields, underscore disk names, and list-form allowed-tools are forbidden.

### I-SKILL-02 — Progressive discovery

Describe purpose and when to use the skill. Load instructions/resources on demand through authorized access. No prose voice is a format requirement.

### I-SKILL-03 — Standard Markdown body

No required headings. Useful procedures and grounding remain guidance, not compliance conditions. Runtime policy and approvals do not depend on persuasive body text.

### I-SKILL-04 — Description parity

Bundle manifest descriptions match disk frontmatter after the canonical synchronization utility. Compliance tests and guards pin the format and parity.

## Retrieval, scratch, and connectors

### I-RET-01 — Permission-Filter-At-Retrieval (`permission-filter-at-retrieval`)

Every indexed candidate passes current entry.read policy before exposure. Retrieval is a projection, not another authority store.

### I-RET-02 — No-Index-Bypass (`no-index-bypass`)

Index/Track prefilters supplement rather than replace per-candidate authorization. Preserve sharing and guest semantics.

### I-RET-03 — Soft-Delete-Not-Hard-Delete (`soft-delete-not-hard-delete`)

Hard-deleting an Entry soft-deletes its embedding with deleted_at and excludes it from search. Embedding hard deletion is reserved for offline/retention work, not the normal record-delete path.

### I-RET-04 — Track scope index prefilter

A vector driver exposes track_id as a queryable filter and pushes supported Track scope into search. Mock and actual backend tests verify propagation. Prefilter does not replace policy.

### I-RET-05 — No tag-only reembedding

Reembed content changes and relevant text changes, not tag-only edits. Maintain the canonical reembedding hooks and scope/index contract.

### I-SCRATCH-01 — Scratch-Track-In-Personal-Workspace

Scratch belongs in the user's personal workspace, never whichever organization scope happens to be active. Provision through the canonical idempotent helper.

### I-SCRATCH-02 — Scratch-Track-Kind-Discriminator

Find scratch through its declared kind discriminator, not its human title. Preserve compatible empty defaults on ordinary Tracks.

### I-SCRATCH-03 — Promote-Preserves-Derived-From

Promotion sets the new record's provenance.derived_from to a list containing the source identity.

### I-SCRATCH-04 — Source-Archive-Via-Status-Not-Delete

Archive the source with status, not hard deletion. Soft-delete its embedding best effort; unavailable retrieval logs a warning rather than undoing record promotion.

### I-SCRATCH-05 — Promote-Permission-Gates

Check source entry.read and target entry.create. Either denial blocks promotion and uses the canonical denial event path. Scratch location does not create a staging exemption.

### I-CON-01 — Connector provenance split

Synced records use source='connector' and separate source_id='<connector_id>:<external_id>'. Never stuff the external identity into the ActorKind literal.

### I-CON-02 — Explicit connector binding

IS_CONNECTED_TO is the connector-to-Track binding and carries mapping metadata. Subclass selection uses subclass_slug, never the legacy mapping_profile field.

### I-CON-03 — Unique registration keys

Duplicate connector subclass slug registration fails. Only the registry interface and its test reset mutate registration. Stable identities support deduplication.

### I-CON-04 — Connector policy at creation

Create the per-connector policy edge along with the rooted connector through its canonical helper. Preserve connector.sync and authorized entry create/update actions. Dedup identity must be derived through the registered contract before writing.

### I-CON-05 — Implementations and package metadata remain separate

Executable connector adapters belong in the reviewed integration/agentive modules. Domain mapping schemas belong to packages admitted through configured roots. Agentive boot is always on; Core-only filtering admits generic Core packages without absorbing domain templates into Core.

### I-SYNC-01 — Connector actor in every sync event

Lifecycle and per-record sync events carry the connector actor, not the human requester. Preserve source identity independently in provenance.

### I-SYNC-02 — No accidental external work in tests

The startup TESTING/pytest short-circuit prevents scheduler external calls. Normal boot uses the always-on agentive layer; no unsupported AGENTIVE_ENABLED condition is introduced.

### I-SYNC-03 — Deduplicate before write

Look up the stable idempotency identity before mutation. Existing records enter their configured mirror/update/conflict branch; repeated sync never blindly duplicates them. Any replacement lookup must preserve identity and scope.

### I-SYNC-04 — Rooted conflicts and authorized resolution

Conflicts remain rooted Nodes with local/external snapshots, detection/resolution times, resolver, result, and status. Reads are authorized; the obsolete all-authenticated listing assumption is invalid. Resolution inherits underlying Entry edit authority. Applying an external snapshot emits one entry.update with conflict identity; retaining local content does not fabricate a record mutation.

## UI settings, speech, and tests

### I-CONV-01 — jvspatial endpoints

HTTP routes use @endpoint. Websocket dispatch has the documented framework-limitation APIRouter exception with inline explanation. No new raw HTTP routers or decorators.

### I-CONV-02 — Canonical exceptions

Use JVSpatialAPIException subclasses from the canonical errors module, not raised HTTPException. Preserve the standard error envelope and typed validation handling.

### I-CONV-03 — Schema modules

Request/response BaseModels live under schemas/, including API/agentive submodules. No inline transport models in route modules. Preserve the endpoint framework's flat-body parameter handling.

### I-SET-01 — Settings proxy existing contracts

Settings use canonical surfaces rather than another state store. Retrieval environment config is read-only, library counts come from runtime results, sharing reuses resource management, and speech preferences use their validated per-user slot/route and canonical event. Extending a read-only panel requires its actual reviewed backend contract.

### I-SET-02 — Always-on agentive settings

Agentive panels assume the loaded layer. A compatibility capability hook cannot imply a real disabled boot mode. Permission/configuration can limit features without inventing a kill switch.

### I-SPEECH-01 — Provider keys remain server-side

The browser receives only a short-lived transcription-only credential where supported, with no-store handling. Workspace guests cannot mint it. Transcription binds credentials/files to the authorized workspace; adapters receive ProviderContext and do not decrypt or traverse the graph.

### I-SPEECH-02 — All CSP sources agree

Every declared streaming origin is permitted by backend headers and both nginx sources. Keep microphone=(self). Validate origin consistency and secret absence.

### I-PHASE9-01 — Onboarding sentinel and canonical flow

User.onboarded_at is the completion sentinel. First-login experience uses the canonical agentive onboarding flow, not a competing REST/form implementation. Only finalize writes completion and one user.update. Legacy backfill is idempotent and emits no user mutation events. Preserve degraded get-started behavior without inventing an agentive boot switch. Finalization establishes the required workspace/App/Track/preferences state under the existing regression contract.

### I-TEST-01 — Success, denied, and invalid boundaries

Registered endpoint coverage includes success, denied, and invalid axes in the canonical audit catalog. An axis without validatable input needs a justified explicit exemption. Multi-file evidence can satisfy the union. New endpoints update the catalog and behavior tests.

### I-TEST-02 — Coverage thresholds

Preserve the 80% global coverage requirement and 90% per-file requirement for cataloged new substrate files. Add newly covered files to the authoritative audit bucket. A declared threshold is not a claim that every test command exercises that coverage gate.

## Work and native session authority

### I-WORK-01 — Lease authority

**Scope:** `backend/app/agentive/services/work_items.py`, `work_worker.py`, `work_execution.py`.

**Rule:** Only the current lease token + fence may heartbeat, complete, or fail a `running` WorkItem. Lease loss cancels local execution and blocks effect boundaries (`work.lease_lost`).

**Verification:** `tests/test_work_item_leases.py`, `tests/test_work_worker.py`, `tests/test_work_kernel_chaos.py`.

### I-WORK-02 — Effect identity

**Scope:** `work_execution.py`, `capability_broker.py`, resident embed.

**Rule:** Every brokered effect under a WorkItem derives from deterministic `run_id` / `effect_key` / `logical_step_key` and propagates `WorkExecutionContext` to the adapter. Non-replayable sources fail closed.

**Verification:** `tests/test_work_execution.py`, `tests/test_capability_broker.py`.

### I-WORK-03 — Atomic work units

**Scope:** `work_outbox.py`, `work_approvals.py`.

**Rule:** Postgres commits (1) enqueue+outbox, (2) transition+outbox, (3) WorkApproval+waiting_for_human+outbox, (4) approval decision+WorkItem transition+outbox as single transactions via public `find_one_and_update` / `insert_if_absent`.

**Verification:** `tests/contract/test_work_kernel_postgres.py`.

### I-WORK-04 — Idempotent enqueue adapters

**Scope:** routine scheduler, `work_events.py`.

**Rule:** Routine fires use `routine:{routine_id}:{scheduled_for}`; event triggers use `event:{dblog_id}:{trigger_key}`. Duplicate pages/fires return the same WorkItem.

**Verification:** `tests/test_routine_work_items.py`, `tests/test_event_work_items.py`.

### I-WORK-05 — Fail-closed approvals

**Scope:** `work_approvals.py`, staging/approve paths.

**Rule:** Human waits require a pending `WorkApproval`. Approve requeues the original WorkItem; reject/expiry terminalize. A bounded mandate review cannot use ordinary approval to bypass current-grant and shared-limit admission: it remains non-runnable until the dedicated admission path is implemented and qualified. Cards without `work_approval_id` keep the legacy inline path.

**Verification:** `tests/test_work_approvals.py`.

### I-WORK-06 — Production store posture

**Scope:** `work_lifecycle.py`, `main.py` startup.

**Rule:** Production boots fail closed for Mongo, missing work indexes, or missing public transaction CAS. JSON/SQLite are single-worker development stores with reconciliation only.

**Verification:** `tests/test_work_kernel_lifecycle.py`.

### I-HARNESS-01 — Resident sessions are rooted and tenant-bound

**Scope:** `backend/app/models/nodes.py` (`HarnessSession`),
`backend/app/models/edges.py` (`HAS_HARNESS_SESSION`), and native Harness
session/store services.

**Rule:** Every persisted `HarnessSession` is attached directly to its owning
`ChatThread` through `HAS_HARNESS_SESSION` in the same transaction as session
creation. The session's principal, workspace, thread, binding, and generation
must agree with the authenticated ChatThread and authorized execution scope;
client and model output cannot select or widen those values. Historical
sessions remain attached when a binding changes. A ChatThread pointer to its
active session is a cache only; it never replaces the edge or session record.
Detailed model history, step events, and tool-effect receipts are encrypted
`Object` records scoped to the same execution namespace, not detached graph
Nodes. Production session activation and checkpoint advancement require
shared-store compare-and-set/fencing; process-local locks are insufficient.

**Verification:** `backend/tests/native_harness/wp_02/` and WP-03 PostgreSQL
concurrency tests.

### I-WORK-07 — Approved work prices every physical model dispatch

**Scope:** Native SDK transport, `work_model_admission.py`, host bounds/price hooks,
and the shared mandate ledger.

**Rule:** Durable mandate lineage, not the presence of a chat work context,
selects mandatory admission. The physical adapter supplies final mapped SDK
input, exact route generation and physical request identity. A trusted host
must attest all billed input/output ceilings and applicable account pricing.
Missing, expired or changed evidence prevents SDK dispatch. A current lease and
reviewed shared-budget hold plus fenced dispatch intent precede each SDK call.
After required intent receipt storage, a single-use readiness callback rechecks
current permission and route authority, approved lineage, lease, retained
physical binding and shared hold. Retained bounds and quote expiry are checked
again after the final transaction closes, immediately before entering the SDK.
Missing readiness or a denial prevents dispatch and keeps any existing hold.
Logical ordinals are distinct from physical IDs; a restarted adapter cannot
reuse an uncertain slot. Only persisted definitive provider cost settles the
hold; unknown or calculated cost retains it. Ordinary chat work continues to
use lease/permission/route fencing without acquiring mandate authority.

**Verification:** `backend/tests/test_work_model_admission.py`,
`backend/tests/contract/test_work_model_admission_postgres.py`, and shared-ledger
and model-receipt contract tests. Public executable approval and tool admission
remain separately gated under I-WORK-05.

### I-HARNESS-02 — Restored chat input matches accepted content

**Scope:** `chat_turn_submissions.py`, `chat_turn_worker_input.py`.

**Rule:** A durable native chat worker verifies canonical message parts,
metadata, parent and encrypted typed host context against the accepted request
fingerprint before provider preparation. Matching message identity and tenant
scope alone do not authenticate its content. Missing or changed fingerprints
fail closed. Historical omitted host context may match its typed empty
representation only when the restored context is entirely empty. Host
directives remain outside canonical user message content.

**Verification:** `backend/tests/native_harness/wp_03/test_submission_idempotency.py`
and dispatcher-level changed-message cases in
`backend/tests/native_harness/wp_03/test_chat_turn_worker_postgres.py`.

## Verification and change discipline

The invariant IDs are stable references. Retired A2A IDs impose no runtime contract and are removed from the active catalog; historical records remain in Git. Active authority and admission clauses are not weakened by removing milestone prose.

Run relevant invariant and behavior tests, the staged guards, and the broad local gate for substrate changes. PostgreSQL transaction/CAS proof, browser readback, artifacts, and deployment qualification remain separate. See [contributing](developer/CONTRIBUTING.md) and [qualification](ops/QUALIFICATION.md).
