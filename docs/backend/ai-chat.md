# Resident chat and turn lifecycle

The product chat API discovers providers and their agents, creates scoped threads, and manages messages, turn streams, cancellation, and relevant work state. Current route declarations include `/chat/providers`, `/chat/threads`, thread messages, and enabled event/stream controls. Use the deployed OpenAPI schema for exact request shapes.

## Authority and records

Core derives principal, workspace, binding, and route authority. ChatThread and ChatMessage are product records; HarnessSession is rooted beneath its thread. Provider session handles cannot choose tenancy. Current access is checked for subsequent tools and resumed work.

One thread's active binding and session generation determine continuation. Changing a binding preserves historical sessions but cannot silently execute under a mismatched scope. The product transcript remains distinct from private model history, traces, checkpoints, and effect receipts.

## Image input

Native chat accepts PNG, JPEG, WebP and GIF through the ordinary persisted
chat-upload endpoint and `attachment_ids` (up to four images per turn, 5 MB and
25 million pixels per image). It does not send a duplicate inline `images`
payload. Current accepted message parts are converted to Pydantic AI
`BinaryContent`; the model receives pixels alongside the exact authored text.
The selected model must support image input. Images are untrusted evidence,
never approval or host instructions. Filing remains a separate reviewed action.

Image preparation also accepts text-only durable system messages for approval
outcomes and other host continuations; only user messages may introduce new
image pixels. These internal receipts remain in storage and audit but do not
render as conversation bubbles after reload. Current image history is still
revalidated at dispatch. Approval readback uses exact resource IDs from scoped
receipts, never the proposal's prose as proof that a change succeeded.

Before every physical model attempt, including retries and restored-history
requests, Core checks workspace membership, thread ownership, canonical upload
attachment, scanner visibility, current owning-entry read access and canonical
workspace, stored MIME/size and SHA256, and actual image format/dimensions.
Remote image URLs are not fetched. Changed, detached or revoked files fail
closed before SDK dispatch. Image-bearing history retains its encrypted native
checkpoint for follow-up questions after reload, rather than using the text-only
conversational shortcut. The existing WorkItem, lease, replay, admission and
usage-accounting contracts remain in force.

## Ordinary and durable turns

Native chat uses durable WorkItems by default on the supported PostgreSQL store. An accepted turn is independent of its SSE connection: closing or refreshing the browser only ends event delivery, and the transcript can reattach to the accepted WorkItem. If a worker lease is reclaimed, Core reconciles the prior deterministic run against the WorkItem's terminal status or newer leased attempt before applying the usual checkpoint and receipt checks. This closes only a run proven to have lost its execution fence; it does not replay uncertain tool effects. Unverified mutations still require outcome reconciliation. Set `INTEGRAL_NATIVE_DURABLE_CHAT_ENABLED=false` only to opt into legacy request-bound execution; that mode does not provide process-restart recovery.

Durable admission records accepted identity and content. Workers verify canonical message parts, metadata, parent, and encrypted typed host context against the fingerprint before provider preparation. Lease token and fence control execution. Stable message writes and event replay must agree after interruption.

The durable event contract accepts completion timing from both conversational and operational execution, including `modelPassMs`. Usage completeness flags and content-free request dimensions survive event replay and saved transcript projection under the same public-field allowlist. Provider contract tests normalize actual emitted events; PostgreSQL acceptance verifies atomic completion and exactly one saved assistant response. Worker failures retain server-side exception diagnostics while public errors exclude private exception details.

The collapsed work trail shows one short line based on observable activity:
preparing a response, reading information, checking search readiness, reviewing
results, or writing the answer. Cancellation and retry notices take priority.
Tool arguments and private model reasoning never supply this label.

A successful approved build clears its pending-design state. Once the same
turn's permission-checked verification confirms the build, the setup workflow
reserves a tool-free closing response with links from that readback. It must not
reopen a batch or rediscover capabilities after completing the approved setup.
Partial, denied and failed verification retain their qualified outcomes.
History summaries operate on completed turns only; unchanged retained history
is not summarized again while the active turn grows, and summarization does not
consume the final response reservation. Request and token limits remain enforced.

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


## Agent navigation

`integral_open_view(target_kind, target_id, view_id?)` prepares a local App,
collection or record destination; a collection may name an existing saved View.
This is an ephemeral presentation request, classified as a read because it
changes no stored data. Current resource and optional saved-view permissions,
structural workspace membership, and the owned browser chat are checked before
returning the destination. It cannot navigate to arbitrary URLs or change scope.

The public stream projects a dedicated `navigation` event containing only the
local path, owned thread and workspace IDs; raw tool arguments/results stay
private. Only the originating visible tab's live navigation event is consumed, after a
successful turn. History, recovered work, canceled/failed turns, other threads,
changed workspaces/routes, and a nonempty composer do not trigger navigation.
The browser uses the ordinary router and keeps the conversation available in the
companion panel. Opening a page does not prove that a mutation succeeded.

Local candidate evidence for images, approvals, navigation and their validation
limits is recorded in the native image-input tests under `backend/tests/native_harness/`.

### Chat identity and first-turn workspace orientation

For an interactive first user turn from an account whose `onboarded_at` is unset,
the native provider derives a brief orientation from authenticated user state
and bounded canonical thread-to-message edges. Both the lightweight and
operational model paths receive the guidance. No client flag grants authority;
approval continuations, background origins, established conversations and
onboarded accounts do not trigger it. The greeting does not mark setup complete
or change records. A specific user task takes precedence over a generic tour.

