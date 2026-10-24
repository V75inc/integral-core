# Workspace voice and embedding models

Settings → AI Models → Models groups configuration into a single-open accordion:
Language & multimodal (existing chat profiles and Light/Heavy routing), Embeddings
(workspace semantic search), and Voice (transcription). Providers remains the
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
40-second total limit, an 8 MiB identity-encoded response budget, and at most 32
passages with connect/read timeouts. They validate response
indexes, dimensions and finite numeric values, and expose sanitized failures.

Changing a choice persists configuration, a durable rebuild cursor and audit
intent in the graph transaction. Each workspace/provider/model/endpoint/dimension
and composition identity has a separate vector space. Rebuilds scan bounded pages
and verify canonical containment. Workers reject superseded jobs and check active
profile, owner, generation, lease and source before publication. Searches use the
same selected profile. A workspace provider failure can degrade that request to
graph retrieval without disabling semantic search for other workspaces. Queued
rebuild status is deliberately not a claim of complete coverage; existing Track
search-status tools retain bounded source/receipt/vector coverage checks.

Remote spaces use the pinned local tokenizer for **passage composition**, recorded
in profile format v3; this is not a claim that providers share its tokenization.
Provider context limits are enforced by the provider. Ollama truncation is
explicitly disabled; unsupported/oversized input fails rather than silently
publishing partial text. Old local format-v2 identities remain stable. Retained
old spaces are derived data; this change does not add a vector-retention cleanup
policy.

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
