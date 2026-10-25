# Workspace voice and embedding models

The [semantic search improvement contract](../platform/knowledge-retrieval-v1.md)
distinguishes the initial hardening delivery from remaining resource, migration
and integration gates. This guide describes the implemented configuration.

Settings → AI Models → Models groups configuration into a single-open accordion:
Language & multimodal (existing chat profiles and Light/Heavy routing), Embeddings
(workspace semantic search), Images and documents (attachment processing), and
Voice (transcription). Providers remains the
single private connection/key catalog. Model editors use those saved connections;
Voice input owns microphone, language and shortcut preferences and links back to
AI Models. Mutation results use toasts; failed editors retain drafts.

## Embedding choice

Local/deployment defaults remain unchanged, including existing profile IDs and
local capability-search embeddings. The current owner may select an owned OpenAI,
OpenRouter, Ollama or custom OpenAI-compatible connection, an embedding model ID,
and 16–2,000 output dimensions. Anthropic is excluded because it offers no native
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

Image analysis uses the configured Heavy model if it supports vision, otherwise
the configured vision-capable Light model. It does not require another provider
key or an embedding model. With no compatible route, images retain file details
without generated descriptions. Automatic processing sends a resized image to
that provider and can incur usage charges; turning it off prevents new admitted
analysis, but cannot recall a request already dispatched. Analysis has no chat
history or tools. See the [attachment contract](attachment-agent-contract.md)
for access checks, source identity, format limits and reprocessing behavior.

## Voice choice

The workspace owner can inherit the existing legacy/deployment voice selection,
select a saved OpenAI connection and transcription model, or choose browser-only
recognition. Voice is independent of chat setup: no chat model or duplicate API
key entry is required. Existing settings continue working until explicitly changed.
Only OpenAI streaming transcription is currently supported; other provider
connections are not offered as voice choices. Live resolution follows provider
key rotation and owner changes. An unavailable explicit provider falls back to
browser recognition, rather than silently substituting a different provider key.
Removing a connection referenced by an actively owned workspace is refused until
its model is reassigned. After ownership transfer, a former owner can still
remove their private provider; stale foreign-workspace references cannot block
revocation and cannot fund model requests.

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
