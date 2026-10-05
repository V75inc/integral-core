# WP-06.4 — Session and staging integration evidence

## Implemented contract

The host `AgentRun.run_id` is required by the provider and verified against its
thread, user, workspace, provider, and running status before the Harness is
constructed. The Core session is namespaced by workspace, principal, thread,
permission revision, and capability/profile revision. That same session ID is
passed to brokered tool calls and emitted as `_meta` before any assistant or
tool event. `chat_streaming` persists provider metadata immediately, while the
existing stager receives the broker's server-bound session ID and resolves the
owning thread from `ChatThread.provider_session_id`.

Resumption refuses unresolved prior tool effects. The provider now also cleans
up its temporary projected Agent Skills library when checkpoint/effect
validation fails before streaming begins.

## Offline verification

- `tests/native_harness/wp_06/test_provider_stream.py` exercises real Pydantic
  `TestModel` stream events, stable native session metadata, checkpoint
  creation, and next-turn history restoration.
- `tests/native_harness/wp_06/test_broker_tools.py` asserts server-bound run,
  session, principal, and workspace identities on broker invocation.
- `tests/test_chat_stream_interrupted_persist.py` covers host chat persistence
  on interrupted streams.
- Frontend provider selection checks backend availability; if an already saved
  native selection is unavailable, chat runtime falls back to the configured
  embedded provider. `AgentsSection.test.tsx` covers visible/disabled option
  behavior.
- Frontend verification: `npm run lint:types`, eslint on changed provider/UI
  files, and the AgentsSection plus AI-chat admission/workspace-switch tests —
  25 tests passed.
- `tests/native_harness` passes without external model requests.
- The native-harness, Agent Skills loader, Asset Register artifact, and
  reference-App focused regression set passes.

## PostgreSQL verification

Created an isolated local PostgreSQL cluster at port 5433 for this qualification
(the repository's configured `/tmp:5432` endpoint was unavailable). Ran
`INTEGRAL_TEST_DB=postgres INTEGRAL_TEST_POSTGRES_DSN=postgresql://integral:integral@127.0.0.1:5433/postgres backend/.venv/bin/pytest backend/tests/contract/test_harness_sessions_postgres.py -q` — all three probes passed:

1. Session pointer CAS, rooted `HarnessSession` node, and typed edge commit
   together.
2. An injected edge failure rolls back the pointer and session node.
3. Encrypted StepStore Object records round-trip in PostgreSQL and are not
   readable through a different principal scope.

The cluster does not have the `vector` extension; these tests do not use it.

## Remaining limits

Concurrent cross-process generation races, approval creation/resume after
process restart, duplicate decision delivery, session export/deletion, checkpoint
retention, browser Prompt Sheet behavior, and paid/live provider routing remain
unqualified. The full JSON-backed backend suite completed with three failures:
two existing MCP query `applied_scope` mismatches and
`test_stage_update_entry_prefers_existing_profile_status` raising because its
direct stager test has no bound principal. These are outside the native harness
changes and are preserved as separate Core baseline issues. Keep
`integral_native` opt-in and non-default until the remaining release gates are
satisfied.
