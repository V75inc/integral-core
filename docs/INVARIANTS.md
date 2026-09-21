# Integral Substrate Invariants

**Status:** Living document. **First authored:** Phase 3.1 (`2026-05-16`),
per [AGENTS.md](../AGENTS.md) substrate-touching protocol.

This file is the canonical enumeration of substrate-wide invariants every
phase / plan / commit must preserve. It is the target referenced by the
AGENTS.md *substrate-touching plan protocol*:

> When working on substrate-touching code, consult `docs/INVARIANTS.md`
> (authored in M1) and ensure plan-checker enumerates which invariants
> the change preserves.

Every substrate-touching plan-checker (`*-PLAN-CHECK.md`) MUST enumerate
which invariants below the plan preserves, and MUST append any newly
introduced invariants to this file in the same commit that lands the new
constraint.

---

## Source-of-Truth Literals (single definition + AST grep gate)

Each invariant below states: **(a)** the canonical source file, **(b)**
the grep gate that enforces "exactly one definition".

| Literal | Source file | Gate (expected count) |
| --- | --- | --- |
| `ActorKind` | `backend/app/schemas/provenance.py` | `grep -rE "^ActorKind\s*=\s*Literal" backend/app/ --include="*.py" \| grep -v __pycache__` → **1** |
| `PolicyAction` | `backend/app/schemas/policy.py` | `grep -rE "^PolicyAction\s*=\s*Literal" backend/app/ --include="*.py" \| grep -v __pycache__` → **1** |
| `ChangeEventAction` | `backend/app/schemas/audit.py` | `grep -rE "^ChangeEventAction\s*=\s*Literal" backend/app/ --include="*.py" \| grep -v __pycache__` → **1** |
| `AgentType` | `backend/app/agentive/__init__.py` (Phase 1 D-09) | same idiom against `AgentType` → **1** |

### Additive Literal-expansion idiom

(Phase 2 02-02 + Phase 3 03-03 + 03-05 + Phase 3.1 03.1-03 precedent.)

Extend a Literal by **appending members inside the existing definition**.
NEVER create a second `XxxLiteral = Literal[...]` line elsewhere in the
codebase. The single-Literal grep gates above are the canary; if any of
them returns a count > 1 the plan-check fails and the regression is
treated as a Rule-1 substrate bug.

### `PolicyAction` strict-supersets `ChangeEventAction`

For every member of `ChangeEventAction` that is **not** an audit-only
`*.deny` action, the same string must also be a member of `PolicyAction`.
The audit-only exemptions are `policy.deny` and `anchor.deny` — they
appear in `ChangeEventAction` (recording denials) but not in
`PolicyAction` (no policy can be "allowed" to deny).

Gate (Python):

```python
from typing import get_args
from app.schemas.policy import PolicyAction
from app.schemas.audit import ChangeEventAction

cea = set(get_args(ChangeEventAction))
pa = set(get_args(PolicyAction))
audit_only = {"anchor.deny", "policy.deny"}
missing = (cea - audit_only) - pa
assert not missing, f"PolicyAction missing CEA non-audit members: {missing}"
```

---

## Edge Naming Convention (Phase 1 D-07)

Every edge declares **two names** in `backend/app/models/edges.py`:

- A **PascalCase class** subclassing `Edge` (the persisted type).
- An **ALL_CAPS module alias** pointing at the class (the ergonomic
  callsite name).

Example:

```python
class Anchors(Edge): ...
ANCHORS = Anchors

class TemplatedFrom(Edge): ...
TEMPLATED_FROM = TemplatedFrom
```

Existing pairs in the catalogue: `Owns/OWNS`, `IsMemberOf/IS_MEMBER_OF`,
`CollaboratesOn/COLLABORATES_ON`, `ExcludedFrom/EXCLUDED_FROM`,
`Contains/CONTAINS`, `HasContentProfile/HAS_CONTENT_PROFILE`,
`DefinesTrackProfile/DEFINES_TRACK_PROFILE`, `IsOfType/IS_OF_TYPE`,
`TaggedWith/TAGGED_WITH`, `References/REFERENCES`,
`UsesTemplate/USES_TEMPLATE`, `HasComment/HAS_COMMENT`,
`AuthoredBy/AUTHORED_BY`, `Mentions/MENTIONS`,
`HasAttachment/HAS_ATTACHMENT`, `HasNotification/HAS_NOTIFICATION`,
`InvitedTo/INVITED_TO`, `Catalogs/CATALOGS`, `HasPolicy/HAS_POLICY`,
`Anchors/ANCHORS` (Phase 3.1), `TemplatedFrom/TEMPLATED_FROM`
(Phase 3.1), `HasApplicationDefinition/HAS_APPLICATION_DEFINITION` (WP-04).

### I-APP-DEF-01 — Active-Definition-Authority (WP-04)

Every App materialized through the package lifecycle has one active
`ApplicationDefinition` revision. The definition is attached with
`App —HAS_APPLICATION_DEFINITION→ ApplicationDefinition`, and
`App.active_definition_id` / `App.active_definition_revision` are only
denormalized pointers to that edge target. A revision's canonical manifest,
requirement ledger and provenance are immutable after compilation; a changed
contract appends a revision and marks the prior active revision `superseded`.

Content Profiles remain the schema/composition component. They are not the
sole authorization or execution authority for an installed App: an operation
that needs the effective contract resolves the active definition first. This
preserves package provenance and gives upgrades a stable base for a later
three-way merge.

---

## Anchor Pattern Invariants (Phase 3.1)

### ANCHORS write-path single-helper

`ANCHORS` edge writes occur **only** inside `_sync_anchor_edges` in
`backend/app/services/content_profile_graph.py`. Direct
`entry.connect(track, edge=ANCHORS, ...)` calls in API handlers,
services, seed files, or agent tools are forbidden.

Gate:

```bash
grep -rE "edge=ANCHORS|edge=Anchors\b" backend/app/ --include="*.py" \
  | grep -v __pycache__ \
  | grep -v _sync_anchor_edges \
  | grep -v test_ \
  | wc -l   # expect 0
```

This invariant guarantees that the `anchor.create` / `anchor.delete`
policy gates ALWAYS run before an `ANCHORS` edge is materialized, and
that the same-workspace + same-CP-by-reference invariants below are
enforced at one chokepoint.

### Same-workspace constraint on anchored Tracks

Anchored Tracks always inherit `source_track.workspace_id`. Cross-workspace
target Track ids are rejected at **two** layers:

1. **Validator layer.** `_validate_relation_values` in
   `content_profile_runtime.py` rejects manifests that reference a
   cross-workspace track template id at draft/publish time.
2. **Provision layer.** `materialize_anchor_track` in
   `backend/app/services/content_profile_graph.py` stamps `workspace_id` from the source
   Track during auto-provision; cross-workspace template references
   never reach the `Track.create` call.

Cross-workspace use cases must use the sibling-track pattern plus
guest-grants on the referenced Entry; see
[content-profiles/COMPOSITION_PATTERNS.md](./content-profiles/COMPOSITION_PATTERNS.md)
*Negative Space*.

### Shared CP by reference (no per-anchor clone in v1)

Two anchor entries auto-provisioned from the same template share the
**same** `Track.attached_content_profile_id` scalar **and** the same
`HAS_CONTENT_PROFILE` edge target node id. There is no clone, no
copy-on-write, no fork-on-edit for anchor pattern v1.

Verified by `test_shared_cp_by_reference_invariant` in
`backend/tests/test_anchor_provision.py` and by the end-to-end test in
`backend/tests/test_anchor_integration.py`.

### Scalar + edge agree

For every Track (anchored or not):

```python
Track.attached_content_profile_id == (
    await track.nodes(
        edge=["HAS_CONTENT_PROFILE"], direction="out", node=["ContentProfile"]
    )
)[0].id
```

Fast-path lookups depend on this denormalized scalar matching the
authoritative edge target. Any code path that updates one must update
the other in the same transaction.

### Cascade by policy (hard cascade in v1)

Hard-delete of an Entry walks its outbound `ANCHORS` edges and, per
`policy_engine.evaluate(action="anchor.cascade")`, hard-deletes each
anchored Track plus its contained Entries. Denial preserves the Track
and emits a `policy.deny` ChangeEvent
(`details.failed_action="anchor.cascade"`) via the Phase 3 03-05
denial-emit path.

v1 is **hard cascade only.** Archive-then-delete (soft cascade with
grave period) is deferred to a separate future phase. This invariant
keeps the deletion semantics single-rail.

### USES_TEMPLATE preserved verbatim (NOT overloaded)

`USES_TEMPLATE` edge stays `Track → Track` with pre-3.1 semantics
(track-from-track templating). Template-provenance for auto-provisioned
anchored Tracks uses the **new** `TEMPLATED_FROM` edge
(`Track → ContentProfile`). Overloading `USES_TEMPLATE` is forbidden.

### TEMPLATED_FROM single-writer

`TEMPLATED_FROM` edges are written only inside `materialize_anchor_track`
in `backend/app/services/content_profile_graph.py`. The plan-checker grep
gate enforces:

```bash
grep -rE "edge=TEMPLATED_FROM|edge=TemplatedFrom\b" backend/app/ \
  --include="*.py" \
  | grep -v __pycache__ \
  | grep -v materialize_anchor_track \
  | grep -v test_ \
  | wc -l   # expect 0
```

### No JSON nesting / no per-entry hierarchical containment

Per [docs/content-profiles/README.md](./content-profiles/README.md)
*Modeling Tenets* and [docs/content-profiles/COMPOSITION_PATTERNS.md](./content-profiles/COMPOSITION_PATTERNS.md)
*Negative Space*: an Entry never "owns" a Track in the `CONTAINS` sense.
The `ANCHORS` edge is an **additional pointer**, not a containment edge.

The only containment chain is
`Workspace CONTAINS App CONTAINS Track CONTAINS Entry` (App nodes persist
with the ``WorkspaceApp`` ``__entity_name__`` discriminator — the runtime
class is ``App``). No
`Entry CONTAINS Track`, no `Entry CONTAINS Entry`, no
`Track CONTAINS Track`. JSON-nested operational entities inside
`Entry.custom_fields` are forbidden — see *Negative Space* for the
substrate's rationale.

### No cross-workspace anchoring

(Restated from "Same-workspace constraint", surfaced as a top-level
invariant for downstream phase plan-checkers.) Anchor pattern v1 does
not span workspaces.

---

## ChangeEvent Emission

### Single emission path (Phase 2 D-05)

Every mutation handler emits change events via `emit_change_event`.
Direct `ChangeEvent.create(...)` calls outside the emission helper are
forbidden. AST grep gate: `backend/tests/test_change_event_no_bypass.py`
enforces.

### Recursion guard via `_internal_actor` (Phase 3 03-05 D-10)

`policy_engine.evaluate` accepts `_internal_actor: Optional[Subject]`;
when set, the engine short-circuits with
`Decision(allowed=True, reason="system_subject_internal")`. Used by the
denial-emit path to prevent infinite recursion if a future code path
inside `emit_change_event` itself calls `evaluate`.

`_internal_actor` is **in-process control flow only** — it MUST NEVER be
persisted to the ChangeEvent row, the audit log, or any external
observability surface.

### WS broadcast skip for high-fan-out actions (Phase 3 03-05 + Phase 3.1 03.1-03)

```python
BROADCAST_SKIP_ACTIONS = {"policy.deny", "anchor.cascade"}
```

`policy.deny` is skipped because denial floods are common (one per
forbidden action); `anchor.cascade` is skipped because a single parent
deletion can fan out to thousands of cascade events. Both still land in
the audit log; only WS push is suppressed.

---

## Authorization

### Single `policy_engine.evaluate` (Phase 3 POL-01)

All API handlers consult `policy_engine.evaluate(subject, action,
resource)` for authorization decisions. No legacy `has_*_access` /
`can_*_access` helpers in `backend/app/api/`. Phase 3 sweep grep gate
enforces.

### Fail-closed default for agents (Phase 3 03-03)

`Subject(kind="agent")` with no `HAS_POLICY` edges returns
`Decision(allowed=False, reason="fail_closed_no_policy")`. Agents must
have explicit Policy grants to perform any action.

### Dogfooded admin surface (Phase 3 03-03)

