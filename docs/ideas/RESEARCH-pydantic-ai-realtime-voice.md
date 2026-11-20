# Research: Pydantic AI realtime voice

**Date:** 19 November 2026
**Status:** Planning and test only. No live calling and no production enablement.
**Source card:** [Pydantic AI realtime research and prototype direction](https://planner.cloud.microsoft/ui/web/plan/vGeDOHx9mUWOt37fjKxit2UACae2/view/board/task/p5F-NeI-m0KvotiFBD64emUAC05d?tid=1229a998-708d-402a-ba72-b5dccc4506f2) (18 November 2026)
**Stack checked:** `pydantic-ai-slim==2.59.0` and `pydantic-ai-harness==0.59.0` in `backend/pyproject.toml`
**Related:** [whatsapp-personal-channel.md](whatsapp-personal-channel.md), [SPEC-whatsapp-personal-channel-chat.md](SPEC-whatsapp-personal-channel-chat.md), [SPIKE-whatsapp-voice-jvvoice.md](SPIKE-whatsapp-voice-jvvoice.md), [SPEC-realtime-voice-broker.md](SPEC-realtime-voice-broker.md)

Prices are vendor list prices gathered on 19 November 2026. They exclude tax, regional uplifts, cached-input discounts, tool calls, and the cost of a growing conversation. Confirm the linked pricing pages before budgeting a prototype.

## Finding

The personal WhatsApp text channel can stay as it is. The 18 November card is a later voice prototype, and it does not require a change to that branch.

Phase 1 is signed jvconnect ingress, a native chat thread, durable text approvals, and settings. The card asks for a review of `Agent.realtime().session()`, a small set of broker tools, and an authenticated media bridge. Its own scope is planning and test only.

The note that needs a later rewrite is [SPIKE-whatsapp-voice-jvvoice.md](SPIKE-whatsapp-voice-jvvoice.md). That spike is not implemented. It assumes jvvoice does speech-to-text, Integral runs a full harness turn, and jvvoice does text-to-speech. The card uses a speech-to-speech realtime session and keeps jvvoice as the media bridge. Reconcile the spike after the text branch lands, before any voice code.

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

The implemented thread identity matters for any later handoff. The native turn worker and text-approve reject any `provider_id` other than `integral_native`, so the WhatsApp conversation is isolated on `provider_session_id=wa:{e164}`. The spike's wording `provider_id=whatsapp` does not match the code. A voice completion should route to that thread by session id.

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

WhatsApp, LiveKit, and Meta calling fees are outside these rates. The first proposed test is a fake session, so those fees stay at zero until a live call is explicitly enabled.

A first model trial can price `gemini-3.8-live` and `gpt-realtime-2.1-mini` against `gpt-live-1` plus its backend model, using the published rates, and then record the actual session cost.

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

## JV Voice and WhatsApp

Today's jvvoice path is Meta calls, a jvconnect forward, jvagent, then `POST /api/calls/accept` on jvvoice, then LiveKit and a worker that calls jvagent `/interact`. The Integral spike already says that path must reach Integral directly, with jvagent left out.

The card changes the brain. Pydantic AI does not terminate a WhatsApp call. Something still has to carry audio between Meta and `session.send_audio()` / `session.stream_audio()`, with authenticated session identity, reconnect, barge-in, cancellation, and duplicate-call protection. jvvoice is the candidate for that bridge. LiveKit Cloud remains a constraint of the current WhatsApp connector. Coexistence and WhatsApp Web do not support Cloud Calling. Meta calling eligibility, permissions, and account restrictions have to be confirmed before any live test.

The first test does not need that bridge. A fake session and one read-only broker tool are enough to prove context retrieval, authorization, and a concise reply.

## Sequence

1. Finish review of the WhatsApp text branch. Run `make verify-ci` before any push.
2. After that branch lands, update [SPIKE-whatsapp-voice-jvvoice.md](SPIKE-whatsapp-voice-jvvoice.md) so it records `Agent.realtime().session()`, the broker handoff, the corrected thread id, and the media-bridge role for jvvoice.
3. Prototype only in a fake session. Record installed versions, the extra packages required, provider, model, latency, authorization, approval handoff, and audit evidence.
4. Leave live Meta calls, production flags, and Core dependency changes until that evidence exists.

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
