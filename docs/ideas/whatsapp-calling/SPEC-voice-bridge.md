# Spec: voice-bridge

Module id: `voice-bridge`. Depends on `call-control`.

## Objective

After jvvoice accepts an inbound WhatsApp call, bridge that call's LiveKit audio into Integral's existing realtime voice relay (`VoiceRelay` in `backend/app/services/native_voice.py`). The call uses the same WhatsApp chat thread as text. One tool or approval goes through the current voice services. A spoken "yes" is not authorization.

jvvoice remains the LiveKit Cloud media gateway. It does not run Deepgram, ElevenLabs, or the jvagent `/interact` brain on this path. Integral does not terminate WhatsApp WebRTC and does not hold LiveKit keys.

This is an explicit extension of telephone admission. `docs/backend/adr/019-native-chat-live-voice-parity.md` says a call "remains bound to one workspace, human and agent profile" and that "Telephone admission and switching workspace or profile mid-call are outside this release." Nothing in this spec treats that sentence as already allowing a phone call. The bridge adds telephone admission for this one verified owner. An ADR rewrite is not a prerequisite document; it follows only if a live bridge works. Implementers must not silently contradict the ADR by routing the phone call through the browser socket and calling it the same release.

The user is the verified owner on a handset. Success they can hear is a short realtime conversation whose workspace action shows up on the WhatsApp thread they already text. Success the automated bar can prove is a fake audio peer and a fake realtime model driving that same relay. A unit test does not prove a real WhatsApp call.

## Assumptions

1. The bridge extends `VoiceRelay` with a server audio peer. It does not open a second realtime product that only shares the thread. The peer presents the same frame contract the browser socket uses: binary PCM in, playback bytes and small JSON control messages out. `VoiceRelay._read_browser` (`native_voice.py`) reads `message["bytes"]` and sends `input_audio_buffer.append` as base64. Playback is `browser.send_bytes` of PCM (`response.output_audio.delta`). Format is `audio/pcm` at `PCM_SAMPLE_RATE` (24000) from `session_update`. Chunks must be even-length and at most `MAX_AUDIO_CHUNK` (4800) bytes.
2. The server peer is not `POST`ed into `/ws/native-voice`. That socket requires `FRONTEND_ORIGIN`, a `VoiceStart` ticket (`backend/app/schemas/voice_configuration.py`), and `redeem_ws_ticket_shared` (`backend/app/agentive/api/native_voice.py`). It also inserts the principal into the process-local `_active_calls` set and rejects a second socket for that principal. The phone path does not present a browser origin or a browser ticket.
3. If that principal is already in `_active_calls`, the phone bridge does not start a second relay. `call-control` is told to disconnect. One live realtime session per principal. A browser call and a phone call on the same thread at the same time are refused rather than merged.
4. The thread is the one text already uses. `whatsapp_chat_bridge._session_id` is `wa:{workspace_id}:{e164}` and `_dedicated_thread` loads or creates it via `get_thread_by_session` (`backend/app/agentive/services/whatsapp_chat_bridge.py`). The relay's `VoiceScope.thread` is that `ChatThread` id. `VoiceScope.principal` is the verified identity's user id. `VoiceScope.workspace` is the single workspace from admission. The relay does not mint a parallel thread. `VoiceScope.session` stays the relay's own token; it is not rewritten to the `wa:` string. `authorize` in `native_voice.py` still checks workspace role and thread ownership.
5. jvvoice joins the LiveKit room, reads the WhatsApp connector audio track, converts it to 24000 Hz 16-bit mono PCM, and writes those bytes to the Integral server peer. It writes Integral playback bytes back to the call. LiveKit credentials stay in jvvoice's environment (`README.md`, `connector_client.py`).
6. Integral mints a one-call bearer for that peer, bound to the call id, principal, workspace, and thread, and puts it in the jvvoice accept metadata. The bearer is not the jvvoice API key, not the Meta token, and not an end-user JWT. It dies when the call ends or the relay closes. jvvoice presents it only on the server-peer connection.
7. The existing jvagent worker path stays for accepts that still carry `jvagent_agent_id` and `jvagent_base_url`. `main.py` `entrypoint` currently always builds Deepgram, ElevenLabs, and `JvagentOrchestratorLLM`, and `dispatch.resolve_call_context` shuts the job down when `jvagent_agent_id` or `jvagent_base_url` is missing. An `integral_native` dispatch must skip that brain and must not require those fields. Deepgram and ElevenLabs remain required only for the jvagent path.
8. `review_confirmation_supported` is false for the phone session. `VoiceStart.review_confirmation` defaults false, and `_session_tools` then drops `request_integral_voice_review_decision` and `revise_integral_voice_review`. The exact spoken confirmation in `VoiceConfirmation` (`backend/app/services/native_voice_confirmation.py`) needs the browser: `_request_review_confirmation` sends `{"type":"review_confirmation"}` to `browser`, waits for playback control frames, and appends microphone PCM only during capture. The phone has no browser card and no local WAV playback. The phone session does not start that capture.
9. When work is waiting for a human, the relay already says the request is waiting in Integral and has not been applied (`_delegated_status` / the follow-up readout in `native_voice.py`). On the phone, that spoken line also tells the caller to confirm in the existing WhatsApp chat. The chat already applies one parsed intent through `parse_approval_intent` and `decide_staged_write` (`whatsapp_chat_bridge._apply_single_approval`). That text path is the authority. Call audio is not fed to `parse_approval_intent`. A spoken "yes" does not call `decide_staged_write`. The text parser's bare "yes" token (`approval_intent.py`) stays on typed WhatsApp messages only.
10. The phone session withholds `navigate_integral_voice`. `session_update` may still tell a browser model that the resident can open a view; the phone instructions say the handset cannot move a page. `VoiceScope.page_context` stays empty. Navigation events are dropped by the server peer. `integral_open_view` is unchanged and is not given a phone page to move.
11. Hangup from either side closes the relay and asks jvvoice to disconnect the LiveKit room. A calls webhook that no longer carries SDP for an already accepted call id is a hangup signal for that id, not a second accept (`call-control` owns the webhook; this module owns the relay cleanup it invokes).
12. No customer principal, no outbound dial, no second connector.

