# WP-03.3.3 — Durable event-store slice

Date: 2026-10-04
Status: event-store slice verified; WP-03.3.3 remains in progress. Native
WorkItem producer admission and `chat_turn` worker dispatch remain disabled.

## Implemented

- The durable event allowlist now matches Integral's emitted public event
  shapes, including text replacement, camel-case tool-call IDs, source-backed
  model/usage metadata, and bounded timing/usage objects.
- Private reasoning events and unknown event types are rejected. Raw model
  parts, tool arguments/results, unapproved fields, non-finite numbers, and
  unsupported nested metadata do not enter durable replay.
- Replay verifies the WorkItem's principal, workspace, and thread before
  reading events. It returns the committed sequence head, next replay cursor,
  pagination state, event-log gap signal, and WorkItem status.
- The WP-03.3.3 task brief now assigns ownership by event-store, worker,
  producer/reconnect, and independent-evidence slices without reducing the
  overall acceptance scope.

## Verification

- PostgreSQL contract + focused submission/event tests: **27 passed** with
  `INTEGRAL_TEST_DB=postgres`. Coverage includes encrypted append, duplicate
  event idempotency, overlapping transactional sequence allocation, exact
  scope rejection, cursor pagination, and explicit gap reporting after a
  committed row is missing. Pure normalization tests also reject invalid
  event types, reasoning payloads, malformed metadata, and invalid token,
  timing, attempt, cost, and text-field values.
- Black, isort, and flake8 passed for the changed service, schema, and tests.
- Focused mypy passed for the changed service and schema with imports skipped;
  this checks local annotations while avoiding repository-wide dependency
  errors from a file-target invocation.
- `git diff --check` passed.

## Remaining acceptance work

- Bind append/replay to the worker stream and persist the assistant transcript
  under one stable WorkItem identity.
- Add authenticated accept/replay/cancel routes and reconnect behavior without
  a second provider call.
- Implement terminal WorkItem/outbox/admission reconciliation and WP-09 safe
  recovery policy. Keep worker dispatch and the native WorkItem producer
  disabled until all frozen constraints in the task brief are satisfied.
- Prove worker-process contention, required crash points, cancellation,
  exactly-once transcript completion, usage consistency, native browser
  reconnect, legacy jvagent behavior, and `make verify` against the recorded
  full-suite baseline.
