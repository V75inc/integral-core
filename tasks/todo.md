# Tasks: WhatsApp Personal Channel — Phase 1 Chat

Spec: `docs/ideas/SPEC-whatsapp-personal-channel-chat.md`
Plan: `tasks/plan.md`

## Task 1: Encrypted WhatsApp channel binding node

**Description:** Persist one user-owned jvconnect binding for the personal workspace. Secrets are encrypted before they are stored. Creating the node wires `User —HAS_WHATSAPP_CHANNEL_BINDING→ WhatsAppChannelBinding` in the same function.

**Acceptance criteria:**
- [x] `WhatsAppChannelBinding` stores base URL, personal `workspace_id`, `phone_number_id`, display phone, `enabled`, and a non-secret last-error slot
- [x] `api_key` and `webhook_secret` round-trip through `encrypt_secret_for_storage` with AAD = binding id, and a mismatched AAD does not decrypt
- [x] Create rolls back the node if the structural edge fails, and a second binding for the same user is rejected
- [x] Disable clears `enabled` without deleting ciphertext

**Verification:**
- [x] Tests pass: `cd backend && uv run pytest tests/test_whatsapp_channel_binding.py -v`
- [x] Build succeeds: node type is passed to `server.add_node_type` and the edge class is imported at boot
- [x] Manual check: a created binding’s exported fields do not contain a `jvk_` or `jvs_` secret

**Dependencies:** None

**Files likely touched:**
- `backend/app/agentive/nodes.py`
- `backend/app/agentive/edges.py`
- `backend/app/main.py`
- `backend/app/agentive/services/whatsapp_channel_binding.py`
- `backend/tests/test_whatsapp_channel_binding.py`

**Estimated scope:** Medium: 5 files

## Task 2: Authenticated binding API

**Description:** Let the signed-in user save, read, and disable their binding. The read model returns status and an API-key last-4 only. Workspace scope is the personal workspace, including when the request scope header names an org workspace.

**Acceptance criteria:**
- [x] `PUT` validates a `https` jvconnect base URL and a `jvk_` API key, encrypts the key, and leaves `enabled` false until webhook registration succeeds in Task 5
- [x] `GET` never returns the full API key or webhook secret
- [x] `DELETE` or an explicit disable is owner-only and returns 404 for another user’s binding
- [x] Save fails with a clear error when credential encryption is unavailable

**Verification:**
- [x] Tests pass: `cd backend && uv run pytest tests/test_whatsapp_channel_binding.py -v`
- [x] Build succeeds: `cd backend && uv run python -c "import app.main"`
- [x] Manual check: response JSON for GET has no `api_key` field

**Dependencies:** Task 1

**Files likely touched:**
- `backend/app/schemas/agentive/whatsapp_channel.py`
- `backend/app/agentive/api/channels.py`
- `backend/tests/test_whatsapp_channel_binding.py`

**Estimated scope:** Small: 3 files

## Checkpoint: After Tasks 1-2

- [x] Binding unit and API tests pass
- [x] Secrets are absent from GET and from logs on the save path
- [ ] Review with human before the webhook is exposed

## Task 3: jvconnect Messaging client

**Description:** Add a thin httpx client that loads the phone bound to an API key, registers the Integral callback, and sends one text message. Map jvconnect’s HTTP errors, including 429, into a result the binding and ingress can store. Do not log the bearer token or webhook secret.

**Acceptance criteria:**
- [x] `GET {base}/api/v1/meta/whatsapp/account` returns `phone_number_id` and display phone
- [x] `POST {base}/api/v1/meta/whatsapp/webhook/register` sends `callback_url` and returns `webhook_secret`
- [x] `POST {base}/api/v1/meta/whatsapp/messages` sends `Authorization: Bearer` and `{"message": {...}}` text
- [x] 4xx/5xx and transport errors return a structured failure and do not raise into the webhook response

**Verification:**
- [x] Tests pass: `cd backend && uv run pytest tests/test_jvconnect_whatsapp_client.py -v`
- [x] Build succeeds: client module imports with httpx only
- [x] Manual check: tests assert the bearer header is set and never appear in captured log text

**Dependencies:** None (client is pure; Task 5 wires it)

**Files likely touched:**
- `backend/app/agentive/clients/jvconnect_whatsapp.py`
- `backend/tests/test_jvconnect_whatsapp_client.py`

**Estimated scope:** Small: 2 files

## Task 4: Signature verification and envelope parse

**Description:** Verify the jvconnect forward signature against the raw body, then parse the Meta envelope into phone number id, sender id, message id, and either text or non-text. This module performs no graph writes.

