# Composition Patterns

**Status:** Stable as of Phase 3.1 (`2026-05-16`).
**Cross-references:** [README.md](./README.md) (Modeling Tenets §), [AGENT_CONTRACT.md](./AGENT_CONTRACT.md), [COMPOSITES.md](./COMPOSITES.md), [META_WIDGETS.md](./META_WIDGETS.md), [DRAFT_PUBLISH.md](./DRAFT_PUBLISH.md), [../INVARIANTS.md](../INVARIANTS.md).

This document is the canonical reference for the two composition patterns
agents and manifest authors must choose between when modeling a parent ↔
child information shape in Integral. It expands the high-level Modeling
Tenets in [README.md](./README.md) into a concrete pattern catalogue,
documents the four Phase 3.1 resolved forks, and enumerates the negative
space (the anti-patterns the substrate explicitly forbids).

> **Modeling tenet (canonical, repeated from README.md).** Track ≈ table.
> Entry ≈ row. App ≈ schema / database. Depth lives in **edges**
> (`REFERENCES` or `ANCHORS`), never in nested JSON shapes, never in a
> per-entry hierarchical containment edge. See [README.md](./README.md)
> *Modeling Tenets* for the full statement.

---

## Why two patterns (and only two)

Real domain knowledge is record-shaped at every layer — but record
relationships divide cleanly into two intents:

1. The child is **another row** that happens to live in its own table
   (the child is a first-class record, queryable independently, possibly
   reused across many parents). This is the **sibling-track pattern**
   (value-lookup; materializes a `REFERENCES` edge).
2. The child is **a whole sub-table** that exists *because* of a specific
   parent row (the child container is per-parent, may hold mixed entity
   types, has its own views and governance). This is the **anchor pattern**
   (expansion reference; materializes an `ANCHORS` edge).

Anything else — JSON nesting inside a parent's `custom_fields`, a new
hierarchical containment edge, one anchored track per child *category*,
mixing both patterns for the same parent↔child relationship — is a
violation of the modeling tenets and is forbidden by the substrate
plan-checker.

---

## Pattern 1 — Sibling-Track Pattern (value-lookup, `REFERENCES`)

### Use when

