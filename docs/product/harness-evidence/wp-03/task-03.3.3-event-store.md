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
- Added a worker-input reconstruction boundary that accepts only a claimed
  `chat_turn`, authenticates the capsule against the WorkItem scope and digest,
  verifies the active native thread and accepted user message, rechecks current
  workspace access and graph containment, and currently admits text-only
  messages. Legacy email is not copied into the WorkItem or required by the
  native provider.
- Added a terminalization service that commits the leased WorkItem transition,
  transition outbox fact, active thread pointer release, principal admission
  slot release, and matching AgentRun terminal state in one PostgreSQL
  transaction. Same-outcome retries repair/reconfirm release idempotently;
  conflicting terminal outcomes fail closed.
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
- The worker-input boundary passed its isolated PostgreSQL acceptance test:
  `INTEGRAL_TEST_DB=postgres uv run --frozen pytest
  tests/contract/test_chat_turn_submission_postgres.py::test_claimed_chat_turn_rebuilds_only_its_scoped_text_input -q`.
- Worker-input and terminalization checks passed against PostgreSQL:
  `INTEGRAL_TEST_DB=postgres uv run --frozen pytest
  tests/contract/test_chat_turn_submission_postgres.py::test_terminal_chat_turn_releases_admission_atomically_and_idempotently
  tests/native_harness/wp_03/test_chat_turn_worker_input.py -q` (**4 passed**).
- The fail-closed worker-input unit tests passed (3 tests).
- The focused PostgreSQL worker suite passed (**8 tests**). Separate spawned
  processes prove a single fence-1 winner under simultaneous claims and prove
  that an abrupt worker exit immediately after a committed chat-turn claim
  leaves the WorkItem recoverable under fence 2. This is pre-dispatch claim
  recovery evidence; it does not exercise a provider request or dispatch.
- Focused mypy passed for the changed service and schema with imports skipped;
  this checks local annotations while avoiding repository-wide dependency
  errors from a file-target invocation.
- `git diff --check` passed.

## Remaining acceptance work

- Bind append/replay to the worker stream and persist the assistant transcript
  under one stable WorkItem identity.
- Connect the terminalization primitive to the worker lifecycle only after
  provider stream fencing and transcript/event persistence are integrated.
- Add authenticated accept/replay/cancel routes and reconnect behavior without
  a second provider call.
- Implement terminal WorkItem/outbox/admission reconciliation and WP-09 safe
  recovery policy. Keep worker dispatch and the native WorkItem producer
  disabled until all frozen constraints in the task brief are satisfied.
- Prove abrupt worker death after model dispatch and during provider streaming,
  plus WP-09 reconciliation without replaying an unsettled paid request.
- Prove cancellation, exactly-once transcript completion, usage consistency,
  native browser WorkItem reconnect, legacy jvagent behavior, and `make verify`
  against the recorded full-suite baseline.
