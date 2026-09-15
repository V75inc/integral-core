# MongoDB Performance Runbook

Operational reference for keeping Integral's MongoDB substrate fast. Pairs with
`.env.example` (`JVSPATIAL_MONGODB_*`, `JVSPATIAL_AUTO_CREATE_INDEXES`,
`INTEGRAL_PERF_TRACE`) and the IO-PERF optimization milestone.

## Index posture

Integral relies on jvspatial's `ensure_indexes()` mechanism. At boot,
`backend/app/main.py` lifespan iterates every registered Node + Edge class and
materializes the indexes declared via `attribute(indexed=True)` and
`@compound_index(...)`. The boot loop is idempotent and gated by
`JVSPATIAL_AUTO_CREATE_INDEXES` (default true outside serverless).

### Expected index set

**`db.edge` (5 indexes):**

```
_id_                                                  default
idx_source_target_entity_unique  (source,target,entity) unique  jvspatial default
idx_target_entity                (target,entity)                jvspatial default
idx_entity_source                (entity,source)                jvspatial default
idx_entity_target                (entity,target)                jvspatial default
```

**`db.node` (Integral-owned subset — full set includes agentive indexes):**

```
_id_                                                                default
idx_node_entity                  (entity)                           jvspatial default
context.author_id_1              (context.author_id)                Entry
context.track_id_1               (context.track_id)                 Entry, EntryType, Tag, View
context.workspace_id_1           (context.workspace_id)             Invitation, ShareLink
context.kind_1                   (context.kind)                     Workspace
context.email_1                  (context.email)                    Invitation
context.target_resource_id_1     (context.target_resource_id)       Invitation
context.token_hash_1             (context.token_hash) unique partial ShareLink
context.resource_id_1            (context.resource_id)              ShareLink
context.parent_tag_id_1          (context.parent_tag_id)            Tag
context.app_id_1                 (context.app_id)                   Tag
context.user_id_1                (context.user_id)                  Notification
idx_entry_track_created          (context.track_id, context.created_at desc)   Entry
idx_entry_author_created         (context.author_id, context.created_at desc)  Entry
idx_ws_kind_created              (context.kind, context.created_at desc)       Workspace
idx_notif_user_read              (context.user_id, context.read)               Notification
```

### Verification

After deploy:

```bash
mongosh --eval '
  print("=== edge ==="); db.getSiblingDB("integral_db").edge.getIndexes().forEach(i=>print(i.name));
  print("=== node ==="); db.getSiblingDB("integral_db").node.getIndexes().forEach(i=>print(i.name));
'
```

Or run the explain harness:

```bash
cd backend && python scripts/perf_explain.py --out /tmp/explain.json
```

Every dominant probe MUST report `IXSCAN`. A `COLLSCAN` row is a regression.

## Connection budget

Pool sizing follows `JVSPATIAL_MONGODB_MAX_POOL_SIZE × worker_count ≤
mongo.connections.available`. Default jvspatial cap is **10** (Lambda-tuned).
For containerized backends, raise to 50 and scale with worker count.

| Deploy shape | Recommended pool | Notes |
|---|---|---|
| Local dev | 10 | jvspatial default |
| Single container, 4 workers | 50 (max) / 5 (min) | absorbs short bursts |
| K8s replicas × 4 workers | 30 (max) / 2 (min) | aggregate ≤ Mongo cap |
| Lambda / Cloud Functions | 10 (max) / 0 (min) | cold-start floor |

Check Mongo capacity:

```javascript
db.runCommand({ serverStatus: 1 }).connections
```

## Slow-query profiling

Enable Mongo's built-in profiler when triaging latency regressions:

```javascript
use integral_db
db.setProfilingLevel(1, { slowms: 100 })   // log queries > 100ms

// after reproducing the slow path:
db.system.profile.find({ millis: { $gt: 100 } })
  .sort({ ts: -1 })
  .limit(20)
  .pretty()

db.setProfilingLevel(0)                    // disable
```

Pair with the integral-side trace spans:

```bash
INTEGRAL_PERF_TRACE=1 python -m app.main
# Then grep stdout for `perf.span` lines — each carries name + duration_ms.
```

## Regression detection

Run the bench + explain pair before AND after any substrate-touching plan:

```bash
# baseline (pre-change)
backend/scripts/perf_bench.sh                    # writes .planning/perf-baseline.txt
python backend/scripts/perf_explain.py \
  --out .planning/perf-baseline-pre.json

# apply change, restart server, then:
JWT=... SCOPE=... \
  OUT=.planning/perf-baseline-post.txt \
  backend/scripts/perf_bench.sh
python backend/scripts/perf_explain.py \
  --out .planning/perf-baseline-post.json
```

Diff the JSON: any probe regressing from `IXSCAN` to `COLLSCAN` or whose
`docs_examined` grows materially without a matching schema change is a
blocking issue.

## Adding new indexes

1. Annotate the Node field with `attribute(indexed=True)` (or use
   `@compound_index([("field", 1), ...], name="idx_…")` for compounds). Both
   come from `jvspatial.core.annotations`.
2. Restart backend — `ensure_indexes()` materializes it on next boot.
3. Confirm via `db.<collection>.getIndexes()` or rerun `perf_explain.py`.
4. Never write `db.collection.createIndex(...)` directly — the boot loop
   becomes the source of truth.

For indexes that should ship as jvspatial defaults (every typed-edge graph
benefits), declare in `jvspatial/core/entities/{node,edge}.py`'s
`get_indexes()` override and keep the entry domain-agnostic.

## Out-of-scope from this runbook

- Sharding / replica-set topology — deploy-time concern.
- Mongo server tuning (WiredTiger cache, RAM allocation) — handled by the
  cluster operator.
- Backups / restore — see DEPLOY runbook.
