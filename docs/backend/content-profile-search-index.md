# Content Profile Search and Index Strategy

Integral now stages profile-aware indexing by extracting a lightweight `_cp_index`
document onto each `Entry.custom_fields` payload during create/update.

## Index Source

- Source schema: `EntryType.form_schema.fields[]`
- A field participates when `field.index === true`
- Facets are emitted for `select`, `multi_select`, and `boolean` indexed fields

## Stored Shape

```json
{
  "_cp_index": {
    "entry_type_key": "task",
    "indexed_fields": {
      "priority": "high",
      "estimate_hours": 8
    },
    "facets": {
      "priority": "high"
    }
  }
}
```

## Query Flow Guidance

1. Resolve candidates through existing permission boundary
   (`get_user_accessible_entries`).
2. Filter/sort against `_cp_index` fields.
3. Expand to full-text engine later without bypassing permission gate.

## Next Migration Stage

- Introduce dedicated index backend adapter.
- Keep `_cp_index` as canonical extraction contract from profile schemas.