Inline message and tool errors use a collapsed summary with an explicit
click/keyboard disclosure. Live and restored assistant metadata retain the
API-supplied error message for that disclosure. Tool disclosures extract only
the explicit message/code, not arbitrary result or argument objects. Cancellation
and model-setup guidance keep their separate presentations.

The orientation check resolves both sign-in account IDs and linked workspace
profile IDs through the shared authenticated-user resolver. A sign-in ID must
not be looked up directly as a workspace profile ID.

Every native model turn, on both the lightweight and operational paths, receives
the current signed-in profile's bounded display name when available, using the
same identity resolution as `integral_whoami`. This context is separate from the
first-turn orientation, so established conversations and onboarded accounts can
also be addressed naturally. The name is quoted data, never instructions or
permission authority. Missing names and legacy email-shaped names are omitted;
the assistant must not infer a name from an email address. No identity discovery
tool or additional model call is needed just to use the name, and later replies
must not repeatedly greet the user or repeat their name mechanically.

### Working context and long conversations

The native harness expires older skill-load pairs and tool discoveries before
preparing the next model step. It retains the current and immediately preceding
task plus a 32 KiB historical skill allowance and 16 recent tool disclosures.
Unsettled tool calls and pending staging proposals protect their source task;
these protections can exceed the normal working-set allowance and are subject
to final input admission. Expired skills remain discoverable and can be loaded
again. Discovery uses the authorized Core search rather than sending every
installed skill description with each model request.

A single native tiered pipeline cheaply clears classified read results and then
summarizes older dialogue when required. Recent oversized read results become
explicitly truncated previews; mutation receipts, current user input and
pending proposals are never cheap-cleared. Summary calls retain native usage
accounting. The normalized working history is checkpointed, and older encrypted
snapshots remain available through scoped conversation search. Existing bloated
sessions receive this policy on their next valid resume.

Default pressure/target estimates are 24,576/16,384 input tokens. Final admission
reserves configured output and 10% model-window headroom (at least 1,024 tokens),
caps input at 65,536 tokens, and assumes a 32,768-token window when the native
model profile/registry has no window. These are operational estimates, not
provider billing measurements. Image input reserves an additional estimated
4,096 tokens per image rather than counting compressed bytes as text. Existing
upload byte/pixel and permission checks still apply. Protected input that cannot
fit returns the existing context-limit outcome before inference. Smaller
models, or many unresolved proposals, may require narrowing the task.

The adapter keeps projected instruction parts and the recorded current request
in agreement for native estimation. Measured history reclaim corrects the old
usage anchor without changing its reported token counts or billing records.
New provider responses establish fresh anchors. Content-free context metrics
are logged alongside native compaction spans and existing physical-request
component sizes; diagnostics must never retain prompt/record/key contents.
See I-CONTEXT-02 and the context lifecycle qualification report.

## Completion links

Successful create/update responses carry permission-checked `result_links`.
The same projection serves model output, approved-change cards and their saved
transcript navigation. It does not change mutation authority or receipts.

- App changes link to the App when it contains zero or several Tracks. Exactly
  one actual attached Track links directly to that Track, if readable.
- A single affected Entry links to the Entry on its canonical Track. Several
  Entries link to their containing Tracks, without individual Entry shortcuts.
  Multiple tool calls in one turn and multiple approved writes in a Prompt Sheet
  count together. Bulk acknowledgements resolve their complete successful ID set.
- Dashboard changes open the particular dashboard. Home changes open App Home.
- Failed, skipped, deleted and unexecuted proposals do not produce destinations.

Destinations are built from canonical graph location, current workspace and role
checks, and current resource labels. Model-provided parent IDs and URLs are not
trusted. A bounded two-neighbour read distinguishes one Track from many without
hydrating an entire collection. Prompt Sheet grouping uses the already checked canonical parent metadata and adds
no asynchronous reads inside its queue mutation/save window. No inferred single-Track shortcut is based only
on the subset returned by a tool. Link read failures cannot invalidate committed
writes. The projection preserves I-ACCESS-01, I-CHAT-01, I-APPROVAL-02/05 and the
existing graph/write boundaries; it adds no persisted Node or Edge type.

The final answer retains its prose and gains missing destination links. Verified
App-build completions use the same selection rules. The model receives the
canonical links and completion instructions; output normalization redirects an
App shortcut to its sole Track and removes individual affected Entry shortcuts
when several Entries were changed. Read-only source citations are unaffected.

App destinations accept `?section=home` and
`?section=dashboards&dashboard_id=<opaque-id>`. Explicit destinations override
remembered tab state; normal App navigation still restores its saved preference.
Dashboard selection updates the query so switching and reload agree. These links
navigate only when clicked; they do not replay automatic agent navigation.

## Response latency and activity

`INTEGRAL_CHAT_WORKERS` selects 1–16 durable chat workers per API process
(default 4). Independent conversations can proceed concurrently; per-thread
admission, leases and cancellation still serialize a single conversation.
Deferred discovery reuses immutable description embeddings keyed by document
hash and embedding model. Current authorized candidates, query embeddings,
ranking and tool results are not cached.

Activity distinguishes queue delay, preparation, first provider output, first
requested/started/received tool, first operational tool, first answer text and
completion. Discovery is not an operational tool. Browser receipt timings include
transport and replay delay; they are separate from server/provider timings.
Validated answer buffering remains in place. A fast provider first token is not
a guarantee of equally fast displayed answer text.

The activity table reports each physical call and an aggregate total across
calls; that total is not one request's context size. Tool cards expose safe
outcomes, returned counts and elapsed time, without private reasoning or raw
arguments. The fallback status is “Responding…”; activity is described as
“Activity, tool results, and model usage are shown here.”
