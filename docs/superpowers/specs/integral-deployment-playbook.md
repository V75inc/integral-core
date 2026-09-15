# Integral deployment playbook (Swarm + Postgres)

Canonical ops detail lives in [`docs/ops/DEPLOY.md`](../../ops/DEPLOY.md). This
playbook is the short checklist for substrate-sensitive post-roll steps.

## After rolling an image that pins jvspatial ≥ 0.0.18

1. Confirm stack/env has:
   - `JVSPATIAL_PG_GIN_INDEX=off` (Integral `app/` has no `$all` / `$elemMatch`)
   - `JVSPATIAL_POSTGRES_MAX_POOL_SIZE` set explicitly (default `10` is fine for
     the ADR-005 single-worker posture)
   - Remove any leftover `JVSPATIAL_NODE_EDGE_IDS` from runtime env (removed in
     jvspatial 0.0.19; unknown keys warn under the allowlist)
2. Run the idempotent adjacency scrub against the target DSN (safe online):

```bash
JVSPATIAL_POSTGRES_DSN='postgresql://…' ./deploy/scripts/strip_node_edges.sh
# or:
./deploy/scripts/strip_node_edges.sh --dsn "$JVSPATIAL_POSTGRES_DSN"
```

The script invokes `jvspatial migrate strip-node-edges --dsn … --apply`.
Re-running is a no-op on already-stripped rows.

3. Smoke: create an entry on a seeded hub track; hub node document size must
   not grow with degree, and create must not take `FOR UPDATE` on the track
   row for adjacency rewrite.
