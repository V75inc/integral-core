# Declarative Migrations

Pillar 2 of the agent-authorable substrate. When a draft's schema
changes diverge from existing entry data, the manifest's `migrations[]`
block declares the data-side transforms that must run on publish.

## Where they run

The migration runner lives at
[`backend/app/services/content_profile_migrations.py`](../../backend/app/services/content_profile_migrations.py)
and is invoked from `content_profile_atomic_swap.publish_draft` BEFORE
the manifest swap. With `abort_on_failure=True` (the default), the first
unrecoverable error short-circuits the publish; with `False`, the runner
keeps going and surfaces per-op failures so the operator can repair.

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
| `rename_field`       | `entry_type`, `from`, `to` | Renames a custom_fields key and the matching typed relation-edge field key on every entry of that type. |
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

## Tests

- `backend/tests/test_content_profile_migrations.py` — coerce helpers, op
  registration, abort vs. permissive failure policy.
