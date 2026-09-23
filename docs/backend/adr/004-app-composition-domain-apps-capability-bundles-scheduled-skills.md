# ADR 004 — App composition: domain apps, capability bundles, scheduled skills

**Status:** Proposed (pending architect review — this record is the review artifact)
**Date:** 2026-07

## Context

The first productized layer grew three bundles that, in hindsight, are not
coherent *apps*:

- **Produce** — a render engine (`render_artifact`, `fingerprint_sources`) plus
  Specimen/Proposal libraries and an `artifacts` track. Rendering is
  cross-cutting: Sales wanted it for proposals and Pulse wanted it for digests,
  so a standalone "Produce" app forced consumers to take a dependency on an app
  whose real value is a *reusable routine*.
- **Pulse** — plans/OKR tracking, a `compute_metrics` routine over `datasets`,
  and recurring digests kept alive by an always-on `pulse_resident` and
  `entry.precompute` rollup hooks. Its cadence machinery is exactly what the
  singular resident harness now owns natively via scheduled tasks (ADR-003), and
  its plan/status content is really Projects-domain content (its own
  `plan_review` skill stages tasks on the *Projects* board).
- **Client Proposal** — a thin orchestrator over Sales + Produce. The
  transcript→scope→price→proposal→document arc is a single *business domain*
  (pre-sales), yet it was split across three installable apps.

The pattern behind all three problems is the same: **we let reusable routines
and operational cadence become standalone apps, and we scattered one business
domain across several apps.** That taxes users (install N utility apps to do one
job), sprawls the dependency graph (`requires_apps` chains), and makes domain
content a cross-app scavenger hunt.

## Decision

There are exactly **three kinds of unit** in the suite, each with one rule.

1. **Domain app** — a coherent business surface that **owns its content
   end-to-end**: its tracks, entry types, domain skills, domain tools, and its
   resident persona facet. A domain app is the grouping a user thinks of as
   "one area of work." It is **self-contained** — everything that domain owns
   lives under it; there is no cross-app hunt for its own data.
   *Example:* **Sales** owns discovery → scoping → pricing → proposals →
   engagements → specimen & proposal libraries.

2. **Capability bundle** — a **reusable routine** (one or more `tools[]`, plus a
   thin skill if a conversational entry point helps) with **no tracks or content
   of its own**. Domain apps depend on it via `requires_apps`. Use a capability
   bundle when a routine is domain-agnostic (rendering, metrics, extraction) or
   needed by more than one domain.
   *Example:* **`document-render`** (`render_artifact` + `fingerprint_sources`).

3. **Scheduled skill (cadence)** — recurring / operational work is a **skill run
   on the resident-harness schedule** (ADR-003), **not** a standalone app. There
   is no always-on cadence agent and no "operations app." The content the skill
   reads and writes lives in the relevant domain app.
   *Example:* a weekly status digest is a scheduled run of a `status_digest`
   skill, not a Pulse install.

### Rules that follow

- **No standalone utility apps.** If a thing is a reusable routine, it is a
  capability bundle (multi-consumer / domain-agnostic) or is folded into the one
  domain app that needs it (single-consumer). If it is recurring/operational, it
  is a scheduled skill. It is never its own app.
- **Reusable-routine placement.** Single consumer → build it into that domain
  app. Multiple consumers or domain-agnostic → capability bundle. Prefer folding
  in until a second consumer is real; extract to a capability bundle when it
  arrives (measure, then extract — do not pre-generalize).
- **Domain apps are self-contained.** All content a domain owns lives under one
  app. A domain app may *depend on* capability bundles, but it does not delegate
  ownership of its content to another app.
- **Cadence is scheduled skills, never an app.** The resident harness owns
  scheduling (ADR-003); "keep a living picture / send a recurring digest" is a
  skill on a cron, reading/writing the owning domain app's content.
- **Browsable standards/config → Wiki track with typed pages.** When a domain's
  configuration is both machine-read *and* human-documented (e.g. pricing
  standards), model it as a `wiki` track whose **structured pages drive engines**
  (a rate-catalog page a pricing tool reads) and whose **prose pages document
  the standard** (methods, discount policy, procedures). One track, mixed entry
  types, sliced per view — not a monolithic single-record config node.

