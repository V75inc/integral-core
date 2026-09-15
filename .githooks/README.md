# Integral git hooks

Tracked git hooks. Wire them with one command:

```bash
git config core.hooksPath .githooks
```

Run once per clone. After that, every commit fires the hooks in this
directory before landing.

## What ships here

### `pre-commit`

Runs six substrate guards. Any failure aborts the commit.

1. **`.ci/jvspatial_drift_check.sh`** — blocks raw FastAPI patterns
   (`APIRouter`, `@router.<method>`, `HTTPException`) in
   `backend/app/`. Convention enforced: `CLAUDE.md` §
   "jvspatial Object-Spatial Contract" → Forbidden Patterns. Allowlist
   for in-flight remediation: `.ci/jvspatial_drift_allowlist.txt`.

2. **`.ci/graph_contiguousness_check.sh`** — blocks `<Node>.create(`
   sites in `backend/app/` that lack a same-function structural edge
   wire (or a known wiring-helper call). Invariant enforced:
   `docs/INVARIANTS.md` § `I-GRAPH-01` (all-nodes-reachable-from-root)
   + § `I-GRAPH-02` (object-for-non-graph-records). Allowlist for
   in-flight remediation: `backend/.ci/graph_contiguousness_allowlist.txt`
   (header-only as of Phase 10.5 close, 2026-05-20).

3. **`.ci/ui_drift_check.sh`** — blocks raw typography/surface utilities
   in staged frontend feature code.

4. **`.ci/substrate_domain_drift_check.sh`** — blocks domain bundle slug
   references in substrate scope (I-SUBSTRATE-01).

5. **`.ci/service_layer_drift_check.sh`** — blocks CRUD mutations outside
   canonical service entrypoints (I-CRUD-01).

6. **`.ci/skill_compliance_check.sh`** — runs
   `pytest tests/test_skill_compliance.py` (jv `SKILL.md` frontmatter,
   7-section bar, manifest tool sync). Contract:
   `docs/backend/skill-format-standard.md`.

## Bypass

```bash
git commit --no-verify ...
```

Reserve for documented exceptions only. These guards encode substrate
invariants that downstream code (walkers, cascade-delete, audit
sweeps) depends on. If you find yourself needing `--no-verify`,
investigate the gate failure first — it usually surfaces a real
contract violation.

## Adding a new guard

1. Write the shell script under `.ci/` and `chmod +x`.
2. Append a block to `.githooks/pre-commit` mirroring the existing
   guards — call via `bash .ci/your_check.sh`; aggregate failures into
   the `FAILED` counter; exit non-zero if any guard fails.
3. Document the convention enforced in CLAUDE.md and/or
   `docs/INVARIANTS.md` so the gate has an articulated rationale.