**Acceptance criteria:**
- [x] A body signed the way jvconnect `signWebhookBody` signs (`sha256=` + HMAC-SHA256) verifies with `hmac.compare_digest`
- [x] A forged, missing, or truncated signature fails closed
- [x] A text message yields sender, phone number id, message id, and text; image, document, audio, and status callbacks yield a non-text or ignore classification
- [x] Parser tests cover an empty `messages` array without throwing

**Verification:**
- [x] Tests pass: `cd backend && uv run pytest tests/test_whatsapp_ingress.py -v`
- [x] Build succeeds: module imports without network or graph clients
- [x] Manual check: failing-signature tests do not call a binding lookup

**Dependencies:** None

**Files likely touched:**
- `backend/app/agentive/services/whatsapp_ingress.py`
- `backend/tests/test_whatsapp_ingress.py`

**Estimated scope:** Small: 2 files

## Task 5: Webhook endpoint, idempotency, and short notices

**Description:** Accept `POST /api/agentive/channels/whatsapp/webhook` with no JWT. After a valid signature, resolve the binding by `phone_number_id`, ignore duplicate Meta ids, and send the text-only or link-your-number notice when policy allows. Register the webhook when a binding is saved and `PUBLIC_API_BASE_URL` is set.

**Acceptance criteria:**
- [x] Invalid signatures return 401 and create no receipt, binding update, or chat message
- [x] A repeated Meta message id returns 200 and does not send a second notice
- [x] Non-text inbound stores a receipt and sends “Text only for now” when the binding can send; it does not enqueue a harness turn
- [x] An unknown or unverified sender does not enqueue a harness turn
- [x] Saving a binding with a reachable jvconnect (mocked) stores the encrypted webhook secret and sets `enabled` only after register succeeds
- [x] `WhatsAppInboundReceipt` indexes are ensured at boot the same way `OperationIdempotencyRecord` is

**Verification:**
- [x] Tests pass: `cd backend && uv run pytest tests/test_whatsapp_ingress.py tests/test_whatsapp_channel_binding.py -v`
- [x] Build succeeds: `cd backend && uv run python -c "import app.main"`
- [x] Manual check: endpoint is `auth=False` and the test client sends no bearer token

**Dependencies:** Tasks 2, 3, 4

**Files likely touched:**
- `backend/app/models/whatsapp_inbound.py`
- `backend/app/main.py`
- `backend/app/config.py`
- `backend/app/agentive/api/channels.py`
- `backend/app/agentive/services/whatsapp_ingress.py`

**Estimated scope:** Medium: 5 files

## Checkpoint: After Tasks 3-5

- [x] Forged signature test shows no graph writes
- [x] Valid text is not yet required to run the harness (Task 6)
- [x] Application imports cleanly

## Task 6: Dedicated thread, approval short-circuit, and turn enqueue

**Description:** Map a verified sender to one native `ChatThread` in the binding’s personal workspace and admit the inbound text through `submit_chat_turn`. Short approval replies reuse the text-approve decision path and skip the harness.

**Acceptance criteria:**
- [x] The thread is created with `provider_id="integral_native"`, `workspace_id` from the binding, and `provider_session_id="wa:{e164}"`, and a second inbound reuses it
- [x] Agent id follows the same preference-then-catalog resolution as `POST /chat/threads`
- [x] A single parsed approval calls `decide_staged_write` with `source="channel"` and does not call `submit_chat_turn`
- [x] Other text calls `submit_chat_turn` with `client_request_id` derived from the Meta id hash, then the webhook returns 200 without waiting for the model
- [x] The harness is not invoked when the binding is disabled or the identity is unverified

**Verification:**
- [x] Tests pass: `cd backend && uv run pytest tests/test_whatsapp_chat_bridge.py tests/test_whatsapp_ingress.py -v`
- [x] Build succeeds: bridge imports `submit_chat_turn` and does not import jvagent
- [x] Manual check: test asserts one `submit_chat_turn` call and a 200 response before any mocked model runs

**Dependencies:** Task 5

**Files likely touched:**
- `backend/app/agentive/services/whatsapp_chat_bridge.py`
- `backend/app/agentive/services/whatsapp_ingress.py`
- `backend/tests/test_whatsapp_chat_bridge.py`

**Estimated scope:** Medium: 3 files

## Task 7: Terminal reply delivery

**Description:** After a native chat turn reaches a terminal state, send the committed assistant text to the WhatsApp sender when the thread session id is a WhatsApp session. Delivery failure stays on the inbound receipt.

