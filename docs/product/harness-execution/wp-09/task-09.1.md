# WP-09.1 — Checkpoint manifest and fenced safe pointer

## Objective

Persist a versioned, encrypted Core checkpoint manifest beside Pydantic AI
message snapshots and advance the session's safe-checkpoint pointer only when
the manifest and snapshot describe a consistent, authorized boundary.

## Required manifest fields

- Core run, thread, tenant, principal, native session and binding generation.
- Framework snapshot identity, schema/codec version, timestamp and settled
  state.
- Capability/profile fingerprint and per-capability restore policy.
- Plan revision and protected unresolved obligations.
- Tool effect identities, durable receipts and pending approval references.
- Context/compaction version and external artifact references with retention.
- Model request transition watermark and accounting reconciliation state.

Do not persist API keys, access tokens, hidden reasoning, live coroutine state,
or tenant-selected storage locations from model output.

## Acceptance

- The manifest is encrypted/authenticated and versioned; scope and run identity
  are checked on read.
- A missing, corrupt, mismatched or unsupported manifest cannot advance the
  safe pointer.
- Snapshot, manifest and session pointer advance atomically where supported;
  otherwise recovery deterministically reconciles and leaves the old pointer
  active until consistency is proven.
- Pointer writes atomically compare the active chat run ID, which is the fence
  available on the current direct-streaming provider path, and reject stale
  run continuations. WorkItem lease-token/fence integration remains a separate
  WP-03/WP-08 integration requirement.
- PostgreSQL tests cover concurrent pointer advancement and a crash between
  snapshot and manifest persistence.

## Dependencies and ownership

Depends on WP-02 encrypted scoped persistence and WP-03 run fencing. Own the
checkpoint manifest Object/storage codec and the narrow session-pointer CAS
service. Preserve the existing session Node as the graph identity; the manifest
and execution details remain I-GRAPH-02 Objects. The direct chat adapter has no
WorkItem lease token, so its current CAS fence is the persisted Core run ID;
this does not claim queue-worker lease coverage.

## Verification

Add deterministic unit tests and real PostgreSQL persistence/fence probes under
`backend/tests/native_harness/wp_09/`. Run the owned WP-09 namespace with
`-o addopts='' --strict-markers`; record schema identity, commands, counts and
failure-injection results in a WP-09 evidence receipt.
