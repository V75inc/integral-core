# Engineering review remediation — execution record

Source assessment: [Core engineering review](2026-10-07-core-engineering-review.md).
Branding, source review and terminal chat stream settlement are committed and pushed as `6f2d1372` on `codex/pr-113-staging`. The original findings and browser evidence remain the assessment record.

## Implementation contracts

- Preserve I-SUBSTRATE-01 and I-EXT-01: domain-neutral Core contracts; vendor adapters map payloads and Apps consume published SDK/facade surfaces.
- Preserve I-GRAPH-01/02: typed entries and structural edges commit together; scalar lease/accounting records use Object rather than floating Nodes.
- Preserve workspace and principal authorization, field protection, schema/revision validation, policy staging and effect fences. Existing approval cannot authorize a materially different effect.
- Use public Pydantic AI/Harness extension surfaces for disclosure, history and execution limits. No extra planner, lexical intent gate, third-party source patch or duplicate loop controller.
- Separate execution outcome from accounting completeness; retain known token/cost facts exactly once per physical request. Unknown prices remain unknown. BYOK attribution and Business billing are distinct.
- Keep current chats and data. Qualification uses the isolated `integral-pr113` deployment, realistic user prompts and explicit test fixtures. Source tests, PostgreSQL concurrency proof and browser UX proof are separate acceptance gates.

## Work and acceptance ledger

| Item | Required change | Acceptance | State |
|---|---|---|---|
| R1 | Workspace/instance scoped connector identity | Same upstream source in two workspaces stays isolated; legacy ambiguous matches cannot mutate | Source + PostgreSQL verified; browser pending |
| R2 | Shared typed entry commands for sync | Schema, relations, tags, revisions and rooted graph semantics match canonical CRUD | Source + PostgreSQL verified; browser pending |
| R3 | Durable exclusive connector sync lease/fence | Manual/scheduled workers converge; expired owners cannot commit | Source + PostgreSQL verified; browser pending |
| R4 | Reconcile partial model usage | Cancel/failure retain known tokens/cost; late facts do not double count | Source verified; browser Stop/continuation passed; priced-provider pending |
| R5 | Remove failed-attempt read suppression | Failed read can retry; identical read after write sees the write | Source + browser read/update/repeat verified |
| R6 | Framework disclosure/history and context attribution | Controlled cold/warm/long conversation lookup; authorized tools available; per-request context evidence | Partial browser proof; acceptance still open |
| R7 | Compound related CRUD batch | One coherent preview/approval creates related records; rejection has no effects | Partial browser proof; acceptance still open |
| R8 | Schema-bound field normalization | Unknown/ambiguous/duplicate mappings surface errors before any effect | Source + browser known/undeclared-field case verified; ambiguous variants pending |
| R9 | Extension recovery scoped by identity | Failed view A does not poison healthy view B; retry can renew handshake | Source + browser failure/independent-view/Retry verified; scope/expiry variants pending |
| R10 | Authoritative empty extension catalogue | Undeclared extension view fails compile, including with an empty manifest | Source verified; browser pending |
| R11 | SDK optimistic update contract | Published protocol accepts revisions and matches runtime; stale update has no effect | Source verified; browser pending |
| R12 | Documentation/legacy/hygiene | Current harness/scope docs, explicit compatibility inventory and warning baseline | Source verified; browser pending |
| R13 | Group Edit Layout with view actions | Layout opens/closes beside active view controls; absent from record filters | Source + desktop browser verified; variants pending |

A finding is closed only when its relevant acceptance gates are recorded below. Browser tests use layperson language and do not coach discovery/tool selection.

## Evidence

Initial commit gate: `make verify` passed (frontend 1,454 tests plus CI-faithful smoke and full backend suite). PostgreSQL-only skips are not concurrency qualification.

### Current implementation and qualification checkpoint

All R1–R13 have source changes in this worktree; this does **not** close their browser and deployment acceptance gates. No remediation commit has been pushed yet.

