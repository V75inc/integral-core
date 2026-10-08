# Package-owned App Home

An App package may declare `app.home` to make a usable landing view available on installation. Core owns validation, authorized query execution and rendering. The package owns its copy, selected fields and actions. No mutable Dashboard nodes are provisioned and no user dashboard is overwritten. Existing Dashboards remain independent.

## Declaration

`home` has a nonempty `title` (maximum 100 characters), optional `description` (500 characters), one to six widget specs and up to three actions. Widgets use the public dashboard widget contract, including `id`, `type`, `title`, `grid`, `config`, and `data_source`.

Every source must be a dashboard-enabled query declared by the same App. Its `query_key`, `rows_path`, `total_path` and `query_params` must match that declaration. Core never infers a replacement query or scans another App when a query fails.

```yaml
app:
  home:
    title: Current work
    description: One useful step at a time.
    widgets:
      - id: current
        type: record_summary
        title: Next step
        grid: {x: 0, y: 0, w: 12, h: 5}
        config:
          max_records: 3
          empty_message: No work has been recorded yet.
          fields:
            - {field: custom_fields.next_action, label: Next action}
            - {field: custom_fields.brief, label: Brief, detail: true}
        data_source:
          kind: declared_query
          query_key: current_work
          rows_path: items
          total_path: total_estimate
          query_params: {}
    actions:
      - label: Get started
        draft: Help me get started.
        when: {widget: current, state: empty}
```

The example requires a separately declared `current_work` query with matching dashboard metadata and an explicit output schema. `record_summary` field paths must identify declared nullable scalar fields in its row schema. A complete result must include unique record IDs and a matching total; incomplete, unsupported or failed reads produce unavailable data rather than a false empty state. Record summaries return only selected fields, title and identity, with no inferred drill-through support.

`record_summary.config` accepts one to eight unique field descriptors, `max_records` from 1 to 10, and an optional bounded empty message. A descriptor has `field`, `label`, optional `detail` and optional `value_labels`. Detail fields begin collapsed. Labels change presentation only; saved values remain unchanged. Missing values show “Not yet set.” Entry links use validated Entry identities.

Grid placement uses twelve columns, bounded dimensions and nonoverlapping rectangles. At sufficient **container** width, Home honors declared columns and rows. Narrow containers stack widgets in declaration order, including when the assistant dock reduces available width.

Actions contain a unique bounded `label`, a bounded `draft`, and an optional `when` condition naming a widget and `empty` or `has_records`. Conditions require a successful numeric total; unavailable data activates neither condition. Clicking prepares an editable chat draft and does not send a model request. The existing resident context identifies the focused App; Home introduces no harness or skill-routing override.

## Read API and authority

Authenticated `GET /apps/{app_id}/home` returns the active definition's Home and resolved widget data. `include_data=false` returns declaration metadata without executing queries. The response includes `definition_revision` when Home exists and `{home: null}` when no active owned Home exists.

- App visibility is checked before reading the declaration.
- The definition must match the App's explicit active pointer, own the App and have active status. Reads do not repair pointers or mutate graph state.
- Data reads require the matching active workspace and an active App lifecycle. Query invocation retains existing permission, scope and declared-query enforcement.
- All widgets share a five-second execution budget. Failures and exhausted budgets return unavailable widget data; exception details are not exposed.
- The UI refreshes periodically, offers explicit retry, distinguishes unavailable from empty and disables actions during refresh.

This preserves I-APP-DEF-01, I-SUBSTRATE-01 and I-EXT-01: immutable active definitions remain authoritative, no Business knowledge enters Core, and Core imports no App implementation. Existing graph, authorization, staging and query enforcement remain in their established services.

## Updates and navigation

A package update changes Home through its next compiled active definition. Removing `app.home` removes the package Home on reconciliation. This is package-controlled presentation, not a user-editable dashboard template or a three-way dashboard merge.

Apps with Home default to it when no section preference exists. An explicit Tracks/Dashboards preference remains respected; removing Home falls back to Tracks. The older default-track welcome card remains for Apps without Home. Home installation, existing records, empty state, lifecycle/restart and error recovery need browser qualification in addition to source tests.