## Tech Stack

- Integral: existing `VoiceRelay`, `delegate`, `submit_chat_turn`, `authorize`, PCM session update. OpenAI realtime stays behind `open_upstream` (`wss://api.openai.com/v1/realtime`). No new model vendor.
- Server peer transport: a WebSocket, because `VoiceRelay` is a long-lived byte loop and `@endpoint` cannot register websockets. The existing deviation is `backend/app/agentive/api/native_voice.py` (`APIRouter` only for `/ws/native-voice`). A second socket, if added, carries the same deviation comment and is the only new raw route. Request shapes that are ordinary HTTP stay Pydantic models in `backend/app/schemas/`. Errors on ordinary HTTP stay `JVSpatialAPIException` subclasses. The websocket closes; it does not raise `HTTPException`.
- jvvoice: existing FastAPI app, `livekit` / `livekit-api`, `LiveKitConnectorClient`. No new media framework. The Integral bridge does not import `livekit.plugins.deepgram` or `elevenlabs` and does not call `jvagent_bridge.interact_stream`.
- Secrets: `LIVEKIT_*` on jvvoice. Meta token only inside the accept call, as today (`whatsapp_api_key`). `JVVOICE_API_KEY` on both sides for the connector API. The one-call relay bearer exists only for that call. `OPENAI_API_KEY` stays on Integral with the realtime credential resolver. None of these are written into the chat thread.

## Commands

Integral, from `backend/`:

```bash
uv sync --frozen --extra dev --extra test
pytest tests/test_whatsapp_voice_bridge.py tests/test_native_voice.py -v -m unit
black app/services/native_voice.py app/agentive/api/native_voice.py tests/test_whatsapp_voice_bridge.py
isort app/services/native_voice.py app/agentive/api/native_voice.py tests/test_whatsapp_voice_bridge.py
flake8 app/services/native_voice.py app/agentive/api/native_voice.py tests/test_whatsapp_voice_bridge.py
```

