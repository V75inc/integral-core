# Workspace voice and embedding models

The [semantic search improvement contract](../platform/knowledge-retrieval-v1.md)
distinguishes the initial hardening delivery from remaining resource, migration
and integration gates. This guide describes the implemented configuration.

In **Settings → AI Models**, choose a provider in **Quick Configuration**.
Integral registers its recommended model profiles in **Advanced Configuration →
Providers & Models**, then assigns the applicable defaults. Use **Add provider**
or **Add a model** in that catalog to extend the choices. Each assignment
shows the eligible saved profiles. A settings or preferences link appears under
the dropdown, aligned on the right, when there are additional controls.

| Assignment | Automatic selection and overrides |
| --- | --- |
| Language & multimodal | A new provider selects its main chat profile. Choose single-model or Light + Heavy routing and edit the selected profile’s settings. |
| Images and documents | Automatic follows the main connection’s saved vision profile, with compatible chat fallback. A saved explicit override or Off survives provider changes. |
| Speech to Text | OpenAI setup assigns transcription when dictation has no existing choice or effective model. Voice input preferences control dictation behavior. |
| Realtime Voice | OpenAI setup selects the supported realtime profile for an unconfigured workspace. Existing settings, including an explicit Off, are retained. |
| Embeddings | When provider encoders are supported and no choice has been saved, the dropdown preselects the main connection’s registered recommendation for review. The active label names the serving model. Provider processing starts only after consent and index verification as described below. |

## Embedding choice

Local/deployment defaults remain unchanged, including existing profile IDs and
local capability-search embeddings. Register the encoder on an owned OpenAI, Google (Gemini), OpenRouter, Ollama or custom OpenAI-compatible connection. For a custom model ID, select **Use as an embedding encoder** in the catalog. Choose the registered profile in **Embeddings** and review its 16–2,000 output dimensions under **Dimensions and consent**. Anthropic is excluded because it offers no native
embedding API. Model availability is provider-specific: Test model verifies a
synthetic phrase, never workspace content, and does not activate the selection.

Provider overrides require the PostgreSQL passage store. Atlas and graph-only
installations accurately expose their existing deployment configuration and do
not offer an incompatible vector override. `platform_only` disallows personal
provider changes. No new ENV keys are required; existing local embedding model,
revision, dimensions and passage settings remain the deployment defaults.

