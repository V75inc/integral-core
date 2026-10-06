# WP-03.3.3 — Stable assistant transcript write

Date: 2026-10-04
Status: transcript persistence primitive implemented and PostgreSQL-verified;
WP-03.3.3 remains in progress. Worker dispatch is still disabled.

## Implemented

- Added `persist_work_item_assistant_result`, which derives the accepted user
  message from the claimed WorkItem, verifies principal/workspace/thread and
  graph containment, then writes one deterministic assistant `ChatMessage`
  while holding the WorkItem effect fence.
- Repeated identical writes return the same assistant node. Different output
  under the same WorkItem identity fails with `work.idempotency_conflict`.
- Transcript projection accepts only public text, tool-call summary, source,
  and error parts; it rejects reasoning parts and unapproved provider payload
  or metadata fields. Model, usage, and timing metadata are allowlisted and
  bounded by the transcript size limit.
- Added a PostgreSQL contract for fenced idempotency, parent linkage, and
  divergent-result rejection, plus a non-PostgreSQL projection test.
- Fixed `touch_last_message` to reuse jvspatial's task-local transaction when
  present. Previously `append_message` opened a nested transaction and waited
  on the thread row already locked by submission/fenced transcript writes.
- Added the missing `chat_turn.cancel` ChangeEvent and matching policy action;
  cancellation audit records capture status only and omit lease credentials.

## Verification

- Black, isort, flake8 for the new service, projection test, and `git diff
  --check`: passed.
- Projection test: passed.
- PostgreSQL submission, event, WorkItem fence, and stable transcript
  regressions: **36 passed** with `INTEGRAL_TEST_DB=postgres`.
- Route cancellation, route ownership, audit emission, and policy-action
  regression tests: **54 passed**.
- `make verify`: guards, pre-commit checks, pinned formatting/lint, backend
  mypy, reproducible wheel/import, CI-faithful smoke, and all 1,304 frontend
  tests passed. The full backend suite still fails the same three recorded
  non-harness tests: the two workspace-vs-App MCP applied-scope assertions and
  the direct staging test without a bound principal.
- A direct file-target mypy invocation is inconclusive because installed
  mypy stops while parsing the environment's NumPy stubs. The authoritative
  repository mypy hook in `make verify` passed.

## Remaining acceptance work

- Bind assistant write and event replay together in the worker stream and
  demonstrate stale-fence rejection and crash recovery.
- Integrate the primitive into the worker stream and prove terminal admission
  release, usage reconciliation, reconnect, two-process contention, browser
  recovery, jvagent regression, and the required repository gates before
  enabling `chat_turn` dispatch.