- The child entity is narrow and homogeneous (a single `EntryType`).
- Cross-parent rollups matter ("show me all open tasks across all
  projects, sorted by due date").
- Children are independently queryable + reportable as a flat table.
- The same child may legitimately appear in many parents' contexts
  (e.g. a `Contact` referenced by multiple `Project` and `Deal` entries).

### Mechanism

- A separate Track holds the children at the same logical level as the
  parent Track in the App (Apps own multiple Tracks via `CONTAINS`).
- Each child Entry carries a `relation` field whose `target` is `entry`
  and whose `target_track_types` (or `target_entry_types`) names the
  parent type. Materializes the `REFERENCES` edge (`Entry → Entry`,
  carrying `field_key` and the `cross_track` flag).
- Per-parent projection happens at the view layer: filter the child Track
  by `:entry_id` resolving to the current parent.

### Example manifest (track-scope CP for the children's Track)

```yaml
content_profile_schema_version: 1
scope: track

entry_types:
  - key: task
    name: Task
    fields:
      - { key: title, type: text, required: true }
      - { key: status, type: select, options: [todo, doing, done] }
      - { key: project,
          type: relation,
          relation: { target: entry, target_entry_types: [project] } }

views:
  - { key: all-tasks, kind: table }
  - { key: tasks-for-project,
      kind: kanban,
      config: { group_by: status,
                filters: [{ field: project, op: eq, value: ":entry_id" }] } }
```

### Walking the edge

```python
# Tasks referencing a specific project entry:
project_tasks = await project.nodes(
    edge=["REFERENCES"], direction="in", node=["Entry"]
)

# All open tasks across all projects (the cross-parent rollup the
# sibling-track pattern is designed for):
open_tasks = await Entry.find({
    "entry_type_key": "task",
    "field_values.status": "todo",
})
```

### Canonical examples in Integral

- `Contacts` track in the CRM app (`backend/app/profiles/crm/`): a contact
  is referenced by `project.contact` on the Projects app (cross-app
  `relation` with `target_app: crm`).
- Opportunities referencing Contacts within CRM.
- Turnkey wiring: `crm-pm-workspace` workspace profile.

---

## Pattern 2 — Anchor Pattern (expansion reference, `ANCHORS`)

### Use when

- The child container is heavyweight — mixed entry types, multiple
  views, its own ContentProfile-scoped behavior.
- The container is per-parent: each parent Entry conceptually "expands
  into" its own dedicated Track of detail.
- The detail Track should still be independently queryable as a
  first-class Track (Entries inside it follow the usual access rules) —
  but its existence is *because of* a single parent Entry.

### Mechanism

- A NEW Track is auto-provisioned per parent Entry the first time the
  parent is created (or the anchor field is written).
- The provisioned Track is attached **by reference** to a TEMPLATE
  ContentProfile registered under the Space's
  `space.track_templates[]` manifest key. No CP clone — see Resolved
  Fork 2 below.
- The parent Entry carries a `relation` field with `target: track`,
  materializing the `ANCHORS` edge (`Entry → Track`). The auto-provision
  helper is `materialize_anchor_track` in
  `backend/app/services/content_profile_graph.py`.
- Provenance is recorded via the new `TEMPLATED_FROM` edge
  (`Track → ContentProfile`; see Resolved Fork 4 below). `USES_TEMPLATE`
  remains `Track → Track` and is NOT overloaded.
- Per-parent projection happens via `related_views` on the parent
  Entry's EntryType, using the `:anchored_track` template-var resolver
  (Plan 03.1-04, Pillar 3): `:anchored_track/<view_key>`.

### Example manifest

```yaml
# track-scope CP for the parent (Projects) track:
content_profile_schema_version: 1
scope: track

entry_types:
  - key: project
    name: Project
    fields:
      - { key: title, type: text, required: true }
      - { key: details_track,
          type: relation,
          relation:
            target: track
            target_track_template: project-details
            auto_provision: true
            governance:
              cardinality: one
              cascade: hard
              acl_inheritance: inherit }
    related_views:
      - { view: ":anchored_track/tasks-board", bind: {} }
      - { view: ":anchored_track/activity-feed", bind: {} }
      - { view: ":anchored_track/all", bind: {} }

---
# space-scope CP for the Projects Space:
content_profile_schema_version: 1
scope: space

space:
  tracks:
    - { template_slug: projects }     # the parent Track (above)
  relations: []
  # NEW Phase 3.1 — track-template registry
  track_templates:
    - { key: project-details,
        name: "Project Details",
        template_slug: project-details }
```

### Walking the edge

```python
# Find a project's anchored details Track:
anchored_tracks = await project_entry.nodes(
    edge=["ANCHORS"], direction="out", node=["Track"]
)
details_track = anchored_tracks[0]   # cardinality=one ⇒ exactly one

# Provenance: which template CP backs this Track?
template_cp = (
    await details_track.nodes(
        edge=["TEMPLATED_FROM"], direction="out", node=["ContentProfile"]
    )
)[0]
```

### Canonical example in Integral

- `Projects` track in `backend/app/profiles/projects/`: each `Project` entry's
  `details_track` field auto-provisions an anchored track from template
  `project-details`. `financials_track` and `contracts_track` are **opt-in**
  (`auto_provision: false`) — the UI offers None | Create Track and sends
  `__create_anchor__` (`CREATE_ANCHOR_SENTINEL`) when the user provisions them.

---

## Resolved Forks (Phase 3.1)

These four design forks were resolved during Phase 3.1 (anchor
associations governance). They are locked for v1; revisiting any of
them is a separate substrate-touching plan.

### Fork 1 — System-subject governance

Anchor governance Policies are persisted with `subject_kind="system"`.
This preserves the `ActorKind` single-Literal invariant
(see [INVARIANTS.md](../INVARIANTS.md)) — no new `association` actor
kind is introduced. Policy enforcement at auto-provision time runs
`policy_engine.evaluate(subject=system_subject, action="anchor.create",
resource=anchored_track_descriptor)` against governance Policies
materialized at CP publish time.

### Fork 2 — Shared CP by reference (no per-anchor clone in v1)

Auto-provisioned anchored Tracks `HAS_CONTENT_PROFILE` the TEMPLATE's
existing `ContentProfile` node — there is no clone, no copy, no fork.
Two `Project` entries' anchored Tracks share the same
`Track.attached_content_profile_id` scalar AND the same
`HAS_CONTENT_PROFILE` edge target. Per-anchor variance (fork-on-edit,
copy-on-write) is deferred to a future phase. The invariant is asserted
by `test_shared_cp_by_reference_invariant` in
`backend/tests/test_anchor_provision.py`.

### Fork 3 — Hard cascade in v1

Deleting a parent Entry walks its `ANCHORS` edges and, gated by
`policy_engine.evaluate(action="anchor.cascade")`, hard-deletes each
anchored Track plus every Entry contained in it. Denial preserves the
Track and emits a `policy.deny` ChangeEvent
(`details.failed_action="anchor.cascade"`) via the Phase 3 03-05
denial-emit path. Archive-then-delete (soft cascade with grave-period)
is a separate future phase, **not** Phase 3.1.

### Fork 4 — `TEMPLATED_FROM` provenance (NOT a `USES_TEMPLATE` overload)

A NEW edge `TEMPLATED_FROM` (`Track → ContentProfile`) records template
provenance for auto-provisioned anchored Tracks. The existing
`USES_TEMPLATE` edge (`Track → Track`) keeps its pre-3.1 semantics
verbatim and is NOT overloaded. Plan 03.1-03's plan-check gate verifies
the two edges remain distinct.

### Fork 5 — `space.track_templates` registry (NOT a `DEFINES_TRACK_PROFILE` walk)

Anchored-Track templates live under a new top-level manifest key on
space-scope CPs:

```yaml
space:
  track_templates:
    - { key: <slug>, name: <display>, template_slug|content_profile_id: <ref> }
```

Lookup at auto-provision time is a dict resolution against the parsed
manifest — NOT a `DEFINES_TRACK_PROFILE` graph walk per provision call.
This keeps the hot path constant-time and avoids coupling to the older
space-attached → track-template subgraph edge.

---

## Negative Space — Anti-Patterns

These patterns are **forbidden** by the substrate and are guarded by
plan-checker gates plus runtime validators in
`backend/app/services/content_profile_graph.py` and
`backend/app/services/content_profile_compile.py`.

### No JSON nesting of operational entities

A `Project` entry with `custom_fields.tasks = [...]` inline is forbidden.
That shape is record-shaped only at the top level; the embedded `tasks`
array is JSON-shaped and inherits none of the substrate's guarantees
(no edges, no per-row permissions, no views, no audit log, no agent
introspection). Use the anchor pattern (heavyweight per-parent detail)
or the sibling-track pattern (cross-parent rollups) instead.

### No new hierarchical containment edge

An Entry never "owns" a Track in the `CONTAINS` sense. The `ANCHORS`
edge is an **additional pointer**, not a containment edge. The anchored
Track is top-level under its App via the existing `App → Track`
`CONTAINS` edge; the `ANCHORS` edge layers on top to record the
parent-Entry origin.

Concretely: `Workspace CONTAINS App CONTAINS Track CONTAINS Entry`
remains the only containment chain. There is no
`Entry CONTAINS Track`, no `Entry CONTAINS Entry`, and no other
hierarchical containment edge introduced by the anchor pattern.

### No per-anchor ContentProfile clone in v1

(Restated from Resolved Fork 2.) Auto-provisioned anchored Tracks share
the template CP by reference. Forking the CP per-anchor is forbidden in
v1; the substrate plan-checker grep gate enforces a single
`materialize_anchor_track` code path that ALWAYS resolves the existing
template CP node.

### No cross-workspace anchoring in v1

Anchored Tracks always inherit `source_track.workspace_id`. Targeting a
Track in another workspace is rejected at two layers:

1. `_validate_relation_values` in `content_profile_compile.py`
   (manifest-validator layer) — rejects manifests that reference a
   cross-workspace track template id.
2. `materialize_anchor_track` in `content_profile_graph.py`
   (auto-provision layer) — enforces same-workspace stamping on the
   newly-created Track.

Cross-workspace use cases must use the sibling-track pattern plus
guest-grants on the referenced Entry.

### No mixing the two patterns for the same parent↔child relationship

For a given parent↔child relationship, choose **one** pattern. If your
`Project` has `tasks`, decide: either `Tasks` is a sibling Track that
each `Project` references (sibling-track pattern, cross-parent rollups
work), OR `Tasks` is one of the mixed entry types in the anchored
`Project-Details` Track (anchor pattern, per-project board view works).
Doing both creates two truth surfaces and breaks the agent's ability to
reason about the substrate.

### No direct `edge=ANCHORS` writes outside `_sync_anchor_edges`

`ANCHORS` edges are written ONLY through `_sync_anchor_edges` in
`backend/app/services/content_profile_graph.py`, invoked via the
relation-field write path. API handlers, services, seed files, and
agent tools must NOT call `entry.connect(track, edge=ANCHORS, ...)`
directly. The substrate plan-checker grep gate enforces this:

```bash
grep -rE "edge=ANCHORS|edge=Anchors\b" backend/app/ --include="*.py" \
  | grep -v __pycache__ \
  | grep -v _sync_anchor_edges \
  | grep -v test_ \
  | wc -l   # MUST be 0
```

---

## When to Use Which — Decision Matrix

| Question | Sibling-Track | Anchor |
| --- | --- | --- |
| How many child entity types? | One | Many (mixed) |
| Cross-parent rollups required? | Yes (canonical use case) | No (per-parent only) |
| Child container shape | Shared single Track | One Track per parent |
| Governance | Parent Track-level | Per anchor field (governance block) |
| Materialized edge | `REFERENCES` | `ANCHORS` (+ `TEMPLATED_FROM` for provenance) |
| Provision cost | None (write child entries directly) | Auto-provisions a Track on parent create |
| Views | Filtered by `:entry_id` | Filtered by `:anchored_track` |
| Independent queryability | Yes (flat child Track) | Yes (each anchored Track is top-level) |

If you find yourself wanting *both* — a per-project board (anchor) AND
a cross-project rollup (sibling-track) — that is a signal you have two
different relationships in your domain, not one. Model them as two
distinct fields on the parent: e.g. `Project.tasks` (anchor pattern,
per-project board) AND `Project.linked_jira_issues` (sibling-track
pattern, cross-project Jira rollups). Pick one mechanism per relationship.

---

## Agent Introspection

Agents discover the anchor primitive — including which composition
pattern is available, which governance actions exist, and which template
variables they can use — via the `integral_describe_substrate` MCP tool
(see [AGENT_CONTRACT.md](./AGENT_CONTRACT.md), Pillar 3):

```json
{
  "relation_targets": ["entry", "track"],
  "edges": {
    "REFERENCES": {
      "source": "Entry",
      "target": "Entry",
      "via": "relation.target=entry"
    },
    "ANCHORS": {
      "source": "Entry",
      "target": "Track",
      "via": "relation.target=track"
    },
    "TEMPLATED_FROM": {
      "source": "Track",
      "target": "ContentProfile",
      "via": "auto_provision via space.track_templates"
    }
  },
  "template_var_resolvers": [
    ":current_user",
    ":entry_id",
    ":anchored_track"
  ],
  "governance_actions": [
    "anchor.create",
    "anchor.delete",
    "anchor.cascade"
  ]
}
```

Agents authoring or modifying a ContentProfile that wants to use the
anchor pattern should:

1. Call `integral_describe_substrate` to confirm `relation_targets`
   includes `"track"` and the `governance_actions` list includes
   `anchor.*`.
2. Declare the anchor field on the parent entry type via
   `relation.target = "track"` + `target_track_template = "<key>"` +
   `auto_provision = true` + a `governance` block.
3. Declare the template under `space.track_templates` of the parent
   Space's CP (the registry that `materialize_anchor_track` consults at
   provision time).
