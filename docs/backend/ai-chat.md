# Resident chat and turn lifecycle

The product chat API discovers providers and their agents, creates scoped threads, and manages messages, turn streams, cancellation, and relevant work state. Current route declarations include `/chat/providers`, `/chat/threads`, thread messages, and enabled event/stream controls. Use the deployed OpenAPI schema for exact request shapes.

## Authority and records

Core derives principal, workspace, binding, and route authority. ChatThread and ChatMessage are product records; HarnessSession is rooted beneath its thread. Provider session handles cannot choose tenancy. Current access is checked for subsequent tools and resumed work.

One thread's active binding and session generation determine continuation. Changing a binding preserves historical sessions but cannot silently execute under a mismatched scope. The product transcript remains distinct from private model history, traces, checkpoints, and effect receipts.

## Ordinary and durable turns

Native chat uses durable WorkItems by default on the supported PostgreSQL store. An accepted turn is independent of its SSE connection: closing or refreshing the browser only ends event delivery, and the transcript can reattach to the accepted WorkItem. If a worker lease is reclaimed, Core reconciles the prior deterministic run against the WorkItem's terminal status or newer leased attempt before applying the usual checkpoint and receipt checks. This closes only a run proven to have lost its execution fence; it does not replay uncertain tool effects. Unverified mutations still require outcome reconciliation. Set `INTEGRAL_NATIVE_DURABLE_CHAT_ENABLED=false` only to opt into legacy request-bound execution; that mode does not provide process-restart recovery.

Durable admission records accepted identity and content. Workers verify canonical message parts, metadata, parent, and encrypted typed host context against the fingerprint before provider preparation. Lease token and fence control execution. Stable message writes and event replay must agree after interruption.

The durable event contract accepts completion timing from both conversational and operational execution, including `modelPassMs`. Usage completeness flags and content-free request dimensions survive event replay and saved transcript projection under the same public-field allowlist. Provider contract tests normalize actual emitted events; PostgreSQL acceptance verifies atomic completion and exactly one saved assistant response. Worker failures retain server-side exception diagnostics while public errors exclude private exception details.

Graph transaction bodies remain cancellable on disconnect. Their acquisition and commit/rollback cleanup are shielded from ASGI cancellation in the owning task, so rollback completes before returning the connection to its pool. Disconnecting an SSE reader does not cancel the separate durable worker.

## User experience

Streaming deltas, tool results, review state, final answer, and cancellation have distinct meanings. Reloading a page must not be presented as cancellation. Product readback establishes the final transcript; work and effect receipts establish execution outcomes.

Human-facing text resolves opaque IDs to readable names. Machine arguments retain canonical identity. Review cards should accurately distinguish proposed, approved, applied, failed, canceled, and uncertain states.

See [resident harness](../product/RESIDENT_HARNESS.md), [prompt queue](prompt-queue.md), and [qualification](../ops/QUALIFICATION.md).
