# Native harness review remediation — 2026-10-07

## Scope and evidence

Base: `codex/pr-113-staging` at `d542ffe5`. Input: the unpublished review and source overlay in `/tmp/integral-core-venture-smoke-fix`, based on the same commit. Preserve that checkout and its evidence. Implement generic Core repairs here; founder routing, milestones, wording targets and evidence criteria remain in Business. Use the configured DeepSeek default. No dependency patches, alternate planner or user-text intent gates.

## Implementation sequence

| Work package | Implementation | Required acceptance |
| --- | --- | --- |
| H1: host continuation | Bind/reset actual authored text; textless host runs pass `None` to Pydantic AI. Current scoped receipts supersede old proposals. Host directives remain instructions, never synthetic human messages. | Approve and reject in two unrelated Apps, independent saved-state readback, no reapproval/replay. Failure/expiry/Stop/recovery must preserve uncertainty and authorization. |
| H2: record identity | Resolve proposed type against the destination's current type catalogue after view/default resolution. Exclude only verified different types. Shared body/field prose is not identity. Exact same-type titles require clarification or an explicitly separate staged create. The separate-create preview must disclose that existing records are retained; human approval still governs execution. | Different verified types with the same title; same-type duplicate; distinct titles sharing prose; unknown legacy type remains conservative; explicit separate create without lexical parsing. Both create and filing paths use one guard. |
| H3: moderation input | Parse Markdown through a maintained public parser and collect text/code/HTML contents while removing formatting delimiters. Preserve obfuscated characters and enclosed words, including multiline/nested emphasis. Normalize only moderation input; stored Markdown is unchanged. | Clean headings accepted and actual profanity blocked on authenticated/public create, update and comments. Regression inputs include multiline, nested, code, literal and obfuscated content. No whitelist or bypass. |
| H4: context | Extend per-physical-call content-free diagnostics with stable schema fingerprints/repetition and available cached tokens, timing and usage. Compact only terminal, server-verified staged snapshots into receipt summaries; preserve pending proposals, exact active turns, loaded skill contracts, scopes, identifiers and latest outcomes. | Demonstrate reduced serialized terminal proposal size with identical receipts; measure cold/warm/long small turns on unchanged data. Distinguish characters, provider tokens, cache reports and nullable cost. Correctness is a gate before efficiency claims. |
| H5: qualification | Run focused contracts, full local gate and real browser scenarios across unrelated Apps. Inspect authored messages and current receipts. Exercise skill transitions, rejection, failed application, expiry, interrupted run and recovery. | Record each observed result and limitation. No assertion of full reliability based on a single App or a mocked contract. Preserve the original findings and identify remaining provider/live-vendor variants. |

## Invariants preserved

- I-HARNESS-01, I-WORK-01/02/03/05: authenticated tenant/session binding, fenced execution, exact receipt/effect identity and fail-closed approvals. No approval is created by narration or receipt compaction.
- I-APPROVAL-01/02/03: canonical execution and original actor attribution; rejection cannot emit or replay a write.
- I-CRUD-01, I-GRAPH-01/02: existing typed canonical writes, rooted graph and encrypted Object history remain authoritative. No new persistence primitive or floating node.
- I-RET-01/04, I-APP-05/06, I-SKILL-SCOPE-01: visibility and destination resolution remain scoped. Type hints never widen access.
- I-SUBSTRATE-01, I-EXT-01, I-SKILL-01..04: no App-specific rules in Core and no nonstandard skill frontmatter.
- I-CONV-01..03: established endpoint/error/schema conventions retained.

## Delivery

Update an execution ledger beside this plan with exact source, regression and browser evidence. Keep browser qualification separate from full suite/CI/package gates. Commit only after clean gates. Publication and production qualification are separate actions.

### H5 finding discovered during execution: approval polling

Browser rate limiting caused the approval sheet to disappear while its canonical proposal remained pending. `getPromptQueue` swallowed transport errors as an empty queue, and refresh depended on unstable runtime handles, rebuilding polling/listeners on renders. Remedy: failed refresh is not empty state; preserve the current scoped review, expose a retry status, bound/coalesce refreshes, back off with Retry-After, keep runtime callbacks in a ref, and prevent late results from another conversation overwriting the active review. Verify failure/recovery and conversation switches with hook regressions and deployed UI. Keep existing rate limits enabled.

### H5 finding discovered during execution: late cancellation

A real reload during expiry disconnected a streaming host continuation. Its late host cancellation arrived after the provider generator cleaned up and set a thread-wide pre-start cancellation marker. The next authored request was cancelled before model dispatch. Remedy: bind the host cancel hook to an individual Pydantic `CancellationToken`; clean it up with that run, and ignore hooks whose token no longer owns the thread. Preserve cancellation before initial generator advancement. Verify late cleanup, pre-start Stop, active Stop and subsequent same-conversation recovery without changing model or resetting history.

### H4 finding discovered during execution: completed-write identity

The chat transcript retained the executor's generated record IDs, but the native checkpoint refresh consulted only the current staging token, whose snapshot omitted those IDs. Terminal tokens also leave the pending store after restart. Remedy: supplement exactly scoped current state with server-patched terminal envelopes from the owned thread's rooted message collection, using the public paginated graph API. Current live decisions take precedence; foreign bindings, human text and pending transcript snapshots cannot supply authority. Preserve executor results and rollback evidence. Direct known-ID readback is permitted; broad App queries still require declared capabilities. Use the library's public `ClearToolResults` at a 16,384-token context threshold, retaining five tool pairs and loaded capability contracts. Compare actual per-call metrics in the existing long conversations.

### H5 finding discovered during execution: repeated confirmation notes

The approval action and polling reconciliation can both return the same resolved review. Funnel both paths through one per-review continuation guard, keyed by thread and durable queue item IDs rather than wording alone. A new review with identical wording remains a new event. Bound this UI cache, retain server admission fencing and distinguish UI deduplication from durable cross-client exactly-once delivery.