`tests/test_native_voice.py` is the regression slice if it exists in the tree at implementation time; if the filename differs, run the existing native-voice unit module instead of skipping voice regressions. Do not require the full `make verify` gate as this module's spec bar. Substrate-touching implementation still follows the repo commit gate later.

jvvoice, from `c:\v75\jv\jvvoice`:

```bash
pip install -r requirements.txt pytest httpx
pytest tests/test_api.py tests/test_jvagent_bridge.py tests/test_dispatch_integral_bridge.py -v
```

`test_dispatch_integral_bridge.py` is new. Existing jvagent tests stay green.

Live qualification, not part of either pytest command: one staging call from the verified Cloud Calling number, with LiveKit Cloud, after the unit bar is green. Record that it is manual. A green unit run does not substitute for it.

## Project Structure

Integral:

```
backend/app/services/native_voice.py
    Server peer adapter that satisfies the browser frame methods VoiceRelay already calls,
    phone session flags (no review confirmation, no navigate tool, phone review wording),
    and relay teardown on hangup.
backend/app/agentive/api/native_voice.py
    Optional second websocket for the server peer, with the existing @endpoint deviation.
    Browser /ws/native-voice stays as it is.
backend/app/agentive/services/whatsapp_chat_bridge.py
    Reuse _dedicated_thread / _session_id. Do not fork a phone-only thread helper
    unless the existing function cannot be called without a text envelope.
backend/app/schemas/agentive/whatsapp_call.py
    Accept body shared with call-control: bridge = "integral_native",
    relay URL, one-call bearer, session_id (Integral thread id), user_id
    (principal id), call id. Workspace and principal stay on the bearer.
backend/tests/test_whatsapp_voice_bridge.py
    Fake peer and fake upstream. No LiveKit.
```

jvvoice (specified here, edited in that repo when implementation is allowed):

```
api.py
    AcceptCallRequest gains an Integral bridge mode. jvagent fields become optional
    when bridge == "integral_native". Disconnect is unchanged.
dispatch.py
    resolve_call_context accepts integral_native metadata and does not require
    jvagent_agent_id or jvagent_base_url for that mode.
main.py
    entrypoint branches: integral_native pumps PCM to Integral; otherwise the
    current Deepgram / ElevenLabs / JvagentOrchestratorLLM path.
connector_client.py
    Unchanged LiveKit accept/disconnect. Still the only place LIVEKIT_* are read.
jvagent_bridge.py
    Unchanged. Not called for integral_native.
tests/test_api.py
    Existing jvagent accept test still passes.
tests/test_dispatch_integral_bridge.py
    Integral metadata does not raise MissingDispatchMetadata.
```

What stays in jvvoice: `GET /health`, `POST /api/calls/accept`, `POST /api/calls/disconnect`, bearer `JVVOICE_API_KEY`, `LiveKitConnectorClient`, room prefix, `LIVEKIT_*`, and the jvagent worker for jvagent-shaped accepts.

What changes in jvvoice: `AcceptCallRequest` no longer forces `jvagent_agent_id` and `jvagent_base_url` on Integral calls; `resolve_call_context` no longer shuts those calls down for missing jvagent fields; the worker does not construct Deepgram, ElevenLabs, or `JvagentOrchestratorLLM` when metadata says `integral_native`.

## Code Style

Integral peer adapter is a small class with `receive`, `send_bytes`, and `send_json`, the methods `VoiceRelay` already uses. Phone-specific tool filtering extends `_session_tools` with an explicit argument rather than a WhatsApp-named branch inside the model instructions' authorization logic.

```python
def _session_tools(
    uses_published_profile: bool,
    review_confirmation: bool = True,
    *,
    allow_browser_navigation: bool = True,
) -> list[dict]:
    """Tools for this realtime session. The phone session turns navigation off."""
```

jvvoice stays typed and pydantic-shaped like the current `AcceptCallRequest`. A bridge mode is a literal, not a free-form prompt. Do not log the relay bearer, Meta token, or PCM.