**Acceptance criteria:**
- [x] `_execute_claimed_work` calls the delivery helper only after `_handle_chat_turn` returns
- [x] Threads whose `provider_session_id` does not start with `wa:` make no jvconnect call
- [x] One succeeded turn produces one text send of the terminal assistant text, with no partial tokens
- [x] A send failure leaves the WorkItem status unchanged, records the error on the receipt, and does not send a follow-up WhatsApp error message
- [x] An approval confirmation from Task 6 uses the same send helper

**Verification:**
- [x] Tests pass: `cd backend && uv run pytest tests/test_whatsapp_chat_bridge.py tests/test_ai_chat_work_routes.py -v`
- [x] Build succeeds: `cd backend && uv run python -c "import app.agentive.services.work_worker"`
- [x] Manual check: a non-WhatsApp fixture in the delivery test records zero client calls

**Dependencies:** Tasks 3 and 6

**Files likely touched:**
- `backend/app/agentive/services/work_worker.py`
- `backend/app/agentive/services/whatsapp_chat_bridge.py`
- `backend/tests/test_whatsapp_chat_bridge.py`

**Estimated scope:** Medium: 3 files

## Checkpoint: After Tasks 6-7

- [x] Mocked inbound text enqueues one turn and sends one reply
- [x] Existing chat-turn route tests pass
- [ ] Review with human before Settings UI

## Task 8: WhatsApp channel settings

**Description:** Extend Notifications → WhatsApp with a “WhatsApp channel” section for base URL, API key, enable/disable, and status. Keep phone OTP where it is. Do not add a Connectors entry.

**Acceptance criteria:**
- [x] The section renders under the existing WhatsApp settings card and uses the current button, text, and card primitives
- [x] Save posts the key once; a later GET shows last-4 and webhook status, and the key field is empty
- [x] A failed register or a missing public URL shows the server error on the card
- [x] Disable calls the binding disable API and updates status without removing the linked phone
- [x] The section stays hidden until agentive is enabled, matching the current card

**Verification:**
- [x] Tests pass: `cd frontend && npm run test:run -- src/components/settings/__tests__/WhatsAppSettings.test.tsx`
- [ ] Build succeeds: `cd frontend && npm run lint:types` (fails in `DocumentTemplateEditor.tsx`, outside this change)
- [x] Manual check: existing OTP tests still pass, and no settings route under Connectors mentions this form

**Dependencies:** Task 2 (status fields from Task 5 should be visible before calling this done)

**Files likely touched:**
- `frontend/src/components/settings/WhatsAppSettings.tsx`
- `frontend/src/components/settings/__tests__/WhatsAppSettings.test.tsx`
- `frontend/src/api/agentive.ts`

**Estimated scope:** Medium: 3 files

## Task 9: Public URL docs and spec status

**Description:** Document `PUBLIC_API_BASE_URL` as the API origin jvconnect must reach, and record that Phase 1 chat is specified by the approved spec plus this plan’s provider-id decision.

**Acceptance criteria:**
- [x] `.env.example` and `backend/app/config.py` describe `PUBLIC_API_BASE_URL` with an empty default
- [x] The ops or platform note states the callback path `/api/agentive/channels/whatsapp/webhook` and that the first successful jvconnect register locks the callback host
- [x] The spec header says approved, and it records that the dedicated thread uses `provider_id=integral_native` with `provider_session_id=wa:{e164}`

**Verification:**
- [x] Tests pass: no new runtime tests; config import still loads
- [x] Build succeeds: `cd backend && uv run python -c "from app.config import settings; assert hasattr(settings, 'PUBLIC_API_BASE_URL')"`
- [x] Manual check: docs do not tell the operator to put the jvconnect API key in the environment

**Dependencies:** Task 5

**Files likely touched:**
- `.env.example`
- `docs/ideas/SPEC-whatsapp-personal-channel-chat.md`
- `docs/platform/workspace-email.md` only if that page is the current public-origin note; otherwise a short subsection in the existing deploy/config doc that already lists public URLs

**Estimated scope:** Small: 3 files

## Checkpoint: Complete

- [ ] `cd backend && uv run pytest tests/test_whatsapp_channel_binding.py tests/test_whatsapp_ingress.py tests/test_whatsapp_chat_bridge.py tests/test_jvconnect_whatsapp_client.py -v`
- [x] `cd frontend && npm run test:run -- src/components/settings/__tests__/WhatsAppSettings.test.tsx`
- [ ] `cd frontend && npm run lint:types` (pre-existing failure in `DocumentTemplateEditor.tsx`)
- [ ] `make verify-ci` before push
- [ ] Human review