- Connector identity is now runtime-owned (`connector:v2`, workspace + instance + upstream key). Lookup is destination/provenance constrained; ambiguous legacy records fail for review rather than being adopted. Two bindings intentionally maintain independent records even in one workspace.
- Connector effects use the canonical typed create/update commands. PostgreSQL commits structural/type/relation/tag edges and durable event facts with the effect. Sync ownership uses an Object lease, native conditional claims and fencing in the effect transaction; cursor saves are fenced, and scheduler tasks are supervised.
- Six disposable PostgreSQL contracts passed: exclusive claims, expired-owner rejection, rollback with the fence, typed rooted sync with two durable event facts, concurrent revision conflict, and entry-hook rollback. These are local concurrency/graph evidence, not live Gmail or QuickBooks qualification.
- Accounting keeps known partial usage/cost on interrupted requests, reconciles physical-request identities and keeps unknown prices null. Focused partial/late/duplicate tests passed; priced-provider browser cancellation remains unqualified.
- Read-signature suppression and duplicate broker quotas were removed. Current scope, policy, staging and durable effect checks remain; Pydantic AI owns bounded execution. Retry/read-after-write unit checks passed; live query → update → identical query remains required.
- Structured compound batching returns an explicit result reference. Schema normalization accepts only stable keys or declared schema names, and unknown/ambiguous/colliding data fails before staging.
- Extension failure is identity-scoped and retry renews the host/handshake. Authoritative empty extension declarations reject unresolved references. Published SDK optimistic-update keyword matches runtime; an external SDK-only client passed independent strict mypy checking with `expected_record_revision`.

### Browser receipts (existing populated workspace and chat)

The isolated `integral-pr113` stack continues to use PostgreSQL and DeepSeek V4.1 Flash Cloud. Neither the workspace nor the conversation was reset.

| Input / action | Result | Accounting / evidence |
|---|---|---|
| “Who is in my customer list?” (warm, before history persistence fix) | Ana returned correctly, one tool call | Run `7c6f5f1a-eb93-413f-a672-af682a7ea544`: 33,026 input + 716 output tokens, 4.8s. Still too expensive. |
| “Please add Ravi as a customer, his red Giant bicycle, and a repair job to replace the brake pads. The job is waiting and promised for Friday.” | One approval card with all three linked records | Run `3d5de6f0-a691-4af0-a9f7-bb9c49ef35ee`: 445,467 input + 10,350 output tokens, 11 model calls, 50.8s. Correct proposal, unacceptable repeated context cost. |
| Approve that card once | All three records saved; the job links to Red Giant, has the promised date and appears on the Waiting board | [Linked repair readback](assets/2026-10-07-remediation/linked-repair-readback.jpg). The actual entry dialog closed normally. Approval follow-up remained expensive (~215.9k total tokens). |
| “Who is in my customer list?” after first compaction experiment | Ana and Ravi correct, one call; durable history still grew | Run `5eb7afec-98c0-4122-8620-f1137c999a20`: 43,227 input + 434 output tokens, 7.2s. This falsified the first performance hypothesis. |
| Edit Layout beside view controls | Opens/closes normally; removed from record filters | [View actions](assets/2026-10-07-remediation/view-actions-dark.jpg); three component tests passed. Responsive and permission variants remain to qualify. |

### History correction and compatibility boundary

Per-request telemetry now records character counts for instructions, conversation, tool results and tool schemas, plus message/tool counts. These are diagnostics, not billing token estimates. The populated chat had ~14,448 instruction characters and ~23,550 tool-schema characters before payload/history contributions.

The pinned Pydantic AI version treats replacement of `ModelRequestContext.messages` as request-only. Library compaction therefore reduced an individual request without persisting that working history into later runs. Integral's thin public adapter now writes the processed history through documented `RunContext.messages`, retains typed skill-load evidence and current registered tool availability, and uses library summarization with both message-count and token triggers. Old scoped checkpoints remain searchable. Twenty-seven adapter contracts passed, including compaction followed by a **fresh Agent factory** resuming the conversation, active skill retention and summary-request usage accounting. No private library imports or third-party source edits were added.

That history checkpoint is superseded by the later existing-chat measurements below. R6 remains open for controlled cold/warm and long-history variants. No token ceiling increase is claimed as a remedy.

### Legacy inventory / hygiene

