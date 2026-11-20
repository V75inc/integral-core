# Implementation Plan: Realtime voice broker

## Overview

Add two service-authenticated Core routes so a voice session can read a verified caller's WhatsApp thread and can hand an approval-required request to the existing runtime. The voice session stays in `integral-voice`. This plan does not accept calls, send WhatsApp messages, or decide approvals. Spec: [SPEC-realtime-voice-broker.md](SPEC-realtime-voice-broker.md).

`tasks/plan.md` and `tasks/todo.md` are the unfinished WhatsApp text-channel plan (`make verify-ci` is still open). This plan lives here so that file is left as it is. Tasks are listed below, not in `tasks/todo.md`.

## Architecture decisions

- **Core does not host the realtime socket.** `integral-voice` already opens `Agent.realtime().session()`. Core exposes the broker those tools call.
- **Auth is a service key checked first.** `X-Integral-Voice-Key` is compared with `hmac.compare_digest`. An empty server setting fails closed. A bad key returns 401 before identity lookup.
- **Context is a capped read of `wa:{e164}`.** The thread stays `provider_id=integral_native`. The handler does not submit a chat turn, send through jvconnect, or write an Entry.
- **Delegate admits and returns.** The response is `task_ref` plus a short status. `decide_staged_write` is not on this path. Completion stays with the text-channel worker.
- **The voice tool set stays eager.** `integral-voice` may add one broker tool next to `current_time`. Deferred and approval-required tools stay unregistered.

## Task list

### Phase 1: Auth and context

## Task 1: Service-key gate

**Description:** Add the voice broker secret setting and a helper that accepts only an exact match. Empty configuration and a bad header fail before any graph call.

**Acceptance criteria:**

- [ ] A missing header, a wrong key, and an empty server secret are rejected
- [ ] The comparison is constant-time, and the secret is not in the response or the log line
- [ ] A rejected call does not resolve a `ChannelIdentity`

**Verification:**

- [ ] Tests pass: `cd backend && uv run pytest tests/test_whatsapp_voice_broker.py -v`
- [ ] Manual check: the exported response models have no secret field

**Dependencies:** None

**Files likely touched:**

- `backend/app/config.py`
- `backend/app/agentive/services/whatsapp_voice_broker.py`
- `backend/tests/test_whatsapp_voice_broker.py`

**Estimated scope:** Small: 3 files

## Task 2: Read-only context route

**Description:** `POST /api/agentive/channels/whatsapp/voice/context` resolves a verified sender and returns a capped status from the dedicated thread.

**Acceptance criteria:**

- [ ] A verified sender with a `wa:{e164}` thread receives a short status and at most a few role/text lines
- [ ] An unknown or unverified sender receives an empty status and no work item
- [ ] The handler does not call `submit_chat_turn`, jvconnect, or an Entry write

**Verification:**

- [ ] Tests pass: `cd backend && uv run pytest tests/test_whatsapp_voice_broker.py -v`
- [ ] Manual check: a test spy records no chat submit and no outbound HTTP

**Dependencies:** Task 1

**Files likely touched:**

- `backend/app/agentive/services/whatsapp_voice_broker.py`
- `backend/app/agentive/api/channels.py`
- `backend/app/schemas/agentive/whatsapp_voice_broker.py`
- `backend/tests/test_whatsapp_voice_broker.py`

**Estimated scope:** Medium: 4 files

### Checkpoint: Context

- [ ] Bad keys return 401 with no graph writes
- [ ] The happy-path read is capped and side-effect free

### Phase 2: Handoff

## Task 3: Delegate route

**Description:** `POST /api/agentive/channels/whatsapp/voice/delegate` admits the request on the same thread through the existing native chat path and returns a task reference.

**Acceptance criteria:**

- [ ] A verified sender receives `task_ref` and a short status string
- [ ] The test spy shows the work was admitted and `decide_staged_write` was not called
- [ ] An unverified sender creates no work item
- [ ] The route does not send a WhatsApp message

**Verification:**

- [ ] Tests pass: `cd backend && uv run pytest tests/test_whatsapp_voice_broker.py tests/test_whatsapp_chat_bridge.py -v`
- [ ] Manual check: an existing text-channel approval test still passes unchanged

**Dependencies:** Task 2

**Files likely touched:**

- `backend/app/agentive/services/whatsapp_voice_broker.py`
- `backend/app/agentive/api/channels.py`
- `backend/app/schemas/agentive/whatsapp_voice_broker.py`
- `backend/tests/test_whatsapp_voice_broker.py`

**Estimated scope:** Medium: 4 files

### Checkpoint: Handoff

- [ ] Context and delegate tests pass
- [ ] Existing WhatsApp text-channel tests still pass

### Phase 3: Voice consumer

## Task 4: Broker tool on the fake session

**Description:** In `integral-voice`, add an HTTP client and one eager tool that calls the two Core routes. The fake session keeps working with no Core key by skipping the broker call. `current_time` stays. No deferred tool is registered.

**Acceptance criteria:**

- [ ] With a Core key and a mocked broker, the tool returns the status string and the task ref
- [ ] Without a Core key, `fake_session` still exits 0 and does not open a network call
- [ ] The registered tool set is only `current_time` and the broker tool

**Verification:**

- [ ] Tests pass: `cd integral-voice && uv run pytest`
- [ ] Manual check: `uv run python -m integral_voice.fake_session` prints `skip` or `ok` and does not place a call

**Dependencies:** Task 3

**Files likely touched:**

- `integral-voice/src/integral_voice/broker_client.py`
- `integral-voice/src/integral_voice/session.py`
- `integral-voice/src/integral_voice/config.py`
- `integral-voice/tests/test_poc.py`

**Estimated scope:** Medium: 4 files

### Checkpoint: Complete

- [ ] Core broker tests and the existing WhatsApp chat tests pass
- [ ] `integral-voice` tests pass with no secrets
- [ ] No live call was required
- [ ] Ready for review

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Delegate calls `decide_staged_write` and applies an action inside the voice request | High | The route is forbidden from that function; a spy test fails if it is called |
| Context read uses the chat submit path and spends a model turn | High | The read handler has no submit import path; the test spies the submit function |
| The service key is logged or returned | High | Fail closed, compare with `hmac.compare_digest`, and keep it out of response models |
| The voice session registers the resident tool catalog | High | Task 4 asserts the registered names |
| This plan overwrites the WhatsApp text plan | High | Tasks stay in this file |

## Open questions

The three questions in the spec are unresolved. They do not change the task order. Answering question 3 (whether an already-waiting approval is ever decided on this route) before Task 3 avoids building the wrong handoff.

## Out of scope

Live calls, LiveKit, reconnect and barge-in measurement, speaking a finished task back into the audio session, self-hosted speech models, and edits to `tasks/plan.md` or `tasks/todo.md`.