A provider selection requires explicit consent for indexed workspace text and
search queries to reach that provider. There is no ambient provider-key fallback;
keyless local/custom services omit Authorization. Keys remain encrypted in the
owner's private provider record and are resolved afresh before requests. Fixed
providers use fixed hosts; custom endpoints are validated and redirects refused.
Custom endpoints reuse the harness’s DNS-pinned outbound-request guard; proxy
ENV and redirects cannot move a request to another authority. Requests have a
1-second interactive query deadline (configurable) and a 40-second background limit, an 8 MiB identity-encoded response budget, and at most 32
passages with connect/read timeouts. They validate response
indexes, dimensions, finite numeric values and nonzero finite norms, and expose sanitized failures. Status polling performs no inference. See the [operating limits](../ops/CONFIGURATION.md#indexing-and-retrieval).

Changing a choice commits the desired model, durable build request and audit intent
together. The serving model is pinned separately: saving does not make an empty
new space active. A resumable Walker follows the workspace's rooted Track registry
and Entries with bounded keyset pages. A second pass verifies actual lexical/vector
manifests. Activation checks committed intent epochs, outstanding work, owner,
configuration revision, profile and the worker lease in one graph transaction.
Enumeration and queue exhaustion alone never mean ready. Progress is resumable
beyond 1,000 records; detailed counts are restricted to the owner.

New and edited content maintains both currently authorized serving/building spaces.
Continuing calls to a previous provider requires the explicit checkbox on each
changed selection; it is off by default. Revoked/removed keys, changed owner and
host policy stop further encoding. Existing authorized lexical projections remain
readable without funding the former model. A provider outage affects its workspace,
not the global semantic capability. Requests already dispatched cannot be recalled.

`POST /api/users/me/embedding-configuration/cancel` takes the current `revision`.
It restores the serving choice through the same owner/revision/audit transaction
and fences the superseded build. A removed or no-longer-owned provider must be
replaced with a working choice; cancellation cannot revive its authority.
Search readiness is a verified point in time, not a promise of zero lag for future
writes. The existing Track status API remains a bounded diagnostic. The remaining
schema/restore/lifecycle invalidation audit and production qualification are recorded
in [the implementation contract](../platform/knowledge-retrieval-v1.md).

Remote spaces use the pinned local tokenizer for **passage composition**, recorded
in profile format v3; this is not a claim that providers share its tokenization.
Provider context limits are enforced by the provider. Ollama truncation is
explicitly disabled; unsupported/oversized input fails rather than silently
publishing partial text. Old local format-v2 identities remain stable. Retained
old spaces are derived data; this change does not add a vector-retention cleanup
policy.

## Images and documents

The workspace owner controls automatic image processing under Images and
documents. The deployment setting `ATTACHMENT_IMAGE_DESCRIPTIONS_ENABLED` must
also permit it; unsupported stores expose the feature as unavailable. Native
document text extraction remains separate from this image setting.

Choose a registered profile in the inline **Vision model** dropdown, then
choose **Save vision model**. Choices come from **Providers & Models** and
include saved multimodal chat profiles with supported image input. A saved
provider connection alone contributes no model choices. **Image processing
preferences**, directly below the selector on the right, controls
automatic processing separately. The default model choice is **Automatic**. The automatic choice
follows the recommended vision profile on the main model’s saved connection.
Without that profile, older configurations continue to try compatible Heavy
and Light chat routes. An explicit selection is independent of the chat
default. It uses its own saved connection and stops serving if the profile
becomes unavailable. Off retains the choice. Model setup can be prepared on
a deployment where automatic processing is unavailable. See the
[provider recommendations](model-credentials-byok.md#provider-presets). With no compatible route, images retain file details
without generated descriptions. Automatic processing sends a resized image to
that provider and can incur usage charges; turning it off prevents new admitted
analysis, but cannot recall a request already dispatched. Analysis has no chat
history or tools. See the [attachment contract](attachment-agent-contract.md)
for access checks, source identity, format limits and reprocessing behavior.

## Voice choice

The workspace owner can inherit the existing legacy/deployment voice selection,
select an eligible registered OpenAI transcription model, or choose browser-only
recognition. Realtime Voice similarly offers Off or an eligible registered
OpenAI model. A voice model uses the saved provider connection from its catalog
entry. Voice is independent of chat setup: no chat model or duplicate API
key entry is required. Existing settings continue working until explicitly changed.
Only OpenAI streaming transcription is currently supported; other provider
connections are not offered as voice choices. Live resolution follows provider
key rotation and owner changes. An unavailable explicit provider falls back to
browser recognition, rather than silently substituting a different provider key.
Removing a connection referenced by an actively owned workspace is refused until
its model is reassigned. After ownership transfer, a former owner can still
remove their private provider; stale foreign-workspace references cannot block
revocation and cannot fund model requests.

## In-app realtime calls

The waveform control on Home or in native chat starts a call in the selected
authenticated workspace and agent profile. Changing either starts a new call. The relay requests one opening after readiness and yields to a
caller who speaks first. Recent activity in that same conversation may guide a
welcome back; this does not load a recap of past conversations. A voice-first
call creates and selects a conversation after provider readiness, and reuses
it on the next call. Delegated work is linked to that conversation. One live
call per person is enforced inside each API process; a deployment that runs
more than one API worker can still host concurrent calls for the same person.
Integral
does not automatically publish an ordinary spoken transcript to chat. To share
regular spoken content in chat, use the composer; the pilot does not post it
on a spoken request.

During a live call, use the microphone button to mute or unmute your input.
Mute disables the local audio track and drops queued capture frames, without
disconnecting the call, muting the assistant, or cancelling delegated work.
A new call starts with the microphone unmuted. Mute does not recall audio that
has already been sent.

The workspace assistant can read a supported reversible edit of the record
you selected by voice. For an explicit approve or reject request, a compatible
browser reads the actual change and a one-use challenge through a local device
voice or offline host synthesis. Mac hosts use the built-in speech synthesizer.
The Core runtime container includes eSpeak NG. Linux wheel installations can
install eSpeak NG as a host package. Generated audio is bounded and temporary.
The configured batch-capable Speech to Text model independently recognizes
your response. Only a matching, unexpired confirmation for that
exact current proposal enters the shared approval service. Other changes use
the authenticated approval card. Model tool calls and ordinary spoken
acknowledgments have no approval authority. Ending a call does not cancel a
pending approval. This pilot does not enable phone or WhatsApp voice.


### Live voice tool boundary

The [shared text and voice conversation contract](voice-chat-conversation-contract.md)
defines how both interfaces use the native harness and how to qualify live delivery.

The realtime model can search the canonical tool catalogue while Heavy is
running. Quick capability discovery is private to the default workspace
assistant. The first reviewed reads are visible apps, visible tracks, and
the caller’s accessible workspace names; workspace output identifies the
current workspace without switching to another one. A direct spoken navigation
tool reuses these reads and the canonical record listing service, holds a
bounded numbered list, including authorized saved views, and passes a revalidated selected location into native
chat. Chat status, saved-reply paging, explicit cancellation, queue and revise
remain separate voice lifecycle tools.

To add a quick capability, review a local, scoped, paginated GET binding and
its data classification, then enroll its one label-only collection in the
canonical manifest with `voice_quick_read`. No per-tool realtime schema
is needed. The dispatcher compares the offered manifest row with the
current row, applies normal tool/route policy, and reads one capped page.
The model selects one single-use opaque offer in a correlated text-only
response; it cannot send free tool arguments, target selectors, raw tool
names or a workspace ID. Only projected labels enter the spoken response.
A future argument-bearing enrollment would need an explicit reviewed
per-field schema and binds that never accept identity, scope, URLs, or
permissions from model input. Arbitrary app/connector queries, mutations,
approval tools, and selected profiles are not enabled by this change.
Unmatched or heavier work is still handed off to durable chat.

Provider tool summaries record fixed categories and counts only. They do not
record names returned by the tools, requests, transcripts, or identifiers.

### Durable continuation boundary

The server-side continuation adapter supports one explicit next request in the
normal chat follow-up queue, or an explicit refinement of the current, still
steerable voice-origin task. It does not change a typed worker's permissions,
implicitly convert a new request into a refinement, or replay a previous task.
A queued receipt is not a running or completed task. An accepted refinement is
not proof a worker applied it. Following an interrupted task, the normal
review-and-resume rule still governs the queue.

This adapter is for the default workspace assistant. Selected staff profiles use
a different execution path, so continuing work there requires separate profile
revision and steering support. Realtime voice does not advertise those tools or
their legacy aliases for selected profiles; an attempted continuation is
reported as unavailable without starting or editing another task. The default
assistant uses distinct commands for an explicit next task and a correction to
its current voice request. Diagnostics count requests and confirmed dispositions
without recording spoken content. Observer or deployment wiring must explicitly
opt in. Before a rollback to a build without this storage contract, disable
new voice continuation intake and finish or dismiss pending spoken follow-ups
in the supported build. Older workers reject the encrypted voice-marked queue
instead of stripping its no-voice-approval provenance.

## Invariants preserved

- I-GRAPH-01/02: choices live in a singleton WorkspaceModelConfiguration node
  attached to Workspace by HAS_MODEL_CONFIGURATION; scalar rebuild cursors are
  Object work records. Root validation, transactional creation and workspace
  deletion cleanup preserve graph reachability. Ordinary Workspace saves cannot
  restore stale model selections, and general workspace exports carry no private
  provider references.
- I-CRUD-01 and I-SUBSTRATE-01/I-EXT-01: service-layer mutations, domain-neutral
  configuration and existing authenticated, backend-authoritative scope resolution.
- I-RET-01..05: existing audience, query boundary, source, deletion and tag-only
  rules remain authoritative; provider embedding does not grant data access.
- I-RET-06/07: isolated spaces, durable intent, fenced publication, revision-bound
  owner configuration, transactional audit intent and truthful coverage.

## Native protocol references

- [OpenAI embeddings](https://developers.openai.com/api/reference/resources/embeddings/methods/create)
- [OpenRouter embeddings](https://openrouter.ai/docs/api_reference/embeddings)
- [Ollama embeddings](https://docs.ollama.com/api/embed)
