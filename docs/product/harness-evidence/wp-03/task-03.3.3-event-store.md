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
  messages. Agent-facing worker text applies the same host-marker
  sanitization as the live chat path while the canonical user transcript keeps
  the exact authored text. Legacy email is not copied into the WorkItem or
  required by the native provider.
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
  This test also proves a user-authored `[SYSTEM:...]` marker is neutralized
  before model dispatch without changing the persisted user message.
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
- Recovery was additionally exercised against encrypted PostgreSQL model
  request records for both `dispatch_intent` without a terminal observation
  and `dispatch_intent` followed by `outcome_unknown`. Both cases fail closed
  with `harness_model_request_unsettled` before a checkpoint can load or a new
  model call can begin. These tests validate recovery from the persisted
  uncertainty states; they are not process-kill injections after dispatch.
- A spawned PostgreSQL worker process now commits the same encrypted observer
  states and exits with `os._exit` before normal async cleanup. The parent
  process reads the records and verifies recovery raises
  `harness_model_request_unsettled` before loading a checkpoint. Both the
  dispatch-intent-only and interrupted-stream (`outcome_unknown`) variants
  passed as part of the focused worker file (**12 passed**). This proves the
  persisted-state/process boundary; the child uses a synthetic observer and
  does not issue a LiteLLM/provider request.
- On 2026-10-05 the process-death probe was strengthened to drive the production
  `LiteLLMSDKTransport` through an `httpx.Request`, with a fake in-process SDK
  completion boundary. One child exits from that boundary immediately after
  dispatch intent is durably observed; another consumes a partial streamed
  chunk and exits before response settlement. A marker written by the fake
  completion proves the dispatch boundary was entered exactly once. In both
  cases, the parent reads the encrypted dispatch intent and proves
  `harness_model_request_unsettled` blocks checkpoint loading/replay. The
  PostgreSQL test passed in both crash positions (**2 passed**). It verifies
  production bridge ordering and process-death handling without contacting or
  charging an external provider; live upstream crash behavior remains unproven.
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
- Reconcile the bridge-bound process-death evidence with WP-09 review, and
  qualify behavior against any provider-specific stream semantics needed for
  release. The crash probe uses a fake SDK completion boundary, so external
  provider behavior remains unproven and no live paid-route evidence is
  claimed.
- Prove cancellation, exactly-once transcript completion, usage consistency,
  native browser WorkItem reconnect, legacy jvagent behavior, and `make verify`
  against the recorded full-suite baseline.
