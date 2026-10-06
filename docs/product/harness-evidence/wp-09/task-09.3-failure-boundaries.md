# WP-09.3 evidence — failure boundaries and encryption rotation

## Result

The tested recovery path preserves the run fence when a chat consumer stops
after Core claims the run but before model dispatch. The run is available for
recovery, while the model/tool execution boundary remains untouched.

The two persistence stores are treated as a consistency pair. A complete
framework snapshot without its Core recovery manifest cannot be resumed after
model activity. A Core pointer without its framework snapshot fails closed. A
complete snapshot/manifest pair with a lost pointer CAS can repair the pointer
and resume. Corrupt checkpoint ciphertext raises a persistence error instead
of becoming empty history. Existing Postgres coverage also verifies missing
manifest and stale-fence rejection.

Key-overlap evidence confirms old checkpoint records remain readable while the
previous key is configured, and new records are encrypted with the current
key. `rewrap_harness_session_records` migrates encrypted run, event, snapshot,
manifest, tool-effect, plan, model-request, and turn-input records for one
exact session scope. The sweep is resumable and returns counts only. Mutable
plan state uses revision and ciphertext compare-and-set so a concurrent plan
update is not overwritten. PostgreSQL coverage now writes all eight record
types under the previous key, sweeps them, retires that key, and reads every
payload successfully. A separate injected race changes plan revision and
ciphertext after the rotation read; the stale CAS loses and the concurrent
plan content remains readable.

## Verification

Against the configured local PostgreSQL service:

```sh
cd backend
INTEGRAL_TEST_DB=postgres \
JVSPATIAL_POSTGRES_DSN=postgresql://integral:integral@127.0.0.1:5433/postgres \
.venv/bin/python -m pytest \
  tests/native_harness/wp_09 \
  tests/native_harness/wp_04/test_litellm_sdk_transport.py \
  tests/contract/test_harness_sessions_postgres.py \
  -o addopts='' --strict-markers -q
```

**47 passed, 25 warnings**. This includes the injected interruption after one
snapshot ciphertext was rewrapped, all-eight-record key-retirement proof, and
the concurrent plan-state CAS race. The warnings are jvspatial's existing
`asyncio.iscoroutinefunction` deprecation. The browser smoke result is recorded
separately in `../wp-06/browser-smoke-2026-10-04.md`.

## Review note

The fresh review found the existing per-session scope filter, per-record
durability, and plan revision/ciphertext CAS aligned with the recovery
contract. It identified two evidence gaps: previous-key retirement covered
only snapshot/manifest/plan, and the mutable-plan CAS race was not fault
injected. The PostgreSQL tests above now cover both findings. The helper
remains an internal service with no user-facing endpoint. Deployment
operations still need an authorized caller and audit/rollout controls before
key retirement can be run operationally.

## Remaining qualification

- Operator authorization, audit receipts, and rollout controls for invoking
  the per-session rewrap service remain a deployment integration requirement;
  no user-facing rotation endpoint is added by this task.
- Independent review of the newly added PostgreSQL coverage remains required
  before WP-09.3 is accepted.
- Re-run the full repository gate after integrated changes; this focused suite
  does not qualify the full backend, frontend, wheel, or package release.
