# WP-01.2 — Provider-aware cancellation evidence

`chat_streaming._register_cancel_hook` used to import `jvagent_embed.cancel_interact` unconditionally. That made host cancellation depend on jvagent even when another provider was selected. The provider base module now exposes `register_provider_cancel_hook`; it delegates to a provider's optional synchronous `cancel_turn(thread_id=...)` hook and retains an explicit jvagent fallback for legacy adapters. `JvagentProvider.cancel_turn` implements the hook using its existing embedded cancellation API.

The first native Harness composition factory is in `backend/app/agentive/harness/runtime.py`. It builds a fresh Agent with Instrumentation, ToolSearch, Planning, and StepPersistence. Integral must pass already-brokered tools, an explicit Core skill allowlist/source, and a non-null Integral-scoped StepStore. The factory rejects selected skills without a source and rejects a missing StepStore instead of silently allowing Harness's default in-memory storage. The included TestModel contract proves the composition and checkpoint call path. A jvspatial tenant-aware StepStore, production composition caller, skill resolver, or actual cancellation token adapter is not implemented yet, so this factory is not yet enabled for requests.

The host's `InFlightTurn` continues to own the cancellation command and cancellation event. This change does not alter when a turn is cancelled, nor does it prove how a new transport closes its own I/O. A Pydantic provider must implement the method and cancel its active Pydantic stream/model request; cross-worker cancellation remains with WP-03.

Validation on Python 3.14.3 backend environment:

- `.venv/bin/pytest tests/native_harness/wp_00 tests/native_harness/wp_01 tests/test_chat_stream_interrupted_persist.py -q` — 13 passed.
- `.venv/bin/black --check app/services/chat_providers/base.py app/services/chat_streaming.py app/services/chat_providers/jvagent_provider.py tests/native_harness` — passed.
- `.venv/bin/isort --check-only` over those same paths — passed after correcting pre-existing import ordering in the already-modified jvagent adapter.
- `.venv/bin/flake8` over those same paths — passed.

- Runtime factory proof: TestModel streams one response and records one StepPersistence run; negative cases reject a missing store and unscoped selected skills.

No external provider request was made.
