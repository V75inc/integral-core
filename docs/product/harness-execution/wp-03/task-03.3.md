# WP-03.3 — Leased native execution and live chat integration

**Status:** ready.

**Objective:** connect native chat admission to a durable WorkItem execution lifecycle without changing the existing jvagent path or provider default. A native user request must have one accepted ChatMessage, one WorkItem, shared admission, fenced provider execution, replayable normalized events, accurate terminal state, and recoverable worker-death behavior.

**Dependencies:** WP-03.1 and WP-03.2 implementation/evidence; WP-06 provider adapter and exact-answer browser smoke; WP-09 checkpoint/failure boundaries. Resolve any open WP-09 review finding that changes the restore authority before execution integration.

**First decision gate:** inspect the live `ai_chat.py` context-building path, `ChatTurnContext`, native provider `_prepare`/`stream_turn`, `PydanticAIEventTranslator`, `HarnessEventRecord`, WorkItem claim/recovery APIs, and frontend SSE consumer. Freeze the smallest safe architecture that lets the durable worker rebuild exactly the server-authorized turn context after restart. Do not put user prompt, image bytes, provider credentials, browser authority, or system-context prose directly in WorkItem payload. Persist a versioned encrypted input capsule under the existing thread/session scope and store only its opaque reference and digest on WorkItem. If the existing encrypted store cannot support this contract, extend it with tests before allowing native producer admission.

**Owned files:** concrete ownership is recorded in the child briefs before each edit. Expected areas include `backend/app/api/ai_chat.py`; `backend/app/schemas/api/ai_chat.py`; `backend/app/services/chat_turn_submissions.py`; `backend/app/services/chat_turn_admission.py`; `backend/app/agentive/services/work_worker.py`; `backend/app/agentive/services/work_recovery.py`; `backend/app/services/chat_providers/pydantic_ai_provider.py`; scoped Harness input/event persistence; `frontend/src/features/ai-chat/providers/IntegralNativeProvider.ts`; native provider tests; and browser evidence. Preserve the legacy jvagent provider/runtime compatibility suite.

**Required behaviors:**

1. Frontend creates one opaque request ID per send and reuses it only for transport replay of that send. Backend derives principal, workspace and thread identity from authenticated server state. Retries return the original accepted IDs and never invoke the model twice.
2. API admission uses WP-03.1/03.2 transaction contracts; no raw provider stream begins before the durable WorkItem and admission are committed.
3. Exactly one worker holds the WorkItem lease/fence. Heartbeats renew during long provider calls. Cancellation records durable intent, signals the local Pydantic `CancellationToken`, and prevents subsequent broker effects. A stale attempt cannot append transcript output, checkpoint, event, usage completion or tool effect.
4. Worker dispatch handles `chat_turn` only after its input-capsule reader and effect-boundary fences exist. A restart can resume only from the last safe checkpoint; an unsettled model request, unresolved tool effect or pending approval is reconciled using WP-09 rules, never replayed blindly.
5. Normalized chat events are durably sequenced before they are delivered. The existing response stream can replay from a cursor for the same WorkItem; reconnect or duplicate request cannot duplicate assistant messages or effects. Terminal WorkItem state and admission release are atomic or recoverably reconciled.
6. Event and input records use I-GRAPH-02 Objects with encryption, tenant scope, retention, bounded payload sizes and no bearer secret. UI-visible usage is based on existing Core accounting receipts; do not synthesize token totals.
7. Legacy providers retain current process-local admission/cancellation behavior unless a separately qualified compatibility change is required. `integral_native` remains explicit opt-in; do not switch defaults.

**Execution sequence:**

1. Create child task `03.3.1` for input-capsule schema, encrypted persistence, request-ID frontend/schema plumbing, and producer contract; prove no prompt duplication in WorkItem and exact round-trip after process restart.
2. Create child task `03.3.2` for lease/fence/heartbeat/cancel propagation through the native provider and every brokered effect boundary; add deterministic fake-clock and stale-token tests.
3. Create child task `03.3.3` for worker dispatch, durable event sequencing/replay, terminal-state/admission release, recovery behavior and independent-process crash/contention probes.
4. Create child task `03.3.4` for UI/browser integration, jvagent regression, full repository gates, fresh-context review and bounded repair. Only this child may mark WP-03 accepted.

Do not begin the next child until the prior child has its focused evidence and frozen interface. Update `ledger.yaml` with file ownership, baseline fingerprints, exact commands, outcomes and blockers. Tests must include real PostgreSQL, two independent processes, worker death after claim and during model streaming, cancellation during a pending request, stale effect attempt, duplicate request before/during/after completion, event cursor gap/replay, tenant isolation, session/key rotation as applicable, frontend abort/retry and browser reload.

**Stop conditions:** no recoverable encrypted input capsule; no fence check at each side-effect boundary; model requests that cannot be reconciled after worker death; no durable event replay contract; or any route that can duplicate a paid request/effect under retry. Keep native producer admission disabled until the relevant child acceptance evidence exists.

**Required repository gate:** `make verify` plus the exact WP-03.3 tests. Known baseline full-backend failures are documented in the execution ledger and must be compared by identity. Do not weaken tests, commit a failing state, push, open a PR, deploy, or change provider default without the required evidence/authorization.
