# WP-03.1 — Idempotent chat-turn submission record

**Status:** accepted.

**Objective:** persist one owner-scoped user message, its durable `WorkItem`, and the initial outbox fact as a single idempotent PostgreSQL submission unit that a later worker can safely consume.

**Baseline:** branch `feat/pydantic-ai-harness-v1`; HEAD `b0934bd5d39e7b07d714cb9c8d68ff26835c61b8`; current porcelain-status fingerprint `f8a6459b8b1a24d905a2b228f52bc85b40d8af9faed0a6c4436c09e019fd76f3`. Preserve all existing changes. Harness pins remain `pydantic-ai-harness==0.30.0`, `pydantic-ai==2.54.0`, `litellm==1.101.4` per the execution ledger.

**Dependency receipts:** WP-01 accepted (`task-01.1-execution-scope.md`, `task-01.2-cancel-adapter.md`, `task-01.3-model-request-contracts.md`); WP-02 accepted (`task-02.1-session-contract.md`, `task-02.2-step-store.md`, `task-02.3-session-cleanup.md`); work-kernel enqueue/outbox PostgreSQL transaction contract in `backend/app/agentive/services/work_outbox.py` and `backend/app/agentive/services/work_items.py`.

**Read first:** root `AGENTS.md`; `backend/app/agentive/AGENTS.md`; `docs/INVARIANTS.md` sections I-GRAPH-01/02, I-WORK-01..06, I-CHAT-01, I-CHAT-PAR-01..05, I-HARNESS-01; plan §§6.2–6.5, 7, 10; `backend/app/api/ai_chat.py` `send_message` / `_start_user_turn`; `backend/app/services/chat_threads.py` `append_message`; `backend/app/agentive/services/work_outbox.py` enqueue transaction; `backend/app/agentive/services/work_items.py`; `backend/app/agentive/work_models.py`; `backend/app/schemas/agentive/work.py`; `frontend/src/features/ai-chat/useAIChatRuntime.ts`; `frontend/src/features/ai-chat/providers/types.ts`; and the native provider adapter.

**Owned files:** `backend/app/services/chat_turn_submissions.py` (new); `backend/app/services/chat_threads.py`; `backend/app/models/nodes.py`; `backend/app/agentive/work_models.py`; `backend/app/schemas/agentive/work.py`; `backend/app/agentive/services/work_outbox.py`; `backend/tests/native_harness/wp_03/test_submission_idempotency.py` (new); `backend/tests/contract/test_chat_turn_submission_postgres.py` (new). Do not edit `backend/app/api/ai_chat.py`, frontend provider/runtime, `backend/app/main.py`, `backend/app/config.py`, or `backend/uv.lock` in this slice; send a concrete consumer/interface request if the frozen submission contract requires them.

**Contract to produce:** a Core-only submission service accepts authenticated principal/workspace/thread identity, a validated client request ID, a canonical request fingerprint, and user-message parts. It returns the same accepted `ChatMessage` reference and `WorkItem` identity for an identical retry; reuse of that request ID with a different fingerprint fails with a typed conflict. The durable job carries only opaque references and attribution, not a duplicate prompt, credential, or tenant selector. The request ID namespace includes principal, workspace, and thread. The service remains unwired to live chat; WP-03.2 adds durable admission reservations and WP-03.3 owns the worker dispatcher and API producer integration.

**Required transaction:** under `postgres_graph_transaction`, use the deterministic WorkItem identity as the PostgreSQL concurrency arbiter first, with a payload containing only the accepted message reference and fingerprint; then resolve-or-create the deterministic accepted `ChatMessage` with its `ChatThread —CONTAINS→ ChatMessage` edge and thread activity update. The same transaction commits the initial `WorkOutboxEntry`. This ordering makes concurrent retries serialize on the durable idempotency key before either can create a transcript Node. A duplicate request must return the already accepted message/work pair without a second message, edge, WorkItem, or outbox fact. PostgreSQL production mode fails closed without transaction/CAS support. Do not claim graph+queue atomicity on development stores unless the implementation adds and tests a recoverable submission-intent protocol.

**Steps:**

1. Reconfirm the live Node creation API supports caller-supplied deterministic IDs and that graph writes bind to the yielded PostgreSQL transaction; add the smallest probe before designing a substitute API.
2. Freeze the request identity/fingerprint rules and typed service result/error contract in Core schemas/models.
3. Implement transactional resolve-or-create semantics for the message Node and edge; pass the exact transaction into WorkItem+outbox enqueue.
4. Add PostgreSQL fault injection at each persistence boundary and test exact rollback/retry behavior.
5. Verify identical retries return one accepted message and work item; changed payload under the same scoped request ID conflicts; foreign principal/workspace/thread cannot reuse or inspect the accepted pair.
6. Run WP-03.1 tests, relevant existing work-kernel/chat tests, repository guards and required format/lint checks; record exact environment and results. Do not wire a live producer or modify the default harness in this slice.

**Checks:**

- `cd backend && INTEGRAL_TEST_DB=postgres JVSPATIAL_POSTGRES_DSN=postgresql://integral:integral@127.0.0.1:5433/postgres .venv/bin/python -m pytest tests/native_harness/wp_03/test_submission_idempotency.py tests/contract/test_chat_turn_submission_postgres.py tests/test_work_items.py tests/test_work_outbox.py -o addopts='' --strict-markers -q`
- Run `black`, `isort`, and `flake8` on owned Python files; run the staged repository guards after changes are staged and before any commit.
- Assert Root reaches the accepted `ChatMessage`; assert same-request replay creates exactly one message and `CONTAINS` edge, one `WorkItem`, and one initial outbox record; assert changed-fingerprint replay conflicts; assert injected failures roll back every write.

**Evidence:** `docs/product/harness-evidence/wp-03/task-03.1-submission-idempotency.md`.

**Invariants:** preserve I-GRAPH-01 (message Node and edge are rooted in the same transaction), I-GRAPH-02 (WorkItem/WorkOutbox remain Objects), I-WORK-01..06 (idempotency, transactional outbox, tenant attribution, lease/fence authority), I-CHAT-01 (user-visible transcript remains canonical), I-CHAT-PAR-01..05 (legacy provider and parallel-stream compatibility), and I-HARNESS-01 (thread/session scope). No invariant amendment is proposed.

**Stop conditions:** deterministic Node IDs unsupported by the pinned jvspatial API; graph Node writes escape the transaction context; Object enqueue cannot consume the same transaction; fingerprint conflicts leak message contents; the proposed request identity cannot distinguish a user retry from a new message; or root-reachability/tenant-isolation assertions fail. Stop and report the exact public API gap rather than patching private framework internals.

**Out of scope:** live API/frontend wiring, native worker execution, shared thread/principal admission, heartbeats/cancellation, stale-worker fencing, model invocation, billing reservation, and changing the provider default. WP-03.2/03.3 consume this accepted contract.

**Rollback:** this slice adds an unused Core submission service and schema contract; remove only its newly owned files or additive unused fields if acceptance fails. Never delete accepted messages, queued work, outbox facts, or unresolved execution state during rollback.
