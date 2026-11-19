# Spec: WhatsApp Personal Channel — Phase 1 Chat

**Status:** Approved for implementation planning (2026-11-18). Coding waits on approval of `tasks/plan.md`. The plan keeps `ChatThread.provider_id=integral_native` and stores the sender on `provider_session_id=wa:{e164}`, because the native turn worker and text-approve reject any other provider id.  
**Idea:** [whatsapp-personal-channel.md](whatsapp-personal-channel.md)  
**Follow-on:** [SPIKE-whatsapp-voice-jvvoice.md](SPIKE-whatsapp-voice-jvvoice.md) (not Phase 1 acceptance)

## Capability map (Phase 0)

Phase 1 is one shippable capability (personal WhatsApp text chat). Internal modules for build order:

| Module id | Responsibility | Depends on |
|-----------|----------------|------------|
| `wa-binding` | Store/encrypt jvconnect base URL + API key + webhook secret; register forward | existing `ChannelIdentity` |
| `wa-ingress` | Verify jvconnect HMAC; parse inbound text; resolve identity | `wa-binding` |
| `wa-chat-bridge` | Map WA conversation → dedicated `ChatThread`; run native harness turn; collect reply | `wa-ingress` |
| `wa-egress` | Send reply text via jvconnect Messaging API | `wa-binding`, `wa-chat-bridge` |
| `wa-settings-ui` | Settings: link phone (existing) + enable channel credentials + status | `wa-binding` |

Build order: `wa-binding` → `wa-ingress` + `wa-egress` → `wa-chat-bridge` → `wa-settings-ui`.

## Assumptions

1. Personal workspace only: one verified WhatsApp identity per user is enough for v1; the channel binding is user-owned and scoped to that user’s personal workspace.
2. Customer supplies their own jvconnect base URL + phone-bound API key (`jvk_…`); Integral does not onboard WABAs.
3. Integral owns the single jvconnect webhook forward for that phone (no shared forward with jvagent).
4. Phase 1 is **text-only**; images, documents, and voice notes are deferred.
5. User-initiated inbound messages open the 24h Cloud API window; outbound templates are out of scope.
6. Existing notification `whatsapp_service` (direct Graph) stays as-is for this phase.
7. Messaging is a **channel adapter**, not a data connector (`IS_CONNECTED_TO` / connector catalog).

→ Correct these before implementation if wrong.

## Objective

A personal-workspace user who has linked and verified their WhatsApp number can paste their jvconnect credentials in Integral settings, register the webhook, text that business number from WhatsApp, and receive a resident-harness reply on WhatsApp—subject to the same workspace gate, staging/approvals, and receipts as in-app chat.

### User stories

1. As a personal user, I link my phone via existing OTP so Integral knows which WhatsApp sender is me.
2. As a personal user, I enter my jvconnect base URL and API key so Integral can send and receive without Meta tokens in Core env.
3. As a personal user, I text the linked business number and get a coherent resident-mind reply.
4. As a personal user, if my identity is unverified or credentials are wrong, I get a clear failure (WhatsApp notice or settings status)—not a silent drop.
5. As Integral, freeform approval intents from WhatsApp reuse `/agentive/staging/text-approve` semantics where applicable.

## Tech stack

- Integral Core backend: Python 3.11+, FastAPI via jvspatial `@endpoint`, existing agentive + chat services
- Frontend: React 18 + TypeScript (extend `WhatsAppSettings` / Notifications settings)
- Transport: jvconnect HTTP Messaging API + webhook forwards (same contract as `jvagent` `JvconnectWhatsAppAPI`)
- Crypto at rest: `app.services.credential_crypto` (same pattern as connector `auth_state`)
- No jvagent dependency; no LiveKit/jvvoice in Phase 1

## Commands

```bash
# Backend
cd backend
uv sync --frozen --extra dev --extra test
.venv/bin/python -m app.main

# Targeted tests (expected once implemented)
pytest tests/test_whatsapp_channel_binding.py tests/test_whatsapp_ingress.py tests/test_whatsapp_chat_bridge.py -v

# Frontend
cd frontend
npm install
npm run lint:types
npm run test:run -- src/components/settings/__tests__/WhatsAppSettings.test.tsx

# Local gate for touched areas
make verify-ci   # before push when substrate-adjacent
```

