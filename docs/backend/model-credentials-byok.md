# AI models, providers and BYOK

In **Settings → AI Models → Quick Configuration**, choose a provider and save its
API key to set up the supported model defaults. The card summarizes the current chat, vision, embedding and voice assignments. Choose **Change provider** in its header to edit the connection. Open Advanced Configuration, then Providers & Models to see the same
saved settings in the Models and Providers tabs. Open an individual section
under Model Assignments to set its model. The registered model
catalog supplies compatible models; it is separate from the active assignments.
Dictation preferences are available separately inside Speech to Text.

## Providers and model profiles

A private provider connection owns an encrypted key and immutable provider/endpoint
identity. Profiles select that connection, a model ID and supported tuning.
Changing provider or endpoint requires a new connection. Editing or testing an
existing connection never returns its key to the browser. Legacy active
credentials appear as an idempotent connection/profile snapshot; reading settings
does not activate or rewrite them. Activation maintains the legacy API projection.

Supported controls include reasoning effort or thinking budget, output tokens,
temperature/top-p, verbosity and applicable local context-window settings. The
server and browser use the same model capability contract; unsupported settings
are rejected. Request/idle timeouts, request/tool counts, total tokens and turn
time can lower deployment ceilings, never raise them. Unset values inherit
provider/deployment defaults. Testing a provider establishes access, not support
for every model, tool or tuning parameter. All update feedback uses toasts.

## Deployment limits

Under **Settings → AI Models → Deployment Limits**, anyone with access to AI Models can see the effective installation limits for native chat. A platform administrator can choose **Edit deployment limits** to change model requests, tool calls, total tokens, turn duration, provider request timeout, or stream idle timeout. The form shows the operator’s maximum for each field. Leave a field blank to inherit that maximum. **Use host defaults** clears all six overrides after **Save limits**.

Saved limits apply across workspaces as new turns execute. A running turn keeps its budget. A recovered durable turn retains its original deadline and cannot gain budget from a subsequent increase. A model profile can impose a tighter limit. The token budget uses reported provider usage, so a single response can cross it. Changing these numbers does not alter tool approvals, provider credentials, or worker concurrency.

## Preferred models and role suitability