- New `integral_propose_design` calls require a typed blueprint at dispatch. Structured approval/build fidelity is tested independently of prose (`test_exact_blueprint_plan_builds_without_prose_extras`).
- Prose-only scaffold checks remain only for older persisted design markers/recovery and the selectable compatibility harness. They are not the authorization contract for new typed designs. Removing that migration path without handling existing markers would break old chats; it remains an explicit compatibility boundary.
- Root/agentive guidance now reflects Integral AI as the default and supplied malformed workspace headers as errors. The absent `AGENTIVE_ENABLED` kill switch is not reintroduced. The obsolete old-wheel transaction fallback was removed; the pinned jvspatial public transaction API is required.
- Frontend warning baseline: 452 warnings, 0 errors (453 before this work). No claim that all baseline warnings are defects or resolved. Touched extension lifecycle checks and view-action tests passed.
- Full verification exposed and corrected entry creation passing a Personal Workspace fallback into an explicitly addressed org Track. Headerless direct-resource creation now binds the authorized target; a supplied mismatched workspace remains rejected. Sample-consumer fixtures now create real declared tags and the consumer resolves scoped tag identities and uses the canonical command, rather than relying on invalid free-form tag IDs.

### Remaining qualification

Browser access and authentication are working. Repeat read after update, rejected-write readback, compound verbal approval, attachment filing, unknown-field handling, and extension failure/healthy/Retry have browser receipts below. Remaining variants include cold/long-history measurements, ambiguous attachment destinations, workspace/expiry extension cases, SDK invocation/readback, responsive/permission controls, live vendor integrations and priced-provider interrupted accounting.

### Follow-up qualification and defects discovered

- Full `make verify` passed again (`/tmp/integral-remediation-verify7.log`): 254 frontend files / 1,466 tests, CI-faithful smoke, guards, format/type gates, clean wheel/import checks and full backend suite. Cancellation-accounting and SDK-command refinements made afterward require another full gate before commit.
- The first persisted-history benchmark returned the right records at 60.6k tokens (4 physical requests), followed by 45.1k tokens (4 requests) after catalogue change. A compound contact/date update consumed 339.5k tokens / 19 requests and duplicated both proposed edits. This is a failed efficiency/approval acceptance case, not a passing result.
- The public history adapter now compacts only the completed-turn prefix, retaining the active user/tool sequence exactly. This prevents mid-turn summarization from erasing pending batch evidence. Exact retries of revision-bound updates reuse their batch step; distinct edits/revisions and all create operations remain distinct. Fifty-three history/batch contracts passed.
- The duplicated proposal was rejected. Its follow-up was stopped after three successful read tools. A new user message hit reconciliation because three model dispatch intents had only two response observations: cancellation fencing had also rejected the final accounting append. Terminal accounting now binds to the encrypted authorized intent independently of new effect authority. Cancelled history can continue only after original effect receipts reconcile; accounting uncertainty stays recorded, and unknown mutations still block. Seventy-one focused transport/accounting/recovery contracts passed. Existing-chat browser recovery is being qualified.
- The published SDK's runtime update facade now uses the shared transactional typed-update command, rather than materializing relations before checking a stale revision. This preserves scoped App authorization and deferred event delivery while bringing revision-before-effects and transactional graph/hook semantics to SDK callers. Thirteen SDK/operation/query tests passed.
- Four declared saved views previously appeared as only three tabs because deduplication grouped distinct extension views by renderer/slice. Tab identity now includes the saved name and complete canonical configuration; equivalent definitions still deduplicate. Four focused tests passed; all four tabs appeared in the real browser.
- Extension recovery was exercised through actual temporary API unavailability: one panel showed the failure fallback, a different panel then connected after recovery, and **Retry view** renewed the original panel's handshake and connected it. No UI request mocking, sandbox widening, or workspace reset was used. Evidence: [failure](assets/2026-10-07-remediation/extension-failed.jpg), [independent healthy panel](assets/2026-10-07-remediation/extension-healthy-after-failure.jpg), [Retry connected](assets/2026-10-07-remediation/extension-retry-connected.jpg).
- An explicitly empty extension declaration fixture failed catalogue compilation and was unavailable for installation. A visible browser diagnostic for that catalogue failure, cross-workspace panel recovery, token-expiry recovery, and live vendor integration remain unqualified.

### Existing-chat recovery and compound updates — latest browser round

