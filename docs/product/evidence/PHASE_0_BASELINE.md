# Phase 0 qualification baseline

**Status:** implemented baseline infrastructure; not a release declaration.

Phase 0 establishes repeatable evidence before changing remaining Core
behaviour. The scenario source is
[`phase-0-qualification-manifest.yaml`](phase-0-qualification-manifest.yaml).
It covers four domain-neutral operational shapes and requires independent
readback of schemas, records, projections, effects, scope enforcement, and
lifecycle state.

Run one lane through the evidence runner:

```bash
python3 scripts/run_qualification_lane.py repository
python3 scripts/run_qualification_lane.py core-only
python3 scripts/run_qualification_lane.py contract
python3 scripts/run_qualification_lane.py artifacts
python3 scripts/run_qualification_lane.py postgres
```

Each invocation writes a timestamped JSON record and combined log to the
ignored `.qualification-evidence/` directory. The record identifies the exact
revision, command, result, runtime, non-secret configuration shape, fixture
manifest, and retained output. A later frozen candidate copies only its
relevant immutable records into the acceptance ledger.

## Phase 0 completion rule

Phase 0 is complete when every scenario assertion has a deterministic test or
browser journey, each qualification lane is run through this recorder, and the
acceptance ledger identifies supported provider configurations. The
WP-06 live-model profile is an external exam, not a platform blocker.
Evaluate a redacted trace with:

```bash
backend/.venv/bin/python scripts/evaluate_live_model_qualification.py \
  --profile docs/product/evidence/wp-06-live-model-qualification.yaml \
  --trace .qualification-evidence/live-model-trace.json \
  --report .qualification-evidence/live-model-report.json
```
