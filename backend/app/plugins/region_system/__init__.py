"""Operational Model plugin: the "apex region system" bundle. Registers a
small catalog of Oracle-APEX-style generic "region" view types —
``form_region``, ``layout_container``, ``static_content``, ``tree_region``,
``chart_region``, ``summary_tiles``, ``reverse_relation_list``,
``modal_region``, ``popover_region``, ``drawer_region`` — PLUS the
``create_wizard`` primitive's step kinds (``form``, ``entry_checklist``,
``period_picker``, ``summary``) — usable by any app that wants a
config-driven page region or multi-step create flow without hand-building
bespoke widgets. See docs/operational-models/REGION_SYSTEM.md for the full
usage guide (dev- and agent-facing).

A wizard step is conceptually a region rendered one-at-a-time with
Next/Back navigation instead of a fixed layout — bundling both catalogs in
this one plugin is deliberate, not incidental: they're the same mental
model (config-driven, generic, composed from ordered specs), just two
different render surfaces (``LayoutContainerWidget`` for an existing
entry's related_views slot, ``CreateWizardModal`` for a not-yet-created
entry). See ``operational_model_wizard_steps.py``'s own docstring for why
the registry lives in core but every kind is still plugin-registered here,
not seeded as a framework built-in.

Discovered and loaded automatically by
``app.services.operational_model_plugins.discover_and_register_plugins`` — a
directory scan of ``backend/app/plugins/`` at server startup (see
``app/main.py``). No existing registry file is edited to wire this in.

All view types below are generic, config-driven primitives, not tied to any
one app:

  - ``form_region``: subset-of-fields form bound to the current entry, a
    related entry (via a workspace tool), or an anchored track's entry.
    Config: ``fields`` (ordered field keys), ``title?``, ``columns?``.
  - ``layout_container``: a stack/tabs/accordion wrapper composing other
    regions (nested views or inline sub-forms) within one placement slot.
    Config: ``regions`` (ordered RegionSpec[]), ``mode?``, ``title?``.
  - ``static_content``: read-only markdown block for section instructions,
    headers, or notes between regions. Config: ``body?``, ``body_field?``.
  - ``tree_region``: hierarchical tree over a track's entries via a
    self-relational parent field (e.g. an HR org chart via `manager`).
    Config: ``source_track``, ``parent_field``, ``label_field?``,
    ``sort_siblings?``.
  - ``chart_region``: bar/line/pie/donut chart bound to the current entry's
    own numeric fields, or aggregated (count/sum/avg, optional month
    bucketing) over a track's entries. Config: ``chart_type``, ``x_field``,
    ``y_field?``, ``aggregate?``, ``group_by?``, ``source?``, ``title?``,
    ``fields?``.
  - ``summary_tiles``: read-only KPI strip bound to the current entry's own
    fields. Config: ``tiles`` ([{label, field, format?}]), ``title?``.
  - ``reverse_relation_list``: read-only list of entries elsewhere that
    reference the current entry via a ``relation`` field — e.g. every
    Payslip whose ``pay_run`` field points at this Pay Run. Unlike
    ``:anchored_track`` (forward-only: an entry's own auto-provisioned
    child track), this has no anchor-track prerequisite and works across
    independent, non-anchored tracks (same-app or cross-app) — the
    ``REFERENCES`` edge the ``relation`` field already writes is all it
    needs. Config: ``relation`` (the target field's ``field_key``, e.g.
    ``'pay_run'``), ``title?``.
  - ``modal_region``: a button that opens an Oracle-APEX-style "modal
    page" — the same ``RegionSpec[]`` composition ``layout_container``
    uses (``kind:'view'``/``kind:'form'``, always stacked — a modal is
    already a focused surface; nest a ``layout_container`` as a
    ``kind:'view'`` region here if a more elaborate in-dialog arrangement
    is genuinely needed), rendered in a dialog instead of inline. Config:
    ``trigger_label``, ``trigger_variant?`` (``'primary'|'secondary'|
    'ghost'|'danger'|'outline'``, default ``'primary'``), ``title?``
    (defaults to ``trigger_label``), ``width?`` (one of the platform's
    three dialog-width tokens — ``'max-w-dialog-confirm'``/
    ``'max-w-dialog-form'``/``'max-w-dialog-wide'``), ``regions``
    (ordered ``RegionSpec[]``, same shape as ``layout_container``'s).

``create_wizard`` step kinds — each step in a ``form_schema.create_wizard``
config is one of:

  - ``form``: reuses form_region's own rendering — ordered field KEYS
    (resolved against the entry type's own ``fields[]``) + an optional
    ``prefill_tool`` (a workspace tool called with no args whose JSON keys
    merge into the form's initial values).
  - ``entry_checklist``: pick N of M entries from ``source_track_type`` (a
    track title), optionally filtered by ``active_field``, shown with
    ``display_columns`` ([{key, label, source_field?, join?}] — same
    "resolve through a relation" shape payroll_register's
    ``identity_columns`` uses). Optional ``filter`` narrows candidates to
    rows whose related record matches an earlier step's form value.
  - ``period_picker``: a labeled dropdown of candidate values computed by
    a workspace tool (``periods_tool``, called with an optional
    ``input_fields`` subset of already-collected form values as its args)
    — selecting one fills the listed ``fields``. ``input_fields`` render
    INLINE in this same step as normal controls (e.g. a Frequency select
    above the Period dropdown), not just forwarded from an earlier step.
  - ``summary``: read-only review step, no config.
"""