- Recovery after Stop completed in the same chat. The first readback correctly showed blank Ravi phone and original 2026-10-09 job date, but improperly re-proposed rejected work. No write was approved. This exposed a semantic regression: earlier user commands were being treated as backlog. The provider now explicitly instructs the primary model to fulfil the latest request and not revive rejected/stopped work without a fresh request; no lexical classifier or tool gate was introduced.
- Rejection then completed normally at 24.7k total tokens, followed by the original current-state question: correct live values, no approval card, 36.8k tokens / 2 requests / 4.6s. The actual Ravi entry dialog showed a blank phone and closed normally ([readback](assets/2026-10-07-remediation/rejected-contact-readback.jpg)).
- Fresh explicit update: “Please save Ravi's phone number as 600-0123 and change his brake-pad job's due date to next Tuesday.” One card with exactly two edits, 83.9k tokens / 4 requests / 8.0s ([preview](assets/2026-10-07-remediation/two-edit-preview.jpg)). Compared with the earlier failed 339.5k / 19 requests / 77s attempt, total billed tokens fell about 75% and latency about 90%. This is one observed compound case, not a universal benchmark.
- Ordinary-language “Go ahead” applied that exact card. Readback returned phone 600-0123 and promised date 2026-10-13, with both revisions now 2; 56.2k tokens / 4 requests / 11.4s. The identical follow-up query returned both updated values at 19.1k tokens / one request / 3.1s. Direct Customers and Repair Jobs UI readback confirmed them, and both dialogs closed normally ([job readback](assets/2026-10-07-remediation/updated-job-readback.jpg)).
- Local single-process fallback entry locks now use weak references, retaining active owner/waiter serialization and releasing idle entry identities. Two regression contracts plus 26 provider contracts passed. PostgreSQL transaction ownership is unchanged.


### Receipt dependency, field handling and cancellation — additional browser round

- A real composer upload of a plain-text test receipt exposed a missing staging path: the attachment stager queried the graph for a pending batch entry, so all three supported references failed with “Entry not found”. The incomplete create-only card was rejected; no Parts record was written. This failed attempt cost 371.3k tokens / 13 requests / 51.3s.
- Attachment staging now resolves earlier pending create references using the existing batch validator/resolver within the bound principal and conversation. Upload ownership, exact thread and workspace are checked before staging. Destination permission/policy checks run again during execution after its reference resolves. No new placeholder syntax or bypass was added. Fifty-three attachment/batch/executor contracts passed.
- Retry: “Please file this receipt for me, and keep the file with its record.” One combined card contained the new Parts record and original upload (194.6k tokens / 8 requests / 37.1s). Ordinary “Go ahead” created it and read back its attachment (95.3k / 5 requests / 20.8s). Direct entry UI confirmed BP-100, quantity 12, unit price 1200, Georgetown Cycle Parts and the original receipt in **Attachments 1**. Currency/reference/date/total remain in the purchase body rather than invented fields. The dialog closed normally ([receipt proof](assets/2026-10-07-remediation/receipt-entry-attachment.jpg)). Correctness passed; attachment context cost remains high.
- “We used two pairs … set the quantity … to ten. Also record a reorder level of four.” Proposed only the defined stock field and explained that reorder level is undeclared. “Go ahead with the stock count only. Leave the fields as they are.” updated stock to 10, revision 2, without altering the schema or storing an invented field. Original purchase facts and receipt remained unchanged.
- Stop during a read-only run produced durable `responded` and `cancelled` model outcomes for its two physical dispatches. Follow-up “How many brake pads are on the shelf?” succeeded in the same chat (28.1k tokens / 2 requests / 9.8s), without replaying the cancelled request. This exposed a stale empty “Working” row: the frontend now settles the draft before transport unwind/follow-up transcript capture and shows a compact **Stopped** label. Six targeted runtime tests passed. Deployed Stop followed by “How many brake pads are on the shelf now?” returned 10 with no stale Working row ([continuation](assets/2026-10-07-remediation/stop-continue-stock-readback.jpg)).
- Qualification exports now read physical request observations after authorizing the owning run and validating every encrypted observation's original principal/workspace/thread/run. Known partial usage and decimal cost survive Stop; late facts do not add billable calls or rewrite terminal status. Twenty-six execution/accounting/export tests passed. Commercial invoice policy remains outside Core.
- Full `make verify` passed through round 9: 254 frontend files / 1,466 tests, CI-faithful smoke, guards, pinned format/type gates, clean wheel/import checks and full backend suite. Round 10 found an ESLint require-yield error in the new cancellation test; corrected before commit. Round 11 passed after that correction; the subsequent final round 12 is recorded below.
- Desktop layout-designer open/close proof: [designer](assets/2026-10-07-remediation/view-actions-designer.jpg). The attempted viewport override did not change the actual browser viewport; it is **not** mobile qualification.


