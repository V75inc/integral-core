# Spike: WhatsApp Voice via jvvoice → Integral (Phase 2)

**Status:** Superseded 20 November 2026. Do not implement this spike.
**Current direction:** [RESEARCH-pydantic-ai-realtime-voice.md](RESEARCH-pydantic-ai-realtime-voice.md)
**Broker spec:** [SPEC-realtime-voice-broker.md](SPEC-realtime-voice-broker.md)
**Plan:** [PLAN-realtime-voice-broker.md](PLAN-realtime-voice-broker.md)

This file used to propose a speech-to-text turn, one full harness reply, and text-to-speech, on `provider_id=whatsapp`, through `POST /api/agentive/channels/whatsapp/voice/interact`. That path is retired. The research note holds the baseline, the thread id, the media-bridge role, and the broker.

What to build is the draft broker in Core and the existing `Agent.realtime().session()` in `integral-voice`. jvvoice stays the media bridge. Inbound-first stays the honest call scope. Do not route voice through jvagent, and do not block the Phase 1 text channel on voice work.
