# Region System — "Apex-style" reusable regions + the create_wizard bundle

The region system is a **operational-model plugin bundle**
(`backend/app/plugins/region_system/`) — a small catalog of generic,
config-driven "page region" view types, modeled on Oracle APEX's Region
concept, plus the `create_wizard` primitive's step-kind catalog. Both live
in the same plugin because they're the same mental model: ordered,
config-driven specs composed into a placement slot. They just render on
two different surfaces — `LayoutContainerWidget` for an existing entry's
`related_views` slot, `CreateWizardModal` for a not-yet-created entry.

This is the doc [VIEW_PALETTE.md](VIEW_PALETTE.md) points at for the
region-system entries in the palette; read that doc first for the general
view-palette mental model (contract vs registry vs implementation vs
Operational Model spec). This page is region-system-specific detail plus the
standard for extending it.

For reusable, named arrangements of these existing regions and wizard steps,
see [UI_COMPLEMENTS.md](UI_COMPLEMENTS.md). Those recipes sit above this
system and compile into its ordinary configuration; they do not replace or
fork the Region System or `create_wizard`.

---

## Why this exists

Most view types (`table`, `kanban`, `feed`, …) render a **list of
entries**. A lot of real UI need is smaller than that — a form bound to
one entry, a KPI strip, a chart, a tree, a multi-step create flow. Rather
than every app hand-building a bespoke widget for each of these, the
region system supplies a fixed set of generic primitives that take
**config, not code** — an app's `operational-model.yaml` composes them, nothing
imports React across app boundaries.

---

## The region view types

Registered in `region_system/__init__.py`'s `register()`, `palette_group:
"core"` (genuinely reusable by any app — see [VIEW_PALETTE.md](VIEW_PALETTE.md)
for what that label is supposed to mean and why it matters to keep honest).

| `view_type` | Role | Key config |
|---|---|---|
| `form_region` | Subset-of-fields form bound to the current entry, a related entry (via a workspace tool), or an anchored track's entry | `fields` (ordered keys), `title?`, `columns?` |
| `layout_container` | Stack/tabs/accordion/grid/flex wrapper composing an ordered list of child regions (nested views or inline sub-forms) in one `related_views` slot — `kind:'view'` may resolve to another `layout_container`, so nesting depth is only limited by good sense | `regions` (ordered `RegionSpec[]`), `mode?` (may itself be responsive — switch composition strategy per breakpoint), `title?`, `layout?` |
| `static_content` | Read-only markdown block — section instructions, headers, notes between regions | `body?` or `body_field?` |
| `tree_region` | Hierarchical tree over a track's entries via a self-relational parent field (e.g. an HR org chart via `manager`) | `source_track`, `parent_field`, `label_field?`, `sort_siblings?` |
| `chart_region` | Bar/line/area/scatter/pie/donut/gauge chart bound to the current entry's own numeric fields, or aggregated over a track's entries | `chart_type`, `x_field`, `y_field?`, `aggregate?`, `group_by?`, `source?`, `fields?`, `title?`, `gauge_max?` |
| `summary_tiles` | Read-only KPI strip bound to the current entry's own fields, each tile optionally carrying an icon and a trend/delta indicator | `tiles` (`[{label, field, format?, icon?, trend?}]`), `title?` |
| `reverse_relation_list` | Read-only list of entries elsewhere that reference the current entry via a `relation` field — no anchor-track prerequisite, works across independent tracks | `relation` (the field_key on the OTHER track pointing back), `title?` |
| `modal_region` | A button that opens an Apex-style "modal page" of nested regions in the platform's own dialog | `trigger_label`, `trigger_variant?`, `title?`, `width?`, `regions` |
| `popover_region` | A button that opens a small floating panel anchored to it — lighter than `modal_region`, no backdrop | `trigger_label`, `trigger_variant?`, `placement?`, `regions` |
| `drawer_region` | A button that opens a side panel instead of a centered dialog | `trigger_label`, `trigger_variant?`, `title?`, `side?`, `width_px?`, `regions` |

Full field-level detail for each lives in the plugin's own module
docstring (`backend/app/plugins/region_system/__init__.py`) and each
`ViewTypeSpec.config_schema` — read those before guessing a shape; they're
the actual contract, this table is a summary.

### `layout_container` in depth

The composition primitive the others sit inside of. `regions[]` is an
ordered list of `{key, kind: 'view'|'form', title?, visible_if?, layout?,
accent?, collapsible?, default_open?}`:

- `kind: 'view'` mounts a nested `ComposableViewSlot` for another
  registered `view_type`, scoped to the container's own track — including
  another `layout_container`, so a tab can itself be a grid, a grid cell
  can itself be tabs, etc. Nesting depth is only limited by good sense.
- `kind: 'form'` reuses `FormRegionWidget` directly (imported, not
  duplicated) via a synthetic view carrying the child's `fields`.
- `visible_if: {field, equals}` gates a region on another field's current
  value on the bound entry (e.g. only show "Pay Period Dates" when
  `schedule_type == 'Weekly'") — shared conditional-visibility engine
  (`regionConditions.ts`) also backs `FormRegionWidget`'s per-field gates
  and `EditableTableWidget`'s per-column gates. One mechanism, three
  callers.
