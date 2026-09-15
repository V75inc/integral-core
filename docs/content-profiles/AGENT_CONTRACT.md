# Agent Authoring Contract

Pillar 3 of the agent-authorable substrate. The Integral agent reasons
over content profiles via a small, declarative MCP surface organised
around **introspect → propose → diff → publish**.

## Tool catalogue

| Tool | Stages? | Purpose |
|------|---------|---------|
| `integral_describe_substrate`        | no  | Catalogue of registered field types, view palette keys (`palette_group`, `configurable`, `hot_loadable`, `supported_platforms`), and signed plugins. **Agent's first call** before authoring. Use only `view_type` keys from this catalogue — profiles do not hot-load arbitrary view code ([VIEW_PALETTE.md](VIEW_PALETTE.md)). |
| `integral_describe_profile`          | no  | Published + draft state for a track or space CP. |
| `integral_get_profile_draft`         | no  | Auto-create or fetch a draft (idempotent). |
| `integral_propose_profile_revision`  | yes | Apply a patch DSL to a draft. |
| `integral_diff_profile_draft`        | no  | Structural diff vs published parent + per-track entry-impact. |
| `integral_publish_profile_draft`     | yes | Atomic swap + run migrations. |
| `integral_discard_profile_draft`     | yes | Delete an unpublished draft. |

All "yes" tools route through the existing staging primitive
(`prepare → bless token → execute`). Frontend cards live at
`frontend/src/features/ai-chat/staging/{ProfileRevisionCard,PublishDraftCard,DiscardDraftCard}.tsx`.

## Patch DSL

`backend/app/services/agent_profile_patches.py`. Pure-function
interpreter; ops:

```jsonc
{
  "operations": [
    { "op": "register_composite_field_type", "spec": { "key": "currency", "base": "number", "config": { "currency": "USD" } } },
    { "op": "add_entry_type", "spec": { "key": "task", "name": "Task", "fields": [] } },
    { "op": "modify_entry_type", "key": "task", "patch": { "icon": "list-todo" } },
    { "op": "remove_entry_type", "key": "deprecated" },
    { "op": "add_field", "entry_type": "task", "spec": { "key": "priority", "type": "select", "enum": ["low","medium","high"] } },
    { "op": "modify_field", "entry_type": "task", "field_key": "priority", "patch": { "enum": ["low","medium","high","urgent"] } },
    { "op": "remove_field", "entry_type": "task", "field_key": "legacy" },
    { "op": "add_view", "spec": { "key": "board", "name": "Board", "view_type": "composable_board", "config": { "group_by": "priority" } } },
    { "op": "remove_view", "key": "old_table" },
    { "op": "add_tag", "group_key": "priority", "spec": { "key": "high", "name": "High" } },
    { "op": "remove_tag", "group_key": "priority", "key": "old" },
    { "op": "add_relation", "spec": { "source_track_type": "tasks", "target_track_type": "projects", "relation_field": "project" } }
  ]
}
```

The interpreter never touches the database directly — `apply_operations`
returns a new manifest, which is then validated through
`compile_canonical_manifest` and persisted onto the draft node.

## Recommended agent loop

1. `integral_describe_substrate` — learn field types and palette `view_type` keys.
2. `integral_describe_profile { track_id }` — inspect the current
   published + draft state.
3. `integral_get_profile_draft { content_profile_id }` — auto-create a
   draft to mutate.
4. `integral_propose_profile_revision { draft_id, operations }` —
   stage the patch.
5. `integral_diff_profile_draft { draft_id }` — show the user the
   structural diff + entry-impact summary.
6. `integral_publish_profile_draft { draft_id }` — atomic swap. Or
   `integral_discard_profile_draft` if the user declines.

Every staged write surfaces an approval card in the chat surface; the
user must explicitly bless before the executor runs.

## Tests

- `backend/tests/test_agent_profile_patches.py` — patch DSL coverage.
- `backend/tests/test_agent_profile_substrate_tools.py` — MCP tool
  registration + smoke coverage.
