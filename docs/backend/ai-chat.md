# Resident chat and turn lifecycle

The product chat API discovers providers and their agents, creates scoped threads, and manages messages, turn streams, cancellation, and relevant work state. Current route declarations include `/chat/providers`, `/chat/threads`, thread messages, and enabled event/stream controls. Use the deployed OpenAPI schema for exact request shapes.

## Authority and records

Core derives principal, workspace, binding, and route authority. ChatThread and ChatMessage are product records; HarnessSession is rooted beneath its thread. Provider session handles cannot choose tenancy. Current access is checked for subsequent tools and resumed work.

One thread's active binding and session generation determine continuation. Changing a binding preserves historical sessions but cannot silently execute under a mismatched scope. The product transcript remains distinct from private model history, traces, checkpoints, and effect receipts.

## Ordinary and durable turns

Native chat uses durable WorkItems by default on the supported PostgreSQL store. An accepted turn is independent of its SSE connection: closing or refreshing the browser only ends event delivery, and the transcript can reattach to the accepted WorkItem. If a worker lease is reclaimed, Core reconciles the prior deterministic run against the WorkItem's terminal status or newer leased attempt before applying the usual checkpoint and receipt checks. This closes only a run proven to have lost its execution fence; it does not replay uncertain tool effects. Unverified mutations still require outcome reconciliation. Set `INTEGRAL_NATIVE_DURABLE_CHAT_ENABLED=false` only to opt into legacy request-bound execution; that mode does not provide process-restart recovery.

Durable admission records accepted identity and content. Workers verify canonical message parts, metadata, parent, and encrypted typed host context against the fingerprint before provider preparation. Lease token and fence control execution. Stable message writes and event replay must agree after interruption.

The durable event contract accepts completion timing from both conversational and operational execution, including `modelPassMs`. Usage completeness flags and content-free request dimensions survive event replay and saved transcript projection under the same public-field allowlist. Provider contract tests normalize actual emitted events; PostgreSQL acceptance verifies atomic completion and exactly one saved assistant response. Worker failures retain server-side exception diagnostics while public errors exclude private exception details.

The collapsed work trail shows one short line based on observable activity:
preparing a response, reading information, checking search readiness, reviewing
results, or writing the answer. Cancellation and retry notices take priority.
Tool arguments and private model reasoning never supply this label.

Model streams have both a total request deadline and a configurable silence
limit (see [configuration](../ops/CONFIGURATION.md)). A timeout before output
uses the existing bounded model retry policy; a timeout after output preserves
an uncertain outcome and does not replay tool effects. Neither deadline is
extended by browser reconnects; the total request deadline also bounds streams
that keep sending chunks without finishing.

Graph transaction bodies remain cancellable on disconnect. Their acquisition and commit/rollback cleanup are shielded from ASGI cancellation in the owning task, so rollback completes before returning the connection to its pool. Disconnecting an SSE reader does not cancel the separate durable worker.

## Collection changes and retrieved evidence

`integral_bulk_update_entries` stages the same patch for up to 500 explicitly
selected records under one itemized approval. Page through the full authorized
selection before claiming a whole-collection update. Every selected Entry must
pass workspace/policy/schema admission; revisions and authority are rechecked
at execution. Unknown requested record types are refused before mutation.

Durable per-record receipts preserve completed steps across interruption. A
later failure can stop remaining work after some records changed: report applied
and unresolved results and reconcile the original command identities on retry.
This is not a transaction across all records and does not create a new grant.

An App owner can configure that App's current Tracks through ordinary reviewed
model tools, subject to canonical containment and current access. This exception
does not make App admins Track admins or grant package-library publication.
See the [access contract](../product/ACCESS_CONTROL_CONTRACT.md).

For prose/RAG, use [bounded retrieval](semantic-indexing.md) and the returned
source paths. Preserve relative `action_url` values; never invent a deployment
host. Source record links stay inside the active installation and tab. External
websites remain external. Use structured collection queries for complete
selection and aggregates; semantic evidence does not establish exact totals.

## User experience

Streaming deltas, tool results, review state, final answer, and cancellation have distinct meanings. Reloading a page must not be presented as cancellation. Product readback establishes the final transcript; work and effect receipts establish execution outcomes.

Human-facing text resolves opaque IDs to readable names. Machine arguments retain canonical identity. Review cards should accurately distinguish proposed, approved, applied, failed, canceled, and uncertain states.

See [resident harness](../product/RESIDENT_HARNESS.md), [prompt queue](prompt-queue.md), and [qualification](../ops/QUALIFICATION.md).
