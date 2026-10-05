# WP-03.3.2 — WorkItem lease fencing progress

Date: 2026-10-04
Status: accepted; native WorkItem producer and worker dispatch remain disabled pending WP-03.3.3.

## Implemented in this slice

- Added `authorized_work_item_effect`, which opens a PostgreSQL graph transaction, checks WorkItem attempt, principal, workspace, lease token/fence, cancellation, lease expiry, and deadline, then holds the WorkItem compare-and-set row lock through the caller's same-transaction writes. It fails closed without a transaction-capable database.
- Added optional WorkItem authority propagation to the native provider composition. When a trusted server-created `work_execution_context` is present, model request observations, tool calls, step events, snapshots, run registrations, tool-effect records, checkpoint manifests, and checkpoint pointer advancement use the fence. The ordinary live provider path supplies no WorkItem authority and retains its current behavior.
- Updated the worker lease supervisor so heartbeat errors and durable cancellation cancel the active handler task. Heartbeats still use no more than one third of the configured lease duration. `PydanticAIProvider` cancels its Pydantic AI token when its task is cancelled.
- Made `WorkExecutionContext` a frozen, strict Pydantic contract with canonical non-empty authority values and positive attempt/fence numbers.
- Heartbeat renewal now fails on durable cancellation, an expired lease, or an elapsed WorkItem deadline. The cancellation check is also part of the compare-and-set predicate to close the read/write race.

## Verification

- `tests/native_harness/wp_03/test_work_item_effect_fence.py`: 6 tests passed with PostgreSQL enabled. They cover immutable authority validation, same-transaction commit/rollback, reclaimed-fence rejection, durable cancellation rejection, and the actual encrypted `JvSpatialStepStore` event boundary.
- `tests/test_work_worker.py`, `tests/test_work_item_leases.py`, and `tests/test_work_recovery.py`: passed, including active-handler cancellation on heartbeat loss and durable cancellation.
- Native provider stream, broker tools, runtime factory, model-observation, and checkpoint-manifest regression tests passed.
- `make verify` after the frozen-context contract change: substrate guards (16), pre-commit checks, backend mypy, pinned formatting, frontend lint (0 errors; 394 existing warnings), type check, reproducible wheel/import (SHA-256 `9cdf32a28ef6cdf4d9e83c80cf27754858d1de0dae1982f7c951f6d1f5a063ab`), CI-faithful backend smoke, and all 1,304 frontend tests passed. The full backend stage failed the same three previously recorded non-harness tests: two MCP applied-scope mismatches and the direct stager test with no bound principal.
- `git diff --check`: passed.

## Remaining acceptance work

- Rerun `make verify` after the stream-translator fix and compare the full-suite failures against the recorded baseline.
- Confirm the WorkItem producer constructs and supplies authority only from the claimed row; producer/worker replay wiring belongs to WP-03.3.3 and remains disabled.
- The end-to-end worker path still needs request event replay and browser validation before WP-03.3 can be accepted.

## Regression progress — 2026-10-04

- Added a durable-worker provider stream guard that checks current WorkItem authority before publishing provider-session metadata, each normalized stream event, and message completion. If a WorkItem turn loses its lease, the cancellation token is cancelled and the stale error propagates as an internal typed outcome; it is not converted into an ordinary user-visible provider failure. Non-WorkItem chat keeps the no-op guard path.
- Added regression coverage for suppression of provider events after lease loss, plus PostgreSQL checks that reclaimed attempts cannot complete model-usage observations, persist checkpoint manifests, or enter Core capability broker dispatch/write effect receipts.
- Verification: `tests/native_harness/wp_06/test_provider_stream.py` (9 passed); `tests/native_harness` (all runnable tests passed, 9 PostgreSQL cases skipped in the default run); `tests/native_harness/wp_03/test_work_item_effect_fence.py` on PostgreSQL (10 passed); Black/isort/flake8 passed for the changed Python files.
- Latest host-boundary regression: focused PostgreSQL run of `test_host_stream_fence.py`, `test_work_item_effect_fence.py`, `test_provider_stream.py`, `test_sessions.py`, and `test_chat_stream_interrupted_persist.py` exited successfully with 36 tests. Black and isort checks passed for the seven touched Python files. The host tests verify stale WorkItem output is not yielded or flushed into the assistant transcript, and that successful WorkItem transcript callbacks run inside the fenced effect scope.
- Raw-value diagnosis: a local Gemma response through the LiteLLM adapter arrived as a 207-character `ThinkingPart` followed by a six-character final `TextPart`. The translator suppressed thinking deltas but its generic `PartEndEvent` fallback emitted the complete thinking part as chat text. The raw content was examined in-process and was not copied to logs or evidence. A GPT-4.1-mini control through the same LiteLLM adapter returned only a final text part; the response observer reported 77 input tokens, 2 output tokens, and US$0.000034 provider cost. This standalone diagnostic observer was in-memory and does not qualify persisted Core billing records.
- Fixed `PydanticAIEventTranslator` to discard completed `ThinkingPart`s and only release a fallback part body when it is a `TextPart`. Added a regression that delivers a private thinking part followed by “Hello” and proves only the final answer is emitted.
- Verification after the fix: WP-06 events (10 passed); combined WP-06 plus host stream, WorkItem fence, session, and interrupted-transcript regressions on PostgreSQL (49 passed); Black, isort, and flake8 passed for the translator and test files.
- Latest browser smoke on a fresh native conversation: “Say hello in one word.” returned “Hello.”; a follow-up exact-answer prompt returned the requested sentence verbatim. Both replies showed the UI's token/time indicator (2.1k tokens / 9.6s and 2.1k tokens / 8.6s). The earlier generic safe-normalization error did not recur; its specific rejection reason was not captured, so the current successful retries do not prove that separate rejection path is permanently resolved. The confirmed `ThinkingPart` leak is independently fixed and covered by a regression.
- Latest `make verify` after the translator fix passed repository guards, pre-commit hooks, Black/isort/flake8/mypy, pinned formatting, ESLint (0 errors; existing 394-warning backlog), frontend type checking, reproducible wheel/import (SHA-256 `bc218244260bf95747b454d55078ea6e68f21e3815c5f33560bb9a1404d9dd2e`), CI-faithful backend smoke, and 1,304 frontend tests. The full backend suite failed the same three previously recorded non-harness tests: two MCP applied-scope mismatches and the direct stager test with no bound principal. The 49 focused PostgreSQL regressions and browser checks after the fix passed separately.
- WP-03.3.2 acceptance: focused coverage exercises WorkItem authority across provider output, host persistence, model observations, snapshots, tool effects, and capability dispatch. Lease loss and durable cancellation stop the active handler. Remaining durable producer, worker dispatch, client event replay, and crash recovery are WP-03.3.3 scope.
