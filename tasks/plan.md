# Implementation Plan: WhatsApp Personal Channel — Phase 1 Chat

## Overview

A personal-workspace user with a verified WhatsApp `ChannelIdentity` saves their own jvconnect base URL and API key in Notifications → WhatsApp. Integral registers one webhook forward, accepts signed inbound text, runs the existing native chat turn, and sends the terminal assistant text back through jvconnect. Voice, media, templates, org numbers, and notification-Graph migration stay out of this phase. Spec: `docs/ideas/SPEC-whatsapp-personal-channel-chat.md`.

Tasks are in `tasks/todo.md`.

## Architecture Decisions

- **Thread provider stays `integral_native`.** `load_claimed_chat_turn_input`, text-approve, and several harness gates reject any other `ChatThread.provider_id`. The dedicated WhatsApp conversation is a normal native thread whose `provider_session_id` is `wa:{e164}` of the verified sender. In-app chat lists it like any other native thread. No special transcript chrome.
- **Binding is a User-owned node, not a connector.** `WhatsAppChannelBinding` hangs off `User —HAS_WHATSAPP_CHANNEL_BINDING→` in the same create path (I-GRAPH-01). `api_key` and `webhook_secret` are encrypted with `encrypt_secret_for_storage` / `decrypt_secret_from_storage` and AAD = binding id. `workspace_id` is the caller’s personal workspace from `ensure_personal_workspace_for_user_id`, including when Settings is open under an org scope. One binding per user. A `phone_number_id` already stored on another user’s binding is a conflict.
- **Inbound receipt is an Object.** `WhatsAppInboundReceipt` (I-GRAPH-02) records the raw Meta message id, binding id, and egress outcome. It is the duplicate check. The chat submission id is `wa.` plus the sha256 hex of that message id, because Meta ids contain characters `ChatTurnSubmissionRequest.client_request_id` rejects.
- **Webhook auth is the HMAC, then a fast 200.** Verify `X-Jvconnect-Signature` or `X-Hub-Signature-256` (`sha256=` + HMAC-SHA256 of the raw body, `hmac.compare_digest`) before any graph or chat side effect. Bad signatures return 401. After a valid signature, duplicates, a disabled binding, an unknown or unverified sender, and non-text messages return 200 so jvconnect does not retry. Non-text and unverified senders get one short text reply when egress credentials exist. They never call `submit_chat_turn`.
- **Approvals stay in-process.** When the dedicated thread has one pending staged write and the text parses as a single approval intent, call `parse_approval_intent` and `decide_staged_write` (`source="channel"`) the same way `POST /agentive/staging/text-approve` does. Do not HTTP-loop into that JWT endpoint. Any other text goes to `submit_chat_turn`.
- **Egress is after the turn commits.** `_execute_claimed_work` calls one delivery helper after `_handle_chat_turn` returns. The helper sends only when `provider_session_id` starts with `wa:`. A jvconnect send failure does not change the WorkItem status and does not send a second WhatsApp message. The receipt stores the error; Settings shows the last delivery error.
- **Public callback host is `PUBLIC_API_BASE_URL`.** `APP_BASE_URL` is the SPA (`:9006`). `EMAIL_WEBHOOK_BASE_URL` stays email-only. Register uses `{PUBLIC_API_BASE_URL}/api/agentive/channels/whatsapp/webhook`. Empty outside DEBUG leaves the binding saved and disabled with a visible register error. DEBUG may fall back to the request base URL.
- **Transport client is httpx in Core.** Mirror jvconnect’s Messaging API (`GET account`, `POST webhook/register`, `POST messages` with `{"message": {...}}` and `Authorization: Bearer`). No jvagent import, no Meta token, no connector catalog.

## Task List

### Phase 1: Binding
- [x] Task 1: Encrypted WhatsApp channel binding node
- [x] Task 2: Authenticated binding API

### Checkpoint: Binding
- [x] Binding tests pass; GET redacts secrets; graph edge exists

### Phase 2: Transport
- [x] Task 3: jvconnect Messaging client
- [x] Task 4: Signature verification and envelope parse
- [x] Task 5: Webhook endpoint, idempotency, and short notices

### Checkpoint: Transport
- [x] Forged signatures do not touch the graph; valid non-text and unknown senders do not enqueue a turn

### Phase 3: Chat bridge
- [ ] Task 6: Dedicated thread, approval short-circuit, and turn enqueue
- [ ] Task 7: Terminal reply delivery

### Checkpoint: Chat bridge
- [ ] Mocked happy path enqueues one native turn and sends one reply
- [ ] Existing native chat turn tests still pass

### Phase 4: Settings and docs
- [ ] Task 8: WhatsApp channel settings
- [ ] Task 9: Public URL docs and spec status

### Checkpoint: Complete
- [ ] Targeted backend and frontend tests pass
- [ ] `make verify-ci` before any push
- [ ] Ready for review

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Setting `provider_id` to `whatsapp` drops every turn and every text approval | High | Keep `integral_native`; isolate the channel on `provider_session_id` |
| Delivery hook runs for every native turn | High | Prefix check first; exceptions stay off the WorkItem status |
| JSON parse before HMAC lets a bad body hit the graph | High | Read the raw body, verify, then parse |
| jvconnect locks the callback hostname on first register | Med | Document `PUBLIC_API_BASE_URL` before the first real register |
| Missing `INTEGRAL_CREDENTIAL_ENC_KEY` stores a plaintext key | High | Binding save fails when `encryption_available()` is false |
| Two users paste keys for one phone | Med | Unique `phone_number_id` check before enable |
| Meta id used raw as `client_request_id` 422s the turn | Med | Hash into the allowed submission id; dedupe on the raw id |

## Open Questions

None that block implementation. Approving this plan approves the decisions above, including the `provider_id` deviation from the spec’s literal wording and `PUBLIC_API_BASE_URL` as the callback origin.

## Out of scope

Voice and jvvoice, templates, media understanding, groups, Flows, org-facing numbers, moving notification Graph sends onto jvconnect, Slack/Telegram, a new WorkKind, and Connectors UI.