The server socket, if added beside `/ws/native-voice`, includes the same deviation comment: jvspatial `@endpoint` has no WebSocket primitive. No other file under `backend/app/` gains an `APIRouter`.

## Testing Strategy

Two layers. Only the first is the automated bar.

### (a) Server-side session contract

`backend/tests/test_whatsapp_voice_bridge.py`, pytest, fake objects. No LiveKit, no Meta, no jvvoice process, no browser.

- A fake upstream websocket records JSON events. Feeding one even-length PCM chunk under 4800 bytes through the server peer produces `input_audio_buffer.append` and no `page_context` write.
- An odd-length or oversized chunk is rejected and is not forwarded, matching `_read_browser`.
- A fake `response.output_audio.delta` becomes bytes on the peer output.
- Session tools for the phone omit `navigate_integral_voice`, `request_integral_voice_review_decision`, and `revise_integral_voice_review`.
- One fake `delegate_to_integral` function call invokes `delegate` once. The test's fake `delegate` records a single submission whose thread id is the `wa:{workspace}:{e164}` thread. A second identical provider call id does not submit twice (`VoiceRelay` already tracks `seen` call ids).
- A transcript or tool argument of "yes" does not call `decide_staged_write`. The test supplies a waiting-for-human result and asserts the spoken text tells the caller to confirm in the WhatsApp chat and does not say the change was applied.
- Starting a phone relay while the principal is in `_active_calls` does not open the upstream socket.
- Hangup closes the upstream fake and records a disconnect request. It does not submit another chat turn.

jvvoice unit tests use the existing `TestClient` style in `tests/test_api.py`:

- An accept body with `bridge: "integral_native"` and no jvagent fields is not rejected for missing `jvagent_base_url`.
- Dispatch metadata for that bridge does not raise `MissingDispatchMetadata`.
- A jvagent-shaped accept still requires `jvagent_agent_id` and `jvagent_base_url` and still dispatches the current worker fields.
- The Integral worker path's module under test does not call `interact_stream` or construct the Deepgram/ElevenLabs session. Assert on the branch, not on a live room.

### (b) Live LiveKit qualification

Not CI. One staging call from the verified personal number to a Cloud Calling number, LiveKit Cloud, jvvoice holding `LIVEKIT_*`. Confirm audio in both directions, one delegated action or a waiting approval on the existing WhatsApp thread, and hangup from the handset cleaning up the room. A coexistence or WhatsApp Web number that fails to connect is a failed setup, not a product defect and not a test to automate here.

Passing (a) does not prove (b). Do not write a CI comment that claims a real WhatsApp call succeeded.

## Boundaries

- Always: bind the relay to the existing `wa:{workspace}:{e164}` thread and the verified principal; keep review confirmation off; withhold browser navigation; drop navigation events; send workspace work through `delegate` / `submit_chat_turn` once; tell the caller to confirm on the WhatsApp chat when the existing status is waiting for a human; keep LiveKit keys on jvvoice; tear down on hangup; state in code comments that this is telephone admission beyond ADR 019's current release.
- Ask first: the extra websocket route; any change to `decide_staged_write` or `parse_approval_intent`; enabling `review_confirmation_supported` for the phone; sharing one relay between a live browser socket and the phone; an ADR 019 text change.
- Never: start Deepgram, ElevenLabs, or jvagent `/interact` for `integral_native`. Never invent an approval authority or treat spoken "yes" as `decide_staged_write`. Never run `VoiceConfirmation` browser capture on the phone. Never call `integral_open_view` as the phone UI. Never put Meta tokens or `LIVEKIT_*` on the chat record or in Integral's LiveKit-less process as LiveKit credentials. Never add a customer principal, an outbound call, or a second connector. Never claim the unit test placed a real WhatsApp call. Never use `APIRouter` anywhere in `backend/app/` except the documented websocket file.

## Success Criteria

