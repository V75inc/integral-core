# Content Profile Substrate

Reference docs for the agent-authorable Content Profile substrate
(implemented across Phases 1–3 of the v1.1 manifest evolution).

## Modeling Tenets

When advising on how to model an information space within Integral, orient
every answer around the following analogy. It is the canonical mental model
for the substrate.

### The Track ↔ Table, Entry ↔ Record analogy

- A **Track is a typed table.** It holds records of one or more declared
  entity types, governed by an attached ContentProfile.
- An **Entry is a record in that table.** Its shape is constrained by its
  `EntryType`, which lives under the track's ContentProfile.
- An **App is a logical schema / database.** It groups related tracks and
  declares cross-track relations.

### How records express depth: two reference patterns

When a record needs to point at information beyond its own fields, choose
one of two reference patterns — never both for the same relationship, and
never invent a third:

1. **Lookup / value reference — `relation` field with `target: entry`.**
   The record references another record in another table (e.g. a Project
   entry references a Contact entry for the project's client). Materializes
   a `REFERENCES` edge. Use when the target is itself a first-class record
   and the relationship is many-records-point-at-one-record.

2. **Expansion reference — `relation` field with `target: track`** *(anchor
   pattern, Phase 3.1).* The record anchors a whole companion table that
   houses an expansion of that single record's context (e.g. a Project
   entry anchors a Project Details track containing the project's tasks,
   activities, updates, resources). Materializes an `ANCHORS` edge. Use
   when one parent record legitimately owns a heavyweight, mixed-entity
   detail collection with its own views, ACLs, and lifecycle.

### Depth through mixed entity types in one track

Instead of provisioning one anchored track per category (one for tasks, one
for activities, one for updates), declare **multiple `EntryType`s under a
single anchored track's ContentProfile** and let each view project a slice
of the entry-type set:

- Kanban view → constrained to `task` entries, grouped by status.
- Feed view → constrained to `activity` + `update` entries, sorted by date.
- Table view → no entry-type constraint, shows everything.

Per-view entry-type filtering is a first-class substrate primitive via
`View.entry_type_keys` + `View.default_entry_type_key` — not a generic
`filters: [{field: entry_type, ...}]` workaround. This is what gives the
anchor pattern its flexibility without requiring per-category tracks.

### Negative space

Do not:

- Stuff a collection (e.g. tasks) inside a parent entry's JSON/custom-field
  payload — the child entities lose first-class status (no comments, no
  ACLs, no agent visibility, no cross-references).
- Invent a new hierarchical containment edge — anchored tracks remain
  top-level under their App; the anchor is an additional pointer, not a
  containment.
- Provision one anchored track per child category when mixed entity types
  in one anchored track + per-view filtering does the job.
- Use the expansion pattern when a lookup will do (and vice versa) — the
  two patterns answer different shapes of question.

### Why this matters

The track-as-table mental model gives users, agents, and authors a shared
vocabulary for designing an information space. Every "how should we model
X?" answer reduces to: *which tables, which records, which lookups, which
expansions, which entity types per table, which views per slice.* The
substrate's job is to make those choices cheap and reversible; the agent
contract's job is to expose them introspectively so agents can author by
the same rules a human would.

## View palette (profiles compose, clients render)

Content profiles **do not** hot-load arbitrary view source at runtime. They
declare `view_type` keys from a **prebuilt palette** (`feed`, `kanban`,
`composable_board`, …); backend validates config; web/mobile clients render
implementations shipped with the release.

Full convention (contracts, manifests, catalog apply, developer workflow):
[VIEW_PALETTE.md](VIEW_PALETTE.md).

## Pillars

| Pillar | Doc |
|--------|-----|
| 0 — View palette (keys + config, not hot-load)      | [VIEW_PALETTE.md](VIEW_PALETTE.md) |
| 1 — Declarative type system (composites + plugins) | [COMPOSITES.md](COMPOSITES.md), [PLUGINS.md](PLUGINS.md) |
| 2 — Versioned, sandboxed schema lifecycle          | [DRAFT_PUBLISH.md](DRAFT_PUBLISH.md), [MIGRATIONS.md](MIGRATIONS.md) |
| 3 — Agent reasoning contract                       | [AGENT_CONTRACT.md](AGENT_CONTRACT.md) |
| 4 — Composable meta-widgets                        | [META_WIDGETS.md](META_WIDGETS.md) |

**App operational layer** (skills, agents, bundle tools/hooks — distinct from schema pillars above): [../backend/app-bundles-v1.md](../backend/app-bundles-v1.md), [../backend/content-profile-authoring-and-library.md](../backend/content-profile-authoring-and-library.md), [../backend/workspace-agent-profile.md](../backend/workspace-agent-profile.md).

For the high-level overview + capability matrix see
[../platform/content-profile.md](../platform/content-profile.md).

The full pattern catalogue — sibling-track pattern, anchor pattern,
governance policy, exemplars — will land at
`COMPOSITION_PATTERNS.md` as part of Phase 3.1 (ANC-10). This README's
**Modeling Tenets** section is the orienting principle that doc expands.