4. Optionally declare `related_views` on the parent entry type
   referencing `:anchored_track/<view_key>` to render the anchored
   Track's views inline in `EntryDetail`.
5. Validate the draft via `integral_diff_profile_draft` before
   publishing. Publish materializes the governance Policies that gate
   `anchor.create` / `anchor.delete` / `anchor.cascade` for each
   anchor field.

---

## See Also

- [README.md](./README.md) — Modeling Tenets (canonical orienting
  principle this document expands).
- [AGENT_CONTRACT.md](./AGENT_CONTRACT.md) — Agent introspection +
  publish-cycle tools (Pillar 3).
- [DRAFT_PUBLISH.md](./DRAFT_PUBLISH.md) — Draft / publish lifecycle
  governance Policies are materialized in.
- [META_WIDGETS.md](./META_WIDGETS.md) — Composable views referenced by
  the anchor pattern's mixed-mode `all` view.
- [../INVARIANTS.md](../INVARIANTS.md) — Canonical substrate-wide
  invariants enumeration (including anchor-pattern invariants).
- `backend/app/profiles/crm-pm-workspace/profile.yaml` —
  Canonical exemplar (Projects + Project-Details anchor pattern).
- `backend/tests/test_anchor_integration.py` — End-to-end integration
  test exercising the full anchor pipeline.
