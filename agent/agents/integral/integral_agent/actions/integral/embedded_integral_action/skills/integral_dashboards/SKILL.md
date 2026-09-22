---

name: integral_dashboards
description: "Compose and customize app-scoped analytics dashboards — metrics, charts, activity digests, and multi-track summaries. Use for bar charts, KPI tiles, or vague requests like the best dashboard for this App."
spec: jv
allowed-tools:
  - integral_describe_dashboard_substrate
  - integral_list_dashboards
  - integral_suggest_dashboard
  - integral_create_dashboard
  - integral_update_dashboard
  - integral_delete_dashboard
  - integral_list_tracks
  - integral_describe_model
  - integral_activity_digest
  - integral_count_entries
  - integral_list_apps
requires-actions:
  - EmbeddedIntegralAction
extends: action:integral/embedded_integral_action
tags:
  - integral
  - dashboards
  - analytics
  - charts

---

# Integral dashboards — SOP

## When to use

The user wants an **app-level dashboard** — KPI tiles, bar/line/pie charts,
activity summaries, or a full layout of widgets across the app's tracks.

## When NOT to use

- Saving a **track view** (kanban, table, feed) — delegate to the
  `integral_insights` skill for operational surfaces on one track.
- Schema changes — delegate to the `integral_models` skill.

## Grounding

- Confirm `app_id` from page focus or `integral_list_apps` before staging.
- Call `integral_describe_dashboard_substrate` this turn for widget types and
  default grid sizes — do not invent widget `type` strings.
- For chart breakdowns, resolve `track_id` via `integral_list_tracks` when the
  user names a track; otherwise app-wide `grouped_count` is valid.
- Only cite dashboard names and widget titles returned by tools in this turn.

## Procedure

1. **Ground** — confirm `app_id` (page focus or `integral_list_apps`). Optionally
   `integral_list_tracks`, `integral_activity_digest(scope=app, scope_id=app_id)`.

2. **Introspect** — `integral_describe_dashboard_substrate` for widget types and
   default sizes.

3. **Always include widgets** — never stage `integral_create_dashboard` with an
   empty or omitted `widgets` array. A name-only create is a bug.

4. **Specific requests** (e.g. "bar chart of pipeline by stage"):
   - Resolve track + field via profile / track list.
   - Pick `chart_bar` with `data_source: { kind: grouped_count, track_id,
     group_by: custom_fields.<field_key> }` for an operational profile field.
     Bare `status` means Integral's platform lifecycle status, not a business
     field named Status. Read the track schema first and use the exact field
     key. Do not rely on fallback grouping.
   - Stage via `integral_create_dashboard` or `integral_update_dashboard` **with
     the full `widgets` list**.

5. **Vague requests** ("best dashboard for this app" / "create a dashboard"):
   - Call `integral_suggest_dashboard(app_id)`.
   - Explain the rationale, then stage `integral_create_dashboard` with the
     returned `name`, `layout`, **and `widgets`** (copy the widgets array
     verbatim — do not drop it).

6. **Bless** — user approves the staging card; executor persists the dashboard.
   Confirm the staging card shows a non-zero widget count before asking for
   approval.

7. **Read back after approval.** On the following turn, call
   `integral_list_dashboards(app_id)` before describing the dashboard. Name
   the dashboard and its persisted widgets from that result. Never say you do
   not know its layout immediately after creating it, and never describe a
   generic starter set as a domain dashboard when the app has operational
   tracks available for a better suggestion.

### Widget palette (common)

| Type | Use when |
|------|----------|
| `metric_card` | Single count KPI |
| `chart_bar` / `chart_pie` | Breakdown by status, tag, entry_type |
| `chart_line` | Trend over time (`group_by: date`) |
| `activity_digest` | What's happening per track |
| `recent_entries` | Latest updates |
| `track_breakdown` | Multi-track overview |

### Grid layout (12 columns)

Place widgets on a **12-column grid**. Spread them across rows — do not stack
everything at `x: 0`.

| Pattern | Example grid positions |
|---------|------------------------|
| Full-width KPI row | `metric_row` at `{x:0, y:0, w:12, h:2}` |
| Two charts side-by-side | `{x:0, w:6}` and `{x:6, w:6}` on same `y` |
| Three KPI tiles | `{x:0,w:4}`, `{x:4,w:4}`, `{x:8,w:4}` |

Use `integral_describe_dashboard_substrate` for each widget's `default_size`.
After composing widgets, verify no two widgets overlap and rows use horizontal
space (not a single column at `x: 0` unless intentional).

## Staging discipline

- `integral_create_dashboard` and `integral_update_dashboard` are **propose**
  tools — they return a staged-change envelope; the user blesses before the
  dashboard is persisted.
- Present the approval card and wait; do not claim the dashboard exists until
  `[SYSTEM:STAGING-RESOLVED] … state=consumed`.
- Surface error envelopes verbatim — never retry blindly.

## Forbidden patterns

- Do not invent `app_id`, `dashboard_id`, or widget ids — resolve via
  `integral_list_dashboards` / page focus.
- Do not stage a create with zero widgets. If unsure, call
  `integral_suggest_dashboard` and forward its `widgets`.
- Do not stack every widget at `x: 0` on a 12-column grid unless intentional.
- Do not use track-view tools for dashboard layout — delegate to
  `integral_insights` for per-track saved views.

## Example

> **User:** "Add a status pie chart to this app's dashboard."

1. `integral_list_apps` or read page focus → `app_id`.
2. `integral_list_dashboards(app_id)` → pick target dashboard id (or stage a create).
3. `integral_describe_dashboard_substrate` → confirm `chart_pie` and default size.
4. `integral_update_dashboard(app_id, dashboard_id, widgets=[…])` with
   `data_source: { kind: grouped_count, track_id, group_by: custom_fields.<field_key> }`
   after reading the profile to resolve `<field_key>` → present card and wait.
5. Only after `state=consumed` confirm the chart is live.
