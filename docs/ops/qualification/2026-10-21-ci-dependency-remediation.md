# Dependency audit remediation — 2026-10-21

## Change and deployment impact

The current FastEmbed package requires Pillow below 13, while the backend audit
requires Pillow 13.0.0 to address eight published advisories. The normal resolver
cannot satisfy both. Replace the text embedding wrapper with a small ONNX CPU
adapter using the same MiniLM weights and tokenizer. No advisory suppression,
dependency override, unpublished package or external embedding service is used.

Default artifact: `qdrant/all-MiniLM-L6-v2-onnx` at immutable source revision
`d13954661f83248295ba75c1ed411eef3b7b936e`, `model.onnx`, normalized masked mean
pooling. Runtime imports remain deferred. Batch size is bounded to 16. Deployment
configuration can select other pinned artifacts with explicit pooling; model
names alone no longer select an implicit FastEmbed catalogue entry.

Profile format 2 incorporates artifact and preprocessing identity. Existing
canonical records and old vectors are preserved; a separate vector space is
backfilled and canonical keyword search remains available while it warms.
Custom model deployments must migrate using the explicit ONNX settings described
in [semantic indexing](../../backend/semantic-indexing.md).

Frontend dependency remediation uses Vite 8.1.6 and follow-redirects 1.16.1.
Backend metadata and the frozen lock select Pillow 13.0.0, certifi 2026.10.19
and uv 0.13.4. Existing reviewed frontend build-only exceptions stay unchanged.

## External handler initialization

The broad backend run exposed a compiled external-App handler reference that
skipped namespace initialization after archive extraction. A structural test
reproduced the defect (one failure, three passes) before remediation. Normalize
references for the current bundle through the same initialization path whether
they are relative, legacy in-tree names or already external absolute names.
Foreign bundle references retain their prior behavior. The four structural
cases pass after the change. The dispatcher/install contract selection passes
all 23 tests (including opt-in tests), and the final structural plus extracted-App
HTTP/resident/MCP query readback run passes all five tests under xdist with
`TESTING=1 DEBUG=false`. The entire artifact contract module also passes
15 tests with three declared skips. No scope check, approval or dispatcher assertion
is weakened.

## Completed local evidence

- Old FastEmbed versus the native encoder, six synthetic passages including
  empty, multilingual and long input: identical tokenizer IDs, 384-dimensional
  normalized vectors, maximum absolute vector difference `6.413240680180898e-08`.
  This establishes encoder equivalence on the sampled inputs, not a general
  language-model quality benchmark.
- Structural encoder and profile contracts: **19 passed**.
- Retrieval driver/rank-fusion/deferred-import unit contracts in a separate
  process: **16 passed**.
- Isolated PostgreSQL 14 with pgvector, real model indexing/retrieval and
  transaction/access-control checks: **33 passed**. Covers freshness, deletion,
  exclusions, foreign scope, stale workers, lease retries, direct Entry grants,
  REST/agent boundaries and canonical transaction rollback.
- Backend dependency audit: **passed**, no known vulnerabilities reported.
- Full frontend suite: **341 files / 1,978 tests passed**; TypeScript and
  production Vite build passed (existing chunk-size warning).
- Frontend dependency audit: **passed** under the unchanged reviewed policy.
- Fresh wheel installation with public dependency metadata and outside-checkout
  ASGI import: **passed**.
- Repeated wheel build and outside-checkout artifact import: **passed**.
- Pinned formatters, flake8 and backend mypy: **passed**. NumPy dependency stubs
  remain outside the existing mypy follow-imports boundary; application code is
  still checked.

## Validation limits

Docker's local overlay storage was full; no unrelated images or volumes were
removed. PostgreSQL qualification instead used a new disposable host cluster on
port 57021, separate from the preview database. Hosted CI supplies the PostgreSQL
16 and Docker build lanes. The initial PR 164 full run reported the external handler failure described
above; its isolated case passed on retry. Both CI-faithful smoke runs passed
before the final namespace regression was added; that regression and the
previously failing end-to-end case then passed in the same CI environment. Full-suite and exact-head CI
results are recorded in
the PR after completion. This is not a published package, version bump or deploy.

The old and new cold-start measurements were not controlled performance samples;
no latency improvement is claimed. Existing retrieval token-budget contracts
were exercised; provider chat token usage was not re-benchmarked. Text inference
stays local and introduces no embedding-provider token charges.

A mixed exploratory pytest invocation included a unit module that explicitly
reloads the entire retrieval package and invalidated class identities held by
already-collected PostgreSQL tests. The clean PostgreSQL qualification above
ran in its own process, matching the documented lane separation. No production
access check or test assertion was weakened to accommodate that attempt.