- `mode`: `stack` (default) | `tabs` | `accordion` | `grid` | `flex`. Grid
  and flex modes take a container-level `layout` config plus optional
  per-region `layout` overrides (`span`/`order`/`column`/`row` for grid;
  `grow`/`shrink`/`basis` for flex) — every layout value may be a bare
  value or `{desktop?, tablet?, mobile?}` for responsive overrides.
  `mode` ITSELF may also be responsive (`{desktop?, tablet?, mobile?}`) —
  the whole composition strategy can switch per viewport, e.g. `tabs` on
  desktop, `stack` on mobile where a horizontal tab strip wouldn't fit.
  Grid's `column`/`row` are explicit 1-indexed CSS grid-column-start/
  grid-row-start — the actual APEX-style "put this region at exactly
  this position" control, not just auto-flow ordering: two regions
  sharing the same `row` (with non-overlapping `column`/`span`) sit side
  by side regardless of declaration order; a region on a later `row`
  starts a new line even if the previous row isn't full. Omitting both
  falls back to plain auto-flow (pack left to right, wrap).
- `accent`: any CSS color string (hex/rgb/named) on an individual region —
  the region's own card takes on that color (a tinted fill + matching
  border), in every mode (stack/grid/flex wrapper, accordion's panel;
  tabs get a colored dot + underline instead, since an inactive tab has
  no persistent surface to tint). Caller-supplied runtime data, not a new
  design token — same free-form-color convention already used for tag
  colors elsewhere in the app. Omitted = unaccented.
- `collapsible` / `default_open`: **stack mode only** — a region with
  `collapsible: true` renders behind its own collapse/expand toggle,
  independent per region (`default_open`, default `true`, sets its
  initial state). Accordion mode is already collapsible by definition and
  ignores these; tabs/grid/flex don't have a natural "collapsed" state
  for a single region.

Frontend component: `frontend/src/components/views/LayoutContainerWidget.tsx`.

---

## The `create_wizard` step kinds

`create_wizard` is an **entry-type-level** primitive (`form_schema.create_wizard`
on any entry type — same opt-in shape as `open_as_page`), not a `view_type` —
it replaces the default single-form create dialog with a multi-step flow.
Its step *kinds* are registered by this same plugin via a second registry,
`operational_model_wizard_steps.py`, mirroring the view-type registry's shape
exactly (`WizardStepKindSpec`, `register_step_kind()`, `is_known()`).

That registry lives in core (so `operational_model_compile.py` can validate
against it — see `_normalize_create_wizard`), but starts **empty** at
import: every step kind, including the four shipped ones below, is
registered by this plugin at discovery, not seeded as a framework
built-in. If region_system were ever not loaded, `create_wizard` would
correctly fail to validate any step, the same way `layout_container`
becomes an "Unsupported view type" without it.

