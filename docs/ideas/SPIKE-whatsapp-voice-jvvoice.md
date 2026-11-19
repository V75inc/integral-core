# Spike: WhatsApp Voice via jvvoice → Integral (Phase 2)

**Status:** Spike appendix — run only after Phase 1 chat is confirmed working  
**Depends on:** [SPEC-whatsapp-personal-channel-chat.md](SPEC-whatsapp-personal-channel-chat.md)  
**Idea:** [whatsapp-personal-channel.md](whatsapp-personal-channel.md)

## Goal

Prove that **inbound** WhatsApp Cloud Calling can reach Integral’s resident harness through jvvoice **without** jvagent, with accept/disconnect owned by Integral and audio turns mapped to harness replies.

Outbound “place call” is **out of spike scope** unless jvvoice already exposes it (today it does not).

## Current JV stack (baseline)

```
Meta calls webhook
  → jvconnect forward
  → jvagent WhatsAppVoiceAction
  → POST jvvoice /api/calls/accept  (SDP + jvagent_base_url + jvagent_agent_id)
  → LiveKit WhatsApp Connector + worker
  → jvagent POST /api/agents/{id}/interact  (channel=whatsapp_call)
```

Key references:

- `c:\v75\jv\jvvoice\README.md`, `api.py`, `jvagent_bridge.py`, `jvagent_llm.py`
- `c:\v75\jv\jvagent\jvagent\action\whatsapp_voice\`
- jvconnect: `GET /api/v1/meta/whatsapp/calling/credentials`; calls in webhook forwards
- Constraint: LiveKit **Cloud** required for WhatsApp Connector; coexistence/WA Web do not support Cloud Calling

## Spike hypothesis

jvvoice’s interact bridge can be generalized (or forked with a second backend mode) so the worker calls an **Integral** channel-voice endpoint instead of jvagent `/interact`, reusing the Phase 1 WhatsApp `ChatThread` / principal resolution.

## In scope for the spike

1. **Inventory jvvoice accept payload** — fields required today; which are jvagent-specific (`jvagent_base_url`, `jvagent_agent_id`).
2. **Design Integral voice interact contract** — minimal request/response for one user utterance → one assistant text (SSE optional). Prefer parity with what `jvagent_bridge.py` already consumes so TTS keeps working.
3. **Caller identity** — map WhatsApp caller id → verified `ChannelIdentity` → same personal user/workspace as Phase 1 chat; reject unverified callers.
4. **Call control ownership** — Integral (not jvagent) receives `calls` webhook events from jvconnect forward (or a dedicated calls path), fetches calling credentials from jvconnect, then `POST /api/calls/accept` / `disconnect` on jvvoice with `Authorization: Bearer JVVOICE_API_KEY`.
5. **Session continuity** — prefer reusing Phase 1 WhatsApp `ChatThread` (`provider_id=whatsapp`) so voice and text share context; document if a separate voice session id is required.
6. **Latency budget** — Meta answer window (~60s); measure accept→first TTS and per-turn STT→Integral→TTS on a staging path.
7. **Failure modes** — Integral down, unverified caller, harness turn timeout, LiveKit accept failure; define hangup vs spoken error.

## Out of scope

- Production UI for “call me”
- Outbound dial from Integral
- Replacing Deepgram/ElevenLabs on jvvoice
- Org-facing multi-customer routing
- Changing Phase 1 text path beyond shared identity/thread reuse
- Shipping a full Phase 2 product spec before spike evidence

## Proposed Integral surface (draft — validate in spike)

| Piece | Draft |
|-------|--------|
| Ingress | Extend WhatsApp webhook handling to branch `calls` events (or dedicated route) after Phase 1 message handling |
| Accept | Core → jvvoice `POST /api/calls/accept` with SDP + Integral callback base URL + binding/thread ids instead of jvagent agent id |
| Interact | `POST /api/agentive/channels/whatsapp/voice/interact` (auth: shared JVVOICE/Integral service secret or signed service envelope) body: `{ thread_id or session, text, turn_id }` → `{ response: string }` or SSE text chunks |
| Disconnect | Core → jvvoice `POST /api/calls/disconnect` on Meta hangup / policy reject |

Exact auth scheme must not reuse browser JWTs; follow service-to-service patterns already used in agentive middleware, or a dedicated `JVVOICE_INTEGRAL_KEY` mirrored on both sides.

## jvvoice change options (pick one after spike)

| Option | Pros | Cons |
|--------|------|------|
| A. Config mode `brain=integral` alongside jvagent | One deployable; shared LiveKit path | Couples jvvoice release to Integral contract |
| B. Thin adapter fork / Integral-specific bridge module | Clear boundary | Two codepaths to maintain |
| C. jvvoice calls generic HTTP “LLM URL” only | Least Integral knowledge in jvvoice | Still need Integral to own accept/webhook |

**Spike recommendation to validate first:** Option A or C — keep LiveKit + STT/TTS on jvvoice; move **call accept ownership** and **interact URL** to Integral-supplied values per accept request (already partially true for `jvagent_base_url`).

## Acceptance for “spike done”

- [ ] Written note of accept/disconnect + interact field mapping (jvagent vs Integral)
- [ ] Prototype or sequence diagram proving Integral can accept an inbound WA call and return at least one spoken turn (staging or mocked LiveKit if Cloud unavailable — document which)
- [ ] List of Core endpoints and secrets required
- [ ] Go/no-go: blockers (LiveKit Cloud, jvconnect calling credentials, answer deadline, harness latency)
- [ ] If go: draft Phase 2 spec outline (objective + success criteria only); if no-go: document pivot (e.g. chat-only product)

## Suggested spike timebox

2–3 engineering days after Phase 1 chat qualification, with one staging jvconnect number that already works for Phase 1 text.

## Verification commands (when prototyping)

```bash
# jvvoice
cd c:/v75/jv/jvvoice
# health + accept against staging; see README

# Integral (once stub exists)
cd c:/v75/integral-core/backend
pytest tests/test_whatsapp_voice_interact_stub.py -v   # if added during spike
```

## Explicit non-goals reminder

Do not route voice through jvagent “temporarily.” Do not claim outbound calling. Do not block Phase 1 chat on this spike.