Choose **Google (Gemini)** in the first AI Models card and enter a Gemini
Developer API key from Google AI Studio. Integral validates the key with
Google’s models endpoint, encrypts it on the server and reuses the connection
for chat, vision and optional search embeddings. The key field stays blank
when you return. Google account sign-in and Vertex AI credentials are separate
from this integration. [Google API keys](https://ai.google.dev/gemini-api/docs/api-key)
and [Models API](https://ai.google.dev/api/models).

### Choose a vision model

Expand **Model Assignments → Images and documents**. Select a registered
**Vision model** directly in the dropdown and choose **Save vision model**,
or leave it on **Automatic**. The dropdown lists saved profiles from
**Providers & Models** that support image reading. Register another compatible
model there to make it available here. For image-reading models such as GPT-6.1
Sol that accept images but require a different API for tool calls, the attachment
worker requests JSON and validates it against the same image-description schema.
[GPT-6.1 Sol modalities and endpoints](https://developers.openai.com/api/docs/models/gpt-6.1-sol).
Automatic follows the active provider’s registered vision preset.
For older configurations it checks the assigned Heavy, then Light chat model
for image support. An explicit choice survives a provider change. Use **Image processing preferences** to turn automatic image analysis on or
off. The saved model choice is retained. These settings govern descriptions
of image attachments. PDF and Office text extraction runs independently.

### Provider presets

These are recommended starting points for the transports included in Integral.
Quick Configuration registers the main and specialist profiles before assigning models. Add other providers or model IDs under **Providers & Models**. The [assignment guide](voice-and-embedding-models.md) describes the default for each role and the search consent step. A model that covers two roles is registered once. Changing the active
provider’s key keeps its chat tuning. Switching to another provider selects
its main model in single-model mode. Light profiles are available if you
choose automatic Light/Heavy routing. Model access depends on the account.

| Provider | Main | Light | Vision for image descriptions | Search embeddings | Dictation / live voice |
| --- | --- | --- | --- | --- | --- |
| OpenAI | `gpt-6.1-sol` | `gpt-5.4-mini` | `gpt-5.4-mini` | `text-embedding-3-small` | `gpt-transcribe` / `gpt-realtime-2.1-mini` |
| Anthropic | `claude-opus-5-5` | `claude-haiku-5-5` | `claude-sonnet-4-6` | — | — |
| Google (Gemini) | `gemini-3.6-flash` | `gemini-3.1-flash-lite` | `gemini-3.1-flash-lite` | `gemini-embedding-001` | — |
| OpenRouter | `openai/gpt-6.1-sol` | `openai/gpt-5.4-mini` | `openai/gpt-5.4-mini` | `openai/text-embedding-3-small` | — |
| Ollama Cloud | `deepseek-v4.1-flash` | `gpt-oss:20b` | `gemma4:31b` | — | — |
| Ollama Local (advanced setup) | Choose an installed model | `gemma4:e2b` | `qwen3-vl:4b` | `embeddinggemma` | — |
| Custom OpenAI-compatible (advanced setup) | Operator-supplied ID | Operator-supplied ID | No verified preset | Operator-supplied ID and tested dimensions | — |

A dash means there is no Integral preset for that provider and role. The
OpenAI voice defaults apply to untouched roles. Explicit voice choices and
working inherited dictation are preserved. Embedding profiles are registered
for review. Sending workspace text and rebuilding an index still require the
owner’s explicit embedding selection and consent. Local models require a
separate installation on the configured Ollama daemon.

**Selection evidence, reviewed November 24, 2026**

- OpenAI documents image input and text output for GPT-5.4 mini. Its smaller footprint fits frequent attachment descriptions. Dictation and two-way audio have separate model contracts. [Vision](https://developers.openai.com/api/docs/models/gpt-5.4-mini), [transcription](https://developers.openai.com/api/docs/models/gpt-transcribe), [realtime](https://developers.openai.com/api/docs/models/gpt-realtime-2.1-mini) and [embeddings](https://developers.openai.com/api/docs/models/text-embedding-3-small).
- Anthropic’s Sonnet supports image interpretation and Haiku is intended for fast extraction and routing. Anthropic directs embedding users to other providers, which require their own credentials. [Claude model comparison](https://platform.claude.com/docs/en/models/overview), [vision](https://platform.claude.com/docs/en/build-with-claude/vision) and [embeddings](https://platform.claude.com/docs/en/build-with-claude/embeddings).
- Google’s stable 3.6 Flash supports agent workflows. Flash-Lite is designed for lower-latency multimodal extraction. The text embedding preset uses 768 dimensions for Integral search. Google also offers `gemini-3.5-transcribe` and `gemini-3.8-live`. Integral’s dictation and live voice adapters speak OpenAI’s protocols, so quick setup leaves these roles alone. [Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.6-flash), [Flash-Lite](https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-lite), [embedding dimensions](https://ai.google.dev/gemini-api/docs/models/gemini-embedding-001), [Google transcription](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-transcribe) and [Gemini Live](https://ai.google.dev/gemini-api/docs/live).
- OpenRouter lists these exact publisher-qualified models for image input and search. Its transcription endpoint has a separate request contract from Integral’s dictation transport. [Vision model](https://openrouter.ai/openai/gpt-5.4-mini), [embedding model](https://openrouter.ai/openai/text-embedding-3-small) and [audio API](https://openrouter.ai/blog/insights/every-modality-one-api/).
- Ollama’s hosted catalog lists `gemma4:31b` with vision and tool capabilities. For a local daemon, Qwen3-VL provides a smaller vision-language option, and EmbeddingGemma is a dedicated local encoder. Custom endpoint IDs cannot establish modality or account access on their own. [Hosted tags](https://ollama.com/api/tags), [Qwen3-VL](https://ollama.com/library/qwen3-vl) and [EmbeddingGemma](https://ollama.com/library/embeddinggemma).

The versioned harness policy separates candidate availability from observed
Integral workflows; neither is certification. OpenAI Sol has limited workflow
observations, Ollama DeepSeek has user-reported scaffolding success, and the
other provider routes remain candidates. Provider catalogs and model IDs can
change. Validate representative workflows before relying on a configuration.

Known light models are allowed only in the light role of a dual setup, not as
the single/default, heavy, or fixed conversation model. Unknown advanced models
remain configurable with an unverified-suitability warning. Normal preferred
choices have plain labels and no warning banners; warnings appear only for
unsuitable roles or unverified selections. Failure guidance suggests a heavy
model without automatically changing providers, spending more, or assuming the
model rather than the harness caused the failure.

Anthropic Opus 5.5 requires adaptive thinking, automatic tool choice and preserved
signed thinking across tool calls; it does not accept legacy thinking budgets,
thinking-off, temperature or top-p. See the publisher's
[migration contract](https://platform.claude.com/docs/en/models/opus-5-5/migration-guide).
Model metadata is distinct from end-to-end transport and workflow verification.

## Single or automatic routing

Single model is the default. A workspace owner may select Automatic (light +
heavy), assigning an owned Light profile and the default Heavy profile. Light
handles conversation without workspace tools; operational work escalates to
Heavy. Both roles share one principal, turn budget, deadline, approval boundary
and operational checkpoint. There is no peer-agent or delegation layer.

Saving a profile does not enable routing. Save the routing configuration to
activate the pair. Assignment validates ownership, credential/endpoint identity
and tool support, using revision checks. Assigned or conversation-referenced
profiles cannot be removed until reassigned; referenced providers cannot be
removed. Key or route changes are rechecked before physical model dispatch.

The chat's brain menu selects Automatic, Heavy only or a saved profile for that
conversation. Its collapsed Reasoning section exposes only settings supported by
the selected models. Per-role overrides live on the thread, never contain keys,
and do not modify shared profiles or operator ceilings. Model changes clear
incompatible conversation reasoning choices. Reset restores workspace routing.

## Deployment settings and funding

`INTEGRAL_AGENT_KEY_MODE` selects `hybrid`, `byo_strict` or `platform_only`.
Resolution uses the current workspace owner's configuration, not an arbitrary
member's personal key. Hybrid permits the owner's saved model to override ENV
settings and uses permitted platform fallback otherwise. Strict BYOK requires
that owner's usable credential; it never silently borrows a platform key.
Platform-only ignores personal model routes and exposes server-managed settings.
ENV keys are never copied into personal Providers.

Deployment routing uses:

- `INTEGRAL_NATIVE_ROUTING_MODE=single|light_heavy`.
- `INTEGRAL_NATIVE_MODEL` for Heavy/default and `INTEGRAL_NATIVE_LIGHT_MODEL`
  for optional Light, using the supported provider/model route syntax.
- `INTEGRAL_NATIVE_HEAVY_MODEL_SETTINGS` and
  `INTEGRAL_NATIVE_LIGHT_MODEL_SETTINGS` as validated JSON tuning objects.
- `INTEGRAL_NATIVE_CREDENTIAL_REF` for the operator credential identity.

The ENV readout describes inherited settings. Under hybrid or strict BYOK,
workspace owners can add Providers and profiles through settings to override
them. See [deployment](../ops/DEPLOY.md#optional-native-lightheavy-model-roles)
for operator ceilings and [resident chat](ai-chat.md) for execution and timing.

## Security and persistence

Credentials use `INTEGRAL_CREDENTIAL_ENC_KEY`; previous-key configuration supports
rotation. OAuth has its separate jvspatial key. Keys never enter chat, manifests,
logs or public diagnostics. Private scalar configuration uses scoped `Object`
records; service-layer writes enforce current authority and revision checks.
Account deletion removes provider/profile records with the legacy credential.

Authenticated `/users/me/` APIs expose `ai-configuration`, `model-capabilities`,
`model-connections` (save/test/remove), `model-profiles` (save/activate/remove),
`model-routing` and `conversation-model`. Responses whitelist safe metadata.
The legacy primary/speech credential API remains a compatibility projection;
its obsolete slot names are not the new profile-based routing contract.

When repairing duplicate `UserModelCredential` records, query through
`GraphContext.find` before creating a unique index. Test real PostgreSQL duplicates
and rejection of another duplicate after repair. See the
[release gate](../ops/DEPLOY.md#jvspatial-compatibility-release-gate).

Voice preferences and provider-backed/local-default embeddings are documented in
[voice and embedding models](voice-and-embedding-models.md).