| `kind` | Role | Key config |
|---|---|---|
| `form` | Ordered field-key inputs, optionally prefilled by a no-arg workspace tool | `fields`, `prefill_tool?` |
| `entry_checklist` | Pick N of M entries from a track, optionally filtered by an earlier step's form value | `source_track_type`, `active_field?`, `display_columns`, `filter?: {join: {track_type, on_field, match_field}, against_form_field}` |
| `period_picker` | A labeled dropdown of candidate values computed by a workspace tool; `input_fields` render INLINE as normal controls and re-trigger the tool call when changed | `periods_tool`, `fields`, `input_fields?` |
| `summary` | Read-only review step before submit | none |

`on_create_tool` (a workspace tool key) is called after the entry is
created, with `entry_id` plus the checked `entry_checklist` step's ids
under `on_create_ids_param` (default `"employee_ids"`).

Frontend component: `frontend/src/components/entries/CreateWizardModal.tsx`,
wired into the app-wide "+" button in `EntryComposer.tsx`
(`findCreateWizardEntryType` picks the wizard over the plain create dialog
when an entry type declares `create_wizard`).

**Canonical example**: `payroll-app/operational-model.yaml`'s `pay_run` entry type —
a `period_picker` step (with `input_fields: [frequency]` rendering a
Frequency select inline, re-fetching periods per frequency) followed by an
`entry_checklist` step (filtered to employees whose Compensation Record
`pay_frequency` matches the picked frequency) followed by `summary`. Read
it end to end as the reference implementation before building a new
wizard elsewhere.

---

## The standard for adding a new region or step kind

1. **Genericity gate — decide before writing code.** Does the config ever
   need to reference a specific field key, track title, or tool name that
   only makes sense in one app? If yes, it's a **domain** widget (like
   `payroll_register`), not a region-system one — build it in that app's
   own plugin, and give it an honest `palette_group` (not `"core"`).
   Region-system config only ever takes *caller-supplied* keys/strings and
   does nothing with their meaning.
2. **One registration, backend + frontend, always paired.** Backend:
   `view_type_registry.register_view_type(ViewTypeSpec(...))` (or
   `wizard_step_registry.register_step_kind(WizardStepKindSpec(...))`)
   inside `region_system/__init__.py`'s `register()`. Frontend: a matching
   `src/views/manifests/<type>.manifest.ts` (auto-loaded via
   `import.meta.glob`, no registry file edited) pointing at a component
   under `src/components/views/`. Never one without the other — backend-only
   compiles into Operational Models but renders as `MissingWidget` in the browser.
3. **`config_schema` is mandatory, and it's the contract.** Every key the
   widget reads from `view.config` (or the wizard step reads from its own
   step config) needs an entry with a `description` — this is what a human
   author reads, what `integral_describe_substrate` hands the resident
   agent when composing a Operational Model, and what a future palette gallery would
   render.
4. **Reuse existing sub-primitives before adding new ones.**
   `layout_container`'s `kind: 'form'` regions reuse `FormRegionWidget`
   directly; `visible_if` conditional-visibility is shared via
   `regionConditions.ts` across three widgets. A new widget reinventing
   field rendering or conditional visibility instead of importing the
   existing one is drift, same as the substrate's "wrong pillar" rule in
   the root `AGENTS.md`.
5. **Compile-through test, not just a unit test in isolation.** Add a case
   that runs a real (or representative) manifest through
   `compile_canonical_manifest` — this is what catches "compiles fine in a
   script but the real create/validate path 400s" class bugs (hit twice
   this session: a field patched onto the wrong tier of a three-tier
   schema store). See `tests/test_operational_model_wizard_steps.py` for the
   pattern.

---

## Related docs

- [VIEW_PALETTE.md](VIEW_PALETTE.md) — the general view-palette mental
  model this bundle plugs into
- [META_WIDGETS.md](META_WIDGETS.md) — the composable meta-widgets
  (`composable_list`/`grid`/`board`/`timeline`), a sibling but separate
  `palette_group`
- [PLUGINS.md](PLUGINS.md) — signed code plugins in general
- [AGENT_CONTRACT.md](AGENT_CONTRACT.md) — introspection + patch DSL a
  resident agent uses to discover and compose these primitives
