# Research: Pydantic AI realtime voice

**Date:** 19 November 2026
**Status:** Planning and test only. No live calling and no production enablement.
**Source card:** [Pydantic AI realtime research and prototype direction](https://planner.cloud.microsoft/ui/web/plan/vGeDOHx9mUWOt37fjKxit2UACae2/view/board/task/p5F-NeI-m0KvotiFBD64emUAC05d?tid=1229a998-708d-402a-ba72-b5dccc4506f2) (18 November 2026)
**Stack checked:** `pydantic-ai-slim==2.59.0` and `pydantic-ai-harness==0.59.0` in `backend/pyproject.toml`
**Related:** [whatsapp-personal-channel.md](whatsapp-personal-channel.md), [SPEC-whatsapp-personal-channel-chat.md](SPEC-whatsapp-personal-channel-chat.md), [SPIKE-whatsapp-voice-jvvoice.md](SPIKE-whatsapp-voice-jvvoice.md), [SPEC-realtime-voice-broker.md](SPEC-realtime-voice-broker.md), [PLAN-realtime-voice-broker.md](PLAN-realtime-voice-broker.md)

Model prices are vendor list prices gathered on 19 November 2026. They exclude tax, cached-input discounts, tool calls, and the cost of a growing conversation. Meta prices in this note are the Guyana case only: market **Other**, because Guyana (+592) is not a named market and is not in Rest of Latin America. Confirm the linked pricing pages, and Meta's USD CSV, before budgeting a prototype.

## Finding

The personal WhatsApp text channel can stay as it is. The 18 November card is a later voice prototype, and it does not require a change to that branch.

Phase 1 is signed jvconnect ingress, a native chat thread, durable text approvals, and settings. The card asks for a review of `Agent.realtime().session()`, a small set of broker tools, and an authenticated media bridge. Its own scope is planning and test only.

[SPIKE-whatsapp-voice-jvvoice.md](SPIKE-whatsapp-voice-jvvoice.md) is retired. It assumed jvvoice would do speech-to-text, Integral would run a full harness turn, and jvvoice would do text-to-speech, on `provider_id=whatsapp`. This note replaces that path: a speech-to-speech session, jvvoice as the media bridge, and the broker in Core.

## The product idea

Source: [whatsapp-personal-channel.md](whatsapp-personal-channel.md). Phase 1 decisions live in [SPEC-whatsapp-personal-channel-chat.md](SPEC-whatsapp-personal-channel-chat.md). Phase 1 is implemented on the current branch. Voice is the follow-on.

A personal-workspace user talks to their Integral resident mind over WhatsApp, chat first and calls after, on a phone and API key they own in jvconnect. The product is a messaging channel adapter. It is separate from the data-connector catalog (`IS_CONNECTED_TO`). Core does not hold a long-lived Meta token, and it does not route the turn through jvagent.

Phase 1, already on the branch:

- The user links a verified `ChannelIdentity` and stores their jvconnect credentials on a user-owned binding. Secrets are encrypted for that binding. The phone number id is unique. The workspace is the personal workspace.
- jvconnect forwards the signed Meta webhook to `POST /api/agentive/channels/whatsapp/webhook`. Core checks the HMAC before it parses, then returns 200.
- The turn is a native chat turn: `provider_id=integral_native`, `provider_session_id=wa:{e164}`. Approvals stay on the durable staging path (`parse_approval_intent`, `decide_staged_write`, source `channel`).
- The reply goes out through jvconnect's Messaging API only when that session id starts with `wa:`.
- Settings sit under Notifications / WhatsApp.
- Inbound receipts are an `Object` (I-GRAPH-02), not a graph node.
- Org numbers, templates, groups, Flows, coexistence, WhatsApp Web, and notification-graph migration stay out of Phase 1.

Phase 2 is inbound WhatsApp Cloud Calling. jvvoice carries the media. Core accepts and disconnects and owns the session. Outbound place-call waits until jvvoice can place a call. LiveKit Cloud is required for the current connector. The older spike's "one utterance, one full harness reply" shape is the piece to reconcile with the broker below.

`tasks/plan.md` and `tasks/todo.md` remain the Phase 1 text plan. `make verify-ci` is still open on that plan before any push.

## The broker spec

Source: [SPEC-realtime-voice-broker.md](SPEC-realtime-voice-broker.md) (draft, not approved for implementation) and [PLAN-realtime-voice-broker.md](PLAN-realtime-voice-broker.md). This section is the whole decision. Those files are the build artifacts.

Three modules:

| Module | Job | Home |
|---|---|---|
| `voice-session` | `Agent.realtime().session()`, fake session, eager tools | `integral-voice` (exists) |
| `voice-media` | Signed jvconnect calls webhook, jvvoice accept/disconnect, LiveKit audio | `integral-voice` (exists) |
| `voice-broker` | Authorized read of the WhatsApp thread, and handoff of approval-required work | Integral Core (specified, not built) |

Build order is the broker in Core, then one consumer tool in `integral-voice`. The media bridge is not a dependency of the fake-session test. `integral-voice` today proves the socket with `current_time` only. Its own spec says Integral, chat threads, approvals, and audit are outside that POC.

The voice process stays a separate service. Core does not open the provider socket and does not accept WhatsApp calls. `REALTIME_MODEL` stays in `integral-voice`. It is not written into the chat model profile or the transcription `voice_configuration` record.

Auth is a deployment service secret on `X-Integral-Voice-Key`, compared with `hmac.compare_digest`. It is not a browser JWT and not the jvconnect key. The check runs before any graph or identity work. An empty secret fails closed. A bad key returns 401 and writes nothing. The secret is absent from responses and logs.

Two routes, under the existing agentive channel surface:

1. `POST /api/agentive/channels/whatsapp/voice/context` — the caller sends a verified E.164. Core resolves the `ChannelIdentity`, requires `verified`, loads that user's personal-workspace WhatsApp binding, and reads `wa:{e164}`. The response is a short status plus a few capped role/text lines a voice model can speak. An unknown thread returns a short empty status. The handler does not call `submit_chat_turn`, jvconnect, or an Entry write.
2. `POST /api/agentive/channels/whatsapp/voice/delegate` — the same identity check, then the request is admitted on the existing native chat path. The response is `task_ref` plus a short status. The handler does not call `decide_staged_write`, does not apply an approval, and does not send WhatsApp. Completion stays with the text-channel worker. Speaking that completion back into the live audio session is a later module.

The voice agent may register `current_time` and one broker tool that calls those two routes. Skills, deferred tools, and approval-gated tools stay off the session. Core does not advertise the resident catalog to the voice process.

Done, for this slice, means all of the following:

1. A valid key and a verified sender return a capped status, and a test spy shows no chat submit, no jvconnect send, and no Entry write.
2. An approval-required request returns a `task_ref` and a short status. The spy shows the work was admitted and `decide_staged_write` was not called.
3. A missing or wrong key returns 401 and creates no graph writes and no work item.
4. An unverified or unknown sender returns an empty status and creates no work item.
5. The fake-session agent registers only eager tools.
6. The chat model profile and the transcription voice settings are unchanged.

The plan is four tasks, in `docs/ideas/PLAN-realtime-voice-broker.md`: service-key gate, context route, delegate route, then the `integral-voice` broker tool beside `current_time`. The fake session still exits cleanly when no Core key is set and does not open a network call. Live Meta calls, the LiveKit bridge, reconnect and barge-in measurement, NemotronLabs VoiceChat, Dessa, org numbers, and any edit to the Phase 1 webhook or to `tasks/plan.md` / `tasks/todo.md` are out of the slice.

Three questions are still open. The third one has to be answered before the delegate task:

1. One deployment secret, or one secret per voice binding?
2. Is context the last few thread messages (the spec assumes this, capped), or one governed query result?
3. Should `delegate` ever decide an approval that is already waiting, or does that stay on the text channel only? The spec admits work and does not decide it.

## What the card asks for

The card reports that installed Pydantic AI exposes `Agent.realtime().session()`, with OpenAI and Gemini realtime options. It sets four boundaries:

1. Keep voice provider and model configuration separate from the normal text agent configuration.
2. Leave deferred and approval-dependent tools off the voice session. An unresolved approval fails inside a realtime session. It does not become Integral's durable paused approval.
3. Expose a small, eagerly registered set of authorized broker tools. Delegate skills, long-running work, and approval-dependent actions to the existing runtime. Return a task reference and a short status to the voice session. Keep authorization, approval state, and audit history in the durable runtime.
4. Design an authenticated bidirectional JV Voice media bridge, with session identity, reconnect, interruption, cancellation, and duplicate-call protection. MCP may carry control and tool calls. The media stream needs its own transport.

The proposed first test is a fake session that retrieves read-only WhatsApp context through one authorized broker tool. That test sends no messages, places no calls, and changes no data. Later exercises cover disconnect and reconnect, interruption, and an approval-required request that is delegated rather than executed in the voice session.

Prototype acceptance is a written record of audio and tool latency, provider, model, and installed versions, reconnect and interruption behavior, tool authorization, approval handoff, and audit evidence. Existing Core releases stay as they are.

## Relationship to the current branch

| Item | Current branch | Voice card |
|---|---|---|
| Product | Personal WhatsApp text chat | Spoken session prototype |
| Transport | jvconnect webhook and Messaging API | Authenticated media bridge plus a realtime provider socket |
| Brain | Existing native chat turn | `Agent.realtime().session()` |
| Thread | `provider_id=integral_native`, `provider_session_id=wa:{e164}` | Separate voice config and session. Completion routes back to the right conversation |
| Approvals | Durable staging on the text path, including short approval replies | Leave deferred approvals out of the audio session |
| Release posture | Implementation under review | Planning and test only |

The text path already calls the durable staging runtime. That stays. The card is about the audio session, where Pydantic AI cannot pause into `DeferredToolRequests` and resume later.

`tasks/plan.md` still has `make verify-ci` open before push. That gate belongs to the text branch.

The implemented thread identity matters for any later handoff. The native turn worker and text-approve reject any `provider_id` other than `integral_native`, so the WhatsApp conversation is isolated on `provider_session_id=wa:{e164}`. The retired spike's wording `provider_id=whatsapp` does not match the code. A voice completion should route to that thread by session id.

## Runtime boundary

Published Pydantic AI realtime docs, read on 19 November 2026 against the pages that describe the 2.59 API:

- The entry point is `agent.realtime(model).session()`. The application owns the audio transport. Pydantic AI owns the session, tools, history, and the provider socket. Guide: [Realtime (speech-to-speech)](https://pydantic.dev/docs/ai/realtime/).
- Tools are fixed when the session connects. A `defer_loading=True` capability that contributes tools raises `UserError` before connect. Mid-session tool updates are still an open limitation ([#7288](https://github.com/pydantic/pydantic-ai/issues/7288), [#7191](https://github.com/pydantic/pydantic-ai/issues/7191)). The prototype should treat them as unavailable.
- A realtime session cannot end with `DeferredToolRequests` and wait for an out-of-band human decision ([#7301](https://github.com/pydantic/pydantic-ai/issues/7301)). With no inline `HandleDeferredToolCalls` handler, the call is refused. An inline handler can approve, deny, or wait inside the process. That wait dies if the process restarts. Integral's resident runtime does the other thing: the text run ends on `DeferredToolRequests` and resumes through staging. See [Tools](https://pydantic.dev/docs/ai/realtime/tools/) and [Capabilities and hooks](https://pydantic.dev/docs/ai/realtime/capabilities/).
- Regular model settings do not configure a realtime model. Pass `RealtimeModelSettings` through `realtime(model_settings=...)`.
- The OpenAI realtime extra is `pydantic-ai-slim[openai-realtime]`, and it currently wants `openai>=3.31.0`. Core pins plain `pydantic-ai-slim` and avoids the upstream OpenAI extra because that extra selects a different SDK major. Gemini needs `pydantic-ai-slim[google-realtime]`. A prototype has to record whether those extras import cleanly before they become a Core dependency change.
- Provider resumption handles cannot be persisted and resumed in another process (`#7302`). Reconnect behavior has to be measured per provider.

Integral already stores voice settings separately from the chat model profile. That record is for speech transcription, and the save path is an OpenAI speech credential (`backend/app/services/voice_configuration.py`). A conversational realtime model needs its own configuration. Writing it into the chat profile or the dictation credential would mix two different jobs.

The broker pattern fits the library limit and Integral's approval model. Voice tools that are safe to run immediately can be registered up front. A skill load, a long job, or an approval returns a task reference. The existing runtime owns the work, the receipt, and the route back to the WhatsApp thread.

## Models

Pydantic AI ships no self-hosted realtime model id. Ollama (`ollama:`) and vLLM (`vllm:`) are ordinary text models. They do not open a realtime audio session. An `OpenAIProvider` can point at a custom base URL that already speaks the OpenAI Realtime WebSocket. That hook is what a local speech model has to satisfy. The one candidate checked for this note is [NemotronLabs VoiceChat 11B](#self-hosted-nemotronlabs-voicechat), below.

The other local architecture in the same Pydantic AI guide is speech-to-text, then a normal text agent, then text-to-speech. That text agent can be Ollama or vLLM. That is the shape of the older jvvoice spike, and it is a different product choice from `Agent.realtime().session()`.

### OpenAI

Docs: [Pydantic AI — OpenAI](https://pydantic.dev/docs/ai/realtime/openai/), [OpenAI Realtime guide](https://platform.openai.com/docs/guides/realtime), [API pricing](https://developers.openai.com/api/docs/pricing), [voice cost guide](https://developers.openai.com/api/docs/guides/voice-latency-cost?voice-api=realtime).

| Family | Example ids | How it works |
|---|---|---|
| gpt-realtime | `gpt-realtime`, `gpt-realtime-2`, `gpt-realtime-2.1`, `gpt-realtime-2.1-mini` | One speech-to-speech model over the Realtime API (`/realtime`) |
| GPT-Live | `gpt-live-1` | A separate Live API (`/live/sessions`). The voice model talks. A second text model does tools and reasoning |

[`gpt-realtime`](https://developers.openai.com/api/docs/models/gpt-realtime) is marked deprecated. Current pages: [gpt-realtime-2](https://developers.openai.com/api/docs/models/gpt-realtime-2), [gpt-live-1](https://developers.openai.com/api/docs/models/gpt-live-1). OpenAI's model list is the canonical one, and names move.

An `openai:` name starting with `gpt-live` uses `OpenAILiveModel`. Every other `openai:` name uses `OpenAIRealtimeModel`. Live can pin its backend with `openai:gpt-live-1+<backend-model>`. The Pydantic AI gateway does not route GPT-Live yet. `gateway/openai:gpt-realtime` does route.

Azure serves the gpt-realtime family, including Voice Live, and does not serve GPT-Live. An `azure:` prefix always builds `AzureRealtimeModel`. Docs: [Pydantic AI — Azure](https://pydantic.dev/docs/ai/realtime/azure/).

GPT-Live details that matter here:

- The spoken persona is `openai_live_instructions`. The agent's instructions and tools go to the backend model.
- Live bills by the second of session time, including silence and time spent waiting on the backend. Closing the session ends the bill. Muting the microphone does not.
- There is no command to cancel a delegation. A tool that changes state can still finish after the caller takes the request back.
- Text output is unsupported. The answer is the speech transcript.

### Gemini Live

Docs: [Pydantic AI — Gemini Live](https://pydantic.dev/docs/ai/realtime/gemini/), [Gemini Developer API pricing](https://ai.google.dev/gemini-api/docs/pricing), [Vertex Live API](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/live-api), [Vertex model lifecycle](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/model-versions), [3.8 launch note](https://blog.google/innovation-and-ai/technology/developers-tools/build-real-time-voice-applications-gemini-audio/).

Gemini Developer API (`google:`):

- `gemini-3.8-live`
- `gemini-3.8-live-extended-thinking`
- `gemini-3.1-flash-live-preview`
- `gemini-2.5-flash-native-audio-latest`

Vertex (`google-cloud:`) uses different ids:

- `gemini-live-2.5-flash` in `global`
- `gemini-live-2.5-flash-native-audio` in `us-central1`

Developer API ids are not Vertex ids. The Pydantic AI gateway proxies Gemini Live through Vertex, so a gateway string uses a Vertex id, for example `gateway/google:gemini-live-2.5-flash`.

`gemini-3.8-live` is the model Google currently recommends for low-latency voice. Vertex lists it as generally available from 24 September 2026, with retirement no earlier than 25 September 2027. Vertex lists `gemini-live-2.5-flash-native-audio` as retiring on 13 December 2026, with `gemini-3.8-live` as the replacement.

Secondary write-ups say the Developer API previews `gemini-3.1-flash-live-preview` and `gemini-2.5-flash-native-audio-preview-12-2025` shut down on 17 November 2026. This note is dated 19 November 2026. Confirm those ids on Google's deprecations page before a prototype uses them. `gemini-3.8-live` is the stable id to try first.

`gemini-3.8-live` keeps proactive audio on, so listening time is billed as audio input. It has no cache discount on resent history. Extended Thinking bills thinking tokens as text output and always runs tools asynchronously.

`gemini-3.5-transcribe-live-preview` returns captions only. `gemini-robotics-er-2-streaming-preview` returns text for a camera stream. Neither is a conversational voice model.

### Other hosted providers

These sit on the same Pydantic AI realtime interface. They were outside the original card. They are listed so the model choice is complete.

| Provider | Ids | Docs |
|---|---|---|
| xAI Grok Voice | `grok-voice-latest`, pinned `grok-voice-think-*` | [xAI Grok Voice](https://pydantic.dev/docs/ai/realtime/xai/) |
| Eleven Realtime | `eleven_realtime_v1_mini`, `eleven_realtime_v1_max` | [ElevenLabs](https://pydantic.dev/docs/ai/realtime/elevenlabs/) |
| ElevenLabs Agents | `elevenlabs-agents:<agent_id>` | same page |

Pricing: [xAI](https://docs.x.ai/developers/pricing), [ElevenLabs API](https://elevenlabs.io/pricing/api).

## Costs

### Published rates

OpenAI's cost guide defines the audio token math for the Realtime API. User audio is 1 token per 100 ms, which is 600 tokens per minute. Assistant audio is 1 token per 50 ms, which is 1,200 tokens per minute.

| Model | Audio input | Audio output | Text input | Text output | Notes |
|---|---:|---:|---:|---:|---|
| `gpt-realtime-2`, `gpt-realtime-2.1` | $32.00 / 1M | $64.00 / 1M | $4.00 / 1M | $24.00 / 1M | Image input $5.00 / 1M. Cached audio and text input $0.40 / 1M |
| `gpt-realtime-2.1-mini` | $10.00 / 1M | $20.00 / 1M | $0.60 / 1M | $2.40 / 1M | Cached audio input $0.30 / 1M. Cached text input $0.06 / 1M |
| `gpt-realtime` (deprecated) | $32.00 / 1M | $64.00 / 1M | $4.00 / 1M | $16.00 / 1M | Same audio price as 2.1. Lower text output price |
| `gpt-live-1` | $0.05 per minute of session | included in that rate | backend model, separate | backend model, separate | Billed per second, including silence and backend wait. Not rounded up to a minute |
| `gemini-3.8-live` and `gemini-3.8-live-extended-thinking` | $3.00 / 1M, stated by Google as $0.005 / min | $12.00 / 1M, stated as $0.018 / min | $0.75 / 1M | $4.50 / 1M, including thinking | Image or video input $1.00 / 1M, stated as $0.002 / min. Developer API paid tier. A free tier exists and may be used to improve Google's products |
| `grok-voice-3.0` / `grok-voice-latest` | $0.08 per minute | included | $0.004 per text input message | included in the minute rate | xAI page dated 10 October 2026. Server-VAD sessions bill session duration, not only speech |
| `eleven_realtime_v1_mini` | $0.08 per connected minute, plus $0.20 / 1M input tokens | plus $6.00 / 1M output tokens | those token rates | those token rates | Above the concurrency limit, connected audio is $0.16 / min |
| `eleven_realtime_v1_max` | $0.08 per connected minute, plus $1.80 / 1M input tokens | plus $9.00 / 1M output tokens | those token rates | those token rates | Same $0.16 / min burst rate. Unavailable in the India and Singapore residency regions |

Azure prices are a portal check against the matching OpenAI model, plus any regional uplift. They were not copied into this note.

### Illustration

This illustration prices one minute of user audio plus one minute of assistant audio. It includes no text, no tools, no images, and no repeated conversation history. It is a comparison of published meters, not a quote for an Integral call.

| Model | 1 min user audio | 1 min assistant audio | Sum |
|---|---:|---:|---:|
| `gpt-realtime-2.1` | 600 × $32 / 1M = $0.0192 | 1,200 × $64 / 1M = $0.0768 | $0.096 |
| `gpt-realtime-2.1-mini` | 600 × $10 / 1M = $0.006 | 1,200 × $20 / 1M = $0.024 | $0.030 |
| `gpt-live-1` | session rate | included | $0.05 of voice, plus the backend model's own tokens |
| `gemini-3.8-live` | $0.005 | $0.018 | $0.023 |
| Grok Voice | session rate | included | $0.08, plus $0.004 for each text input |
| Eleven Realtime mini or max | connected-minute rate | included in the $0.08 | $0.08 plus token charges |

A real OpenAI Realtime call costs more than that sum times the number of minutes. Each response is billed for the whole conversation so far, so later turns are more expensive. Cached input, at $0.40 / 1M on the 2.1 audio and text rates, is the main discount. Tool results and instructions add text tokens.

A GPT-Live minute is the voice meter plus a full backend request whenever the voice model delegates. A tool-heavy call can cost more than $0.05 per minute.

Gemini 3.8 bills listening time because proactive audio stays on. A ten-minute session is about ten minutes of input ($0.05) plus the minutes the model actually speaks at $0.018 each. Thinking tokens on the extended-thinking model add text-output charges at $4.50 / 1M.

For a short voice-only exchange, before tools and history growth, the published order is:

1. `gemini-3.8-live` is the lowest speech-to-speech rate in this set.
2. `gpt-realtime-2.1-mini` is next, when the smaller realtime model is good enough.
3. GPT-Live's voice meter is near mini, and the backend model is extra.
4. Full `gpt-realtime-2.1` is several times the Gemini audio rate once both sides are speaking.
5. Grok Voice and Eleven Realtime start at $0.08 per connected or session minute, before their extra text or token meters.

A first model trial can price `gemini-3.8-live` and `gpt-realtime-2.1-mini` against `gpt-live-1` plus its backend model, using the published rates, and then record the actual session cost. The fake session pays none of the Meta or LiveKit fees below.

### Extra model costs and limits

These sit on top of the per-minute illustration.

| Item | What it adds |
|---|---|
| OpenAI Realtime history | Each response is billed for the whole conversation so far. Tool results and instructions are text tokens. Cached input is the discount ($0.40 / 1M on `gpt-realtime-2.1` audio and text) |
| GPT-Live silence | The $0.05 / minute covers user speech, assistant speech, silence, and time waiting on the backend. Muting the microphone does not stop the bill. Closing the session does. There is no duration cap in `UsageLimits`; a `cost_limit` bounds a priced call |
| GPT-Live tools | Tool and reasoning tokens are billed as a normal Responses request on the backend model, in addition to the voice minute |
| Gemini 3.8 listening | Proactive audio stays on, so input audio is billed while the session is listening. There is no cache discount on resent history. Extended Thinking adds text-output tokens at $4.50 / 1M |
| Gemini tool timing | A slow tool is silence or a spoken filler, depending on the model. On some Gemini models the user talking during a pending call cancels that call. The model can speak as if the action finished before the tool result arrives |
| Session length | Provider connection caps still apply. Gemini sends `GoAway` before its cap and Pydantic reconnects after the drop, so a long call can break mid-turn. GPT-Live and xAI end or restart at their own duration limits. Measure the cap in the prototype |
| Core install | `pydantic-ai-slim[openai-realtime]` wants `openai>=3.31.0`. Core pins plain `pydantic-ai-slim` and avoids that extra because it selects a different SDK major. Gemini needs `pydantic-ai-slim[google-realtime]`. Either extra is a dependency change, not a free import |
| LiveKit | The current WhatsApp connector needs LiveKit Cloud. That is a separate vendor bill. It is not in the model rates or the Meta rates |
| Self-hosted | NemotronLabs VoiceChat has no per-minute API price and needs a dedicated NVIDIA GPU with at least 80 GB. Dessa has no published price and no API yet |

### What Meta charges for Guyana

Sources, read 20 November 2026: [Calling API pricing](https://developers.facebook.com/documentation/business-messaging/whatsapp/calling/pricing), [WhatsApp Business Platform pricing](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing), and [Twilio's WhatsApp pricing](https://www.twilio.com/en-us/whatsapp/pricing) country table. Rates change on the quarter boundaries (1 January, April, July, October). Confirm Meta's official USD CSV before budgeting. Figures below exclude tax.

**Guyana (+592) is Meta market Other.** As of 1 October 2026 Meta's card has 39 named countries and 8 regions. Rest of Latin America is fifteen markets: Bolivia, Costa Rica, Dominican Republic, Ecuador, El Salvador, Guatemala, Haiti, Honduras, Jamaica, Nicaragua, Panama, Paraguay, Puerto Rico, Uruguay, and Venezuela. Guyana is not on that list and is not a named country. Meta's rule is that a country absent from the list is Other. Twilio lists Guyana on the Other row: user-initiated calls $0.00 per minute, business-initiated calls $0.0132 per minute. French Guiana is a different country.

Calling, for a Guyana number:

- A call the WhatsApp user starts is free, whether or not the business accepts it.
- A call the business starts is charged by duration, in six-second pulses, rounded up. A 56-second call is 10 pulses. The rate is the called number's market, here Other, and it drops by volume tier inside the calendar month. A call that crosses a tier is priced entirely at the cheaper tier. A payment method is required before the business can place calls.
- A call-permission request is charged as a normal message at the Other message rate, not as call minutes.
- Call recording and transcription are free for now. Meta says separate pricing is planned, with notice before it starts.
- A user call, and a user accepting a business call, starts or refreshes the 24-hour customer service window.

Business-initiated minutes to an Other-market number, reprinted from the 1 October 2026 calling card. A personal-workspace prototype stays in the first tier. Inbound-first, which is the honest scope until jvvoice can place a call, pays Meta $0 for the call minute.

| Business-initiated minutes in the month | USD per minute |
|---|---:|
| Up to 50,000 | $0.0132 |
| 50,001–250,000 | $0.0113 |
| 250,001–1,000,000 | $0.0096 |
| 1,000,001–2,500,000 | $0.0083 |
| 2,500,001–5,000,000 | $0.0076 |
| Above 5,000,000 | $0.0072 |

Confirm calling eligibility before any outbound plan. Some write-ups say Meta blocks business-initiated calls from United States numbers while customers can still call the business. That check is on Meta's calling eligibility page. It does not change the inbound price.

Messages to a Guyana number, separate from calls. Meta charges when a business message is delivered, by category. A message the user sends to the business is free. Conversation-based pricing ended on 1 July 2025. These list rates are reprints of Meta's 1 October 2026 USD card for market Other (ChatMaxima, Broadchat, and Calypso agree). The official CSV is the one to budget from.

| Category | USD per delivered message |
|---|---:|
| Marketing | $0.0604 |
| Utility | $0.0077 |
| Authentication | $0.0077 |
| Service | $0.0077 |

From 1 October 2026 each business phone number gets 1,000 delivered service messages a month at no charge. The 1,001st is charged at the service rate above. Unused free messages do not roll over. Without a payment method, Meta delivers the free tier and stops after it. A reply inside a free entry point window (a user who arrived from a click-to-WhatsApp ad) is not charged for up to 7 days. That window is not the normal personal-chat path.

Phase 1 text replies are service messages inside the customer service window. They consume that 1,000-message free tier, then $0.0077 each. Utility, authentication, and marketing templates are a different meter and stay out of the Phase 1 text scope.

A ten-minute call the user places to a Guyana number is $0 in Meta call fees, plus the model cost in the table above (about $0.23 of Gemini 3.8 audio if the caller is heard for ten minutes and the model speaks for about ten, before tools), plus LiveKit. A ten-minute business-initiated call to a Guyana number adds $0.132 of Meta fees at the first-tier Other rate, billed as 100 six-second pulses.

## Self-hosted: NemotronLabs VoiceChat

[NVIDIA-NemotronLabs-VoiceChat-11B](https://huggingface.co/nvidia/NVIDIA-NemotronLabs-VoiceChat-11B) is an open 11B full-duplex speech-to-speech model, released 3 August 2026 under [OpenMDW 1.1](https://huggingface.co/nvidia/NVIDIA-NemotronLabs-VoiceChat-11B). The serving code lives on the experimental [`nemotron-labs-voicechat`](https://github.com/NVIDIA-NeMo/Speech/tree/nemotron-labs-voicechat) branch of NVIDIA-NeMo/Speech. That branch is not accepting contributions. The NeMo code is Apache 2.0. The weights are OpenMDW 1.1.

It is the strongest open full-duplex model to consider, and it is not a drop-in `Agent.realtime()` provider.

The interactive path is an NVIDIA container (CUDA, Triton, vLLM) with a bidirectional WebSocket. The [API reference](https://github.com/NVIDIA-NeMo/Speech/blob/nemotron-labs-voicechat/voicechat_realtime_instructions/api-reference.md) says the socket is compatible with the OpenAI Realtime protocol. It serves `ws://host:9000/v1/realtime`, plus `/realtime` as an alias for the OpenAI SDK and Pipecat. Audio is mono PCM16 at 24 kHz on the wire. The server resamples input to 16 kHz and output from 22.05 kHz. A session starts with `session.created` / `session.update`, streams `input_audio_buffer.append`, and returns `response.output_audio.delta` plus transcripts. Tool calls arrive as `response.function_call_arguments.done`. The client returns `conversation.item.create` with `function_call_output`.

That overlap is why it belongs in this note. Pydantic AI can point an OpenAI realtime provider at a custom base URL. Nobody on this review has connected `OpenAIRealtimeModel` to this container, so "compatible" here means the server documents a subset of the same events. The client events it lists are `input_audio_buffer.append`, `session.update`, `conversation.item.create`, and `session.close`. Unknown `session.update` fields, including `turn_detection` and `voice`, are ignored. `instructions` apply only to the first inference call of the session. A Pydantic session also sends later instruction updates, turn-detection settings, commits, cancellations, and truncation. Any of those can fail or be dropped. A prototype has to prove a session opens, audio comes back, and one broker tool completes before this is treated as a supported provider.

The model also misses several product limits from the card, on NVIDIA's own known-limitations list and the model card:

| Limit | What NVIDIA states |
|---|---|
| Hardware | An NVIDIA GPU with at least 80 GB, x86_64 Linux. A100, H100, H200, B100, B200, or RTX 6000. Mixed precision |
| Context | Trained on audio windows of at most 2 minutes. Longer context may not be retained |
| Tools | At most 5 tools recommended. Parallel tool calls are unreliable. The caller cannot interrupt the agent while a tool runs |
| Tool quality | Full-Duplex-Bench v3: tool selection 82.5%, argument accuracy 42.2%, Pass@1 33%. AU Harness spoken BFCL-v3 average 56.1% |
| Voice | One fixed voice. No voice cloning |
| Text in and out of tools | System prompts and tool responses must be ASCII, and tool responses must be short and speakable |
| Conversation quality | Can self-talk after a turn, loop on a sentence, cut the user off at a mid-sentence pause, drop words from the user transcript, and degrade into gibberish after several turns. Weaker than its Nemotron-Nano-9B-v2 backbone on knowledge, instruction following, and safety |

The broker pattern still fits the tool cap better than mounting Integral's skill catalog. One read-only context tool is inside the recommended maximum of five. Durable approvals still stay in the existing runtime. The model cannot be interrupted during a tool call, so a delegated approval has to be a short spoken status and a task reference, with the long wait happening off this session.

There is no per-minute API price. The cost is a dedicated 80 GB GPU for as long as the container is up. A short Gemini 3.8 exchange is a few cents on the published meter. This model pays off when that GPU is already owned or kept busy, and when the data has to stay on that machine.

### Other local models

For a full-duplex open model that can call tools, VoiceChat is the one to try first. NVIDIA's paper reports a normalized VoiceBench average of 55.1 among open full-duplex models, 25.6 points above Moshi and 24.5 above PersonaPlex, and tied with Freeze-Omni at 55.2. It is also the first open full-duplex model with a tool channel. [Paper](https://arxiv.org/abs/2609.21967).

The alternatives are better at a different job:

- [Moshi](https://github.com/kyutai-labs/moshi) is the smaller full-duplex model. Kyutai quotes about 160 ms theoretical latency and about 200 ms practical latency on an L4, and the PyTorch build fits in about 24 GB. It has no tool interface. It is the better local demo when the machine is small and the agent only has to talk. It is the worse fit for an Integral broker.
- Freeze-Omni is tied with VoiceChat on that VoiceBench average and stronger on some open-ended sets. It does not add the tool channel that makes VoiceChat relevant here.
- A local cascade (speech-to-text, a normal local LLM, text-to-speech) scores higher on spoken-assistant benchmarks because the LLM is a full text model. Public VoiceBench rows put Whisper plus Llama 3.1 8B near 77 and Parakeet plus Qwen3 8B near 79, against the full-duplex cluster near 55. That cascade is the older jvvoice spike. It keeps Integral's approvals, skills, and long context. It gives up simultaneous listen-and-speak.
- Qwen3-Omni, Ultravox, and similar audio-LLMs rank much higher on VoiceBench, often in the mid-80s. They are turn-based speech and audio models, not a full-duplex phone session, and they do not speak `Agent.realtime()`.
- [Dessa](#watch-block-dessa), below, is the full-duplex model to revisit once Block publishes weights or an API. As of 19 November 2026 it is a waitlist preview.

Use VoiceChat when the requirement is an open full-duplex voice with a few tools on an 80 GB NVIDIA GPU. Use a cascade when the requirement is correct tool use, approvals, and a conversation longer than two minutes. Use Gemini or `gpt-realtime-2.1-mini` when the requirement is the Pydantic session with no GPU to host.

## Watch: Block Dessa

[Dessa](https://block.xyz/dessa) is a full-duplex speech-to-speech model from Block's AI research lab, announced 23 October 2026. Block says it was trained from scratch on recordings of human conversation only: no text and no pretrained model. Inference is described as faster than real time, and Block says it runs locally on a MacBook Pro with an M4 Max or better. Training used one NVIDIA GB200 NVL72. Coverage: [Voice AI Space](https://www.voiceaispace.com/articles/block-launches-dessa-a-full-duplex-speech-model-trained-only-on-audio).

It is relevant as the local full-duplex direction that could run on a laptop. It is not something the prototype can call.

Block has published recordings and a waitlist. A small group of AI safety researchers gets early access. The launch page and the Voice AI Space write-up both leave out an API, model weights, pricing, a parameter count, latency numbers, language coverage, tool calling, and a technical report. There is no shared benchmark against Moshi, PersonaPlex, or NemotronLabs VoiceChat. One published demo is a live conversation with `gpt-live-1`. Block notes that OpenAI is not affiliated with that demo.

Until weights or an API exist, Dessa stays off the prototype path. The broker-tool test still needs a session that can call an authorized tool, which VoiceChat documents and Dessa does not. Revisit it when Block opens access, and then check tool use, context length, and whether the audio socket can sit behind the same media bridge.

## Tool calling and Integral Core

The realtime models can call tools. They do not fit Integral's full tool catalog or its durable approval pause. A small broker in front of the existing runtime does fit.

Pydantic AI runs ordinary function tools inside `Agent.realtime().session()` on every supported provider. The session validates arguments, runs the same tool hooks as a text run, and sends the result back. `integral-voice` already does this with one eager tool, `current_time`. OpenAI gpt-realtime and Gemini can keep speaking while a tool runs. GPT-Live puts the agent's tools on its backend text model and can talk while that backend works. It cannot cancel a delegation once it has started, so a tool that changes state can still finish after the caller takes the request back.

What does not carry over from the text harness:

| Text runtime | Realtime session |
|---|---|
| Skills load mid-turn and reveal more tools | A `defer_loading=True` capability that contributes tools raises `UserError` before connect. Tools are fixed at connect |
| An approval ends the run as `DeferredToolRequests` and resumes later through staging | The session cannot pause that way. With no inline handler the call is refused. An inline handler decides during the call and dies if the process restarts |
| The resident catalog is large and many tools stage a review | Those tools must stay off the voice agent. Registering them would fail at connect or refuse the call without writing the approval record |
| Graph node hooks, output validators, and history processors run around each model request | A realtime session has no agent graph. Those hooks do not run. Instructions are resolved again between turns and appended |

The shape that integrates with Core is the one in [SPEC-realtime-voice-broker.md](SPEC-realtime-voice-broker.md). The voice agent registers a few tools that return immediately. A read calls Core, which checks a service secret and a verified `ChannelIdentity`, then returns a short status from the existing `wa:{e164}` thread. An approval-required request calls Core, which admits the work on the native chat path and returns a task reference. `decide_staged_write`, the skill catalog, and the audit record stay in Core. The audio session never applies the change.

That split also matches the weaker open models. NemotronLabs VoiceChat can call tools, and NVIDIA recommends at most five, with weak argument accuracy. Moshi has no tool interface. Dessa has not published one. None of them should receive Integral's catalog. They can only sit in front of the same broker, and only after a prototype proves the socket.

Gemini and `gpt-realtime-2.1-mini` are the cleanest hosted fit for that broker: one speech-to-speech session, eager tools, a separate voice model setting. GPT-Live is a worse fit for "the voice model calls Integral," because the tools run on OpenAI's backend model and the spoken instructions are a different prompt. It remains useful if the prototype wants the voice model to delegate thinking while Integral is still just one function tool.

## JV Voice and WhatsApp

Today's jvvoice path is Meta calls, a jvconnect forward, jvagent, then `POST /api/calls/accept` on jvvoice (SDP plus `jvagent_base_url` and `jvagent_agent_id`), then LiveKit and a worker that calls jvagent `/interact`. Calling credentials today come from jvconnect `GET /api/v1/meta/whatsapp/calling/credentials`. That path has to reach Integral directly, with jvagent left out. A live answer still has to fit Meta's answer window, about 60 seconds. The retired spike's options for keeping speech-to-text on jvvoice are closed.

The card changes the brain. Pydantic AI does not terminate a WhatsApp call. Something still has to carry audio between Meta and `session.send_audio()` / `session.stream_audio()`, with authenticated session identity, reconnect, barge-in, cancellation, and duplicate-call protection. jvvoice is the candidate for that bridge. LiveKit Cloud remains a constraint of the current WhatsApp connector. Coexistence and WhatsApp Web do not support Cloud Calling. Meta calling eligibility, permissions, and account restrictions have to be confirmed before any live test.

The first test does not need that bridge. A fake session and one read-only broker tool are enough to prove context retrieval, authorization, and a concise reply.

## Sequence

1. Finish review of the WhatsApp text branch. Run `make verify-ci` before any push.
2. Prototype only in a fake session, after the broker spec is approved. Record installed versions, the extra packages required, provider, model, latency, authorization, approval handoff, and audit evidence.
3. Leave live Meta calls, production flags, and Core dependency changes until that evidence exists.

## Sources

- Planner card, 18 November 2026: https://planner.cloud.microsoft/ui/web/plan/vGeDOHx9mUWOt37fjKxit2UACae2/view/board/task/p5F-NeI-m0KvotiFBD64emUAC05d?tid=1229a998-708d-402a-ba72-b5dccc4506f2
- Pydantic AI realtime overview: https://pydantic.dev/docs/ai/realtime/
- Pydantic AI realtime tools: https://pydantic.dev/docs/ai/realtime/tools/
- Pydantic AI capabilities in a realtime session: https://pydantic.dev/docs/ai/realtime/capabilities/
- OpenAI pricing: https://developers.openai.com/api/docs/pricing
- OpenAI voice cost guide: https://developers.openai.com/api/docs/guides/voice-latency-cost?voice-api=realtime
- Gemini Developer API pricing: https://ai.google.dev/gemini-api/docs/pricing
- Gemini Live API on Vertex: https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/live-api
- xAI pricing: https://docs.x.ai/developers/pricing
- ElevenLabs API pricing: https://elevenlabs.io/pricing/api
- NemotronLabs VoiceChat 11B: https://huggingface.co/nvidia/NVIDIA-NemotronLabs-VoiceChat-11B
- VoiceChat serving branch and API: https://github.com/NVIDIA-NeMo/Speech/tree/nemotron-labs-voicechat
- VoiceChat paper: https://arxiv.org/abs/2609.21967
- Block Dessa: https://block.xyz/dessa
- Voice AI Space on Dessa, 24 October 2026: https://www.voiceaispace.com/articles/block-launches-dessa-a-full-duplex-speech-model-trained-only-on-audio
- Meta Calling API pricing: https://developers.facebook.com/documentation/business-messaging/whatsapp/calling/pricing
- Meta WhatsApp Business Platform pricing: https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing
- Twilio WhatsApp pricing (Guyana listed as Other): https://www.twilio.com/en-us/whatsapp/pricing
- Product idea: [whatsapp-personal-channel.md](whatsapp-personal-channel.md)
- Phase 1 chat spec: [SPEC-whatsapp-personal-channel-chat.md](SPEC-whatsapp-personal-channel-chat.md)
- Broker spec: [SPEC-realtime-voice-broker.md](SPEC-realtime-voice-broker.md)
- Broker plan: [PLAN-realtime-voice-broker.md](PLAN-realtime-voice-broker.md)