## Project structure

```
docs/ideas/
  whatsapp-personal-channel.md          # idea one-pager
  SPEC-whatsapp-personal-channel-chat.md # this spec
  SPIKE-whatsapp-voice-jvvoice.md        # Phase 2 spike

backend/app/agentive/
  api/channels.py                       # extend: binding CRUD + webhook receive
  services/channel_identity.py          # reuse resolve/verify
  services/whatsapp_channel_binding.py  # NEW: encrypt/store binding, register webhook
  services/whatsapp_ingress.py          # NEW: signature verify + parse Meta envelope
  services/whatsapp_chat_bridge.py      # NEW: thread map + native turn + reply text
  clients/jvconnect_whatsapp.py         # NEW: thin send/account/register client

backend/app/schemas/agentive/           # request/response models for binding + webhook

frontend/src/components/settings/
  WhatsAppSettings.tsx                  # extend: credentials + channel status (not Connectors)

backend/tests/
  test_whatsapp_channel_binding.py
  test_whatsapp_ingress.py
  test_whatsapp_chat_bridge.py
```

Exact filenames may shift slightly during implementation; module ids above are stable.

## Decisions (former open questions)

### 1. Secret storage

Store jvconnect credentials on a dedicated graph node (working name `WhatsAppChannelBinding`) owned by the User (structural edge in the same create path — I-GRAPH-01), with:

- `jvconnect_base_url` (non-secret)
- encrypted `api_key` and `webhook_secret` via `encrypt_secret_for_storage` / `decrypt_secret_from_storage` (AAD = binding id), same pattern as connector `auth_state`
- denormalized `phone_number_id` / display phone from `GET …/account` after save
- `workspace_id` = caller’s personal workspace at bind time
- `enabled` flag

Do **not** put API keys in browser storage, env-only global `WHATSAPP_*`, or the data-connector registry.

### 2. Chat thread mapping

Use a **dedicated** `ChatThread` per user+channel binding:

- `provider_id = "whatsapp"`
- `provider_session_id` = stable WhatsApp sender id (E.164 / wa_id) for the personal user
- `workspace_id` = personal workspace on the binding
- Reuse the same thread across turns; do not dump WhatsApp turns into an arbitrary in-app “primary” thread

In-app UI may list this thread like any other; Phase 1 does not require a special WhatsApp transcript chrome.

### 3. Media

**Text-only.** Non-text inbound messages get a short outbound notice (“Text only for now”) and no harness turn. No image attachment_ids path in Phase 1.

### 4. Settings naming

Keep under existing Notifications / WhatsApp settings surface (`WhatsAppSettings`). Label the new controls **“WhatsApp channel”** (credentials, webhook status, enable/disable). Do **not** add an entry under Settings → Connectors.

## Webhook contract

### Register (egress to jvconnect)

After saving a binding, Core calls (authenticated with the customer API key):

`POST {jvconnect_base}/api/v1/meta/whatsapp/webhook/register`

Body includes Integral’s public callback URL, e.g.  
`https://{integral-host}/api/agentive/channels/whatsapp/webhook`

Persist the returned `webhook_secret` encrypted on the binding.

### Ingress (jvconnect → Integral)

- Method: `POST /api/agentive/channels/whatsapp/webhook`
- Auth: verify `X-Jvconnect-Signature` / `X-Hub-Signature-256` HMAC against the binding’s `webhook_secret` (constant-time). Reject 401/403 on failure.
- No user JWT; resolve binding by phone_number_id (or configured route key) from the Meta envelope, then `resolve_channel_identity` for the sender.
- Idempotency: ignore duplicate Meta message ids already processed (store recent ids on binding or a small Object log).
- Unverified / unknown sender: do not run harness; optional short “link your number in Integral settings” reply if egress is available and policy allows.

### Egress (Integral → jvconnect)

`POST {jvconnect_base}/api/v1/meta/whatsapp/messages` with `Authorization: Bearer {api_key}` and Meta-shaped `{ "message": { … } }` text payload (mirror jvagent `JvconnectWhatsAppAPI`).

## Native chat turn bridge

