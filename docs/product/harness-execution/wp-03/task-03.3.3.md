# WP-03.3.3 — Durable native chat dispatch, event replay, and recovery

**Status:** in progress.

**Objective:** connect the already-idempotent native chat submission and encrypted input capsule to a fenced `chat_turn` worker. Commit normalized output events before delivery, replay by cursor after transport loss, persist one assistant result, and terminalize the WorkItem and admission exactly once. Resume only from WP-09 safe checkpoints; never repeat an unsettled paid model request or unresolved tool effect.

**Dependencies:** accepted WP-03.3.1 input capsule and WP-03.3.2 WorkItem fence boundaries; WP-06 native provider/event translation; WP-09 recovery authority. Native provider remains explicit opt-in and no default changes are in scope.

**Frozen design constraints:**

1. The worker validates the claimed row and builds `WorkExecutionContext` from that claim only. It loads the capsule by exact principal/workspace/thread/WorkItem scope and digest, reads the canonical accepted `ChatMessage`, and rechecks authenticated workspace access before preparing `ChatTurnContext`.
2. No prompt, image bytes, credentials, or system-context prose is copied into the WorkItem or user-visible status API. The capsule remains encrypted. Native message limitations (images/attachments) are explicit at admission until a secure reconstruction contract exists.
3. Dispatch is for `kind=chat_turn` only and remains unreachable until event sequencing, host transcript writes, cancellation, terminal release, replay authorization, and recovery policy are all in place.
4. Add a dedicated tenant/thread/WorkItem scoped event log. Sequence allocation, append-idempotency, fence check, and append commit atomically. Public replay exposes only normalized user-facing events, never raw Pydantic objects, private reasoning parts, prompts, or credential data. Cursor gaps are explicit and replay resumes from the last committed sequence.
5. A reconnect reads durable events and observes terminal WorkItem state. It does not re-run the model. Duplicate sends with the same client request ID return the accepted receipt and attach to the same event stream.
6. Assistant transcript persistence uses a stable message identity per WorkItem and is fenced. Completion, admission release, and terminal outbox/event state are atomic or recoverably reconciled. A stale attempt cannot append a second assistant message.
7. A worker death before a physical request can rebuild only from the accepted user message and capsule. Once model dispatch intent, tool effect, or approval is unsettled, recovery blocks for reconciliation. Safe complete snapshots may resume under WP-09; no blind paid-request replay.
8. Keep `jvagent` provider behavior, its default selection, and legacy process-local streaming unchanged. Do not change default provider, pricing, or billing semantics.
9. Every accepted durable chat WorkItem has a persisted absolute execution deadline. A provider stream that exceeds it is cancelled, terminalized with a normalized timeout error, and preserves only committed public output; heartbeat renewal must not keep a wedged turn alive indefinitely.

**Owned files and implementation slices:**

1. **Event contract and storage:** `backend/app/services/chat_turn_events.py`, the `HarnessChatEventCursor` / `HarnessChatEventRecord` additions in `backend/app/models/harness_records.py`, and `backend/tests/native_harness/wp_03/` event-store tests. Normalize only the established public chat envelope; reject private reasoning and unapproved fields; prove fenced append idempotency, ordering, cursor replay, and exact-scope reads before any durable event is delivered.
2. **Worker execution and terminalization:** `backend/app/agentive/services/work_worker.py`, narrowly scoped helpers under `backend/app/services/`, and WP-03 PostgreSQL worker/recovery tests. Build context only from the claimed WorkItem and capsule, connect provider streaming to committed event append, stable transcript persistence, and exact-once WorkItem/admission terminal transition. Keep `chat_turn` out of `HANDLED_KINDS` until this slice and the following recovery gates pass.
3. **Authenticated producer and reconnect:** `backend/app/api/ai_chat.py`, request/response schemas under `backend/app/schemas/`, `frontend/src/features/ai-chat/providers/IntegralNativeProvider.ts` and focused provider tests. Make acceptance idempotent; replay authorized committed events after transport loss; cancel through WorkItem authority; never start a second model run for a duplicate request.
4. **Independent evidence:** PostgreSQL contention/crash tests, native browser send/reload/reconnect evidence, legacy jvagent regression, and the required repository gates. Do not refactor unrelated jvagent behavior or unrelated workspace edits.

**Required validation:** PostgreSQL transaction tests; two independent worker processes; crash after claim, before model request, after model dispatch, and during stream; duplicate request before/during/after completion; sequence replay and gap detection; tenant/thread isolation; one assistant message under replay; cancellation and stale-fence rejection; usage receipt consistency; native browser send/reload/reconnect smoke; legacy jvagent regression; `make verify` compared against the three known non-harness backend failures.

**Stop conditions:** input capsule cannot be restored after process restart; event append is not ordered/idempotent/fenced; worker death can duplicate a paid call or tool effect; terminal status and admission leak; tenant can read another scope; raw model reasoning is emitted as assistant text; or browser can display a success without durable transcript and usage evidence.

**Rollback:** keep the producer and dispatch explicitly gated until all acceptance criteria pass. Roll back only WP-03.3.3-owned route/worker/event integration, preserving encrypted capsules, the WorkItem fence, ordinary Integral Native streaming, jvagent compatibility, and unrelated workspace edits.