## Decision tree (authoring)

```
Is it content a user works with as one area of business?      → Domain app.
Is it a reusable routine (tool/skill, no content of its own)?
   ↳ needed by >1 domain, or domain-agnostic?                 → Capability bundle.
   ↳ needed by exactly one domain today?                      → Fold into that app.
Is it recurring / operational (digest, rollup, review)?       → Scheduled skill on the harness.
Is it configuration that is both machine-read and documented? → Wiki track, typed pages.
```

## Consequences (this ADR's initial application)

- **Produce is retired.** Its render capability was first extracted as a
  `document_render` capability bundle, then **folded into Sales** (see Addendum);
  its Specimen Library and Proposal Library (Sales content) fold into **Sales**;
  the generic `artifacts` track is replaced by attaching the rendered document to
  the owning entry (the proposal).
- **Client Proposal is retired.** Its `engagements` track, `proposal_from_transcript`
  skill, and resident persona fold into **Sales**, which becomes the single,
  self-contained pre-sales domain app.
- **Pulse is retired.** Its cadence (`weekly_standup`, `status_rollup`,
  `plan_review`, `data_summary`) becomes **skills fired by scheduled tasks**; its
  Plans/Status content folds into **Projects**; `datasets` + `compute_metrics`
  are dropped until a real consumer needs them (then: a `metrics` capability
  bundle, per the rules above). The always-on `pulse_resident` and precompute
  rollup hooks go away — a scheduled recompute replaces live mirror fields
  (`open_count`/`at_risk`); if real-time rollup is ever required it is a single
  hook in Projects, not an app.
- **Sales Pricing becomes a Wiki track** — a structured Rate Catalog page (drives
  à-la-carte pricing), structured Discount / Engagement-Tier pages, and prose
  Method / Procedure pages.

## Relation to existing decisions

- **ADR-003 (singular resident harness)** — cadence-as-scheduled-skill is only
  possible because one resident owns the schedule; this ADR is the app-shape
  corollary of that harness decision.
- **App bundles v1 / I-BUNDLE-01..05** — capability bundles are still bundles
  (same manifest, trust tier, facade boundary); this ADR constrains *what a
  bundle should be*, not *how it is built*.
- **Operational Model modeling tenets** — "Track ≈ table, Entry ≈ record, App ≈
  schema/database" still holds; this ADR adds the layer above: *which* records
  and tables belong together as an App, and what belongs outside apps entirely.

## Addendum — render is a core substrate tool (`integral_document_render`)

The render routine was the ADR's motivating capability-bundle example because it
had **two** prospective consumers: Sales (proposals) and Pulse (digests). This
ADR retires Pulse, leaving one consumer today — but document rendering is a
genuinely **domain-agnostic substrate capability** any app may need (a rendered
`.docx`/`.pdf` of an entry's composed content). Rather than trap it inside one
domain app, it is exposed as a **core `integral_*` tool**: `integral_document_render`.

- Engine lives in the substrate: `app/services/document_render/`
  (`render.py`, `branding.py`, `fingerprint.py`; default letterhead in
  `assets/`). It is **domain-neutral** (I-SUBSTRATE-01): no pricing/domain field
  names are baked in — the caller passes `redact_fields` (privileged keys to
  strip when `client_safe`), `exclude_fields`, and a caller-built `summary_table`.
- Surface: `POST /api/entries/{entry_id}/render` + a manifest entry + binding, so
  it appears in the standard `integral_*` tool catalogue available to every
  agent/app. `op_class: execute` (renders + attaches immediately; the write is
  gated on owner/editor of the entry by `ToolContext`).
- Sales no longer owns render tools. Its `price_engagement` returns a
  client-safe `summary_table` (the Investment table); the `proposal_from_transcript`
  skill calls `integral_document_render` with that `summary_table` + its
  `redact_fields`. Sales `requires_apps: [crm, projects]` only.

The capability-bundle *pattern* still stands for reusable routines that are
neither domain-agnostic substrate primitives nor single-consumer (extract to a
bundle when a real second consumer appears — e.g. a future `metrics` routine).