### Controlled lookup and settled Markdown qualification

Identical input in the same populated workspace: “Who is in my customer list?” No workspace data was reset. These are single-run measurements, not percentile or provider-independent guarantees.

| Conversation | Physical requests | Reported input | Reported output | Total | Latency |
|---|---:|---:|---:|---:|---:|
| Fresh conversation, no previous skill activation | 5 | 58,538 | 780 | 59,318 | 10.2s |
| Immediate repeat in that conversation | 1 | 17,675 | 470 | 18,145 | 3.4s |
| Original long Bike Repair Shop conversation | 3 | 59,767 | 2,857 | 62,624 | 12.3s |

All returned Ravi and Ana with the correct phone/blank fields. The long case included one summarization request and one real query. No unavailable remembered-tool recovery occurred. Cold run `8aafd565-b8b6-490e-ac87-baac345cf5f8`; warm `594c48e2-295f-44be-9081-c9b748fd58c8`; long `e14c350f-e389-46f8-8456-9192b7e827cf`. Component observations distinguish the long chat's larger activated tool surface (45 tools, ~42.5k schema characters) from the fresh chat's 25 (~23.6k). This remains a measurable optimization target; do not hide it by resetting history.

The cold response also exposed a rendering defect: Message debug held the complete table, while the visible smooth Markdown renderer remained at a partial URL with a streaming cursor after settlement. Integral's public renderer adapter now enables smoothing only while the owning message is running. Settled/interrupted text renders directly through the same library primitive; no dependency patch or Markdown heuristic was added. Two lifecycle tests passed. Browser readback of the same stored answer and a new warm answer confirmed both complete customer links, values, trailing paragraph and absence of the stuck cursor ([render/readback](assets/2026-10-07-remediation/cold-warm-customer-readback.jpg)).

Provisional follow-up measurement targets: cold lookup <= 80k total tokens, warm <= 25k, long populated <= 80k, compound two-record update <= 150k. These are engineering targets based on this small baseline, not qualified release p95 budgets. Freeze a representative multi-run/provider sample before claiming a production budget. Combined attachment filing remains above 150k and must not be called efficiency-complete.


### Destructive approval wording — newly discovered defect

The natural delete request produced a card claiming “Soft-deletes … Reversible by an admin”. Reinspection showed the canonical executor calls `entry.delete(cascade=False)` and removes comments/sharing sidecars; only its retrieval embedding is soft-deleted. The card was rejected, and live readback confirmed the record and stock 10 survived. Tool documentation and the staged preview now state permanent deletion, possible policy-authorized child-track cascade, attachment visibility loss and no undo. No deletion/recovery mechanism was invented. Focused staging/manifest contracts passed. Deployed browser retest showed the corrected permanent/no-undo card and matching model explanation; the proposal was rejected ([preview](assets/2026-10-07-remediation/permanent-delete-preview.jpg)). Permanent deletion itself has not been approved or exercised in this round.

Full `make verify` round 12 passed: 255 frontend files / 1,469 tests, full backend suite, CI-faithful smoke, pinned formatter/lint/type gates, clean wheel/import checks and repository guards. The subsequent destructive-preview change is text-only and has focused staging/manifest tests; final CI reproduction (round 27) and staged hooks (round 27) also passed after both final declarative/text corrections.

The attachment skill now explicitly exposes `integral_cancel_batch` and directs the primary model to cancel an incomplete prepared record/file set instead of committing only the create. This is declarative workflow guidance through existing tools, not another planner or text gate.


### Final deployment readback and commit gate

The final API image was rebuilt and deployed on the isolated 9107 stack without resetting workspace data. Sign-in succeeded using the local `.env` credentials without displaying them. Browser readback confirmed the Parts record still has stock 10, BP-100, original purchase facts and exactly one receipt attachment after the rejected permanent-deletion proposal. The entry dialog closed normally, and Edit layout remains grouped with the selected view controls ([final readback](assets/2026-10-07-remediation/final-stock-receipt-readback.jpg)).

Full behavior gate: `make verify` round 12 passed. Subsequent text-only delete-preview and declarative attachment-cancellation guidance passed focused contracts, final `make verify-ci` and staged hooks. The changes are ready for the authorized remediation commit/push. The unqualified cases listed above remain open; this checkpoint does not assert complete production or live-vendor qualification.
