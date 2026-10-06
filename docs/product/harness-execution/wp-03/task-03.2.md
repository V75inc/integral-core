# WP-03.2 — Shared native-turn admission reservations

**Status:** in progress.

**Objective:** add PostgreSQL-backed per-thread and per-principal admission authority to the idempotent WP-03.1 submission unit. Process-local `chat_turn_registry` remains a transport/cancellation bridge and is never the source of cross-process admission truth.

**Dependencies:** WP-03.1 accepted; plan §§4.1, 6.5, 7, 10; invariants I-WORK-01..06, I-CHAT-PAR-01..05, I-GRAPH-02, I-HARNESS-01.

**Owned files:** `backend/app/models/harness_records.py`; `backend/app/models/nodes.py`; `backend/app/services/chat_turn_submissions.py`; `backend/app/services/chat_turn_admission.py` (new); `backend/tests/native_harness/wp_03/`; `backend/tests/contract/test_chat_turn_submission_postgres.py`; this task brief, ledger entry, and its evidence report. Preserve all pre-existing changes. Do not wire API/frontend or make `chat_turn` worker-claimable in this slice.

**Contract:**

1. The WP-03.1 PostgreSQL transaction acquires one shared per-principal slot and claims the ChatThread's active WorkItem pointer before it commits the accepted message, WorkItem and outbox. All reservations share that transaction; any conflict or persistence error rolls back every write.
2. Identical submission retries return their original WorkItem/message pair and do not consume another admission slot. Reuse of the scoped request ID with changed content continues to return a content-free typed conflict.
3. A distinct WorkItem for a thread with an active accepted/running item fails with `thread_busy`. A principal at `MAX_CONCURRENT_TURNS_PER_USER` fails with `user_turn_limit`. Admission is shared across processes and workspaces through transaction-bound Object CAS, not an in-memory count.
4. Slot and thread-pointer release is conditional on exact WorkItem identity. It is permitted only after the durable WorkItem is terminal. A stale turn cannot clear a newer reservation. Release is idempotent.
5. A reservation held by queued/running/waiting work is not reclaimed solely because an HTTP process or local task disappeared. WP-03.3/WP-09 must connect lease recovery and terminalization before worker/API wiring; until then native live admission remains unused.
6. Coordination records are log/queue-shaped `Object`s, contain no prompt or credentials, and do not participate in graph walks. The ChatThread remains the rooted source of its active pointer.

**Verification:** PostgreSQL tests prove concurrent admissions across independent transactions/processes for (a) the same thread, and (b) distinct threads belonging to the same principal at a configured one-slot limit; retries reserve once; changed-content conflicts reserve nothing new; terminal release clears only its own slot/pointer and supports subsequent admission; stale release cannot affect a different WorkItem; and rollback leaves no slot, pointer, message, WorkItem or outbox. Add a bounded non-Postgres contract test that the service fails closed without public transaction support.

Run the focused WP-03 suite plus existing `tests/test_work_items.py`, `tests/test_work_outbox.py`, and chat admission regressions. Run `make verify`; preserve the known three full-backend baseline failures as distinct evidence unless the current run shows a different set. No commit until all required checks pass; never push without explicit user authorization.

**Handoff:** WP-03.3 owns API idempotency-key plumbing, the native request producer, worker dispatch, lease heartbeat/cancellation/fence integration, event transport compatibility, multi-process contention and worker-death/recovery proof. It must consume the reservation ID from the WP-03.1 receipt and release it only after terminal WorkItem transition.

**Stop conditions:** no public transaction/CAS primitive; any cross-tenant reservation visibility; changed-content or duplicate requests allocating multiple slots; release without terminal authority; a stale release clearing a newer WorkItem; or a process-local fallback being represented as multi-process-safe.
