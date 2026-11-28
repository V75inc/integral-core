# Capability Map: WhatsApp calling through jvvoice

Approved with `docs/ideas/whatsapp-calling-jvvoice.md`. Module ids stay as written here. Specs, plans, and later work select modules by these ids.

Calling is a capability of the existing Integral WhatsApp JV Connect line. The catalogue id stays `whatsapp_jvconnect` (`docs/backend/connectors.md`). The package id stays `whatsapp` (`backend/app/connectors/bundled_packages/whatsapp/connector.yaml`). This map does not add a connector.

| Module id | Responsibility | Depends on |
|---|---|---|
| call-admission | Decide answer vs refuse by matching the inbound caller to the verified personal number on the workspace WhatsApp binding. No network I/O. | existing WhatsApp binding |
| call-control | Signed `calls` webhook ingress, jvconnect calling-credential fetch, jvvoice accept/disconnect. Refused callers never get an accept. | call-admission |
| voice-bridge | Bridge the accepted LiveKit WhatsApp audio track into Integral native realtime voice, bound to the existing WhatsApp thread, including one tool or approval through the current voice services. | call-control |

Build order: call-admission → call-control → voice-bridge

## Specs

| Module id | Spec |
|---|---|
| call-admission | `docs/ideas/whatsapp-calling/SPEC-call-admission.md` |
| call-control | `docs/ideas/whatsapp-calling/SPEC-call-control.md` |
| voice-bridge | `docs/ideas/whatsapp-calling/SPEC-voice-bridge.md` |

The contract a module offers its dependents is specified in that module's spec. `call-admission` owns the answer/refuse decision. `call-control` owns the signed webhook and the jvconnect/jvvoice HTTP calls. `voice-bridge` owns the server audio peer, the jvvoice worker change, and the thread-bound realtime session.

## Shared scope

In scope: one inbound call from the verified personal number, accept through jvvoice, realtime conversation on the existing WhatsApp thread, one tool or approval through the current voice services, refusal of every other caller without an accept.

Out of scope, for every module: a second catalogue connector, a customer principal, outbound dial, Integral terminating WhatsApp WebRTC itself, browser navigation during the call, and coexistence or WhatsApp Web numbers. LiveKit Cloud is an accepted dependency and stays behind jvvoice.

`docs/backend/adr/019-native-chat-live-voice-parity.md` leaves telephone admission outside the current voice release. `voice-bridge` is an explicit extension of that boundary. It is not a claim that the ADR already allows a phone call.
