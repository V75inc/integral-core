# Spec: Realtime voice broker

**Status:** Draft. Not approved for implementation.
**Date:** 19 November 2026
**Research:** [RESEARCH-pydantic-ai-realtime-voice.md](RESEARCH-pydantic-ai-realtime-voice.md)
**Plan:** [PLAN-realtime-voice-broker.md](PLAN-realtime-voice-broker.md)
**Source card:** [Pydantic AI realtime research and prototype direction](https://planner.cloud.microsoft/ui/web/plan/vGeDOHx9mUWOt37fjKxit2UACae2/view/board/task/p5F-NeI-m0KvotiFBD64emUAC05d?tid=1229a998-708d-402a-ba72-b5dccc4506f2)
**Session POC:** `integral-voice` (`SPEC.md` there). That repo proves `Agent.realtime().session()` and the jvvoice media path with a `current_time` tool. It does not call Integral.

## Capability map

This document specifies one module. The others already have a home or stay out of this slice.

| Module id | Responsibility | Where |
|---|---|---|
| `voice-session` | Open `Agent.realtime().session()`, fake session, eager tools | `integral-voice` (exists) |
| `voice-media` | Signed jvconnect `calls` webhook, jvvoice accept/disconnect, LiveKit audio | `integral-voice` (exists) |
| `voice-broker` | Authorized read-only WhatsApp context, and delegation of approval-required work to the durable runtime | This spec, in Integral Core |

Build order for this slice: `voice-broker` in Core, then a consumer change in `integral-voice` that calls it. `voice-media` is not a dependency of the fake-session acceptance test.

## Assumptions

1. The voice process stays a separate service (`integral-voice`). Core does not open the provider socket and does not accept WhatsApp calls.
2. The first proof is a fake or test session. A live Meta call is not a success criterion.
3. The broker is authenticated with a service secret, not a browser JWT and not the user's jvconnect API key.
4. "Read-only WhatsApp context" means bounded text already on the caller's dedicated native thread (`provider_id=integral_native`, `provider_session_id=wa:{e164}`), for a verified `ChannelIdentity`. It does not send a WhatsApp message, place a call, or write an Entry.
5. An approval-required request is admitted to the existing chat/staging runtime and returns a task reference plus a short status. The voice request does not call `decide_staged_write` and does not execute the tool.
6. Voice provider and model selection stay in `integral-voice` (`REALTIME_MODEL`). They are not written into the chat model profile or the transcription `voice_configuration` record.
7. NemotronLabs VoiceChat and Block Dessa are research options. They are not dependencies of this slice.
8. `tasks/plan.md` and `tasks/todo.md` remain the WhatsApp text-channel plan. This work's plan is the file linked above.

Correct these before implementation if they are wrong.

## Objective

A test voice session can ask Integral for a short, authorized read of the caller's WhatsApp thread, and can hand an approval-required request to the existing runtime without performing that action inside the audio session.

The user of this slice is the voice service, acting for a personal-workspace user who already has a verified WhatsApp identity. Success is a test that shows the read, a test that shows the handoff, and a test that shows a missing or bad service secret does nothing.

### User stories

1. As the voice service, I present a service secret and a verified sender, and I receive a short text status about that sender's WhatsApp thread.
2. As the voice service, I present an approval-required request and receive a task reference and a short status. The action is not applied during that call.
3. As Integral, a request with a bad secret, an unknown sender, or an unverified identity does not read the thread, enqueue work, or send WhatsApp traffic.
4. As a reviewer, I can see that authorization, approval state, and any later audit stay on the durable runtime the text channel already uses.

## Tech stack

- Integral Core backend: Python 3.11+, FastAPI via jvspatial `@endpoint`, existing agentive chat and staging services
- Voice consumer: `integral-voice`, pinned `pydantic-ai-slim[google-realtime,openai-realtime]==2.59.0`
- Auth between them: a shared service secret compared in constant time
- No new provider SDK inside Core for this slice
- No jvagent import

## Commands

```bash
# Core, from backend/
uv sync --frozen --extra dev --extra test
uv run pytest tests/test_whatsapp_voice_broker.py -v

# Voice consumer, from the integral-voice checkout, after the Core broker exists
uv run pytest
uv run python -m integral_voice.fake_session
```

`make verify-ci` stays the pre-push gate for the Core change. It is not required to draft this spec.

## Project structure

```text
docs/ideas/
  SPEC-realtime-voice-broker.md          # this spec
  PLAN-realtime-voice-broker.md          # implementation plan
  RESEARCH-pydantic-ai-realtime-voice.md # model and cost research

backend/app/agentive/
  api/channels.py                        # extend: broker routes
  services/whatsapp_voice_broker.py      # NEW: auth, read, delegate
backend/app/schemas/agentive/
  whatsapp_voice_broker.py               # NEW: request and response models
backend/tests/
  test_whatsapp_voice_broker.py          # NEW

integral-voice/src/integral_voice/
  session.py                             # add the broker tool beside current_time
  broker_client.py                       # NEW: HTTP client for the Core routes
```

Exact Core filenames may shift. The module id `voice-broker` stays.

## Decisions

### 1. Two routes, one secret

Both routes live under the existing agentive channel surface:

- `POST /api/agentive/channels/whatsapp/voice/context`
- `POST /api/agentive/channels/whatsapp/voice/delegate`

Auth is a dedicated header, `X-Integral-Voice-Key`, checked with `hmac.compare_digest` against the server secret. No user JWT. A missing, empty, or wrong key returns 401 before any graph or chat call.

The secret is an environment setting, absent from API responses and logs. Implementation fails closed when the setting is empty.

### 2. Context is the existing thread

The caller sends the verified E.164. Core resolves the `ChannelIdentity`, requires `verified`, loads the personal workspace on that user's WhatsApp binding, and reads the dedicated thread `wa:{e164}`.

The response is a short status string plus at most a few source lines (role and text). It is capped so a voice model can speak it. An unknown thread returns a short empty status and no error that would invite a retry storm.

The handler does not call `submit_chat_turn`, does not call jvconnect, and does not write Entries, bindings, or receipts.

### 3. Delegate admits work and stops

`delegate` identifies the same user and thread, then admits the request through the existing native chat path (`submit_chat_turn` or the existing staging admission that path already uses). It returns:

- `task_ref`: the durable id of the admitted work
- `status`: a short sentence the voice model can speak, such as that the request was handed to the workspace and is waiting

The handler does not call `decide_staged_write`, does not apply an approval, and does not send a WhatsApp reply. Completion continues to belong to the text-channel worker. Speaking that completion back into a live audio session is a later module.

### 4. Tool set stays small

The voice agent may register `current_time` and one broker tool that calls these two routes. Skills, deferred tools, and approval-gated tools stay off the realtime session. Core does not advertise the resident tool catalog to the voice process.

## Code style

Follow `backend/app/agentive/AGENTS.md`: `@endpoint`, schemas in `schemas/agentive/`, `JVSpatialAPIException`, no `HTTPException`.

```python
status = await read_whatsapp_voice_context(
    e164=payload.e164,
    voice_key=request.headers.get("x-integral-voice-key"),
)
```

The service checks the key before it resolves an identity. Secrets are not logged.

## Testing strategy

| Level | What |
|---|---|
| Unit | Constant-time key check; empty key fails closed; response text is capped |
| Service | Verified sender reads the dedicated thread; unverified sender does not; delegate returns a task ref and does not decide a staged write |
| API | 401 on a bad key with no graph writes; context does not call the chat submit path; delegate does not call jvconnect |
| Consumer | `integral-voice` fake session, with Core mocked or skipped when no key is set, registers the broker tool and does not register a deferred tool |

No live WhatsApp call and no provider spend are required for the Core tests.

## Boundaries

**Always**

- Check the service secret before any graph or chat side effect
- Require a verified `ChannelIdentity` before a read or a delegate
- Keep the thread id `integral_native` / `wa:{e164}`
- Leave approval decisions on the durable runtime
- Run the focused pytest file before calling the slice done

**Ask first**

- Adding a third voice route, or sending the delegate result back out through jvconnect
- Putting the provider model name on a Core workspace setting
- Changing `integral-voice` to place a live call as part of this slice
- Choosing a self-hosted speech model (NemotronLabs VoiceChat, Dessa, Moshi)

**Never**

- Mount Integral's deferred or approval-required tools on `Agent.realtime().session()`
- Execute an approval inside the voice request
- Send a WhatsApp message or accept a Meta call from the broker routes
- Log the service secret, jvconnect API key, or webhook secret
- Overwrite `tasks/plan.md` or `tasks/todo.md` with this work
- Treat a live call or a Core release flag as part of done

## Success criteria

1. A valid service key and a verified sender return a capped text status from the dedicated WhatsApp thread, and the test spy shows no chat submit, no jvconnect send, and no Entry write.
2. The same caller, with an approval-required request, receives a `task_ref` and a short status. The test spy shows the work was admitted and `decide_staged_write` was not called.
3. A missing or wrong service key returns 401 and creates no graph writes and no work item.
4. An unverified or unknown sender returns a no-content status and creates no work item.
5. The voice agent used by the fake session registers only eager tools. A deferred or `requires_approval` tool is absent from that set.
6. Core's chat model profile and transcription voice settings are unchanged by these routes.

## Out of scope

Live Meta calls, the LiveKit bridge, reconnect and barge-in measurement, speaking a completed task back into the audio session, NemotronLabs VoiceChat, Dessa, org-facing numbers, and any change to the Phase 1 text webhook.

## Open questions

1. Is the service secret one deployment value, or one secret per voice binding?
2. Is "context" the last few thread messages, or a single governed query result? This spec assumes the thread messages, capped.
3. Should `delegate` always call `submit_chat_turn`, even when the text is an approval of an item that is already waiting? This spec admits work and does not decide it. Confirm that a second path for "approve the item that is already waiting" stays on the text channel only.
