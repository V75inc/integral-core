# WP-02.3 — Session lifecycle cleanup and PostgreSQL proof

**Status:** accepted.

**Objective:** erase native Harness-private state whenever an authorized Integral hard-delete removes a native ChatThread, without deleting the Core run and usage records governed by independent audit/accounting retention.

**Owned paths:** `backend/app/services/harness_sessions.py`, `backend/app/services/chat_threads.py`, `backend/app/services/workspace_lifecycle.py`, `backend/app/api/ai_chat.py`, `backend/app/schemas/api/ai_chat.py`, `backend/app/config.py`, `backend/app/agentive/services/work_lifecycle.py`, and `backend/tests/contract/test_harness_sessions_postgres.py`.

**Behavior:** the owner-only `GET /chat/threads/{thread_id}/harness-sessions/{session_id}/export` contract validates thread ownership, workspace access, session ownership, and the typed thread edge before returning typed JSON-compatible session, plan, run, event, checkpoint, tool-effect, and model-request history. A configurable 90-day default policy (bounded 1–3650 days) purges only closed, expired, or revoked sessions after the retention interval. A scope-checked PostgreSQL transaction removes their encrypted run, event, checkpoint, tool-effect, plan, and model-request Objects and rooted HarnessSession nodes. Active and suspended sessions, ChatThread transcripts, and Core AgentRun/RunStep and usage/accounting records remain outside this policy. The shared hard-delete message helper continues to remove all attached Harness state regardless of the thread's current provider field; account and workspace deletion paths call the same helper before deleting the thread.

**Verification:** PostgreSQL contracts assert owner API export shape and cross-principal service denial; valid JSON-compatible run/event/checkpoint export; hard-delete removal of encrypted payload Objects plus session Node/typed edge; and retention removal of an old terminal session while preserving a new active session and its records. Session-generation contention, transaction rollback, encrypted tenant-scope round-trip, and Root reachability remain covered by the same contract module.

**Required invariants:** I-GRAPH-01/02, I-ACCESS-01, I-CHAT-01, I-WORK-01..06, I-HARNESS-01.

**Acceptance:** passed for WP-02.3 and the WP-02 package after the PostgreSQL graph/session, encrypted store, export, retention, transcript, key-rotation and reachability gates passed. This does not establish multi-host production runtime fencing or paid-provider qualification; those remain in their owning packages.
