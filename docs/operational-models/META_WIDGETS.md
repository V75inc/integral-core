# Composable Meta-Widgets

Pillar 4 of the agent-authorable substrate. Generic, declaratively-driven
front-end widgets that let the agent express "I want X view of Y data" as
a config rather than a new React component.

## Catalogue

| Widget | Config keys |
|--------|-------------|
| `composable_list`     | `group_by`, `sort`, `filter`, `projection`, `density` |
| `composable_board`    | `group_by`, `color_by`, `swimlanes`, `sort_within_column`, `filter` |
| `composable_grid`     | `group_by`, `color_by`, `projection`, `card_layout`, `sort` |
| `composable_timeline` | `date_field`, `end_date_field`, `color_by`, `filter` |

Source: [`frontend/src/components/views/composable/`](../../frontend/src/components/views/composable/).
Backend registration: [`backend/app/views/operational_model_view_types.py`](../../backend/app/views/operational_model_view_types.py).
Palette convention: [VIEW_PALETTE.md](VIEW_PALETTE.md).

## Config conventions

Filter rules:

```yaml
filter:
  rules:
    - { field: status,   op: eq,        value: open }
    - { field: priority, op: in,        value: [high, urgent] }
    - { field: due_date, op: lt,        value: 1735689600000 }
    - { field: title,    op: contains,  value: invoice }
```

Sort rules:

```yaml
sort:
  - { field: due_date, direction: asc }
  - { field: priority, direction: desc }
```

Projection (which fields surface as inline metadata on each card):

```yaml
projection: [priority, due_date, owner]
```

## When to use

- Use a composable widget when the requirement is "new view config" —
  agent has full latitude.
- Use a composite over a composable widget when the requirement is "we
  always show tasks this way for this domain" — the composite locks in
  the convention and gives it a domain name (`roadmap`, `pipeline`,
  `weekly_planner`).
- Use a built-in widget (`kanban`, `table`, `calendar`, `gallery`,
  `wiki`, `feed`) when the existing UX is exactly what you want — no new
  rendering, no new config surface.
- Use `wiki` (labeled **Pages** in the UI) when entries are hierarchical pages with a parent relation
  field; use `feed` for chronological activity streams.
- Use a code plugin only when the renderer itself doesn't exist
  (Mermaid, 3D viewer, custom chart kit). See [PLUGINS.md](PLUGINS.md).
