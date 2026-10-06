# WP-03.3.2 — WorkItem lease fencing and native effect boundaries

**Status:** accepted.

**Objective:** make every native provider request and every brokered side effect conditional on the currently held WorkItem lease, monotonic fence, and non-cancelled execution context. Lease loss or durable cancellation must stop the native stream and reject any later transcript, checkpoint, event, accounting, or tool-effect write from the stale attempt.

**Dependencies:** accepted WP-03.1, WP-03.2, WP-03.3.1 input capsule contract, WP-01 execution scope/cancel hook, WP-06 native provider/event adapter, WP-09 checkpoint/failure-boundary rules. Preserve the live provider path; do not enable the native WorkItem producer or worker dispatch in this child.

**Owned source:** `backend/app/agentive/harness/contracts.py`; `backend/app/schemas/agentive/work.py` for the immutable WorkExecutionContext authority contract; `backend/app/agentive/services/work_items.py` only where a public fence assertion is missing; `backend/app/agentive/services/work_worker.py` for lease-monitor cancellation; `backend/app/agentive/harness/broker_tools.py`; `backend/app/agentive/harness/runtime.py`; `backend/app/agentive/harness/scoped_store.py` and `jvspatial_store.py` only where append boundaries need fence checks; `backend/app/agentive/harness/model_observations.py`; `backend/app/agentive/harness/checkpoint_manifests.py`; `backend/app/services/chat_providers/pydantic_ai_provider.py`; and, by this ledger-approved interface adjustment, `backend/app/services/chat_streaming.py` for the host's assistant-transcript, run-event, and terminal persistence callbacks plus `backend/app/services/harness_sessions.py` for atomic session activation and run claims. Add new/updated tests under `backend/tests/native_harness/wp_03/` and targeted WP-01/WP-06 regressions; update this task brief, ledger entry, and evidence report.

Preserve all pre-existing edits. The execution ledger records the host stream and Harness session boundaries needed to keep WorkItem authority across provider execution and persistence. WorkItem-backed turns skip the redundant `ChatThread.provider_session_id` cache write; `HarnessSession` remains the native continuity authority. Do not modify frontend, `ai_chat.py`, WorkItem dispatch, or provider defaults here.

**Frozen execution authority:**

1. Build one immutable native execution authority from the claimed WorkItem: work item ID, attempt, run ID, principal, workspace, thread, lease token, monotonically increasing lease fence, deadline, and cancellation state. Validate every field against the durable row before use.
2. Renew the lease on a heartbeat interval no greater than one third of the configured lease duration. A heartbeat failure or changed token/fence immediately cancels the local Pydantic AI `CancellationToken` and the worker handler; no provider event after lease loss may be committed or delivered as authoritative output.
3. Poll or subscribe to durable cancellation while a long model call is active. Durable cancellation must cancel the in-flight model request when supported, end the stream as cancelled, and block completion as succeeded.
4. Add one shared assertion at the exact effect boundary. Every chat transcript append, event sequence append, Harness checkpoint/manifest write, model-request outcome/usage completion, tool-effect transition, and brokered capability dispatch must validate the current lease token/fence and cancellation state atomically with that write or immediately inside its transaction. A preflight-only check before model execution is insufficient.
5. A stale attempt may record a content-free stale-attempt diagnostic only if that append is itself fenced or the event is deliberately outside the stale attempt's authority. It must not overwrite or advance newer state.
6. Cancellation and lease failure use typed internal outcomes. No raw lease token, cancellation secret, prompt, provider credential, or private model/reasoning body enters user-visible diagnostics.
7. Ordinary live Integral Native chat (without WorkItem authority) retains the current behavior. The new execution authority is mandatory only for future durable-worker turns; no compatibility behavior is silently changed.

**Required tests:** deterministic fake clock for lease renewal/expiry; lease loss during a pending provider stream cancels the provider and rejects late output; heartbeat loss cancels the active worker handler; durable cancellation cancels the local token; old token/fence rejected after a newer claim; stale attempt rejected at each independent side-effect boundary (transcript, event, checkpoint, model/usage observation, effect record, capability dispatch); deadline expiry; no secret values in normalized errors. PostgreSQL tests must demonstrate atomic check-and-write where the storage boundary supports one transaction.

**Validation:** focused WP-03.3.2 plus adjacent WP-01/WP-06 regressions on PostgreSQL; Black/isort/flake8/mypy; `make verify` and compare full-suite failures to the current ledger baseline. Keep native admission disabled until WP-03.3.3 worker recovery and replay are accepted.

**Stop conditions:** any persisted or delivered late output after fence loss; any brokered effect can execute after cancellation; a side-effect boundary cannot prove current lease authority; or cancellation/heartbeat failure can be interpreted as success.

**Rollback:** keep this guard behind the unenabled durable-worker call path. Revert only the child-owned fence/cancel wiring if any boundary cannot validate atomically; preserve WP-03.3.1 capsule contract, current live provider path, and all unrelated working-tree changes.