- Invoke the same resident harness path used for native chat (workspace scope = personal workspace; principal = resolved Integral user).
- Preserve one-active-turn admission, staging, approvals, and receipts.
- Prefer asynchronous work-item admission if inbound webhook latency would otherwise exceed jvconnect forward timeouts (~120s); still return 200 quickly after durable enqueue when the turn is accepted.
- Map terminal assistant text to WhatsApp egress; do not stream partial tokens to WhatsApp in Phase 1.
- Reuse text-approve parsing for short approval replies when a pending staging item exists for that user/thread.

## Code style

Follow existing agentive conventions (`backend/app/agentive/AGENTS.md`):

- `@endpoint` + schemas in `schemas/agentive/` + `JVSpatialAPIException`
- Never hang messaging on `IS_CONNECTED_TO`
- Encrypt secrets before persist; never log raw API keys or webhook secrets

Example shape (illustrative):

```python
binding = await create_whatsapp_channel_binding(
    user_id=user_id,
    workspace_id=personal_workspace_id,
    jvconnect_base_url=payload.jvconnect_base_url,
    api_key=payload.api_key,  # encrypted inside service
)
await register_jvconnect_webhook(binding, callback_url=public_webhook_url)
```

## Testing strategy

| Level | What |
|-------|------|
| Unit | HMAC verify accept/reject; encrypt round-trip; message-id idempotency; text vs non-text filter |
| Service | Binding create/update/disable; webhook register client mocked; identity resolve → thread reuse |
| API | Webhook endpoint with forged vs valid signature; unknown sender; happy-path enqueue |
| Frontend | Settings form validation; status display; no key echoed after save |
| Manual / qual | Real jvconnect phone + Integral staging: inbound text → reply (recorded in Phase 1 evidence) |

Coverage expectation: new services/endpoints have focused tests; no need to expand notification WhatsApp tests unless touched.

## Boundaries

**Always**
- Verify webhook signatures before any graph/chat side effect
- Require verified `ChannelIdentity` before harness turns
- Encrypt jvconnect secrets at rest; scrub from logs and API readbacks (show last-4 / status only)
- Keep workspace gate and approval/staging semantics
- Run targeted pytest + frontend tests for touched files before commit

**Ask first**
- Changing ChatThread provider_id conventions used elsewhere
- Adding outbound template sends
- Sharing one phone across multiple Integral users
- Migrating notification Graph sends onto jvconnect
- Any jvvoice / calling work (Phase 2)

**Never**
- Route WhatsApp through jvagent
- Store Meta long-lived tokens in Core for this channel (jvconnect holds them)
- Put this feature in the connector catalog / `IS_CONNECTED_TO`
- Expose API keys to the browser after save
- Implement org-facing business-line routing in Phase 1
- Treat coexistence / WA Web as supported paths

## Success criteria

1. With a verified ChannelIdentity and valid jvconnect binding, an inbound text message produces a resident-harness reply delivered back to the same WhatsApp chat.
2. Invalid webhook signature never creates messages or runs the harness.
3. Unverified sender never runs the harness.
4. Non-text inbound does not run the harness; user receives a short text-only notice when send is possible.
5. API key is encrypted at rest and not returned in full on GET.
6. Settings live under WhatsApp channel UI, not Connectors.
7. Automated tests cover signature failure, happy-path bridge (mocked jvconnect), and binding encryption.
8. No jvagent import or process in the path.

## Out of scope (Phase 1)

Voice/calling, templates, media, groups, Flows, org-facing numbers, notification Graph migration, Slack/Telegram, jvconnect OAuth into Core, multiplexed webhook forwards.

## Open questions (remaining for implementer / reviewer)

1. Public webhook URL derivation in multi-host / reverse-proxy deployments — confirm canonical `PUBLIC_BASE_URL` (or equivalent) already used elsewhere.
2. Whether pending WhatsApp turns should appear in the in-app chat UI for the dedicated thread in v1 (default: yes, as normal thread messages).
3. Exact retry/backoff when jvconnect send fails after a successful harness turn (dead-letter vs user-visible error message on next inbound).

## Approval gate

Do not start implementation coding until this spec is explicitly approved. After approval, use `planning-and-task-breakdown` then `incremental-implementation` + TDD per module build order.