1. A fake server peer delivers one 24000 Hz 16-bit mono PCM chunk into `VoiceRelay` and the fake upstream receives `input_audio_buffer.append`. Playback PCM from a fake `response.output_audio.delta` is returned on the peer. No LiveKit room is opened in this test.
2. The relay's `VoiceScope` uses the verified principal, the admission workspace, and the `ChatThread` id for `wa:{workspace_id}:{e164}`. The test does not create a second thread.
3. One `delegate_to_integral` invocation submits one chat turn on that thread through the existing `delegate` path. A repeated provider call id does not submit again.
4. The phone tool list omits `request_integral_voice_review_decision`, `revise_integral_voice_review`, and `navigate_integral_voice`. `page_context` stays empty. A navigation event produces no page-moving call.
5. With `review_confirmation_supported` false, a waiting-for-human result is spoken as not applied and directs the caller to the existing WhatsApp chat. No test transcript of "yes" calls `decide_staged_write` or `VoiceConfirmation.matches`.
6. A principal already present in `_active_calls` does not get a second upstream connection from the phone bridge.
7. jvvoice `AcceptCallRequest` accepts `bridge: "integral_native"` without `jvagent_agent_id` or `jvagent_base_url`. `resolve_call_context` does not shut that job down for missing jvagent fields. The jvagent-shaped accept path still requires those fields.
8. The Integral worker branch under test does not call `jvagent_bridge.interact_stream` and does not construct the Deepgram or ElevenLabs session. `LIVEKIT_*` remain read only inside `LiveKitConnectorClient`.
9. `pytest tests/test_whatsapp_voice_bridge.py -v -m unit` passes without network. The jvvoice unit commands above pass. Neither command is described as a real WhatsApp call.
10. Hangup closes the fake upstream and records one jvvoice disconnect. The module adds no customer principal, no outbound dial, and no connector id.

## Open Questions

- Server peer versus a separate realtime session that only shares the thread and the approval services. This spec assumes the server peer inside `VoiceRelay`, because the approved direction says the call uses that relay. Rejecting the assumption blocks implementation of this module. `call-admission` and `call-control` do not depend on it.
- The transport for that peer. This spec assumes a websocket next to `/ws/native-voice` because that is the loop `VoiceRelay` already runs, and `@endpoint` cannot register it. A different byte transport blocks this module if the websocket deviation is refused. It does not block the two earlier modules.
- Whether a live browser call should queue behind the phone call instead of failing closed. Failing closed is the specified behavior and is testable. Changing it can wait.
- Meta's answer deadline versus relay setup time. `call-control` returns after jvvoice accept and does not wait for the model. Measuring the live window can wait until qualification (b). It does not change the fake-peer tests.
- Spoken refusal without an accept. Owned by `SPEC-call-control.md`. This module does not play a refusal, because a refused caller never reaches the bridge.
- Resampling details inside LiveKit's WhatsApp track. The contract this module requires of jvvoice is 24000 Hz 16-bit mono PCM. How jvvoice produces those samples is internal to jvvoice and can be settled during live qualification, as long as the bytes that cross into Integral match the fake-peer contract.

### Core transport boundary and qualification

The connector owns verified call admission, the per-call media peer, provider
call-control lifecycle, and line/grant revalidation. Core owns the configured
voice credential, model/protocol selection, and the media relay through
`supports_phone_voice`, `open_upstream`, `open_phone_relay`, and
`open_public_phone_relay`. Both relay factories consume the same PCM peer
contract (`receive`, `send_bytes`, `send_json`); the connector does not
translate model-specific events. The public relay is intentionally tool-free.

Only the qualified Realtime wire protocol is accepted on phone transport.
GPT Live is currently supported on browser transport only; phone calls must
not fall back to a different model. To qualify another protocol, add the
corresponding Core relay adapter and validate PCM, turn interruption, completion,
and hangup behavior for both admitted member and tool-free public sessions.
Do not enable phone review/confirmation until its verified conversational
approval flow is implemented and qualified with the common approval contract.
Browser confirmation is unchanged.

Process-local call ownership currently requires the webhook and media peer
to share one worker. Distributed ownership and lifecycle recovery require a
durable coordinator before multi-worker production deployment. Provider
event handling and external handset behavior require a separate controlled
qualification run.
