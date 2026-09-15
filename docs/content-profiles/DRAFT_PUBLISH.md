# Draft / Publish Lifecycle

Pillar 2 of the agent-authorable substrate. Every Content Profile is
versioned. Mutations land on a *draft* sibling; the user (or agent, with
explicit blessing) promotes the draft to published via an atomic swap
that runs any declared migrations in flight.

## Node fields

`ContentProfile` (see [`backend/app/models/nodes.py`](../../backend/app/models/nodes.py))
gains lifecycle metadata:

| Field | Meaning |
|-------|---------|
| `status`              | `"published"` (default) or `"draft"` |
| `published_at`        | ISO-8601 timestamp of last publish |
| `draft_of_id`         | When `status == "draft"`, points at the published parent |
| `published_id`        | When `status == "published"`, last promoted draft id |
| `parent_version_id`   | Version-graph parent (today equal to `published_id`) |
| `version_number`      | Monotonic, bumped on publish |
| `version_label`       | Optional human label ("v1.1", "Q2 cleanup") |
| `signature`           | Audit envelope (proposer / approver / tool) |

Existing rows default to `status="published"`, `version_number=1` — no
migration needed.

## REST API

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/content-profiles/{id}/draft`         | Fork a draft from a published CP |
| POST | `/api/content-profiles/{id}/publish`       | Atomic swap + run migrations |
| POST | `/api/content-profiles/{id}/diff`          | Structural diff + entry-impact |
| POST | `/api/content-profiles/{id}/discard-draft` | Delete an unpublished draft |
| PUT  | `/api/content-profiles/{id}`               | Mutate a draft's manifest (existing endpoint, extended) |

Permission gate: caller must satisfy `_resolve_cp_edit_permission`
(library owner, track editor, or space editor depending on attachment).

## Lifecycle

```
                 ┌─────────────────┐  fork  ┌──────────────┐
publish base ───►│   published CP  │◄───────│    draft     │
                 └────────┬────────┘        └─────┬────────┘
                          │                       │ patch
                          │                       ▼
                          │              [optional re-edits]
                          │                       │
                          │   ┌───────────────────┘
                          │   │
                          │   ▼
                          │  diff (structural + entry-impact)
                          │   │
                          │   ▼
                          ▼  publish (atomic swap + migrations)
                  ┌──────────────────┐
                  │  published CP    │  version_number += 1
                  │  (same node id)  │  manifest replaced
                  └──────────────────┘
```

The published CP keeps its node identity through the swap — the
`HAS_CONTENT_PROFILE` edges from Tracks / Spaces are never re-pointed,
which is what makes the swap atomic. Only the `manifest` payload and
the lifecycle metadata change.

## Atomic swap implementation

[`backend/app/services/content_profile_atomic_swap.py`](../../backend/app/services/content_profile_atomic_swap.py)

1. Validate the draft's manifest compiles cleanly (early abort on shape
   errors).
2. Run the declarative migration runner (see [MIGRATIONS.md](MIGRATIONS.md)).
3. Copy the draft's manifest onto the published CP, bump
   `version_number`, set `published_at`, update `signature`.
4. Mark the draft as consumed (`status="published"`, `published_id=<parent>`).
5. Push composite metadata onto materialized `EntryType.form_schema`
   nodes so entry validation dispatches without re-compiling.
6. Invalidate the manifest compile cache.
7. Emit a single `content_profile.publish` change event.

## Tests

- `backend/tests/test_content_profile_drafts.py` — fork → mutate → diff
  → publish round-trip; discard-draft; substrate endpoint.