from __future__ import annotations

from typing import Any


def register(
    *,
    field_type_registry: Any,
    view_type_registry: Any,
    wizard_step_registry: Any = None,
) -> None:
    for spec in (
        view_type_registry.ViewTypeSpec(
            type="operations-ui/settings-hub",
            label="Settings Hub",
            description="Grouped settings with configurable inline controls.",
            config_schema={
                "sections": {"type": "array", "description": "Ordered setting groups."}
            },
            source="plugin",
            scope="track",
            palette_group="core",
        ),
        view_type_registry.ViewTypeSpec(
            type="operations-ui/workflow-stepper",
            label="Workflow Stepper",
            description="Visual status progression for an operational record.",
            config_schema={
                "status_field": {
                    "type": "string",
                    "description": "Field containing the current status.",
                },
                "steps": {
                    "type": "array",
                    "description": "Ordered steps with key, label, and statuses.",
                },
            },
            source="plugin",
            scope="both",
            palette_group="core",
        ),
        view_type_registry.ViewTypeSpec(
            type="operations-ui/data-health",
            label="Data Health",
            description="Configurable completeness checks for any record — "
            "field-presence checks evaluated client-side, or a named "
            "workspace tool's server-computed checks for anything that "
            "needs cross-entity logic (related-record counts, effective-"
            "dated lookups, etc.).",
            config_schema={
                "title": {"type": "string", "description": "Panel title."},
                "checks": {
                    "type": "array",
                    "description": "Client-side mode: [{key, label, field, "
                    "required?, equals?, message?}] evaluated against the "
                    "current entry's own fields. Ignored when `tool` is set.",
                },
                "tool": {
                    "type": "string",
                    "description": "Server mode: a workspace tool key called "
                    "with {entry_id}, returning {status, summary?, checks: "
                    "[{key, label, message, state: 'pass'|'warning'|'block', "
                    "action?}]}. Use for checks a single entry's own fields "
                    "can't answer (e.g. 'does this run have any employees "
                    "linked yet').",
                },
                "summary_fields": {
                    "type": "array",
                    "description": "Server mode only: [{label, field, "
                    "format?}] — badges rendered from the tool result's "
                    "`summary` dict.",
                },
            },
            source="plugin",
            scope="both",
            palette_group="core",
        ),
        view_type_registry.ViewTypeSpec(
            type="operations-ui/report-center",
            label="Report Center",
            description="Configured operational reports with metrics.",
            config_schema={
                "title": {"type": "string", "description": "Report panel title."},
                "reports": {
                    "type": "array",
                    "description": "Report cards: {key, title, description?, "
                    "kind?, metrics?, columns?, x_field?, y_field?}. kind "
                    "defaults to a 2-col metric grid; 'table' renders "
                    "columns as rows, 'trend' renders x_field/y_field as a "
                    "sparkline, 'composition' renders metrics as "
                    "proportional bars (each width relative to the largest "
                    "metric value in the card).",
                },
                "source_track": {
                    "type": "string",
                    "description": "Optional source track key or id.",
                },
            },
            source="plugin",
            scope="track",
            palette_group="core",
        ),
    ):
        view_type_registry.register_view_type(spec)

    view_type_registry.register_view_type(
        view_type_registry.ViewTypeSpec(
            type="operations-ui/command-center",
            label="Command Center",
            description="Configurable operational dashboard with metrics and child views.",
            config_schema={
                "metrics": {
                    "type": "array",
                    "description": "[{label, field, aggregate?, format?}]",
                },
                "child_views": {
                    "type": "array",
                    "description": "Ordered child views rendered below metrics — "
                    "each entry is a bare view key (label falls back to a "
                    "title-cased key) or {key, label}.",
                },
                "title": {"type": "string", "description": "Dashboard title."},
                "subtitle": {
                    "type": "string",
                    "description": "Optional subtitle under the title "
                    "(defaults to a generic 'review records' line).",
                },
                "record_label": {
                    "type": "string",
                    "description": "Plural noun for the entry-count badge "
                    "(e.g. 'pay runs'); defaults to 'records'.",
                },
            },
            source="plugin",
            scope="track",
            palette_group="core",
        )
    )
    view_type_registry.register_view_type(
        view_type_registry.ViewTypeSpec(
            type="form_region",
            label="Form",
            description=(
                "Config-driven subset-of-fields form region, always-editable "
                "inline — the reusable Oracle-APEX-style 'Form' region."
            ),
            config_schema={
                "fields": {
                    "type": "array",
                    "description": "Ordered field keys to render as inputs.",
                },
                "title": {
                    "type": "string",
                    "description": "Optional section heading.",
                },
                "columns": {
                    "type": "number",
                    "description": "Optional grid column count for field layout.",
                },
            },
            source="plugin",
            scope="entry",
            palette_group="core",
        )
    )
    view_type_registry.register_view_type(
        view_type_registry.ViewTypeSpec(
            type="layout_container",
            label="Layout Container",
            description=(
                "Grid/flex/stack/tabs/accordion wrapper composing an ordered "
                "list of child regions (nested views or inline sub-forms) "
                "within one related_views placement slot — the reusable "
                "Oracle-APEX-style Region layout primitive."
            ),
            config_schema={
                "regions": {
                    "type": "array",
                    "description": (
                        "{key, kind: 'view'|'form', title?, visible_if?, "
                        "layout?, accent?, collapsible?, default_open?} & "
                        "({kind:'view', view} | {kind:'form', fields})[] — "
                        "ordered child regions. `kind:'view'` may resolve "
                        "to ANY registered view_type, including another "
                        "layout_container — nesting depth is only limited "
                        "by good sense. `visible_if: {field, equals}` gates "
                        "a region on another field's current value on the "
                        "bound entry (e.g. only show 'Pay Period Dates' "
                        "when schedule_type == 'Weekly'). `layout` is a "
                        "per-region placement override: {span?, order?, "
                        "column?, row?} in grid mode — `column`/`row` are "
                        "explicit 1-indexed grid-column-start/grid-row-"
                        "start, the actual 'put this region at exactly "
                        "this position' control (two regions sharing a "
                        "`row` sit side by side regardless of declaration "
                        "order); {grow?, shrink?, basis?} in flex mode. "
                        "Any of these may be a bare value or {desktop?, "
                        "tablet?, mobile?} for responsive overrides. "
                        "`accent` is any CSS color string (hex/rgb/named) "
                        "— the region's own card takes on that color (tint "
                        "fill + matching border); caller-supplied runtime "
                        "data, not a fixed design token. `collapsible`/"
                        "`default_open` — STACK mode only — renders the "
                        "region behind its own collapse toggle."
                    ),
                },
                "mode": {
                    "type": "string",
                    "description": "'stack' (default) | 'tabs' | 'accordion' | 'grid' | 'flex'.",
                },
                "layout": {
                    "type": "object",
                    "description": (
                        "Container-level layout config, mode-specific. Grid: "
                        "{columns?: number|responsive (default 12), gap?: "
                        "'none'|'sm'|'md'|'lg' (default 'md')}. Flex: "
                        "{direction?: 'row'|'column', align?: 'start'|"
                        "'center'|'end'|'stretch', justify?: 'start'|"
                        "'center'|'end'|'between'|'around', gap?, wrap?: "
                        "boolean}. Responsive values use "
                        "{desktop?, tablet?, mobile?} (breakpoints: mobile "
                        "<640px, tablet 640-1023px, desktop >=1024px), "
                        "cascading toward desktop as the base when a more "
                        "specific breakpoint isn't set."
                    ),
                },
                "title": {
                    "type": "string",
                    "description": "Optional container heading.",
                },
            },
            source="plugin",
            scope="entry",
            palette_group="core",
        )
    )
    view_type_registry.register_view_type(
        view_type_registry.ViewTypeSpec(
            type="static_content",
            label="Static Content",
            description=(
                "Read-only markdown block for section instructions, headers, "
                "or notes between regions."
            ),
            config_schema={
                "body": {
                    "type": "string",
                    "description": "Inline markdown content.",
                },
                "body_field": {
                    "type": "string",
                    "description": (
                        "Alternative to `body` — read markdown from this "
                        "field key on the bound entry."
                    ),
                },
            },
            source="plugin",
            # Used both as a standalone track view and referenced from
            # related_views[] (region-gallery's demo profile) — confirmed by
            # reading real operational-model.yaml usage, not assumed.
            scope="both",
            palette_group="core",
        )
    )
    view_type_registry.register_view_type(
        view_type_registry.ViewTypeSpec(
            type="tree_region",
            label="Tree",
            description=(
                "Hierarchical tree over a track's entries via a self-"
                "relational parent field — e.g. an HR org chart."
            ),
            config_schema={
                "source_track": {
                    "type": "string",
                    "description": "Track id/slug to source entries from.",
                },
                "parent_field": {
                    "type": "string",
                    "description": (
                        "Dotted field path (e.g. 'custom_fields.manager') "
                        "whose value is the parent entry id."
                    ),
                },
                "label_field": {
                    "type": "string",
                    "description": "Field to render as each node's label (default 'title').",
                },
                "sort_siblings": {
                    "type": "object",
                    "description": "{field, direction} sort within each tree level.",
                },
            },
            source="plugin",
            # Used both as a standalone track view (e.g. hr_app's org chart)
            # and referenced from related_views[] (region-gallery's demo
            # profile) — confirmed by reading real operational-model.yaml usage, not
            # assumed.
            scope="both",
            palette_group="core",
        )
    )
    view_type_registry.register_view_type(
        view_type_registry.ViewTypeSpec(
            type="chart_region",
            label="Chart",
            description=(
                "Config-driven bar/line/area/scatter/pie/donut/gauge chart "
                "bound to the current entry's own fields, or aggregated "
                "over a track's entries — the reusable Oracle-APEX-style "
                "'Chart' region."
            ),
            config_schema={
                "chart_type": {
                    "type": "string",
                    "description": (
                        "'bar' (default) | 'line' | 'area' | 'scatter' | "
                        "'pie' | 'donut' | 'gauge'. 'gauge' renders only "
                        "the FIRST data point as a semi-circle progress "
                        "ring against gauge_max — a gauge inherently shows "
                        "one number, not a series."
                    ),
                },
                "gauge_max": {
                    "type": "number",
                    "description": "'gauge' chart_type only — the value representing 100% fill. Default 100.",
                },
                "source": {
                    "type": "string",
                    "description": "'track' (default, aggregate entries) | 'self' (bound entry's own fields).",
                },
                "x_field": {
                    "type": "string",
                    "description": "Field to group by ('track' mode) or single-point label ('self' mode).",
                },
                "y_field": {
                    "type": "string",
                    "description": "Numeric field to sum/avg ('track' mode) or read ('self' mode).",
                },
                "aggregate": {
                    "type": "string",
                    "description": "'count' (default) | 'sum' | 'avg' — 'track' mode only.",
                },
                "group_by": {
                    "type": "string",
                    "description": "Field to group entries by in 'track' mode; defaults to x_field.",
                },
                "fields": {
                    "type": "array",
                    "description": "'self' mode only — [{key, label?}] fields to plot as one point each.",
                },
                "title": {
                    "type": "string",
                    "description": "Optional chart heading.",
                },
            },
            source="plugin",
            # Genuinely dual-placement, not a fixed-scope widget: 'track'
            # mode (source: 'track', aggregating a track's entries) is used
            # as an ordinary track view/tab (e.g. payroll-app's
            # 'payroll_totals_chart'); 'self' mode (source: 'self', bound to
            # the current entry's own fields) is used from related_views
            # (e.g. payroll-app's 'pay_composition_chart'). Confirmed by
            # reading real operational-model.yaml usage, not assumed.
            scope="both",
            palette_group="core",
        )
    )
    view_type_registry.register_view_type(
        view_type_registry.ViewTypeSpec(
            type="summary_tiles",
            label="Summary Tiles",
            description=(
                "Read-only KPI strip bound to the current entry's own "
                "fields — the reusable Oracle-APEX-style 'Value'/KPI region."
            ),
            config_schema={
                "tiles": {
                    "type": "array",
                    "description": (
                        "[{label, field, format?: 'number'|'currency'|"
                        "'count', icon?, trend?}] — ordered tiles. `icon` "
                        "is a lucide-react icon name (e.g. 'TrendingUp'), "
                        "resolved dynamically — an unrecognized name just "
                        "renders no icon. `trend` is {field, format?, "
                        "direction?: 'up'|'down'} — a SECOND field on the "
                        "same bound entry read as a delta (e.g. a "
                        "`score_delta` field alongside `score`), not a "
                        "computed comparison against historical data (this "
                        "widget has no time-series access); direction "
                        "defaults to the value's own sign."
                    ),
                },
                "title": {
                    "type": "string",
                    "description": "Optional strip heading.",
                },
            },
            source="plugin",
            scope="entry",
            palette_group="core",
        )
    )
    view_type_registry.register_view_type(
        view_type_registry.ViewTypeSpec(
            type="operations-ui/record-collection",
            label="Record Collection",
            description=(
                "Configurable related-record table or card collection with "
                "columns, formatting, status styling, and row actions."
            ),
            config_schema={
                "relation": {
                    "type": "string",
                    "description": "Relation field pointing to the host entry.",
                },
                "title": {"type": "string", "description": "Optional section heading."},
                "entry_type": {
                    "type": "string",
                    "description": "Optional related entry type filter.",
                },
                "presentation": {
                    "type": "string",
                    "description": "table, cards, or responsive (default).",
                },
                "columns": {
                    "type": "array",
                    "description": "[{field, label, format?, emphasis?}] columns to render.",
                },
                "row_actions": {
                    "type": "array",
                    "description": "open and/or download row actions.",
                },
            },
            source="plugin",
            scope="entry",
            palette_group="core",
        )
    )
    view_type_registry.register_view_type(
        view_type_registry.ViewTypeSpec(
            type="reverse_relation_list",
            label="Related Records",
            description=(
                "Read-only list of entries elsewhere that reference the "
                "current entry via a relation field — the reusable "
                "'what points at me' region for tracks that relate to each "
                "other without an anchor-track relationship."
            ),
            config_schema={
                "relation": {
                    "type": "string",
                    "description": (
                        "field_key of the relation field on the OTHER "
                        "track's entries that points back at this one "
                        "(e.g. 'pay_run' for payslip.pay_run)."
                    ),
                },
                "entry_types": {
                    "type": "array",
                    "description": "Optional list of source entry types to include.",
                },
                "title": {
                    "type": "string",
                    "description": "Optional section heading.",
                },
            },
            source="plugin",
            scope="entry",
            palette_group="core",
        )
    )
    view_type_registry.register_view_type(
        view_type_registry.ViewTypeSpec(
            type="modal_region",
            label="Modal Region",
            description=(
                "A button that opens an Oracle-APEX-style 'modal page' — "
                "the same RegionSpec[] composition layout_container uses, "
                "rendered in a dialog instead of inline."
            ),
            config_schema={
                "trigger_label": {
                    "type": "string",
                    "description": "Button label — required.",
                },
                "trigger_variant": {
                    "type": "string",
                    "description": (
                        "'primary' (default) | 'secondary' | 'ghost' | "
                        "'danger' | 'outline'."
                    ),
                },
                "title": {
                    "type": "string",
                    "description": "Modal header title. Defaults to trigger_label.",
                },
                "width": {
                    "type": "string",
                    "description": (
                        "One of the platform's three dialog-width tokens: "
                        "'max-w-dialog-confirm' | 'max-w-dialog-form' "
                        "(default) | 'max-w-dialog-wide'."
                    ),
                },
                "regions": {
                    "type": "array",
                    "description": (
                        "{key, kind: 'view'|'form', title?} & "
                        "({kind:'view', view} | {kind:'form', fields})[] — "
                        "ordered regions, always stacked inside the modal. "
                        "Same shape as layout_container's own regions[] "
                        "(minus layout/accent, which are composition-mode "
                        "concerns a modal doesn't have)."
                    ),
                },
            },
            source="plugin",
            scope="entry",
            palette_group="core",
        )
    )
    view_type_registry.register_view_type(
        view_type_registry.ViewTypeSpec(
            type="popover_region",
            label="Popover Region",
            description=(
                "A button that opens a small floating panel anchored to "
                "it — one step lighter than modal_region: no backdrop, no "
                "viewport takeover, for a quick peek rather than a focused "
                "task."
            ),
            config_schema={
                "trigger_label": {
                    "type": "string",
                    "description": "Button label — required.",
                },
                "trigger_variant": {
                    "type": "string",
                    "description": (
                        "'primary' | 'secondary' (default) | 'ghost' | "
                        "'danger' | 'outline'."
                    ),
                },
                "placement": {
                    "type": "string",
                    "description": "'bottom' (default) | 'top' — which side of the trigger the panel opens on.",
                },
                "regions": {
                    "type": "array",
                    "description": (
                        "{key, kind: 'view'|'form', title?} & "
                        "({kind:'view', view} | {kind:'form', fields})[] — "
                        "ordered regions, always stacked inside the panel. "
                        "Same shape as modal_region's own regions[]."
                    ),
                },
            },
            source="plugin",
            scope="entry",
            palette_group="core",
        )
    )
    view_type_registry.register_view_type(
        view_type_registry.ViewTypeSpec(
            type="drawer_region",
            label="Drawer Region",
            description=(
                "A button that opens a side panel (slides in from the "
                "left or right) instead of a centered modal dialog — for "
                "content that reads better as a panel than a full "
                "takeover."
            ),
            config_schema={
                "trigger_label": {
                    "type": "string",
                    "description": "Button label — required.",
                },
                "trigger_variant": {
                    "type": "string",
                    "description": (
                        "'primary' | 'secondary' (default) | 'ghost' | "
                        "'danger' | 'outline'."
                    ),
                },
                "title": {
                    "type": "string",
                    "description": "Drawer header title. Defaults to trigger_label.",
                },
                "side": {
                    "type": "string",
                    "description": "'right' (default) | 'left'.",
                },
                "width_px": {
                    "type": "number",
                    "description": "Panel width in pixels. Default 420.",
                },
                "regions": {
                    "type": "array",
                    "description": (
                        "{key, kind: 'view'|'form', title?} & "
                        "({kind:'view', view} | {kind:'form', fields})[] — "
                        "ordered regions, always stacked inside the "
                        "drawer. Same shape as modal_region's own "
                        "regions[]."
                    ),
                },
            },
            source="plugin",
            scope="entry",
            palette_group="core",
        )
    )

    if wizard_step_registry is None:
        return

    wizard_step_registry.register_step_kind(
        wizard_step_registry.WizardStepKindSpec(
            kind="form",
            label="Form",
            description=(
                "Ordered field-key inputs (form_region's own rendering), "
                "optionally prefilled by a no-arg workspace tool."
            ),
            config_schema={
                "fields": {
                    "type": "array",
                    "description": "Ordered field keys to render.",
                },
                "prefill_tool": {
                    "type": "string",
                    "description": "Optional workspace tool (called with no args) whose JSON keys merge into initial values.",
                },
            },
        )
    )
    wizard_step_registry.register_step_kind(
        wizard_step_registry.WizardStepKindSpec(
            kind="entry_checklist",
            label="Entry Checklist",
            description="Pick N of M entries from a track, optionally filtered by an earlier step's form value.",
            config_schema={
                "source_track_type": {
                    "type": "string",
                    "description": "Track title to source candidate rows from.",
                },
                "active_field": {
                    "type": "string",
                    "description": "Optional boolean/status field name to pre-filter active rows.",
                },
                "display_columns": {
                    "type": "array",
                    "description": "[{key, label, source_field?, join?: {track_type, on_field, show_field}}] — ordered display columns.",
                },
                "filter": {
                    "type": "object",
                    "description": "{join: {track_type, on_field, match_field}, against_form_field} — narrow rows to those whose related record matches an earlier step's value.",
                },
            },
        )
    )
    wizard_step_registry.register_step_kind(
        wizard_step_registry.WizardStepKindSpec(
            kind="period_picker",
            label="Period Picker",
            description=(
                "A labeled dropdown of candidate values computed by a workspace tool, "
                "with an optional inline input_fields selector (e.g. Frequency) that "
                "re-triggers the tool call when changed."
            ),
            config_schema={
                "periods_tool": {
                    "type": "string",
                    "description": "Workspace tool returning {periods: [{label, ...field values...}]}.",
                },
                "fields": {
                    "type": "array",
                    "description": "Field keys each returned period's values fill in.",
                },
                "input_fields": {
                    "type": "array",
                    "description": "Subset of already-collected form values to render inline and forward as the periods_tool's call args.",
                },
            },
        )
    )
    wizard_step_registry.register_step_kind(
        wizard_step_registry.WizardStepKindSpec(
            kind="summary",
            label="Summary",
            description="Read-only review step before submit — no config.",
            config_schema={},
        )
    )
