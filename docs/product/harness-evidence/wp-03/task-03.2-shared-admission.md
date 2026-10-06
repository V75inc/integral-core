# WP-03.2 Evidence — shared native-turn admission reservations

WP-03.1 submission now reserves native-turn admission in the same PostgreSQL transaction as its message, WorkItem and initial outbox fact. A rooted `ChatThread.active_work_item_id` pointer is installed by conditional update. Principal concurrency permits are `HarnessTurnAdmissionSlot` Objects with stable hashed IDs and typed scalar attribution; they contain no message, credential or prompt content. Both thread and principal conflicts return typed `ResourceConflictError` details (`thread_busy` / `user_turn_limit`) and roll back the candidate WorkItem and outbox.

An exact WorkItem retry reuses its existing reservation. Distinct requests on an active thread conflict. Distinct threads for the same principal contend on shared permit slots, including simultaneous transactions at a configured one-slot limit. Slot and thread-pointer release requires a terminal WorkItem, is idempotent, and uses compare-and-set against the exact WorkItem ID so an old completion cannot clear a newer turn. A terminal stale holder can be reclaimed by a later admission. Until WP-03.3 connects execution recovery and release, queued/running reservations are intentionally not reclaimed based only on a missing HTTP process.

Validation environment: Python 3.14.3, jvspatial 0.1.1, isolated PostgreSQL 14.17. Focused command:

```text
cd backend && INTEGRAL_TEST_DB=postgres JVSPATIAL_POSTGRES_DSN=postgresql://integral:integral@127.0.0.1:5433/postgres .venv/bin/python -m pytest tests/native_harness/wp_03 tests/contract/test_chat_turn_submission_postgres.py tests/test_turn_admission_limits.py tests/test_work_items.py tests/test_work_outbox.py -o addopts='' --strict-markers -q
```

Result: **39 passed**. Coverage includes same-thread and shared-principal transaction races, exact retry reuse, prompt-free WorkItem, tenant denial, graph reachability, terminal-only release, stale release protection, and rollback of slot/pointer/message/WorkItem/outbox. Tests use concurrent PostgreSQL transactions in one process; WP-03.3 still owns independent-process contention and worker-death recovery proof. jvspatial emitted 25 existing Walker deprecation warnings.

`make verify` passed all 16 substrate guards, pre-commit black/isort/flake8/mypy, pinned format checks, ESLint with its existing warnings, reproducible wheel/import validation, CI-faithful backend smoke, and all **1,304 frontend tests**. The full backend suite failed the same three existing unrelated tests: `test_declared_asset_query_matches_dashboard_http_resident_and_mcp`, `test_mcp_declared_app_query_matches_authenticated_http_query`, and `test_stage_update_entry_prefers_existing_profile_status`. No WP-03.2 test appears in that failure list. The full-suite failure prevents a commit under the repository's commit gate.
