# WP-03.3.1 — Encrypted native turn-input capsules

**Status:** in progress.

**Objective:** define and persist a bounded, versioned, encrypted native-turn context capsule alongside the durable chat WorkItem, while retaining the canonical user prompt and image bytes only in the existing permissioned ChatMessage. Add an opaque client request ID to the existing browser/API contract so later worker integration can safely reuse it after transport retries.

**Dependencies:** WP-03.1 submission idempotency and WP-03.2 shared admission; WP-06 native provider and browser exact-answer smoke. This task does not enable the native WorkItem producer or dispatch `chat_turn` work.

**Owned files:**

- `backend/app/models/harness_records.py`
- `backend/app/agentive/harness/turn_input.py` (new)
- `backend/app/schemas/agentive/work.py`
- `backend/app/schemas/api/ai_chat.py`
- `backend/app/services/chat_turn_submissions.py`
- `backend/app/api/ai_chat.py`
- `backend/app/agentive/harness/key_rotation.py`
- `backend/app/services/harness_sessions.py`
- `backend/app/agentive/services/work_lifecycle.py`
- `frontend/src/features/ai-chat/providers/JvAgentProvider.ts`
- relevant focused tests under `backend/tests/native_harness/wp_03/`, `backend/tests/contract/`, and frontend AI chat provider tests
- this brief, `ledger.yaml`, and the evidence report

Preserve all existing edits. Do not enable WorkItem worker dispatch, change provider defaults, remove the jvagent path, or alter frontend UI behavior.

**Frozen input contract:**

1. `client_request_id` is a bounded opaque token created once per user send and reused only when the same HTTP submission is replayed after authentication refresh. It is optional for old clients during this migration.
2. The accepted ChatMessage remains the sole source for the user's prompt, uploaded image bytes, and attachment references. The capsule contains no prompt text, image bytes/base64, bearer credentials, provider keys, browser callbacks, user email, or monotonic process values.
3. The strict capsule schema contains server-authorized, bounded fields needed to rebuild a native turn: schema version; tenant/principal/workspace/thread; work item and accepted message references; client request ID; run ID; focus IDs; sanitized system-context blocks; and allowlisted host `extra_data` fields required by the native adapter (agent ID, entity references, lightweight page context, pending approval context and marker). Unknown fields fail closed.
4. Persist encrypted JSON using AES-GCM and Object ID as AAD. The Object is scoped by a canonical hashed `(workspace, principal, thread)` namespace, with encrypted tenant/principal/workspace/thread references inside the payload. WorkItem `input_payload` may contain only the accepted message ID, the context digest/fingerprint, and opaque capsule Object ID plus digest; it never receives cleartext context.
5. Capsule, ChatMessage, WorkItem, outbox fact, and admission reservation are committed in the same PostgreSQL transaction. A deterministic retry with identical inputs returns the same capsule; any changed context bound to the same request ID conflicts. Loading validates Object scope, ciphertext prefix, digest, schema version, and encrypted identity.
6. Capsule key rotation is resumable and includes the thread-scoped Object type. Hard-delete of a ChatThread deletes its capsules under exact owner/workspace checks. Retention purges only capsules whose WorkItem is terminal or missing and whose age exceeds the configured Harness retention; queued/running work is never purged.
7. Thread-scope key rotation invocation may follow the existing session-scope rotation mechanics, but the record type and canonical thread-scope helper must be implemented and covered now. If shared-key rotation orchestration cannot represent mixed scope kinds without changing its API, record an explicit WP-09 handoff and keep this task's capsule producer opt-in.

**Validation:** focused crypto/schema/idempotency tests, PostgreSQL transactional submission tests, frontend provider auth-replay test proving request ID reuse, focused format/lint, and `make verify`. Do not enable native durable admission unless the subsequent WP-03.3.2 and 03.3.3 fences, worker dispatch, and replay contracts are accepted.

**Stop conditions:** plaintext prompt or inline image bytes appear in capsule or WorkItem; capsule encryption or scope validation can be bypassed; retry identity can bind changed context; hard-delete/retention can remove queued or running input; or transaction atomicity cannot be demonstrated on PostgreSQL.

**Rollback:** leave the new producer call sites disabled, remove only this task's unused capsule record and references if the contract fails, and preserve legacy chat behavior and the accepted WP-03.1/03.2 records.
