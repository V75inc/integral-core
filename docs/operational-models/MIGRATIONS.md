# Declarative Migrations

Pillar 2 of the agent-authorable substrate. When a draft's schema
changes diverge from existing entry data, the manifest's `migrations[]`
block declares the data-side transforms that must run on publish.

## Where they run

The migration runner lives at
[`backend/app/services/operational_model_migrations.py`](../../backend/app/services/operational_model_migrations.py)
and is invoked from `operational_model_atomic_swap.publish_draft` AFTER the
manifest swap. It marks affected entries `pending` before responding, then
runs each Track-wide declarative operation once per affected Track. If an
operation fails, the entries governed by that Track are marked failed while
other Tracks continue; Core never replays a Track-wide operation once per row.
A failed entry never causes the new manifest to be presented as successfully migrated. Editors can
inspect `GET /api/operational-models/{id}/migration-status` and retry supported
declarative work with `POST /api/operational-models/{id}/retry-migration`.
On a process restart, in-flight rows are reconciled to an explicit retryable
failure rather than silently reported complete.
The retry endpoint accepts only that `failed` state; it refuses completed and
currently running migrations so historical transformations cannot be replayed
by accident.

## Manifest declaration

```yaml
migrations:
  - from_version: "v1"
    to_version:   "v1.1"
    ops:
      - { op: rename_field,      entry_type: task, from: prio,    to: priority }
      - { op: default_fill,      entry_type: task, field: priority, value: medium }
      - { op: prune_enum_option, entry_type: task, field: priority, option: urgent, replacement: high }
      - { op: coerce_type,       entry_type: task, field: estimate, to: number }
      - { op: delete_field,      entry_type: task, field: legacy }
      - { op: move_field,        from_entry_type: task, to_entry_type: subtask, field: owner }
```

## Op catalogue

| Op | Args | Behavior |
|----|------|----------|
| `rename_field`       | `entry_type`, `from`, `to` | Renames a custom_fields key, matching typed relation-edge field keys, and saved View field references on every entry of that type. |
| `default_fill`       | `entry_type`, `field`, `value` | Fills the field with `value` on entries where it is null/missing. |
| `delete_field`       | `entry_type`, `field` | Drops the key from custom_fields. |
| `prune_enum_option`  | `entry_type`, `field`, `option`, `replacement?` | Removes an enum option; replaces with `replacement` (single-select) or strips from list (multi-select). |
| `coerce_type`        | `entry_type`, `field`, `to` (text/number/boolean/json) | Coerces the value; invalid coercions surface in `errors[]`. |
| `move_field`         | `from_entry_type`, `to_entry_type`, `field` | Pending — flagged for manual review (entry re-typing not implemented). |

## Run record

`run_publish_migrations` returns:

```jsonc
{
  "executed": true,
  "ops": [
    { "op": "rename_field", "from_version": "v1", "to_version": "v1.1",
      "mutated_entries": [...], "errors": [], "pending_manual_review": [] },
    ...
  ],
  "errors": [<unrecoverable errors aggregated across ops>],
  "mutated_entry_count": 42,
  "pending_manual_review": [<rows requiring human follow-up>],
  "actor_id": "..."
}
```

## Field-reference safety

`rename_field` changes only declared field-reference positions in a View
configuration, such as `columns[].field`, `filters[].field`, `group_by`, and
chart/tree field settings. It does not rewrite arbitrary labels or text.

Use `custom_fields.<key>` for a business field whose key collides with a
platform attribute, such as `status`. A bare `status` is the platform
lifecycle field and is intentionally not rewritten by a business-field rename;
the qualified path makes the migration unambiguous.

## Tests

- `backend/tests/test_operational_model_migrations.py` — coerce helpers, op
  registration, abort vs. permissive failure policy, and a populated
  preservation fixture covering null fields, status namespace collisions,
  relation edges, attachments, collaborators, and saved views.