`/api/policies` CRUD endpoints themselves consult `policy_engine.evaluate`
with `action="policy.create|read|update|delete"`. The authorization
substrate authorizes its own admin surface — no bypass.

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
  `can_edit_*`; everything else (tags, content_profiles, entry_types, anchors,
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
direct grant. Owner-only carve-outs (delete, collaborator management,
share-link minting) likewise never cascade — they are bound to the
`OWNS` edge on the specific resource.

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

---

## Workspace Scope

### Backend-authoritative workspace scope

The active workspace for a request is resolved backend-side, with precedence:
the `X-Integral-Scope: ws:<workspace_id>` header (the caller's declared active
workspace) → the user's stored `active_workspace_id` → the user's personal
workspace (fail-closed default). Resolution lives in
`backend/app/services/request_scope.py` +
`backend/app/services/workspace_resolver.py`. The backend validates the caller
has access to the resolved scope and refuses cross-workspace reads — no
client-supplied workspace bleed.

The agent's chat turns carry the same scope: the frontend sends the live active
scope header on the chat stream (the in-memory `getActiveScopeHeader()` kept in
sync by `ScopeContext`, authoritative on a fresh boot), so the resident agent's
tool calls operate in the workspace the user is looking at — not a personal
fallback. Bless-time executors bind the same scope via the
`current_scope_workspace_id` context var.

---

## describe_substrate Additive Extension

The `integral_describe_substrate` agent-introspection tool's payload
shape extends **additively only**. Legacy keys
(`field_types`, `view_types`, `relation_targets`, etc.) keep their
pre-3.1 shape. New keys introduced in Phase 3.1
(`edges`, `template_var_resolvers`, `governance_actions`) are appended;
no existing key is renamed, removed, or has its value type changed.

Plan-checker for `agent_profiles.describe_substrate` MUST verify legacy
keys' shapes are unchanged in any future phase.

---

## How to Update This File

Future substrate-touching plans:

1. Identify the substrate surface the plan touches (Literal, edge,
   policy action, change event action, write-path helper, validator,
   workspace-scope, change-event emission, agent introspection).
2. If the plan introduces a **new** invariant, append a section under
   the appropriate H2 above. State **(a)** the rule in plain prose,
   **(b)** the gate (test name, grep command, or invariant assertion)
   that enforces it.
3. If the plan preserves existing invariants, enumerate them in the
   plan's `*-PLAN-CHECK.md` `<must_haves>` block per the AGENTS.md
   substrate-touching plan protocol.
4. Cross-link the plan that introduced or modified the invariant in the
   commit body that lands the INVARIANTS.md change.
5. Never silently weaken or remove an invariant. Weakening requires a
   dedicated plan that justifies the change in CONTEXT.md, with
   plan-checker explicitly enumerating the deprecation and any
   migration test cases.

---

## Phase 4 — Hybrid Retrieval Invariants

The following invariants apply to every retrieval call (`POST /api/retrieve`,
`integral_query` MCP tool, agent in-process retrieval). Plan 04-01 stages
the substrate; Plan 04-02 wires the endpoint that consumes it. Both must
preserve the contract below.

### I-RET-01 — Permission-Filter-At-Retrieval (`permission-filter-at-retrieval`)

Tag: `permission-filter-at-retrieval`. Invariant id: `I-RET-01`.

Every result returned from the embedding store MUST round-trip through
`policy_engine.evaluate(action="entry.read", resource=...)` before being
included in a response. The embedding store is a PROJECTION of the
graph, NOT a parallel store and NOT a permission bypass. Code path: see
`backend/app/api/retrieve.py` per-candidate filter loop (Plan 04-02).

### I-RET-02 — No-Index-Bypass (`no-index-bypass`)

Tag: `no-index-bypass`. Invariant id: `I-RET-02`.

The vector store MUST NOT be the system-of-record for any access
decision. SQL pre-filter on `track_id` (I-RET-04 below) is a perf
optimization that runs IN ADDITION TO (never INSTEAD OF) the
per-candidate policy check. Removing the policy check would break
ShareLink / collaborator / workspace-guest access paths that SQL-on-
`track_id` alone cannot express.

### I-RET-03 — Soft-Delete-Not-Hard-Delete (`soft-delete-not-hard-delete`)

Tag: `soft-delete-not-hard-delete`. Invariant id: `I-RET-03`.

On `Entry.delete` (hard delete), the embedding row MUST be
soft-deleted (`deleted_at` set; excluded from `search`), NEVER
hard-deleted. Preserves agent-memory recall + audit-log integrity.
TTL cleanup is a separate background concern, out of scope for v1.
The entry-delete hook in `backend/app/api/entries.py`
(`_soft_delete_embedding`) routes through `EmbeddingStore.soft_delete`;
`hard_delete` is reserved for offline backfill / TTL operations only.

### I-RET-04 — Scope-Pre-Filter-Via-Track-Id-Index-Field (`scope-pre-filter-via-track_id-index-field`)

Tag: `scope-pre-filter-via-track_id-index-field`. Invariant id: `I-RET-04`.

The embedding-store driver MUST expose `track_id` as a queryable
field on the vector index so the driver can pre-filter at index level
when `scope="track:<id>"`. Concretely:

* MongoDB Atlas Vector Search driver (`atlas_vector_driver.py`,
  Wave 3 of the SaaS-deployment plan) declares `track_id` as a
  `filter`-typed field on the `entry_embedding_vector_idx` index and
  applies it inside `$vectorSearch`.
* (Retired) sqlite-vec driver carried `track_id` on the
  `entry_embedding_meta` sibling SQL table for the same purpose; the
  driver was removed in Wave 3, but the invariant predates and
  outlives any specific backend.

This is a perf-only optimization; the per-candidate policy filter
(I-RET-01) is ALWAYS still applied to whatever survives the
pre-filter.

### I-RET-05 — No-Tag-Only-Reembed (`no-tag-only-reembed`)

Tag: `no-tag-only-reembed`. Invariant id: `I-RET-05`.

Tag mutations (`add_tag_to_entry`, `remove_tag_from_entry`,
`add_reaction`, `remove_reaction`) MUST NOT trigger re-embed. Embed
input is `title + body + string-valued custom_fields` only; tags are
projected at retrieval time via filter, not embedded.

### Single-Literal Invariants Preserved

Phase 4 introduces zero new members on any of the three single-Literal
substrate gates:

- `PolicyAction` (`backend/app/schemas/policy.py`) — Phase 4 adds ZERO members.
- `ChangeEventAction` (`backend/app/schemas/audit.py`) — Phase 4 adds ZERO members.
- `ActorKind` (`backend/app/schemas/provenance.py`) — Phase 4 adds ZERO members.

The grep gates established in earlier phases (exactly one top-level
`Literal` declaration per name across `backend/app/`) continue to hold.

---

## Phase 4 — Agent Scratch Substrate Invariants

These invariants apply to the per-user scratch Track and the
``promote_scratch_entry`` operation introduced in Plan 04-04
(MEM-01 + MEM-02 + MEM-03). They sit alongside (not above or below)
the Phase 1–3 invariants — substrate-touching changes that intersect
the scratch surface MUST preserve all five.

### I-SCRATCH-01 — Scratch-Track-In-Personal-Workspace

The per-user scratch Track MUST live in the user's personal workspace
(``Workspace.kind == "personal"``). One scratch Track per user. NEVER
provisioned in an organization-kind workspace. Enforced by
``app.services.agent_scratch.provision_scratch_track`` via
``ensure_personal_workspace(user)``.

### I-SCRATCH-02 — Scratch-Track-Kind-Discriminator

Scratch Tracks MUST carry ``Track.kind = "agent_scratch"``. The
discriminator is graph-queryable; scratch-lookup MUST NOT use
name-string matching against ``Track.title``. The field is additive
(default ``""``) so existing Tracks are unaffected — no migration
needed (CONTEXT lock #1).

### I-SCRATCH-03 — Promote-Preserves-Derived-From

``promote_scratch_entry`` MUST set
``new_entry.provenance.derived_from = [source_entry_id]`` on the
target-track entry (single-element list — ``Provenance.derived_from``
is ``List[str]``, not ``Optional[str]``). This is the MEM-03 contract
for agent-memory recall + audit traceability.

### I-SCRATCH-04 — Source-Archive-Via-Status-Not-Delete

After promotion the source scratch entry MUST be archived
(``Entry.status = "archived"``) — NOT hard-deleted. Reuses the
existing ``Entry.status`` field (CONTEXT lock #2 — no new
``archived_at`` / ``deleted_at`` field on Entry). The retrieval-side
embedding for the source is soft-deleted best-effort via
``embedding_store.soft_delete(source.id)`` in the same operation;
unavailability of the embedding store is a logged warning, never a
raised exception.

### I-SCRATCH-05 — Promote-Permission-Gates

``promote_scratch_entry`` MUST call ``policy_engine.evaluate`` TWICE:

1. ``action="entry.read"`` on the source (scope ``track:<source>``)
2. ``action="entry.create"`` on the target (scope ``track:<target>``)

Either denial raises ``InsufficientPermissionsError``. The policy
engine auto-emits ``policy.deny`` ChangeEvents on denial via the
Phase 3 D-12 path — no additional emission is required at the
``promote_scratch_entry`` boundary.

### Single-Literal Invariants Preserved by Plan 04-04

- ``PolicyAction`` (``backend/app/schemas/policy.py``) — Plan 04-04
  adds ZERO members.
- ``ChangeEventAction`` (``backend/app/schemas/audit.py``) — Plan
  04-04 emits existing ``track.create`` / ``entry.create`` /
  ``entry.update`` actions only.
- ``ActorKind`` (``backend/app/schemas/provenance.py``) — Plan 04-04
  uses existing ``"human"`` / ``"agent"`` members only.

---

## Phase 5 — Connector Framework Invariants

Architecture authority for the full connector subsystem (native adapters, MCP
mounts, package catalog, credentials, inbound/outbound split):
``docs/backend/adr/010-connector-subsystem-architecture.md``. MCP client mount
implementation: ``docs/backend/adr/009-mcp-as-connector.md``.

The following invariants apply to every connector authoring + sync path
(``backend/app/services/connectors/``, ``backend/app/agentive/connectors/``,
``backend/app/profiles/*/`` connector bundles when present).

### I-CON-01 — Provenance Split Shape

Synced Entries MUST set ``provenance.source = "connector"`` (the
ActorKind Literal member, single 9-char string) AND
``provenance.source_id = "<connector_id>:<external_id>"`` (free string
for the canonical instance discriminator). NEVER write the free-string
form into ``provenance.source`` — the assignment fails the Pydantic
ActorKind boundary because
``ActorKind = Literal["human", "agent", "connector", "system"]``.

Plan-checker grep gate (expected: 0 matches):

```bash
grep -rE 'provenance\.source\s*=\s*"connector:' backend/app/ --include='*.py'
```

### I-CON-02 — IS_CONNECTED_TO Is The Only Connector→Track Binding

Connector → Track binding MUST flow over the ``IS_CONNECTED_TO`` edge
(``backend/app/models/edges.py`` — Phase 5 Plan 05-01). The legacy
``Connector.mapping_profile`` field is deprecated; new connectors MUST
NOT read or write it for track-binding purposes. The edge carries the
per-binding mapping YAML and supports one connector binding to multiple
Tracks (e.g. a single GitHub Issues connector pulling issues into Track A
and PRs into Track B).

SyncConnector subclass dispatch reads ``Connector.subclass_slug`` — NEVER
``Connector.mapping_profile`` — to look up the registered subclass.

### I-CON-03 — Sync-Connector Slugs Are Global Keys

``app/services/connectors/registry.py`` enforces single-registration:
duplicate ``@register_sync_connector("<slug>")`` raises ``ValueError``.
Slugs participate in the idempotency-key namespace (see I-CON-04 default
``idempotency_key_for``). NEVER register two classes against the same
slug; NEVER mutate ``_SYNC_REGISTRY`` directly — only the decorator and
``reset_sync_registry()`` (test-only) touch it.

### I-CON-04 — Per-Connector Policy Materialized At Create-Time

Every ``Connector`` Node created via
``connector_registry_node.create_connector`` gets a per-connector
``Policy`` attached via ``HAS_POLICY`` edge at create time
(``materialize_policies_for_connector``). Without this,
``policy_engine.evaluate(Subject(kind="connector"), ...)`` would always
return ``fail_closed_no_policy`` and the sync runtime (Plan 05-03) could
never write Entries on the connector's behalf. Policy actions baseline:
``connector.sync``, ``entry.create``, ``entry.update``.

The default ``SyncConnector.idempotency_key_for`` hashes
``(slug, external_id)`` together so two connectors against the same
external service cannot produce colliding keys (RESEARCH Pitfall 4).

### I-CON-05 — Reference Connectors Are Agentive; Their Seeded CPs Are Core

Reference ``SyncConnector`` subclasses (the implementations that talk to
external APIs — e.g. ``GitHubIssuesConnector``) live in
``backend/app/agentive/connectors/`` and are AGENTIVE_ENABLED-gated by
the package boundary (D-08 invariant — Phase 1). The seeded
``ContentProfile`` packages that define how external entities map to
Entries (e.g. GitHub Issues track templates) live in
``backend/app/profiles/<slug>/profile.yaml`` and are discovered by
``content_profile_loader.load_library_profiles()`` at boot.

**Rationale:** The CP package is pure metadata (manifest dict describing
the ``github_issue`` EntryType shape). It belongs in core because authors
may preview / merge it onto a Track regardless of whether the agentive
layer is loaded. The connector subclass is executable code that calls
external APIs — it belongs in agentive because the sync runtime only
invokes it when ``AGENTIVE_ENABLED=1``.

A non-agentive boot has the seeded CP available (for library inspection
+ manual track attachment) but the connector subclass is not registered
— sync attempts against a connector whose slug points to an agentive-
resident subclass fall through with a logged warning ("no SyncConnector
registered for slug …") rather than raising. The slug namespace is
global; the registration is conditional.

Side-effect imports of reference subclasses live in
``backend/app/agentive/connectors/__init__.py`` (e.g.
``from . import github_issues # noqa: F401``) so the slug registers at
agentive package import time and only at agentive package import time.

### Single-Literal Invariants After Phase 5 Plan 05-01

- ``PolicyAction`` (``backend/app/schemas/policy.py``) — Phase 5 Plan
  05-01 ADDS 8 members inside the existing Literal: ``connector.sync``,
  ``connector.sync.start``, ``connector.sync.complete``,
  ``connector.sync.failed``, ``migration.publish``,
  ``migration.force_publish``, ``migration.run``, ``conflict.resolve``.
  Single-Literal AST grep gate continues to return 1.
- ``ChangeEventAction`` (``backend/app/schemas/audit.py``) — Phase 5
  Plan 05-01 ADDS 4 members inside the existing Literal:
  ``connector.sync.start``, ``connector.sync.complete``,
  ``connector.sync.failed``, ``migration.run``. Single-Literal AST grep
  gate continues to return 1.
- ``ActorKind`` (``backend/app/schemas/provenance.py``) — Phase 5 Plan
  05-01 adds ZERO members (``connector`` is already a Phase 1 member).
- ``AgentType`` (``backend/app/agentive/__init__.py``) — UNCHANGED.

### Strict-Superset Invariant After Phase 5 Plan 05-01

Every new ``ChangeEventAction`` member from Plan 05-01 (4 members) is
also a ``PolicyAction`` member. The PolicyAction-only additions
(``connector.sync``, ``migration.publish``, ``migration.force_publish``,
``conflict.resolve``) are policy-only by design:

- ``connector.sync`` is a per-attempt gate (the start/complete/failed
  events log the lifecycle independently).
- ``migration.publish`` + ``migration.force_publish`` separate the
  destructive escape from normal publish.
- ``conflict.resolve`` gates the resolve endpoint (no separate audit
  event needed — the underlying ``entry.update`` covers it).

---

## Phase 5 — Schema Migration Invariants

The following invariants apply to every Content Profile publish / migration
path (``backend/app/services/migrations/``,
``backend/app/services/content_profile_migrations.py``,
``backend/app/services/content_profile_atomic_swap.py``). Landed by
Phase 5 Plan 05-02 (MIG-01 / MIG-02 / MIG-03).

### I-MIG-01 — Single Migration Runner

The op catalogue at ``_OP_HANDLERS`` in
``backend/app/services/content_profile_migrations.py`` is the ONLY place
where declarative migration ops are dispatched. New ops are added by
APPENDING to this dict (Plan 05-02 added 3 — total 9 ops:
``rename_field``, ``default_fill``, ``delete_field``,
``prune_enum_option``, ``coerce_type``, ``move_field``,
``add_field_with_default``, ``rename_entry_type``, ``change_view_type``).
NEVER create a parallel ``_OP_HANDLERS`` dict in another module. The async
runner (``backend/app/services/migrations/runner.py``) imports this dict
and walks it — it does NOT define its own handlers. Locked decision #2 of
Plan 05-02: extension, never greenfield.

### I-MIG-02 — Async Per-Entry Tracker, Recovery, and Retry

Migration runs spawn via ``asyncio.create_task`` inside the request
lifecycle. The task may outlive the HTTP response. At startup,
``reconcile_orphaned_migrations`` converts durable ``pending`` / ``running``
entries under an in-progress Content Profile to ``failed`` with an
interruption reason; Core never calls an interrupted migration complete.
Authorized editors inspect bounded failed-item diagnostics through
``GET /api/content-profiles/{id}/migration-status`` and restart supported
declarative operations through ``POST /api/content-profiles/{id}/retry-migration``.
Retry always uses the same dispatcher and idempotent operation catalogue; it
never introduces a recovery-only execution path.

### I-MIG-03 — No-Migration-Path Reject Default; force=true Is Destructive Escape

``publish_draft`` rejects any manifest whose diff produces
``would_need_migration`` impacts with no matching ``migrations[].ops[]``
entry (HTTP 422 with ``details.unhandled_breaks``). Override via
``?force=true`` query param. **Forced publish does NOT migrate existing
Entries** — they remain on the prior schema until manually fixed.
``migration.publish`` and ``migration.force_publish`` are SEPARATE
``PolicyAction`` members so a Policy can allow normal publish but DENY
force (security: prevents force-escape abuse — STRIDE
Tampering/Repudiation mitigation T-05-02-01).

### I-MIG-04 — Declarative Ops Only In v1

The migration DSL is declarative-only: only ops in ``_OP_HANDLERS`` are
executable. Python migration class hooks are NOT a v1 feature
(security + audit black hole — see Pillar 4 signed-code-plugin concern).
One-off transforms requiring imperative code MUST go through the
fork-draft → agent-patch → publish lifecycle, NOT through inline Python
in the manifest. A future phase may revisit if the declarative catalogue
proves insufficient.

### Single-Literal Invariants After Phase 5 Plan 05-02

Phase 5 Plan 05-02 adds ZERO new Literal members. ``PolicyAction`` +
``ChangeEventAction`` + ``ActorKind`` grep gates all remain at exactly
1 match. The migration runner consumes the 3 ``PolicyAction`` members
(``migration.publish``, ``migration.force_publish``, ``migration.run``) +
the 1 ``ChangeEventAction`` member (``migration.run``) added by Wave 1
sibling Plan 05-01.

---

## Phase 5 — Connector Sync Runtime Invariants

The following invariants apply to every connector pull + materialize +
conflict path (``backend/app/services/connectors/sync_runtime.py``,
``backend/app/services/connectors/sync_scheduler.py``,
``backend/app/services/connectors/conflict_records.py``,
``backend/app/api/connectors.py``,
``backend/app/api/conflicts.py``,
``backend/app/api/entries.py::update_entry``'s mirror_only gate).

### I-SYNC-01 — Connector-As-Actor In Every Sync Emit

Every ``ChangeEvent`` emitted from inside ``sync_one_connector`` MUST
carry ``actor_kind="connector"`` + ``actor_id=<connector.id>`` —
NEVER the caller's user_id (e.g. the human who invoked the on-demand
``POST /api/connectors/{id}/sync`` endpoint). This covers the lifecycle
pair (``connector.sync.start`` + ``connector.sync.complete`` /
``connector.sync.failed``) AND every per-record emit (``entry.create``
for new + ``entry.update`` for upsert).

The audit-trail contract: operators must be able to distinguish
sync-driven writes from human / agent writes by inspecting ``actor_kind``
alone (``GET /api/audit-log?actor_kind=connector`` returns only synced
writes).

Provenance carries the same identity split (mirrors I-CON-01):
``provenance.source = "connector"`` (the ``ActorKind`` Literal member) +
``provenance.source_id = "<connector_id>:<external_id>"`` (free string).
NEVER the free-string ``"connector:..."`` form on ``source`` — the
Plan 05-01 grep gate
``grep -rE 'provenance\.source\s*=\s*"connector:' backend/app/services/connectors/``
returns 0 matches.

### I-SYNC-02 — sync_loop TESTING Gate Inherited; AGENTIVE_ENABLED Independence

The asyncio ``sync_loop`` spawn site sits in ``app/main.py:_startup``
immediately after the existing ``ttl_reclaim_loop`` spawn (around
L260-272). It does NOT add its own TESTING gate — the function-scope
``PYTEST_CURRENT_TEST`` / ``TESTING`` short-circuit at the TOP of
``_startup`` (L137) returns before reaching the spawn site, so test
runs never accidentally fire external API calls (Pitfall 6, T-05-03-05).

The scheduler runs UNCONDITIONALLY of ``AGENTIVE_ENABLED``. Connector
subclasses live in ``app/agentive/connectors/`` and only register their
slugs when AGENTIVE_ENABLED=1. In non-agentive boots the SyncConnector
registry is empty AND ``Connector.find()`` returns zero rows (the
Connector Node itself stays AGENTIVE_ENABLED-gated), so each tick is a
cheap no-op.

### I-SYNC-03 — Idempotency Dedup Before Write; Re-Sync Is Upsert Never Duplicate

Every record yielded by ``SyncConnector.sync_pull`` MUST be
dedup-looked-up via
``Entry.find({"context.idempotency_key": sub.idempotency_key_for(record)})``
BEFORE any write. If a matching Entry exists, dispatch to the
conflict-policy branch (``mirror_only`` / ``last_write_wins`` /
``manual_resolve``). If absent, create a new Entry with the computed
``idempotency_key`` populated and the split provenance shape.

Locked decision §A3 fallback: if jvspatial's field-index on
``Entry.idempotency_key`` proves O(N) at execute time, fall back to a
``ConnectorRecord`` Node lookup via a ``Dedups`` edge (Connector → Entry
carrying ``idempotency_key`` as an edge property). The
``sync_one_connector`` call shape does NOT change — only the internal
``_lookup_by_idempotency_key`` helper differs. **Plan 05-03 execute-time
A3 verification:** the existing
``Entry.find({"context.<field>": value})`` selector idiom is in
production use across ``permissions.py``,
``workspace_storage_usage.py``, ``share_links.py``, etc. The
``context.idempotency_key`` filter performs at parity with those —
``ConnectorRecord`` fallback is NOT required for Plan 05-03.

### I-SYNC-04 — Conflict Records Persist As Nodes; Resolve Inherits entry.update Permission

``Conflict(Node)`` is core (registered in ``main.py:node_types``
ALWAYS-ON, NOT AGENTIVE_ENABLED-gated). The locked 9-field shape
(``connector_id``, ``entry_id``, ``local_snapshot``, ``external_snapshot``,
``detected_at``, ``resolved_at``, ``resolution``, ``resolved_by``,
``status``) is FROZEN — additions go through plan-checker.

``POST /api/conflicts/{id}/resolve`` gates via
``policy_engine.evaluate(action="conflict.resolve",
resource=Resource(kind="entry", id=<conflict.entry_id>))``. The
permission inherits from the underlying Entry's edit permission — a user
who can edit the Entry can resolve a Conflict on it. v1 ships records +
REST surface only; UI is Phase 7.

The ``applied_external`` resolution writes the external snapshot back
into the Entry AND emits a single ``entry.update`` ChangeEvent carrying
``details.resolved_conflict_id`` — ``conflict.resolve`` is
INTENTIONALLY NOT in ``ChangeEventAction`` (locked decision #5 final
list). The audit trail for a resolution event IS the ``entry.update``
emit. ``kept_local`` resolutions emit NOTHING — the Entry is unchanged
so there is no resource mutation to audit (the Conflict row itself
records the resolver + timestamp).

T-05-03-04 (future hardening): per-conflict permission filter on
``GET /api/conflicts`` is deferred; v1 returns ALL matching conflicts
to any authenticated caller.

### Single-Literal Invariants After Phase 5 Plan 05-03

Phase 5 Plan 05-03 adds ZERO new Literal members. ``PolicyAction`` +
``ChangeEventAction`` + ``ActorKind`` grep gates all remain at exactly
1 match. Plan 05-03 consumes:

- ``PolicyAction``: ``connector.sync`` (per-attempt gate), the three
  ``connector.sync.{start,complete,failed}`` lifecycle members, and
  ``conflict.resolve`` (PolicyAction-only — see I-SYNC-04 — NOT a
  ``ChangeEventAction``).
- ``ChangeEventAction``: ``connector.sync.{start,complete,failed}``.
- ``ActorKind``: ``"connector"`` on every emit (I-SYNC-01).

All consumed members were added by Wave 1 Plan 05-01.

---

## Phase 6 — ~~A2A Fabric~~ (RETIRED) & Profile-Aware Agent Tooling (Plan 06-01)

> **⚠️ I-A2A-01 … I-A2A-05 are RETIRED** — [ADR-003 · Singular resident harness](backend/adr/003-singular-resident-harness.md).
> The agent-to-agent fabric is not built: no cross-agent discovery, delegation, or
> `a2a.delegate` action. IDs are kept below for historical traceability only — they
> impose **no enforcement on new code**. Do not add `agent.discover` / `a2a.delegate`
> to `PolicyAction`/`ChangeEventAction`, and do not wire `mcp_adapter.py:_wrap_for_a2a`
> or `agent_actions/delegate.py`. External agents reach the substrate through the MCP
> surface under the same policy/staging/audit path as the resident (see
> [RESIDENT_HARNESS.md §7](product/RESIDENT_HARNESS.md)). The `AgentConfig.capabilities`
> and `policy_scope` fields may persist as inert metadata pending the facet-refactor plan.
> The **Profile-Aware Agent Tooling** invariants in this section (if any below the I-A2A
> block) remain in force.

The detailed I-A2A-01..05 specifications (capability validation, per-agent
baseline A2A grants, `a2a.delegate` emission, cross-agent detection) are
**removed** — the agent-to-agent fabric they governed was retired (ADR-003).
IDs retained for historical cross-reference only:

- **I-A2A-01** — `AgentConfig.capabilities` catalogue-validation *(retired)*
- **I-A2A-02** — `AgentConfig.policy_scope` Policy-id list *(field persists as inert metadata)*
- **I-A2A-03** — per-agent baseline Policy at registration *(now grants only `profile.author`)*
- **I-A2A-04** — capability validation registration-time-only *(retired)*
- **I-A2A-05** — `a2a.delegate` server-side-emit-only *(retired)*

`AgentConfig.scope` (`personal|org_facing|system`) survives as the facet
discriminator (ADR-003). The live profile-authoring invariants continue below.

---

## Phase 6 — Profile-Aware MCP Tools (Plan 06-04)

### I-PROFILE-01 — `integral_author_profile` v1 is deterministic template-fill

The `integral_author_profile` MCP tool (`POST /api/content-profiles/author`)
is v1-deterministic: it maps the agent-supplied `description` token set
against a keyword→library-package ruleset (`DOMAIN_KEYWORD_MAP` in
`backend/app/services/content_profile_author.py`) and template-fills the
matched library package's manifest. NO inline LLM call is made in v1
(CONTEXT lock §"Locked: integral_author_profile NL → manifest implementation").
v2 will swap in an inline LLM via the SAME handler signature; the
agent-side LLM currently does the NL→structured-input lift BEFORE
calling the tool.

**Keyword buckets (see `DOMAIN_KEYWORD_MAP`):**

- `bug | issue | defect | ticket` → "Bug Tracking"
- `lead | deal | opportunity | crm | contact` → "CRM"
- `project | delivery | pm | task` → "Projects"
- `goal | habit | okr` → "Personal Goals & Habits"
- `event | wedding | conference | rsvp` → "Event Planning"
- `content | post | publishing | calendar` → "Content Calendar"
- `candidate | applicant | hiring | recruitment` → "Recruitment Pipeline"
- `note | knowledge | wiki | kb` → "Personal Knowledge Base"

Scoring is `len(set(keywords) & desc_tokens)`; ties broken by declaration
order. Zero matches → generic `post` entry-type fallback with the
caller's `fields` payload (or default `body: markdown`).

**Two-tier gate (Pitfall 5):**

1. `policy_engine.evaluate(action="profile.author", scope=f"workspace:{workspace_id}")` — entry gate
2. `can_publish_content_profiles_under_workspace(user_id, workspace_id)` — workspace publish gate

BOTH must pass. Tier 1 is the agent-vs-human differentiator (humans get
default-human dispatch that delegates to the same workspace check;
agents need a Policy granting `profile.author`).

**Workspace scoping (locked-post-research #4):** `workspace_id` is
REQUIRED in the request body. NEVER derived from caller's
`AgentConfig.workspace_id` or personal-workspace fallback. Authored
profiles land in the library (`library_package=True`). Published CPs
are cataloged under the `ContentProfiles` registry; drafts are NOT
cataloged and do NOT surface via `integral_list_profiles?type_hint=`
until explicitly published (preserves the draft/publish lifecycle
semantic gap).

### I-PROFILE-02 — `integral_modify_profile` routes through Phase 3.1 + Phase 5

The `integral_modify_profile` MCP tool (`POST /api/content-profiles/{id}/modify`)
applies operations through Phase 3.1 `agent_profile_patches.apply_operations`
(NEVER direct manifest mutation; NEVER `eval`/`exec` — same allow-list
discipline as I-MIG-04) and publishes through Phase 3.1
`publish_draft(force=force)` — which automatically fires the Phase 5
reject_gate on non-migratable changes. `force=True` bypasses the
reject_gate (destructive escape; matches Phase 5 I-MIG-03 CONTEXT lock #5).

**Two paths (locked-post-research #5):**

- **Library package** (`library_package=True`): publish materializes new
  version; existing attached instances are NOT auto-migrated (manual
  re-merge required — librarian path).
- **Attached instance** (track/space scope): publish triggers Phase 5
  migration runtime over in-flight Entries.

**Reject-gate semantics:** non-migratable change WITHOUT `force=True` →
4xx with rejection descriptors surfaced by
`migrations/reject_gate.detect_unhandled_breaks` (Phase 5 I-MIG-03
preserved). The handler does NOT re-implement the gate check; it
delegates to `publish_draft(force=force)` so the invariant lives in
exactly one place.

**`force=True` authorization:** the existing `migration.force_publish`
PolicyAction (Phase 5 Plan 05-02) gates forced publishes — `modify`
itself does not enforce this; it relies on the underlying publish
endpoint's gate firing inside `publish_draft`.

**`type_hint` resolution (CONTEXT lock §"Locked: type_hint resolution" +
locked-post-research #8):** `type_hint` on `POST /api/tracks` and
`POST /api/spaces` resolves via the `resolve_type_hint` SERVICE helper
(Pitfall 6 — direct call, NOT MCP-wrapped re-dispatch — no recursion
through `integral_list_profiles`). Resolution rules:

- Zero scored>0 matches → fall back to default empty profile + warning.
- Single best match (top-1 by score, all others strictly lower) →
  adopt as `library_content_profile_id` (lossless when scores diverge).
- Two-or-more packages share the top score → 400
  `content_profile.type_hint_ambiguous` with `candidates: List[...]`
  in `details` (caller picks via re-call with the explicit picker).

`type_hint` is mutually exclusive with explicit picker fields
(`library_content_profile_id`, `space_track_template_content_profile_id`,
`space_track_type_key` on tracks); combining → 400
`content_profile.conflicting_picker`.

**MCP tool name overrides (locked-post-research #9):** three new
entries land INSIDE the existing `MCP_TOOL_NAME_OVERRIDES` dict at
`backend/app/agentive/tooling/name_overrides.py` (canonical home; the
`mcp_adapter_legacy.py` re-export shim has been deleted — nothing
imported it). Single-helper grep gate continues to return 1 (additions are
inside the existing literal):

```
("/api/content-profiles/author", "POST")                       → integral_author_profile
("/api/content-profiles/{content_profile_id}/modify", "POST")  → integral_modify_profile
("/api/content-profiles", "GET")                               → integral_list_profiles
```

Phase 4's `("/api/retrieve", "POST"): "integral_query"` entry survives.

---

## M5 Foundation Hardening — Convention Invariants (Plan 06-05)

The three invariants below codify the jvspatial Object-Spatial Contract
from root `AGENTS.md` § Forbidden Patterns. They were appended in Plan
06-05 (Wave 4 directive-plan remediation) when `.ci/jvspatial_drift_allowlist.txt`
was drained to zero entries and the pre-commit `jvspatial-drift-guard`
hook became fully enforcing. Substrate-touching plans MUST enumerate
which of these three invariants the change preserves.

### I-CONV-01 — Routes via `@endpoint`

Every backend HTTP route in `backend/app/` is registered via
`@endpoint` from `jvspatial.api`. `@router.*` and `APIRouter` instantiation
are hard-forbidden outside `backend/app/main.py` (which legitimately uses
the jvspatial `Server` bootstrap).

**Plan-checker AST grep:** `from fastapi import APIRouter` in `backend/app/`
(excluding `main.py` and lines carrying `# deviation:` annotations) returns
0 matches. Same regex powers the pre-commit hook `jvspatial-drift-guard`
(`.pre-commit-config.yaml` + `.ci/jvspatial_drift_check.sh`) and the
in-process gate
`backend/tests/test_jvspatial_convention_compliance.py::test_no_apirouter_import_in_backend_app`.

**Documented carve-out (single):** jvspatial's `@endpoint` does not
support WebSocket dispatch. `backend/app/api/events_ws.py` and
`backend/app/agentive/api/agent_events.py` retain
`APIRouter().websocket(...)` with an inline
`# deviation: @endpoint does not support WebSocket — fastapi APIRouter required for ws routes (jvspatial framework limitation)`
annotation. The deviation is single-file scope; any new ws route lands
the same annotation pattern.

Mirrors root `AGENTS.md` § jvspatial Object-Spatial Contract → Forbidden
Patterns (hard-forbidden, no efficiency exception).

### I-CONV-02 — Errors via `JVSpatialAPIException`

Every HTTP error response raised in `backend/app/` flows through a
`JVSpatialAPIException` subclass from `backend/app/api/errors.py`.
`raise HTTPException(...)` is hard-forbidden outside framework-internal
handlers (none currently exist in `backend/app/main.py`).

**Plan-checker AST grep:** `raise HTTPException` in `backend/app/`
(excluding `main.py` and `# deviation:` lines) returns 0 matches.
Dual gate via pre-commit hook + `test_jvspatial_convention_compliance.py::test_no_raise_httpexception_in_backend_app`.

**Canonical error-class mapping** (status → subclass):

| HTTP | Subclass | Source |
|------|----------|--------|
| 400 | `BadRequestError` | `app/exceptions.py` (re-exported from `app/api/errors.py`) |
| 401 | `MissingAuthenticationError` | `jvspatial.api.exceptions` re-export |
| 403 | `InsufficientPermissionsError` | `jvspatial.api.exceptions` re-export |
| 404 | `ResourceNotFoundError` | `jvspatial.api.exceptions` re-export |
| 409 | `ResourceConflictError` | `jvspatial.api.exceptions` re-export |
| 422 (manual domain reject) | `BadRequestError(message=...)` | as above |
| 500 (boundary fault) | `InternalServerError` | `app/api/errors.py` (Plan 06-05) |
| 501 (unimplemented dispatch) | `NotImplementedAPIError` | `app/api/errors.py` (Plan 06-05) |
| 503 (service unavailable) | `ServiceUnavailableError` | `app/api/errors.py` (Plan 06-05) |
| Agentive 422 (unknown caps) | `UnknownCapabilitiesError` | `app/api/errors.py` (moved from `agentive/api/uplink.py` in Plan 06-05) |

Every subclass serializes to the canonical 5-key envelope
`{error_code, message, details, timestamp, path}` via jvspatial's
exception handler. Custom 422 paths (Pydantic `RequestValidationError`)
keep their per-app handlers; the unified handler at `main.py:484` covers
the core surface and
`agentive/api/errors.py::install_agentive_error_handlers` covers the
agentive surface.

### I-CONV-03 — Request/response schemas under `backend/app/schemas/`

Every request/response Pydantic `BaseModel` lives under
`backend/app/schemas/` (including subpackages `schemas/agentive/` and
`schemas/api/` added in Plan 06-05). Inline `BaseModel` declarations
inside `backend/app/api/*.py` or `backend/app/agentive/api/*.py` are
hard-forbidden.

**Plan-checker AST grep:** `^class .*BaseModel` in `backend/app/api/`
and `backend/app/agentive/api/` returns 0 matches. Dual gate via
pre-commit hook + `test_jvspatial_convention_compliance.py::test_no_inline_basemodel_in_api_modules`.

**Layout convention:**

- `backend/app/schemas/<entity>.py` — top-level entity schemas
  (`track.py`, `entry.py`, `space.py`, `user.py`, …) — unchanged by
  Plan 06-05.
- `backend/app/schemas/api/<source-file>.py` — schemas for handlers in
  `backend/app/api/<source-file>.py`. Naming mirrors the source file
  verbatim. Added by Plan 06-05.
- `backend/app/schemas/agentive/<source-file>.py` — schemas for handlers
  in `backend/app/agentive/api/<source-file>.py`. Same naming convention.
  Added by Plan 06-05.

**Practical note for new agentive code:** jvspatial's `@endpoint`
flattens body parameters into a generated `EndpointParameterModel` with
`extra="forbid"` at the outer level. Body fields are declared as flat
keyword arguments on the handler signature (mirror of
`backend/app/api/entries.py:307`); the extracted schema modules under
`schemas/agentive/` serve as typed reference + documentation for the
field set rather than the parsed body type. The pre-existing top-level
`backend/app/schemas/<entity>.py` schemas (used as response models like
`PolicyResponse`, `ConnectorResponse`) continue to work as response
model declarations.

---

## Phase 7 — Library Growth Invariants (Plan 07-01)

### I-LIB-01 — Every seeded library package carries `package.tags`

Every spec returned by `registered_seeded_library_specs()` whose
display name is NOT in `{Agent Scratch, Personal CRM, GitHub Issues}`
MUST have `spec.manifest['package']['tags']` declared as a
`List[str]` with length `>= 3`. The tag set must be locked to the
recommendations locked in each package's `profile.yaml` under
`backend/app/profiles/` (typically six to eight discovery tags per
catalog package).

The three exempt specs predate Plan 07-01 and reach the substrate via
different routes (per-user scratch via Phase 4 MEM-01; alias-style
Personal CRM space; connector-paired manifest under Phase 5 CON-02 /
CON-04). They are deliberately omitted from the tag-presence
assertion and may opt-in in a future plan.

Manifest v1 schema compatibility — `_normalize_package_meta` in
`backend/app/services/content_profile_runtime.py` is permissive on
`package` subkeys (`out = dict(pkg)`), so additive `tags` round-trips
through `compile_canonical_manifest` unchanged. Legacy
`ContentProfile` rows without `package.tags` continue to load and
serialize without migration.

Enforced by
`backend/tests/test_seeded_library_packages.py::test_refined_package_has_tags`
(parametrized over every refined spec).

### I-LIB-02 — `package.tags` semantics

`package.tags` is a free-form domain keyword list used SOLELY for
MCP-04 `type_hint` keyword resolution. Two consumers walk it:

1. `app/api/content_profiles.py:resolve_type_hint` (the canonical
   keyword-resolution helper that backs
   `GET /api/content-profiles?type_hint=...`,
   `integral_list_profiles`, and the inline `integral_create_*`
   resolvers via Pitfall-6 single-dispatch). The helper appends a
   space-joined tag blob to the haystack before tokenizing on
   whitespace+hyphens.
2. `app/agentive/services/agent_tools.py:integral_create_track` +
   `integral_create_space` (the inline resolver inside the MCP tool
   handlers — walks `manifest.package.tags` per candidate and takes
   `max(name_score, max(tag_scores))` with the unchanged `>= 0.5`
   threshold).

`package.tags` does NOT cross any Pydantic `extra=forbid` boundary —
it lives inside the open-shape `manifest` JSON blob on
`ContentProfile.manifest`. The frontend does not consume it; no REST
schema enumerates it. Future use cases (search facets, library
navigation) may add consumers but the source-of-truth definition stays
in library bundle files under
``backend/app/profiles/<slug>/profile.yaml``.

I-LIB-02 corollaries:

- Strict-superset rule preserved — Plan 07-01 adds ZERO `PolicyAction`,
  `ChangeEventAction`, or `ActorKind` Literal members.
- Resolver back-compat — extending the haystack is monotonic; the
  `>= 0.5` threshold ensures the extended haystack returns
  strictly more matches, never fewer.
- Tag set updates land in the seeded package file with a description
  bump, not via REST migration; library rows refresh on app boot via
  `canonical_manifests_differ` fingerprint comparison.

---

## Phase 7 — Library Growth + Profile Detach/Revert Invariants (Plan 07-02)

### I-LIB-03 — Derived library packages stamp `package.provenance`

Library packages created via `POST /api/content-profiles/from-track/{id}`
or `POST /api/content-profiles/from-space/{id}` MUST carry
`manifest['package']['provenance'] = {source: 'track'|'space',
source_id: <originating_id>, derived_at: <ISO>, derived_by: <user_id>}`.
The provenance block is stamped BEFORE the new ContentProfile is
persisted; the derived package is immediately re-mergeable into a fresh
Track / Space via the existing `merge-library` flow.

Enforced by:
- `backend/tests/test_content_profile_derive_from_track.py::TestDeriveLibraryFromTrack::test_from_track_stamps_provenance`
- `backend/tests/test_content_profile_derive_from_space.py::TestDeriveLibraryFromSpace::test_from_space_stamps_provenance`
- `backend/tests/test_content_profile_derive_from_track.py::TestDeriveLibraryFromTrack::test_from_track_derived_package_round_trips`

Satisfies ROADMAP Phase 7 AC#2.

### I-LIB-04 — Space detach + revert are non-cascading

`POST /api/spaces/{space_id}/content-profile/detach-library` and
`POST /api/spaces/{space_id}/content-profile/revert-customizations`
affect ONLY the Space's directly-attached ContentProfile. Tracks
reachable from the Space's attached CP via `DEFINES_TRACK_PROFILE`
edges (track-template ContentProfiles materialized by the multi-track
space manifest) retain their independent track-attached
ContentProfiles UNCHANGED. Cross-Track expansion of cascading
semantics is explicitly out of scope for v1 (CONTEXT post-research
lock #12).

Enforced by:
- `backend/tests/test_content_profile_revert_space.py::TestRevertSpaceProfileCustomizations::test_revert_space_does_not_cascade_to_tracks`
- `backend/tests/test_content_profile_detach_space.py::TestDetachLibraryFromSpace::test_defines_track_profile_edges_preserved_after_detach`

### I-LIB-05 — Revert-customizations is reject-gated by entry impact

Both `POST /api/tracks/{track_id}/content-profile/revert-customizations`
and `POST /api/spaces/{space_id}/content-profile/revert-customizations`
consult
`compute_entry_impact_for_attached(cp, candidate_manifest=lib_cp.manifest)`
BEFORE the destructive re-apply. If any Track in the impact set
reports `would_fail_validation > 0`, the endpoint raises
`BadRequestError` with `details.blocking_impacts` unless body
`{force: true}` is passed. Mirrors the Phase 5
`migrations/reject_gate.detect_unhandled_breaks` pattern and
preserves Entry data integrity per ROADMAP Phase 7 AC#3.

Both endpoints emit `content_profile.update` with
`details={revert: True, force: <bool>, impacts: [...]}` via the
canonical `emit_change_event` helper (D-05 single-emission preserved).
ZERO new `PolicyAction`, `ChangeEventAction`, or `ActorKind` literals
are introduced.

Enforced by:
- `backend/tests/test_content_profile_revert_track.py::TestRevertTrackProfileCustomizations::test_force_true_accepted_when_no_blocking_impacts`
- `backend/tests/test_content_profile_revert_track.py::TestRevertTrackProfileCustomizations::test_revert_emits_change_event_with_revert_details`
- `backend/tests/test_content_profile_revert_space.py::TestRevertSpaceProfileCustomizations::test_force_true_accepted_when_no_impacts`

### I-UX-01 — `ProvenanceBadge` is the sole consumer of `Entry.provenance.source` in the frontend

The four visual variants (`human`, `agent`, `connector`, `system`) and
the click-to-detail popover behaviour live ONLY in
`frontend/src/components/entries/ProvenanceBadge.tsx`. Surfaces that
need to display provenance MUST mount `<ProvenanceBadge entry={entry} />`
— they MUST NOT duplicate the per-source styling or inline an
alternate render path. This keeps the "who wrote this" signal
consistent across feed cards, entry detail headers, and any future
provenance-aware UI (e.g. inbox, search results, mission control).

Current mount sites:
- `frontend/src/components/entries/EntryCard.tsx` — meta chip strip,
  adjacent to the entry-type pill.
- `frontend/src/components/entries/EntryDetail.tsx` — dialog header
  next to the entry title.

Backend contract preserved: every Entry response carries
`provenance` (PROV-01 — `backend/app/schemas/provenance.py`). Legacy
rows without persisted provenance receive `Provenance.legacy_default()`
via entry export (`export_node` / `entry.export`) — lazy DB backfill via
`provenance_backfill.py` is deferred. The frontend type (`Provenance` /
`ActorKind` in
`frontend/src/types/index.ts`) is hand-mirrored per the Phase 1 AGT-05
convention; backend MUST NOT add new `ActorKind` variants without a
lockstep update to the frontend type union AND the badge's
`VARIANT_STYLES` map.

Enforced by:
- Plan 07-05 Vitest coverage for `ProvenanceBadge` (TEST-04).

### I-UX-02 — `normalizeChangeEvent` is the sole WS-vs-polling actor-shape unifier

The backend emits ChangeEvents in TWO wire shapes:
- **WS broadcast** (`ChangeEventEnvelope.to_wire` in
  `backend/app/services/change_event_logger.py`): nested actor object
  — `event.actor.{kind, id, capability}`.
- **HTTP polling** (`ChangeEventEnvelope.to_wire_flat`): flat actor
  fields — `event.actor_kind / actor_id / actor_capability`.

Per CONTEXT post-research lock #10 the mismatch is fixed in the
frontend, not the backend. The unifier at
`frontend/src/utils/changeEvent.ts::normalizeChangeEvent` is the SOLE
adapter; every consumer of WS or polling events MUST flow input
through this function (e.g. `useEventStream`, `fetchEventsPage`).

If the backend ever introduces a third actor wire shape it MUST be
added to `normalizeChangeEvent` in lockstep — the normalizer is the
contract boundary.

Current consumers:
- `frontend/src/api/events.ts::fetchEventsPage` — normalizes each
  event in the polling page before returning.
- `frontend/src/components/activity/useEventStream.ts` — normalizes
  every WS message via `JSON.parse → normalizeChangeEvent` before
  upserting into the dedup map.

Enforced by:
- Plan 07-05 Vitest coverage for `normalizeChangeEvent` (TEST-04).
- Plan 07-05 Vitest coverage for `useEventStream` state machine
  (mocked WS + polling, asserts both inputs produce the same shape).

## Phase 7 — Agent-Action Review Surface Invariants (Plan 07-04)

### I-APPROVAL-01 — Approval re-run is recursion-guarded

`execute_approval` in
`backend/app/services/approval_executor.py` MUST thread
`_internal_actor=Subject(kind="system", id="approval_approve")`
through every helper call on the approve path. The `policy_engine.evaluate`
intercept that originally captured the write (matched.requires_human_approval
AND _is_write_action(action) AND subject.kind=='agent') MUST NOT fire on
re-entry — the recursion guard at `policy_engine.py:457-459`
short-circuits to `Decision(allowed=True, reason="system_subject_internal")`
for any caller threading a system `_internal_actor`.

The `_internal_actor` surface lives ONLY in:
- `backend/app/services/policy_engine.py` (consumer / guard site)
- `backend/app/services/change_event.py` (forward-declared kwarg only)
- `backend/app/services/approval_executor.py` (producer site)

Widening this surface widens the bypass — any new producer of
`_internal_actor` MUST justify the new bypass in an INVARIANTS.md update
and add a corresponding test to
`backend/tests/test_change_event_no_bypass.py`.

### I-APPROVAL-02 — Reject emits `policy.deny`; Approve emits the ORIGINAL action with the ORIGINAL agent as actor

**Reject path** (POST /api/approvals/{id}/reject) emits a single
`policy.deny` ChangeEvent — NEVER the original write action. The
`details` payload carries `{approval_id, original_action, agent_id,
reason}` so the audit trail records WHO got rejected for WHAT.

**Approve path** (POST /api/approvals/{id}/approve) executes the
original action through the recursion-guarded helper. The re-run's
ChangeEvent (e.g. `entry.create`) carries `actor_kind=pw.actor_kind,
actor_id=pw.actor_id` — i.e. the ORIGINAL agent captured at submit time,
NOT the approving human. The approving human's identity surfaces ONLY
in the separate `approval.approve` PolicyAction gate evaluation
(PolicyAction-only — no ChangeEventAction twin).

Why: the audit trail must preserve "Agent X created Entry Y" with the
human approval visible as a *separate* policy event. Collapsing the
two into "Human Z created Entry Y" silently destroys agent attribution.

### I-APPROVAL-03 — PolicyAction strict-supersets ChangeEventAction modulo audit-only exemptions

Phase 7 Plan 07-04 additions preserve the strict-superset invariant:

- **ChangeEventAction-only** (system-emitted, no PolicyAction twin):
  - `policy.deny` (Phase 3)
  - `anchor.deny` (Phase 3.1)
  - `approval.expired` (Phase 7 Plan 07-04 — TTL reclaim loop)

- **PolicyAction-only** (gate without ChangeEventAction twin — audit
  signal lives in the re-run's ORIGINAL action or `policy.deny`):
  - `migration.publish` / `migration.force_publish` (Phase 5)
  - `conflict.resolve` (Phase 5)
  - `profile.author` (Phase 6)  <!-- `agent.discover` retired with A2A — ADR-003 -->
  - `approval.approve` (Phase 7 Plan 07-04)
  - `approval.reject` (Phase 7 Plan 07-04)

The invariant gate test
`backend/tests/test_policy_audit_emit.py::test_policy_action_strict_supersets_change_event_action`
enforces this — every new ChangeEventAction member MUST appear in
PolicyAction unless explicitly whitelisted as audit-only. The whitelist
is the source of truth for which CEA members are exempt from the
strict-superset rule; consult it before adding a new audit-only member.

---

## See Also

- [AGENTS.md](../AGENTS.md) — Substrate-touching plan protocol that
  references this document.
- [ARCHITECTURE.md](product/ARCHITECTURE.md) — Detailed substrate design.
- [docs/content-profiles/README.md](./content-profiles/README.md) —
  Modeling Tenets (Track ≈ table; depth via edges, not JSON nesting).
- [docs/content-profiles/COMPOSITION_PATTERNS.md](./content-profiles/COMPOSITION_PATTERNS.md) —
  Canonical reference for sibling-track vs anchor pattern + the four
  Phase 3.1 resolved forks + negative space.
- [docs/content-profiles/AGENT_CONTRACT.md](./content-profiles/AGENT_CONTRACT.md) —
  Agent-authorable substrate contract (Pillars 1–4).

Phase 7 frontend UX invariants index (registered by Plan 07-03):

- I-UX-01 — ProvenanceBadge sole consumer of Entry.provenance.source.
- I-UX-02 — normalizeChangeEvent sole WS-vs-polling actor-shape unifier.

Phase 7 agent-action review invariants index (registered by Plan 07-04):

- I-APPROVAL-01 — Approval re-run is recursion-guarded.
- I-APPROVAL-02 — Reject emits policy.deny; approve emits the ORIGINAL
  action with the ORIGINAL agent as actor.
- I-APPROVAL-03 — PolicyAction strict-supersets ChangeEventAction modulo
  audit-only exemptions; whitelist is the source of truth.

---

### I-TEST-01 — Every Phase 2-7 REST endpoint has 3-case pytest coverage

Every `@endpoint`-registered route introduced in Phases 2 through 7 MUST
have pytest assertions for status codes (200|201) **and** (401|403)
**and** (400|422) across the test file(s) matched by the audit's
`test_file_glob` pattern. The "denied" and "invalid" axes are satisfied
by either an HTTP `status_code == ...` assertion OR a
`pytest.raises(<MissingAuthenticationError|InsufficientPermissionsError|
BadRequestError|ValidationError|RequestValidationError|ValueError|
ResourceNotFoundError>)` pattern — both are honest evidence that the
boundary contract is exercised.

Enforced by `backend/tests/test_phase_2_7_endpoint_coverage.py::
test_phase_2_7_endpoint_coverage_audit` — the audit walks the endpoint
catalogue (`PHASE_2_7_ENDPOINTS`, 23 entries as of Phase 7 close) and
scans the matched test files. On failure, the harness emits a clean
gap report listing each deficient endpoint and the missing axes; the
report is the audit artifact.

**Exemptions:** an endpoint may declare `exempt_axes: ["invalid"]` in
the catalogue entry when the boundary has no validatable input (e.g.
`GET /api/mcp/tools` — parameterless GET with no body, no typed query).
The exemption MUST carry an inline justification comment in the
catalogue.

**Multi-file coverage:** an endpoint may declare `test_file_glob: [
"glob_a*", "glob_b*"]` (list of strings) when happy/denied land in
one file and invalid lands in another. The audit dedups matches and
scans the union (see `_find_test_files`).

**Adding a new endpoint:** any new `@endpoint`-registered route in
subsequent phases MUST be appended to `PHASE_2_7_ENDPOINTS` (or its
successor catalogue) AND ship a test file matching the glob with the
three required axes covered. CI gates on the audit's exit code.

Per CONTEXT lock #14 (Plan 07-05 closes TEST-01) + ROADMAP AC#7.

---

### I-TEST-02 — Coverage thresholds: 80% global + 90% per-file on new substrate

**Global threshold = 80%.** Enforced via `backend/pyproject.toml`:

```toml
[tool.coverage.report]
fail_under = 80
```

`pytest --cov=app --cov-fail-under=80` exits non-zero when the
project-wide line coverage drops below 80%. CI invokes this on every
backend test run.

**Per-file threshold = 90% on new Phase 7 substrate files.** Enforced
via separate per-file pytest invocations:

```bash
pytest --cov=app.api.approvals --cov-fail-under=90 tests/test_approvals.py
```

The authoritative list of files in the 90% bucket is the
`PHASE_7_NEW_FILES` constant in
`backend/tests/test_phase_2_7_endpoint_coverage.py`. As of Phase 7
close: 13 files spanning `app/api/approvals.py`,
`app/services/approval_executor.py`,
`app/services/approval_ttl.py`, the 4 pure-service writer helpers
(`entry_writer`, `track_writer`, `space_writer`, `comment_writer`),
and the 6 seeded library packages refined by Plan 07-01.

**Adding new substrate files in subsequent phases** carries the same
90% rule forward — append to `PHASE_7_NEW_FILES` (or rename to track
the new phase) so the per-file gate persists.

Per CONTEXT lock #7 (Plan 07-05 thresholds locked) + ROADMAP AC#7.

---

Phase 7 cross-cutting hardening invariants index (registered by Plan 07-05):

- I-TEST-01 — Every Phase 2-7 REST endpoint has 3-case pytest coverage.
- I-TEST-02 — Coverage thresholds: 80% global + 90% per-file on new substrate.

---

## Phase 8 — Settings & Configuration Surface

### I-SET-01 — Settings panels are READ-mostly proxies over substrate REST surfaces

Settings panels in Phase 8 wire existing Phase 3-7 REST surfaces to UI; they do
NOT introduce new substrate state. Specifically:

- The Retrieval config panel (SET-06) is read-only — `GET /api/retrieval/config`
  reads env vars at call time; there is NO PATCH route, NO `SystemConfig` Node,
  NO persistence layer. Editable retrieval config is deferred to v1.2 and
  requires a separate substrate review (env-var-driven vs node-persisted is
  a real architectural fork).
- The Library panel (SET-05) browses `GET /api/content-profiles` filtered by
  `library_package=true`; package count is whatever the backend returns at
  runtime — frontend NEVER hardcodes 6/7/8.
- The Agents panel (SET-04) is read-only in v1.1; edit / register flow is
  deferred to v1.2.
- The Sharing panel (SET-08) aggregates outbound state via
  `GET /api/me/sharing-overview`; per-resource controls are NOT duplicated
  from `ManageAccessModal`.
- The Voice input panel writes exactly one per-user slot,
  `User.speech_preferences`, and only through
  `PATCH /api/users/me/speech-preferences`. That route validates
  (`extra="forbid"`) and emits one `user.update` tagged
  `section=speech_preferences`. The workspace provider shown there is
  read-only; it is configured as the model credential's `speech` slot
  (AI Models).

**Verification:**

- `grep -c "PATCH.*retrieval/config" backend/app/api/retrieval_config.py` returns 0.
- `grep -c "class SystemConfig" backend/app/models/nodes.py` returns 0.
- `grep -c "useMutation" frontend/src/features/settings/sections/AgentsSection.tsx` returns 0.
- `grep -c "useMutation" frontend/src/features/settings/sections/RetrievalConfigSection.tsx` returns 0.
- `grep -c "ManageAccessModal" frontend/src/features/settings/sections/SharingSection.tsx` returns 0.
- `grep -c "total === [0-9]\|content_profiles.length === [0-9]" frontend/src/features/settings/sections/LibrarySection.tsx` returns 0.

### I-SET-02 — Settings agentive panels assume always-on agentive layer

Panels for Policies, Connectors, and Agents assume the agentive layer is
always enabled in Integral. Mutation controls are not gated on a runtime
capability probe. The shared `useAgentiveCapability()` hook at
`frontend/src/features/settings/hooks/useAgentiveCapability.ts` returns
`{ enabled: true, isLoading: false }` for compatibility with existing imports.

**Verification:**

- `grep -c "useAgentiveCapability" frontend/src/features/settings/hooks/useAgentiveCapability.ts` returns at least 1 (the export).
- PoliciesSection, ConnectorsSection, and AgentsSection do NOT render an
  "Agentive layer disabled" banner.

### I-SPEECH-01 — Speech provider keys never reach the browser

The provider API key behind voice input stays server-side:

- The browser receives only a short-lived, transcription-only client secret,
  minted by `POST /api/agentive/speech/session` (TTL
  `SPEECH_SESSION_TTL_SECONDS`, `Cache-Control: no-store`).
- Workspace guests never mint.
- The transcription tool (`integral_transcribe_audio`) resolves the key from
  the BOUND workspace only (PC-2) and refuses attachments from another
  workspace.
- Adapters under `agentive/services/speech/providers/` receive the key in a
  `ProviderContext` and never read the graph or decrypt credentials.

**Verification:** `tests/test_speech_api.py` asserts the owner key string is
absent from every speech response. `tests/test_speech_transcribe_tool.py`
covers the cross-workspace refusal.

### I-SPEECH-02 — Streaming speech origins are allowed in every CSP source

Every origin a streaming adapter declares in
`capabilities.browser_connect_origins` must be in `connect-src` in all three
header sources:

- `SPEECH_CONNECT_ORIGINS` in `backend/app/middleware/security_headers.py`;
- `frontend/nginx.conf`;
- `frontend/nginx.docker.conf`.

In the nginx templates it goes in as a literal ahead of
`${CSP_EXTRA_CONNECT}`. `Permissions-Policy` stays `microphone=(self)` in all
three.

**Verification:** `tests/test_speech_registry.py::test_streaming_adapter_origins_are_in_the_csp`
and `tests/test_security_headers_speech.py`.

---

Phase 8 cross-cutting hardening invariants index (registered by Plan 08-06):

- I-SET-01 — Settings panels are READ-mostly proxies over substrate REST surfaces.
- I-SET-02 — Settings agentive panels assume always-on agentive layer.
- I-SPEECH-01 — Speech provider keys never reach the browser.
- I-SPEECH-02 — Streaming speech origins are allowed in every CSP source.

---

## Phase 9 — Identity & Engagement Polish (Plan 09-05)

### I-PHASE9-01 — Onboarding contract (Plan 09-05 locked decisions A1..A5)

The first-login onboarding surface (Plan 09-05, ONBD-01) is governed by
five locked decisions. They are invariants — re-litigating them later
requires a new milestone-level RFC, not a regular plan revision.

- **A1** — `User.onboarded_at: Optional[str]` is the single sentinel for
  onboarding completion. The frontend reads it via the auth `/me` payload;
  the backend never derives onboarded-ness from any other field
  (e.g. "has the user created a Space"). A user with `onboarded_at = None`
  ALWAYS sees the onboarding surface; a user with `onboarded_at` set
  NEVER sees it (regardless of session-storage flags).
- **A2** — Onboarding runs through the existing `/api/agentive/chat`
  surface seeded with the `integral_onboard_user` MCP-tool dispatch
  prompt. NO new REST wrapper. NO React-form fallback path. The state
  machine lives entirely in `backend/app/agentive/services/integral_onboard_user.py`
  and progresses one step per LLM dispatch call.
- **A3** — `integral_onboard_user` follows the state-machine contract:
  `start → ask_domain → propose_spaces → create_tracks →
  set_retrieval_mode → set_theme → finalize`. Each step returns
  `{next_step, prompt, completed: bool}`. Only `finalize` writes
  `onboarded_at` and emits ONE `user.update` ChangeEvent.
- **A4** — Pre-09-05 users are backfilled at server startup
  (`services/onboarding_backfill.py::backfill_onboarded_at`) so they do
  NOT see the onboarding surface on their next login. The backfill is
  idempotent (the Python filter on `onboarded_at is None` short-circuits
  on re-runs) and emits NO ChangeEvents (system migration, not a
  user-driven update).
- **A5** — When `useAgentiveCapability().enabled === false` AND
  `onboarded_at is None`, Layout renders `OnboardingGetStartedBanner`
  (NOT the modal). The banner links to `/settings#get-started`. No
  broken UI when the agentive layer is unreachable.

The B4 ROADMAP AC further pins the post-flow state: after `finalize`,
the user MUST have a Workspace + ≥1 Space + ≥1 Track + `theme` +
`retrieval_mode` + `onboarded_at`. The dedicated pytest case
`test_finalize_leaves_user_with_space_track_theme_retrieval` enforces
this.

**Verification:**

- `grep -c 'onboarded_at' backend/app/models/nodes.py` returns ≥ 1
  (the User-field declaration).
- `grep -c 'integral_onboard_user' backend/app/agentive/services/agent_tools.py`
  returns ≥ 2 (MCP_TOOLS entry + execute_tool dispatch).
- `grep -c 'backfill_onboarded_at' backend/app/main.py` returns ≥ 1
  (Server `on_startup` wiring).
- `grep -c 'OnboardingGetStartedBanner' frontend/src/components/layout/Layout.tsx`
  returns ≥ 1 (A5 fallback mount).
- `grep -c 'useFirstLoginOnboarding' frontend/src/components/layout/Layout.tsx`
  returns ≥ 1 (hook consumption).

Single-Literal invariants `I-CHA-*`: preserved — no new `ChangeEventAction`
or `PolicyAction` Literal members were added; `user.update` was already
in scope from Phase 2.

---

## Phase 10 — App Bundles v1 Invariants

Phase 10 (Plans 10-01 through 10-07) introduces the declarative agentive
application runtime: hard cutover Space → App, ContentProfile manifest v2
(operational layer: skills, agents, settings_schema, seeds, permissions),
atomic install/uninstall lifecycle, cross-App relations + `requires_apps[]`
dependencies, and the Content Factory canonical reference template.

Five new invariants land with Plan 10-07's closure work. Every invariant
is gated by `backend/tests/test_app_bundles_invariants.py` (grep + behavior
gates) plus the linked Plan 10-05 / 10-06 regression suites.

### I-APP-01 — Manifest v2 only

`content_profile_runtime.py:SCHEMA_VERSION = 2`. The compiler refuses v1
manifests with `ContentProfileValidationError("manifest v1 no longer
supported — see docs/backend/app-bundles-v1.md §13.1")`. Every library bundle
under ``backend/app/profiles/<slug>/profile.yaml`` uses
``integral_profile_version: 2``; the parametrized round-trip test
(``tests/test_seeded_packages_v2.py``) guarantees no regression to v1 shape.

**Why this matters.** Manifest v2 is the substrate operational layer
(skills, agents, settings_schema, seeds, permissions). Downstream phases
(11 Orchestration, 12 Proactive, 13 Packages, 14 Chat, 15 Tiers) all
consume v2 features. Allowing a v1 manifest to ship would silently bypass
every operational-layer validation pipeline.

**Verification:**

- `grep -rn 'content_profile_schema_version.*1' backend/app/profiles/ --include='*.yaml'` returns 0 v1 lines (matches with `version: 2` are excluded).
- `tests/test_seeded_packages_v2.py::test_each_seeded_package_declares_schema_version_2` passes (10 packages).
- `tests/test_seeded_packages_v2.py::test_each_seeded_package_compiles_under_v2` passes (10 packages).
- `tests/test_app_bundles_invariants.py::test_I_APP_01_compiler_rejects_v1_manifest` passes.

### I-APP-02 — App lifecycle atomicity

Install transaction is all-or-nothing per `docs/backend/app-bundles-v1.md` §9.1. Failure
at any of the 12 steps invokes recorded compensating actions in REVERSE
order via `InstallTransaction.compensate()` (Plan 10-05). Failed
compensations are wrapped in try/except so a single failure does NOT
prevent earlier-step compensations from running. `App.lifecycle_state` is
the state-machine column; it always reflects the current state, and no
half-installed Apps survive `pytest -k lifecycle`.

The `awaiting_settings` pause-state is finalized via signed install_token
(HMAC-SHA256, 1h TTL) at `POST /api/apps/{app_id}/install/settings`. The
reaper background task compensates abandoned awaiting_settings installs
past TTL. Force-uninstalls bypass dep + reference checks and emit
`app.force_uninstalled` (NEVER `app.uninstalled`) per the D-05 single-
emission gate.

**Verification:**

- `tests/test_app_lifecycle.py::test_install_transaction_compensates_in_reverse_order` passes.
- `tests/test_app_lifecycle.py::test_install_with_settings_schema_pauses_at_step_9` passes.
- `tests/test_app_lifecycle.py::test_resume_install_with_valid_token_completes` passes.
- `tests/test_app_lifecycle.py::test_uninstall_archives_by_default` passes (normal path emits `app.uninstalled`).
- `tests/test_app_lifecycle.py::test_force_uninstall_emits_force_action` passes (force path emits `app.force_uninstalled`).
- `tests/test_content_factory_install.py::test_end_to_end_install_awaiting_settings_then_finalize` exercises the full pipeline end-to-end against the Content Factory canonical seed.

### I-APP-03 — Cross-App permission propagation via single resolver

All cross-App relation reads route through `relation_runtime.read_cross_app_label()`
(Plan 10-06). The helper consults `policy_engine.evaluate()` server-side
and returns restricted stubs of shape `{kind: "restricted_relation",
relation_field_key, target_resource_kind}` ONLY — zero source-field
content leaks. The `label_field` is the per-field dotted path the
resolver reads on permission-grant; any other reader bypasses the
permission gate and is therefore a leak vector (Risk 4 / Pitfall 5).

**Whitelist** (the only files allowed to reference `label_field`):

- `app/services/relation_runtime.py` — the canonical resolver.
- `app/services/content_profile_runtime.py` — compile-time normalization.
- `app/schemas/cross_app_relations.py` — wire shape declarations.
- `app/models/edges.py` — `REFERENCES.target_app_id` docstring.
- `backend/tests/**` — assertion sites.

**Verification:**

- `tests/test_cross_app_relations.py::test_restricted_stub_carries_no_label_field_value` passes.
- `tests/test_app_bundles_invariants.py::test_I_APP_03_label_field_resolver_is_single_source` enforces the whitelist via grep.

### I-APP-04 — `requires_apps[]` enforcement

Install is blocked when a manifest declares `requires_apps[{key, optional:
false}]` and the dependency is not installed (or version unsatisfied);
raises `AppDependencyError`. Uninstall is blocked while INBOUND hard deps
or `on_target_uninstall: block` REFERENCES edges exist; raises
`AppUninstallBlockedError` with structured details listing every blocker.

Force-uninstall (`?force=true`) bypasses both gates and emits
`app.force_uninstalled` with `details.dependency_overrides[]` (count of
manifest-level deps overridden) + `details.reference_overrides[]` (count
of edge-level refs overridden). Force-uninstalled APPS leave orphaned
REFERENCES edges; subsequent reads via `relation_runtime` surface them
as restricted stubs per I-APP-03.

**Verification:**

- `tests/test_requires_apps.py::test_install_blocks_without_hard_dep` passes.
- `tests/test_requires_apps.py::test_install_proceeds_with_soft_dep_absent` passes (optional=true does not block).
- `tests/test_requires_apps.py::test_install_blocks_when_min_version_unsatisfied` passes.
- `tests/test_requires_apps.py::test_uninstall_blocked_by_dependent_manifest_declaration` passes.
- `tests/test_requires_apps.py::test_force_uninstall_bypasses_dep_check` passes.
- `tests/test_cross_app_relations.py::test_uninstall_blocked_by_inbound_cross_app_references` passes.

### I-APP-05 — Same-Workspace App scope

Cross-App relations resolve only within the same Workspace. No
cross-Workspace App references in v1. The
`relation_runtime.resolve_target_app()` helper rejects any `resolution:
"instance:<id>"` pin where the target App lives in a different Workspace
than the source App; raises `CrossWorkspaceTargetRejectedError`. The
`resolution: workspace` mode walks ONLY same-Workspace Apps when
disambiguating multi-install scenarios.

This is the trust-boundary anchor for App data: a misconfigured manifest
cannot accidentally publish data to a sibling Workspace via a cross-App
relation field.

**Verification:**

- `tests/test_cross_app_relations.py::test_cross_workspace_target_rejected_via_instance_pin` passes.
- `tests/test_app_bundles_invariants.py::test_I_APP_05_cross_workspace_rejection_test_exists` enforces the structural gate.

---

Phase 10 cross-cutting invariants index (registered by Plan 10-07):

- I-APP-01 — Manifest v2 only.
- I-APP-02 — App lifecycle atomicity.
- I-APP-03 — Cross-App permission propagation via single `relation_runtime` resolver.
- I-APP-04 — `requires_apps[]` enforcement.
- I-APP-05 — Same-Workspace App scope.
- I-CRUD-01 — Service-layer canonical writes.
- I-APP-06 — Library install single path.
- I-APP-07 — Signup workspace provisioning.

### I-CRUD-01 — Service-layer canonical writes

HTTP `@endpoint` handlers and `agentive/api/` modules MUST NOT call
`Node.create(` or `.connect(` on graph entities directly. All persisted
mutations delegate to named functions in `backend/app/services/` (or
`agentive/services/`). Enforced by `.ci/service_layer_drift_check.sh`
and documented in `docs/backend/service-layer.md`.

**Verification:**

- `.ci/service_layer_drift_check.sh` exits 0 on clean tree.
- `backend/tests/test_crud_contract.py` asserts library App creation
  delegates to `install_app`.

### I-APP-06 — Library install single path

Workspace library bundle installs MUST use `app_lifecycle.install_app`
(or the HTTP equivalent `POST /api/workspaces/{id}/apps/install`). Partial
create + merge without seeds/skills/agents/version is forbidden. Seed
scripts and `create_app_for_user` with `library_package_id` delegate to
`install_app`.

**Verification:**

- `backend/tests/test_crud_contract.py::test_create_app_for_user_library_delegates_to_install`
- `backend/scripts/migrate_legacy_bundle_installs.py` heals incomplete installs.

### I-APP-07 — Signup workspace provisioning

Every authenticated User MUST receive a Personal Workspace at signup
(`app/api/auth.py::signup` → `ensure_personal_workspace`). List and
scope endpoints MUST NOT lazy-create workspaces on read —
`list_accessible_workspaces` returns only workspaces already linked via
`IS_MEMBER_OF` (including the signup-provisioned personal workspace).

**Verification:**

- `backend/app/services/workspace_permissions.py::list_accessible_workspaces`
  does not call `ensure_personal_workspace`.
- Signup path in `backend/app/api/auth.py` provisions personal workspace.

Single-Literal invariants `I-CHA-*`: preserved — Plan 10-05 added
`app.installed` / `app.uninstalled` / `app.force_uninstalled` to BOTH
`ChangeEventAction` (audit) AND `PolicyAction` (policy) Literals in
lockstep. `grep -cE '^(PolicyAction|ChangeEventAction)\s*=\s*Literal'`
returns 2 (one definition per file, preserved). The strict-superset
relation holds for app-lifecycle actions specifically (these are both
policy-evaluated AND audit-logged); some non-lifecycle ChangeEvent
actions (e.g. `approval.expired`) remain audit-only by design.

---

## Graph Contiguousness Invariants

Authored 2026-05-20 (substrate graph-contiguousness audit). The invariants below
encode two complementary rules:

- **I-GRAPH-01** — every persisted `Node` is reachable from `Root` via
  named edges. Prerequisite for any walker-based, cascade-based, or
  graph-backup-based substrate computation to be sound.
- **I-GRAPH-02** — traditional records that do not benefit from graph
  inclusion are modelled as `Object`, not `Node`. Persisting a
  log-shaped or non-relational record as an orphan `Node` is the
  symptom this invariant prevents.

### I-GRAPH-01 — All-Nodes-Reachable-From-Root (`all-nodes-reachable-from-root`)

Tag: `all-nodes-reachable-from-root`. Invariant id: `I-GRAPH-01`.

Every persisted `Node` subclass MUST be attached to the prime graph
via at least one of:

1. A direct or transitive edge path back to the singleton `IntegralApp`
   (`n.IntegralApp.integral`) — i.e. a `Walker` spawned at `Root` could
   in principle traverse to the node by following named edges.
2. An attachment edge to a Node that already satisfies (1).

Furthermore, when an established App-bound Node anchors a subsystem
(e.g. `App —CONTAINS→ Track`, `App —CONTAINS→ Skill`, `App —CONTAINS→ Dashboards`,
`Workspace —CONTAINS→ Skill`,
`App —HAS_CONTENT_PROFILE→ ContentProfile`), every other Node
belonging to that subsystem MUST extend from that App-Node directly
(via an entity edge) or indirectly (via the appropriate branch /
registry node). Floating "side-car" Nodes that semantically belong to
an App but hang only off `IntegralApp`, off `User`, or off no rooted
ancestor at all are forbidden.

Concretely, at every `<NodeClass>.create(...)` call site the writer
MUST also wire the canonical structural edge (`CATALOGS`, `CONTAINS`,
`OWNS`, `HAS_*`, or a domain-specific named edge) that connects the
new node into the rooted subgraph **in the same transaction / unit of
work**. Denormalized scalar foreign keys
(`entry_id: str`, `user_id: str`, `workspace_id: str`) are permitted
as fast-path caches but are NEVER a substitute for the edge — they do
not participate in spatial-graph traversal.

**No carve-out.** Records that do not benefit from graph inclusion are
not Nodes at all — they are `Object`s (see I-GRAPH-02). Therefore
I-GRAPH-01 admits zero exceptions: if it persists as a `Node`, it is
reachable from Root by named edge path, full stop.

### I-GRAPH-02 — Object-For-Non-Graph-Records (`object-for-non-graph-records`)

Tag: `object-for-non-graph-records`. Invariant id: `I-GRAPH-02`.

`jvspatial.core.Object` (defined at
`jvspatial/core/entities/object.py`) is the canonical persisted-record
primitive that lives **outside** the spatial graph. `Node` extends
`Object` and adds graph membership (edges, walkers, traversal). Use
`Object` when:

- The record is log-shaped (append-mostly time series).
- The record has no semantically meaningful relationship to any other
  persisted entity beyond denormalized identity scalars (actor id,
  resource id stamped at write time).
- The record is read only by point lookup or scalar-filter scan, never
  by graph traversal.
- The record participates in no cascade, no permission resolution, no
  reachability sweep, no walker-driven computation.

Examples that ARE `Object` (not `Node`):

- `ChangeEvent` — append-only audit log; persisted as `DBLog` rows in
  the logging database via `backend/app/services/change_event_logger.py`.
  `DBLog` itself is defined as `class DBLog(Object)` in jvspatial
  (`jvspatial/logging/models.py`), so ChangeEvent persistence already
  conforms to I-GRAPH-02 — an `Object` subclass is the canonical
  primitive in use today. The separation of "logs" DB from "prime" DB
  is operational (independent retention / purge) and NOT a different
  type system. Documented inline at `backend/app/models/nodes.py:694-696`.

**Decision rule when adding a new persisted entity:**

```
Does this thing participate in a cascade, permission check, walker,
or graph-walk read?
├── Yes → Node + wire structural edge at create (I-GRAPH-01).
└── No  → Does it benefit from any edge to any other entity?
         ├── Yes → Reconsider — you probably want a Node anyway.
         └── No  → Object.
```

Mis-modelling a graph-participant as `Object` (no edges, can't be
walked) is just as wrong as mis-modelling a log-shaped record as
`Node` (forces an artificial edge that no consumer reads). The choice
is up-front; conversion is a substrate-touching plan.

**Add a new exempt-from-I-GRAPH-01 class?** You can't — there is no
exempt class. If the new entity legitimately has no graph value,
declare it `Object` from day one and document under I-GRAPH-02.

**Gates:**

- **Static gate (grep / AST):** every `<NodeClass>.create(` callsite under
  `backend/app/` is accompanied by a `.connect(` of a substrate edge
  within the same function body. (No carve-out — log-shaped records
  belong as `Object`, not `Node`; see I-GRAPH-02.)
  Implementation lives in `backend/tests/test_graph_contiguousness.py`
  (added by the reconciliation plan): walks every `Node.create(`
  invocation discovered via AST, asserts the surrounding function also
  contains a `connect(...)` call referencing the new node.

- **Runtime gate (DB sweep):** a server-startup audit (or pytest fixture)
  spawns a `GraphReachabilityWalker` from `Root` and asserts the count
  of reachable nodes per class equals the persisted count. Implemented
  in `backend/app/services/graph_reachability.py` (added by the
  reconciliation plan); invoked from
  `tests/test_graph_contiguousness.py::test_no_orphan_nodes_at_startup`.

- **Allowlist file** at `backend/.ci/graph_contiguousness_allowlist.txt`
  lists every legacy create site grandfathered during the
  reconciliation rollout; allowlist MUST drain to zero (header-only) by
  the end of the milestone that lands the reconciliation plan, mirroring
  the `jvspatial_drift_allowlist.txt` pattern (see I-CONV-01).

**Detached-at-audit-time roster** (snapshot 2026-05-20 — Phase 10.5
RECONCILED 2026-05-20):

| Class | Verdict | Reconciliation outcome |
|---|---|---|
| `UploadSession` | orphan | RECONCILED (10.5-03) — `Entry —HAS_UPLOAD_SESSION→ UploadSession` |
| `ShareLink` | orphan | RECONCILED (10.5-02) — `<resource> —HAS_SHARE_LINK→ ShareLink` |
| `Conflict` | orphan | RECONCILED (10.5-04) — `Entry —HAS_CONFLICT→ Conflict` |
| `Approval` | orphan | RECONCILED (10.5-05) — `Policy —HAS_APPROVAL→ Approval` + `User —HAS_APPROVAL_DECISION→ Approval` on decide |
| `AgentConfig` | orphan (edges defined, never wired) | RECONCILED (10.5-06) — `User —HAS_AGENT_CONFIG→ AgentConfig` (personal); `Workspace —HAS_ORG_AGENT→ AgentConfig` (org-facing); `IntegralApp —HAS_SYSTEM_AGENT→ AgentConfig` (system); `App —CONTAINS→ AgentConfig` (App-bundled) |
| `ConversationContext` | orphan | RECONCILED (10.5-07) — `AgentConfig —CONTAINS→ ConversationContext` (or `User —CONTAINS→` fallback) |
| `ChannelIdentity` | orphan | RECONCILED (10.5-08) — `User —HAS_CHANNEL_IDENTITY→ ChannelIdentity` (canonical edge in `agentive/edges.py`; channels.py REST endpoint wire-gap closed) |
| `Notification` | partial — wired at REST entry, NOT at router / sharing | RECONCILED (10.5-01) — `create_notification` helper centralizes `Notification.create` + `link_notification` so every code path wires `User —HAS_NOTIFICATION→` |
| `ContentProfile` (draft variant) | orphan (post-audit addendum 2026-05-20) | RECONCILED (10.5-10) — `<published> —HAS_DRAFT_PROFILE→ draft` at `fork_draft`; cascade-deleted by `discard_draft` |
| `Policy` | conditional — orphan iff subject is detached `AgentConfig` | RECONCILED transitively via 10.5-06 |

Closure (Plan 10.5-09):
- `backend/.ci/graph_contiguousness_allowlist.txt` is header-only.
- `test_no_unknown_node_classes_are_orphan` runs with
  `raise_on_violation=True` (BLOCKING).
- `test_allowlist_parser_format` asserts `parsed == set()`.
- Every reconciliation commit ships a `scripts/backfill_<class>_edges.py`
  for legacy-row repair on staging / prod DBs.

**Verification index:**

- `backend/tests/test_graph_contiguousness.py::test_no_unknown_node_classes_are_orphan` —
  runtime DB sweep gate (advisory through Plan 10.5-08; blocking from
  Plan 10.5-09).
- `backend/tests/test_graph_contiguousness.py::test_ast_gate_every_create_site_wires_edge` —
  static AST gate over `backend/app/`.
- `backend/tests/test_graph_contiguousness.py::test_orphan_detection_finds_known_orphan` /
  `::test_allowlist_suppresses_known_orphans` /
  `::test_run_audit_raises_when_requested_and_unlisted_orphan_exists` —
  walker + allowlist + raise-on-violation regressions.
- `backend/tests/test_graph_contiguousness.py::test_walker_reaches_canonical_chain` —
  canonical-fixture reachability proof.
- `backend/tests/test_graph_contiguousness.py::test_allowlist_parser_format` —
  allowlist file shape regression (the 8 in-flight classes).
- `backend/.ci/graph_contiguousness_allowlist.txt` — temporary
  grandfather list; header-only by end of Phase 10.5 (Plan 10.5-09).

## Phase 30 — Substrate Decoupling Invariants

Phase 30 enforces the substrate/bundle boundary that the monorepo
rebuild relies on: bundles declare structure and behavior on top of
generic primitives via the bundle Tool (`tools[]`) + Hook (`hooks[]`)
manifest blocks, the substrate stays free of domain-specific tokens,
and the dispatch surface between them is a frozen, reviewable
contract. The two invariants below lock that boundary against drift.

### I-SUBSTRATE-01 — Domain-name drift forbidden in substrate

**Scope:** `backend/app/services/`, `backend/app/api/`, `backend/app/models/`, `backend/app/schemas/` (excluding `app/services/hooks/`, `app/agentive/`, the 6 generic Phase 30 endpoint modules under `app/api/`, `app/services/connectors/sync_runtime.py`, and `schemas/hooks/`).

**Rule:** Substrate code MUST NOT reference bundle slugs (e.g. `crm-plus-pm-suite`, `hr_app`), EntryType names declared in any bundle manifest (e.g. `Project Proposal`, `Case Study`, `Pricing Rubric`), Track names (e.g. `Project Proposals`, `Portfolio`), or workspace-specific tokens (`V75`, `v75_*`).

**Rationale:** Substrate exposes generic primitives. Bundles deploy structure + behavior on top. Cross-coupling guarantees future bundles fork the substrate.

**Enforcement:** `.ci/substrate_domain_drift_check.sh` (pre-commit + CI). Allow-list at `.ci/substrate_drift_allowlist.txt` requires a `# reason:` comment per line; reviewed quarterly.

**Exception path:** Add an allowlist line with a one-line justification AND amend this invariant if the exception is structural (e.g. new connector-slug discriminator). Drive-by domain references in substrate without an invariant amendment = hard block.

**Verification:**
- `.ci/substrate_domain_drift_check.sh` — pre-commit + CI gate (this invariant's enforcement entry point).
- `backend/tests/test_substrate_drift_gate.py` — 4 tests covering script existence, planted-token detection, allowlist format enforcement, and full-repo pass (the full-repo pass is `@pytest.mark.xfail` until Wave E6 completes).

**Origin:** Phase 30 (Substrate Decoupling) Wave A — substrate-domain-drift gate landed alongside the bundle Tool + Hook framework. Substrate stays generic; bundles deploy structure + behavior on top.

### I-PC-01 — Unstaged writes are bounded to the observation stream and the attention log

**Scope:** `backend/app/agentive/unstaged_targets.py`, `backend/app/agentive/tooling/dispatch.py`, `backend/app/services/content_profile_compile.py`, `backend/app/services/content_profile_merge.py`, bundle manifests declaring `app.unstaged_tracks`.

**Rule:** An agent write MAY bypass staging only when ALL of the following hold: the staging kind is `create_entry` or `update_entry`; the target Track carries a manifest key (`Track.template_id`) listed in its App's attached-manifest `app.unstaged_tracks`; that App is `lifecycle_state=active`; the App's Workspace is `kind="personal"`; and the acting principal resolves to that Workspace's `IS_MEMBER_OF{role:"owner"}` User. Every other write stages — including every other track in the same App. The exempt keys are declared in the bundle manifest and gated at compile on `package.trust_tier ∈ {trusted, audited}` with a cap of `MAX_UNSTAGED_TRACKS_PER_APP`; the substrate MUST NOT name a bundle or a track key (I-SUBSTRATE-01). The gate fails closed: any error, missing link or ambiguity resolves to "stage it".

**Rationale:** The approval surface exists so a person can refuse a change to their substrate. An App that records observations about the person would fill that surface with items where refusal means only "do not remember that", draining the signal from every other card. Observations are not changes to what the person owns; beliefs are, and beliefs stage.

**Consequence for merge:** `app.unstaged_tracks` is read off the ATTACHED manifest at write time, so `merge_library_manifest_into_content_profile` MUST carry it through the rebuilt `app` block — the same failure mode I-HOOK-02 §1 documents for `hooks`/`tools`, where a dropped key silently disables the behavior.

**Verification:**
- `backend/tests/test_personal_context_unstaged.py` — 14 tests: exempt tracks mint no token; every fact track and the compiled pages track do; a hand-made Track with no manifest key is never exempt; another user's personal workspace is not exempt while its owner's is; an organization workspace the acting user OWNS is not exempt; only row-write kinds are exemptible; the exempt set is read from the manifest and the substrate names no bundle.
- Each of the four gate clauses (kind, manifest list, personal-workspace, ownership) is mutation-verified — removing it fails a named test.

**Origin:** ADR-006 (`docs/backend/adr/006-personal-context-stream-unstaged.md`), Stage 1 of the Personal Context App. This is the substrate's first unstaged agent write path; there was no prior carve-out, for scratch or anything else.

**Extended by ADR-007:** the decision ledger writes into a third exempt track, `decisions`. Recording that a person approved something cannot itself require approval. `Decision` was moved OUT of the `commitments` track to make this possible without exempting beliefs — the exemption is a whole-track property, and `commitments` holds `Commitment`, which is a belief and stages. Verified by `backend/tests/test_decision_ledger.py`.

### I-HOOK-01 — Hook-point catalog is frozen

**Scope:** `backend/app/services/hooks/registry.py` and any code that calls `hooks.dispatch`.

**Rule:** The set of substrate hook points is a frozen enum. Adding or removing a hook point requires an amendment to this invariant + plan-checker review. Removal additionally requires migrating every bundle that depends on the removed point first. Domain side effects (e.g. HR leave-balance recalculation) live in bundle tools dispatched from these points — never in substrate code branching on an EntryType/Track name (I-SUBSTRATE-01).

**Catalog (8 points):** `entry.create`, `entry.validate`, `entry.update`, `entry.transform`, `entry.public_share`, `entry.precompute`, `connector.dedup`, `connector.auto_link`.

**Rationale:** Hook points are a public contract between substrate and bundles. Ad-hoc additions fragment the dispatch surface; named additions force design review.

**Verification:**
- The frozen-catalog `HOOK_POINTS` set lives in `backend/app/services/hooks/registry.py`. `backend/tests/test_hooks_registry.py` pins the catalog, including the blocking pre-write `entry.validate` point.
- Adding a hook point requires amending this invariant plus updating the test fixture; CI fails if `HOOK_POINTS` diverges from this invariant.

### I-HOOK-02 — A bundle's operational layer is carried, registered, and scoped intact

**Scope:** `backend/app/services/content_profile_merge.py`, `backend/app/services/hooks/install_hook.py`, `backend/app/services/hooks/registry.py`, `backend/app/services/hooks/entry_save_runtime.py`.

**Rule:** A bundle's **operational layer** — its `app.hooks[]` (declarative bindings) and `app.tools[]` (`trust_tier=trusted` Python handlers) — is a first-class part of the manifest and MUST survive every transform between the library package and the live per-workspace registry:

1. **Merge carries it.** `merge_library_manifest` reconstructs the attached profile's `app` block INCLUDING `hooks` and `tools` (canonical-wins, like `permissions`/`settings_schema`). A merge MUST NOT drop them.
2. **Registration self-heals.** Startup rehydration (`rehydrate_all_installed_bundles`) registers each active App's operational layer from its attached profile; if the attached profile is missing hooks/tools its library source declares, it copies them back from the library and persists before registering. Idempotent — once attached == library it no-ops. No bundle requires a manual re-install to regain its bindings.
3. **Handler resolution.** A hook's `tool` resolves to its Python handler at `app.profiles.<slug_underscored>.<module>:<fn>`, where `<slug_underscored>` is the package slug with hyphens replaced by underscores (a hyphenated slug is not a valid Python package name; the handler directory uses the underscore form). The slug is read from `package.slug` or `package.name`.
4. **Type-keyed dispatch.** `entry.create`/`entry.update` bindings match an entry's type by the EntryType **key** (slugified name), so `match: {entry_type: time_off_request}` fires for the "Time-off request" type.
5. **Scoped substrate access.** Bundle tools reach the substrate ONLY through `ToolContext`. `ToolContext.find_entries` scopes results to the active workspace through each entry's **Track** (Entry nodes carry no `workspace_id`); `find_entries_in_track_type` provides a workspace-scoped, type-filtered walk. Bundle tools MUST NOT import `app.models`/`app.services` directly.

**Verification:** `backend/tests/test_content_profile_merge.py` (hooks/tools survive merge); rehydration logs `N apps rehydrated, M healed`; `backend/tests/test_tool_context_find_entries_scope.py` (Track-scoped find); `backend/tests/test_create_entry_selective_drop.py` (type resolution); the `.ci` substrate-import guard rejects `app.models`/`app.services` imports under `profiles/*/tools/`.

### I-BUNDLE-01 — Library packages require profile.yaml

**Scope:** `backend/app/profiles/*/`

**Rule:** Each bundle directory under `backend/app/profiles/` MUST contain `profile.yaml`. Directories without it are skipped at library sync.

**Verification:** `content_profile_loader.load_library_profiles_with_issues()`; `backend/tests/test_app_bundles_invariants.py`.

### I-BUNDLE-02 — Declared skills require SKILL.md on disk

**Rule:** Every skill key declared in `app.skills[]` (or v3 bare-string expansion) MUST have `skills/{key}/SKILL.md` under the bundle directory. Keys without a file are excluded at load with a warning.

### I-BUNDLE-03 — Bundle signature gate (production)

**Rule:** When `INTEGRAL_PROFILE_PUBKEY` is set, Python-shipping bundles MUST pass signature verification or are excluded from the catalog.

### I-BUNDLE-04 — Directory name equals package.slug

**Rule:** `bundle_dir.name` MUST equal `package.slug` in the manifest.

### I-BUNDLE-05 — Overlay skill namespacing

**Rule:** Runtime overlay skill names are `{app_slug}__{skill_key}`; keys are unique per App; global uniqueness is `{slug}__{key}`.

### I-SKILL-SCOPE-01 — Overlay skills ⊆ accessible Apps in active workspace

**Scope:** `backend/app/agentive/workspace_agent_profile.py`, `backend/app/agentive/services/skill_registry.py`

**Rule:** The jvagent workspace overlay MUST include a bundled skill only when:
1. The owning App is installed in the active workspace (`X-Integral-Scope`) with `lifecycle_state=active`, AND
2. The acting user has any effective role on that App (`resolve_role(user, "app", app_id)` is non-null).

Bundle `tools[]` / `hooks[]` remain registered per workspace at install; hook dispatch enforces the acting user's entry/App permissions via `ToolContext`.

**Verification:** `backend/tests/test_skill_access_scope.py`, `backend/tests/test_workspace_agent_profile.py`.

### I-SKILL-01 — JV skill frontmatter (discovery contract)

**Scope:** All on-disk `SKILL.md` under core `integral_*` paths and `backend/app/profiles/*/skills/*/`.

**Rule:** Every public disk skill MUST declare:

- `name` matching the parent directory
- non-empty `description` (third-person discovery: what + when)
- explicit `spec: jv` (or `spec: claude` for future Claude-skill bundles)
- `requires-actions: [EmbeddedIntegralAction]` when coordinating `integral_*` MCP tools
- `extends: action:integral/embedded_integral_action` for bundle overlays and core `integral_*` skills
- `allowed-tools` listing every catalogue tool backtick-referenced in the body

**Forbidden:** `plan-steps`, top-level `version`.

**Verification:** `backend/tests/test_skill_compliance.py`, `.ci/skill_compliance_check.sh`.

**Reference:** [skill-format-standard.md](./backend/skill-format-standard.md), jvagent's own `jvagent/skills/README.md` (upstream repo — https://github.com/TrueSelph/jvagent, not vendored here).

### I-SKILL-02 — Discovery vs body `When to use`

**Rule:** Frontmatter `description` is the orchestrator discovery string (editor: *When should the agent use this?*). Body `## When to use` MUST elaborate routing intents — MUST NOT duplicate `description` verbatim.

### I-SKILL-03 — Seven-section SOP bar

**Rule:** Core and public bundle skill bodies MUST pass the 7-section compliance bar (`when_not`, `grounding`, `procedure`, `staging`, `when_to_use`, `forbidden`, `example`). Missing sections are **errors** in CI.

**Verification:** `skill_compliance.py`, `audit_skills.py`.

### I-SKILL-04 — Manifest description parity

**Rule:** For bundle skills, `profile.yaml` skill `description` MUST match `SKILL.md` frontmatter after `sync_bundle_skill_manifests.py --write`.

**Verification:** `backend/tests/test_skill_compliance.py::test_bundle_manifests_synced`.

## Access Model Invariants

The two invariants below codify the privacy posture for ContentProfile-modelled
data and the lookup primitive that binds entries to workspace members.

### I-ACCESS-01 — Privacy-via-modeling, not field-level visibility

**Scope:** All ContentProfile authoring (`backend/app/profiles/*/profile.yaml`); permission resolver (`backend/app/services/permissions.py`); EntryType field declarations.

**Rule:** Integral's access model resolves a role per **resource** (App / Track / Entry) — there is NO field-level visibility primitive. Data whose audience differs from the rest of an Entry MUST be modelled into a **separate track** (the "anchored privileged track" pattern), not declared as a hidden field on a shared EntryType.

**Pattern (the only sanctioned shape):**

1. Author the privileged data as its own EntryType in its own Track. Examples in production: `Project Financials` and `Contracts & Legal` anchored to `Project`; `Compensation Records` anchored to `Employee`; `Pricing Rubrics` (cost / margin fields per rubric line).
2. Connect the privileged track to the parent entry via the Phase 3.1 anchor pattern — a `relation` field with `target: track` materializes an `ANCHORS` edge.
3. Place the audience that must NOT see the data on `EXCLUDED_FROM` for the privileged track.
4. The cross-track restricted-stub renderer (Phase 10) shows the anchor as a "Restricted" stub for any caller without access to the anchored track, so the parent Entry stays renderable without leaking the field set.

**Forbidden:**

- Declaring a field on a shared EntryType and trying to hide it for some collaborators via a `visibility_rule`, role attribute, or conditional renderer. **No such primitive exists.** Any field declared on an EntryType is visible to every caller with read access to the Entry.
- "Soft" privacy via redaction at the API layer for a single EntryType. Redaction lives at the cross-track boundary (anchor stub), not at the field boundary.

**Rationale:** Field-level visibility would fork the access surface into two evaluation paths (resource-level + field-level), each with its own cascade, deny, and audit semantics. The "model it as a track" pattern reuses the existing resource cascade, the existing `EXCLUDED_FROM` deny, and the existing cross-track stub — no new surface, no new gates.

**Verification:**

- Privilege regression tests under `backend/tests/test_*_privilege*.py` and `backend/tests/test_anchor_restricted_stub.py` cover the cross-track stub, the cascade-deny pair, and per-anchor visibility for representative tracks. These are pinned: adding a privileged-data field to a shared EntryType without splitting it into a track would regress those tests on first run.

**Origin:** Dogfood Phase 16 (REQ-ID ACC-01..02). Surfaced by the 2026-05-23 consulting walkthrough — engineer-visible Project entries carrying hidden cost fields would have been a structural data-leakage path. Resolved by modeling, not by adding field-level visibility.

### I-FIELD-MEMBER-01 — `member` field type binds to graph User node

**Scope:** `backend/app/services/content_profile_field_types.py` (field-type registry); `backend/app/models/edges.py` (`HasMemberRef` / `HAS_MEMBER_REF`); EntryType field declarations across all bundles.

**Rule:** Linking an Entry to a workspace **member** (a `User` account, NOT another Entry or Track) MUST use the `member` field type. The field materializes a `HAS_MEMBER_REF` edge from the Entry to the graph `User` node. Workspace membership is checked at lookup time — the field cannot reference a User outside the entry's workspace member pool.

**Why this is its own type:**

- `relation` is for Entry↔Entry / Entry↔Track lookups inside the ContentProfile graph. Pointing it at `User` would bypass the workspace member-pool gate and conflate two distinct cascade behaviors (relation REFERENCES vs. member binding).
- Denormalizing the user id onto the Entry as a scalar `user_id` field skips the edge entirely — no edge, no cascade-aware traversal, no graph-walk reads, no contiguousness with the rest of the substrate (violates I-GRAPH-01 in spirit).

**Forbidden:**

- `relation` field declarations with `target: user` (no such target exists; the validator rejects).
- Scalar `user_id` / `member_id` string fields on EntryTypes that semantically refer to a User account. Use `type: member` instead and let the edge carry the binding.

**Verification:**

- Field-type validator (`content_profile_field_types.py:243` — the `member` type registration) gates EntryType compile.
- `HasMemberRef` edge declared in `app/models/edges.py` is the canonical edge class; substrate code creating it lives only in the member-field materializer.

**Origin:** Dogfood Phase 16 (REQ-ID ACC-08). Surfaced by Employee↔User and Pricing-Rubric↔User needs; resolved by adding a dedicated field type rather than overloading `relation`.

## Chat & Staging Surface

### I-CHAT-01 — No raw node id surfaces in human-facing chat or staging text

**Scope:** `backend/app/services/id_resolver.py` (the resolver), and every surface that renders agent-authored or staging text to a person: `backend/app/services/chat_streaming.py`, `backend/app/api/ai_chat.py`, `backend/app/providers/jvagent_streaming.py`, `backend/app/agentive/staging.py`, `backend/app/agentive/api/staging.py`.

**Rule:** Text a person reads — assistant chat prose (live stream + persisted transcript) and staging-card `summary`/`diff_human` (including error messages and batch op previews) — MUST NOT contain a raw node id (`n.<Type>.<hex>`, `o.<Type>.<hex>`). Every such surface routes its text through `id_resolver.humanize_ids`, the single canonical pass that replaces each id with the node's human label. Resolution is schema-agnostic (any node type, via the discriminator-dispatching `Node.get`/`Object.get`), batched (`resolve_id_labels`, one `$in` query per collection), and best-effort (an unresolvable id degrades to `"<Type> …<last4>"`, never a raw id).

**Labeling:** the label priority chain is `title → name → display_name → filename → text-snippet`; `User`/`AuthUser` use `display_name|email` (resolved through `batch_resolve_users_by_principal_ids`, which covers both `n.User` and `o.User` principal forms); a `ContentProfile` is named by the Track/App it shapes ("the Opportunities profile"), resolving via the published parent for a draft. Batch placeholders (`{{app.id}}`, `{{step_2.id}}`) render as readable phrases ("the new app", "step 2's result").

**Where ids are kept (NOT humanized):** machine inputs the agent/executor act on — tool-call arguments, `StagedChange.payload`, `StagedChange.diff_machine`, and the raw-JSON inspector — retain ids verbatim. Humanizing is for human-facing TEXT only. Structured FE fields (relation/member values) resolve their own labels client-side and are out of scope.

**Verification:** `backend/tests/test_id_resolver.py` (resolution, batching, ContentProfile/user labeling, placeholder prettify, the streamed-delta buffer that releases only whole words so an id split across tokens is humanized before it reaches the browser).

## F0 — Foundation Extension Boundary

### I-EXT-01 — Core MUST NOT branch on App identity

**Scope:** `backend/app/services/`, `backend/app/api/`, `backend/app/models/`, `backend/app/schemas/` (same carve-outs as I-SUBSTRATE-01).

**Rule:** Core MUST NOT contain conditionals or hardcoded maps keyed on bundle slug, EntryType name, or Track name/template_id. Cross-app handoff aliases belong in `app.track_aliases` on the package manifest and are registered at install via `hooks/track_aliases.py`. NL→package routing uses catalog `package.tags` / name metadata, not a substrate keyword map.

**Enforcement:** `.ci/substrate_domain_drift_check.sh`, `.ci/core_no_app_import_check.sh`, code review against [extension-contract-v1.md](platform/extension-contract-v1.md).

**Origin:** F0 Core separation (`FOUNDATION_EXTENSION_SAAS.md`).

### I-EXT-02 — Substrate-owned defaults are `core_package` manifests

**Scope:** Signup provisioning, scratch provisioning, library sync under `INTEGRAL_CORE_ONLY`.

**Rule:** Defaults every installation receives (e.g. `agent-scratch`) MUST be declared with `package.class: core_package` and installed through `services/core_seed_installer.py` (or equivalent generic installer). Hardcoding domain App slugs in substrate services for commercial/community packages (including `personal-context`) is forbidden. Under `INTEGRAL_CORE_ONLY=1`, library sync loads only `core_package` artifacts.

**Verification:** `backend/tests/contract/test_reference_hello_app.py::test_core_only_excludes_reference_app`; `make verify-core-only`.

**Origin:** F0 Core separation / I-EXT-02.

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

**Rule:** Human waits require a pending `WorkApproval`. Approve requeues the original WorkItem; reject/expiry terminalize. Cards without `work_approval_id` keep the legacy inline path.

**Verification:** `tests/test_work_approvals.py`.

### I-WORK-06 — Production store posture

**Scope:** `work_lifecycle.py`, `main.py` startup.

**Rule:** Production boots fail closed for Mongo, missing work indexes, or missing public transaction CAS. JSON/SQLite are single-worker development stores with reconciliation only.

**Verification:** `tests/test_work_kernel_lifecycle.py`.
