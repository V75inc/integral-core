# Agent Chat UX Remediation Plan

**Status:** UX-01 through UX-08 implemented; release evidence and qualification limits recorded below

**Reviewed:** 2026-10-06; execution contracts and qualification gates strengthened

**Scope:** Integral Core resident chat, approvals, entry deep links/dialogs, reasoning/activity disclosure, and transcript scrolling

**Execution rule:** Implement in small, reviewable changes. Preserve the existing resident harness, approval authority, and multi-tenant boundaries. Do not change backend authorization or approval semantics to solve presentation problems.

## Execution closure — 2026-10-06

Implementation covers all eight work packages. See [execution evidence](evidence/2026-10-06-chat-ux-remediation.md) for live browser receipts, canonical Docker skill assets, schema review binding, accounting reconciliation, and qualification limits. Browser qualification uses ordinary requests against the Pydantic AI resident binding with DeepSeek, including batched records, verbal/button/Inbox decisions, attachments, publication, dashboard links, reload recovery, and narrow-screen chat. Deterministic guards cover denial/replay and stale-review cases. Broader provider and route permutations are not implied by this closure.

## Objective

Make the resident-agent experience clear and dependable during ordinary work: users can dismiss entry dialogs, understand and approve a single coherent proposed change, inspect a compact activity record when they choose, and follow a response as it grows. Preserve explicit user control over consequential writes and do not expose private model chain-of-thought.

## Evidence and confirmed observations

The local browser run created and populated a Home Maintenance app, queried records, updated status and cost, deleted one synthetic record, and read the final state in a new conversation. Those operations mostly succeeded. The run also found:

- Five separately approved record creations made a small app setup unnecessarily interruptive.
- A profile change was approved into a draft, but the system could not prepare the publish review; the field remained unpublished.
- A cost update changed the structured value but left old cost wording in the entry description. The Cost field is numeric and does not encode currency.
- A receipt attachment was staged against a vision-only image identifier. Approval returned `422 attachment_not_found`; readback confirmed the entry was unchanged and had zero attachments.
- The user reports entry dialogs opened from links cannot be dismissed except by browser-history navigation.
- The user reports repeated confirmation copy, missing inspectable thinking/activity content, and a transcript that fails to follow new output to the bottom.

These are separate concerns: the app setup, schema publication, and attachment failures are harness/product-flow findings; dismissal, copy, observability, and scrolling are frontend UX findings. Reproduce each against the current build before assigning a single root cause.

### Evidence discipline

Record the checkout SHA, working-tree changes, running UI/API build, provider/model, workspace, and conversation for each reproduction. Classify evidence as **user-reported**, **browser-observed**, **source-confirmed**, or **hypothesis**. An assistant's explanation of its own failed action is a diagnostic lead; establish the cause from the actual tool error/receipt and upload state.

Source inspection confirms that the current composite attachment adapter routes images to inline base64 with no server upload, while general files use the chat-upload endpoint. It also confirms that `draftToMessage` merges reasoning into a single part before tool-call parts. These establish implementation constraints, but do not prove every reported symptom has the same cause.

## Design decisions

1. **Approval remains effect-based.** Keep a clear review/approval step for writes that require it. Reduce repeated interruptions by grouping compatible writes into one review with an itemized summary, not by granting broad or future approval.
2. **One confirmation prompt per decision.** The server-held proposal and policy decision are authoritative; the card, chat, and inbox render that same proposal. The assistant message should not append a second equivalent “confirm this” instruction. Distinct high-impact confirmations remain separate only when policy requires a stronger decision. Clear verbal approval of one current proposal must remain supported, as specified in [APPROVAL_EXPERIENCE_PLAN.md](APPROVAL_EXPERIENCE_PLAN.md).
3. **Activity is collapsed by default.** Users can open it to inspect concise model-provided rationale/status, tool names, inputs at a safe level, outcomes, and failures. Do not reveal private hidden chain-of-thought. Provider reasoning payloads are optional and inconsistent; where raw reasoning is unavailable, show a short user-facing rationale or status summary and the full tool/effect receipt. Never fabricate a thinking stream.
4. **Following output is the default until the user scrolls away.** If the user intentionally scrolls up, preserve their position and offer a return-to-latest affordance; resume following when they return to the bottom.
5. **Motion is restrained and accessible.** Use the Integral mark or a small status indicator with a subtle transition/shimmer. Honor reduced-motion settings and keep a textual status available to assistive technology.
6. **Library ownership stays clear.** Prefer supported assistant-ui viewport, grouping, and attachment APIs before adding custom observers. Integrate provider differences through the existing adapters and public Pydantic AI extension points. Do not patch installed libraries or introduce a second run/approval state machine. Use shared UI typography, icons, and motion tokens.

## Work packages

### UX-01 — Entry dialog dismissal and URL state

**Priority:** P1

**Likely surface:** `frontend/src/pages/TrackDetailPage.tsx`, `frontend/src/components/entries/EntryDetail.tsx`, `frontend/src/components/ui/Modal.tsx`, and link/deep-link callers.

- Reproduce by opening an entry from a relation link, a chat/result link, and a direct `?entry=` URL. Try the close button, Escape, overlay behavior where supported, browser Back, and forward navigation.
- Trace the contract between `entryModal`, the `entry` query parameter, `closeEntryModal`, route changes, and deep-link navigation. A close action must clear local dialog state and its entry-related query context without navigating away from the underlying track. Back/forward must reopen or close only when the corresponding history entry represents that dialog transition.
- Check all `EntryDetail` renderers, including track, feed, shared-track, and linked-resource flows; do not fix one surface by breaking another.
- Make the close affordance visible, operable by keyboard, and correctly labelled. Preserve focus on the invoking link/control after dismissal.
- Define the history contract before changing it: a same-page user open may add one history entry so Back closes it; explicit close must remove entry-related parameters while preserving view, search, and filters. A direct URL must close to its underlying page even when there is no prior in-app history. Do not use unconditional `navigate(-1)` for explicit close.
- Invalidate or cancel a pending deep-link fetch on close, route change, or a newer open request, so a late response cannot reopen the dialog. Define Escape precedence for nested dialogs and dirty editors; protect unsaved edits using the existing edit contract.

**Acceptance:** Every supported entry-opening route has a clear close action; Escape closes when no inner editor consumes it; browser Back/Forward behaves consistently; closing leaves the user on the underlying page without requiring history navigation; direct deep links still open the intended record.

### UX-02 — Approval copy and review density

**Priority:** P1

**Likely surface:** `frontend/src/features/ai-chat/staging/StagedChangeCard.tsx`, its renderers, and assistant response/approval orchestration in `frontend/src/features/ai-chat/useAIChatRuntime.ts` and backend staging renderers.

- Inventory copy emitted by the model, staging renderer, approval card, review modal, and follow-up turn. Find duplicated prompts that ask the user to confirm the same change twice.
- Keep the card as the single source of truth for operation, target, affected fields/records, and approval action. The surrounding assistant text should briefly state what it prepared and point to the card, with no duplicate confirmation paragraph.
- Group compatible app/track/entry writes into one approval card with readable itemization. Preserve separate approval boundaries for distinct risk classes, high-impact actions, and material external effects.
- Keep failures and partial application explicit. Never render “approved” as “applied”; show an actionable failure and preserve readback/receipt evidence.
- Reuse the existing scoped decision service for both verbal and button approval. Bind decisions to the immutable proposal revision, principal, workspace, and conversation. If the proposal changes materially, invalidate the old approval and present the changed effects once.
- Remove repetition at the source of composition (proposal summary versus detailed diff versus invitation). Preserve meaningful repeated field values and user-authored text; avoid lexical gates or broad regex cleanup of arbitrary model answers.
- A batch is one consent decision, not an implied atomic transaction. Declare the existing executor's actual rollback/partial-apply semantics and show per-item applied/failed/not-attempted outcomes. Retrying resumes only unresolved items with their original idempotency identities.

**Acceptance:** A multi-record app setup with compatible writes requires one review decision, not one approval per record; approval copy appears once; distinct high-impact operations still receive their required confirmation; the applied result is independently read back before the assistant claims success.

### UX-03 — Inspectable reasoning/activity panel

**Priority:** P1

**Likely surface:** `frontend/src/features/ai-chat/components/Thread.tsx`, `Reasoning.tsx`, runtime stream normalization, and activity/tool result renderers.

- The current design intentionally keeps `WorkTrail` collapsed at rest and combines reasoning parts with tool calls under one disclosure. `Reasoning.tsx` already uses compact `text-xs` content and follows its own stream while open. Therefore, collapsed-by-default is intentional; reasoning/activity being unavailable after expansion is not.
- Trace provider events through `useAIChatRuntime` into assistant-ui message parts and the `WorkTrail` grouping. Test a reasoning-capable provider and one that emits no reasoning. Check whether rehydrated conversation history retains allowed reasoning/status data and tool steps.
- Make the disclosure label understandable (for example, “Activity” or “How this was handled”), with a live status while running and elapsed time/step count when complete. Expanding should show available user-facing rationale and chronological tool steps together. Use small, readable text (12px / `text-xs` baseline), constrained height, and internal scrolling.
- Do not expose hidden/private chain-of-thought or present provider-internal reasoning tokens as a faithful explanation. If only private reasoning is emitted, omit it and show a concise rationale/status plus the tool activity and result receipts. Clearly distinguish unavailable reasoning from an empty result.
- Keep the disclosure collapsed by default for new and historical turns; remember a user’s temporary expansion only where it does not cause future turns to open by default.
- Preserve a user's expansion through completion of that same turn. The current `WorkTrail` closes on completion; change this behavior so inspection is not interrupted. Future turns still start collapsed.
- Do not claim chronology from merged parts: the current runtime groups all reasoning first. Show an explicitly grouped explanation and ordered tools unless existing event identifiers can establish an interleaved order. If event ordering is required, reuse the stream's sequence/call identifiers and preserve them through rehydration.
- Do not add an extra model call to manufacture a missing explanation. Use provider-supported public summaries or existing safe activity events. Redact credentials and restrict attachment/tool details to resources the viewer can currently access; do not place raw payloads in analytics or exported evidence.

**Acceptance:** On a provider that supplies a supported user-visible reasoning summary, the expanded panel shows it alongside ordered tool activity; with a provider that does not, the panel still shows tools and outcomes without a fake thinking stream. The panel is collapsed by default, small in type, keyboard accessible, and does not expose private chain-of-thought.

### UX-04 — Agent working status and transitions

**Priority:** P2

**Likely surface:** `frontend/src/features/ai-chat/components/Thread.tsx` (`WorkTrail`, live synopsis, empty-running indicator) and Integral icon/motion tokens.

- Replace the pulsing dot and abrupt status-label replacement with one restrained branded activity treatment: compact Integral mark with subtle pulse/shimmer and a short text status.
- Use deterministic event/status changes (request received, finding capability, reading, preparing change, waiting for approval, applying, verifying) where supplied by actual runtime/tool events. Avoid turning hidden model reasoning into a “live thought” label.
- Animate label changes with a brief crossfade/height-stable transition; avoid jitter as streamed text changes. Respect `prefers-reduced-motion`; ensure status is exposed via `aria-live="polite"` without repeatedly announcing every token.
- On completion, transition to a stable summary such as elapsed time and step count. On failure, state that a step failed and link or disclose the useful error.
- Select one default treatment: a small Integral mark with gentle opacity motion, a short status label, and a 150–200ms label transition. Shimmer is optional only if the existing design system supports it. Avoid flashing. Keep the label stable between meaningful phase changes and do not infer a stall solely from elapsed time.

**Acceptance:** Status changes are legible, do not cause layout jumps, remain understandable without animation, and match actual run events. Screen readers receive useful status changes without token-by-token chatter.

### UX-05 — Transcript auto-follow and scroll recovery

**Priority:** P1

**Likely surface:** `frontend/src/features/ai-chat/components/Thread.tsx`, `ThreadScrollToEndOnSwitch.tsx`, and the assistant-ui viewport/list integration.

- The current custom scroll helper handles thread switches and a bounded load/stream window; verify whether new user messages, streamed assistant text, tool cards, approval cards, reasoning expansion, and late markdown/layout changes are observed after that window.
- Implement bottom-follow based on distance from the viewport end, not a fixed-duration pin. While the user remains near the bottom, follow content/size changes via the viewport/ResizeObserver lifecycle. If the user scrolls upward beyond a small threshold, stop auto-following and show a “Latest response” affordance.
- Resume following when the user selects the affordance or returns near the bottom. On sending a message, bring the new user turn into view; after expansion of activity/tool output, preserve context or follow only if already pinned.
- Avoid scrolling the page body when only the transcript viewport should move.
- Start with assistant-ui's supported follow/anchor behavior; fix competing scroll policies before adding another observer. Use one scroll owner and coalesce size changes per animation frame. Test late image loading, viewport/composer resizing, long-running turns beyond 15 seconds, and simultaneous background conversations. Scrolling must follow only the selected conversation.

**Acceptance:** Long streamed answers, long tool histories, approval/error cards, and expanded activity remain readable at the latest content while pinned; deliberate upward reading is not interrupted; a clear control returns to the latest output; narrow and full-page chat layouts both work.

### UX-06 — Related workflow correctness surfaced by the smoke run

**Priority:** P1

**Likely surface:** composer upload/materialization, attachment tools, staging executor, schema/profile publication review, and create-app batch orchestration. Keep this package separate from visual polish.

- Ensure a composer image intended as a file is materialized into a durable, workspace-scoped upload before offering “attach to entry”; vision-only image references must not be staged as upload IDs.
- Validate file existence, ownership/workspace, target entry, and supported attachment type before presenting approval. If the image is only available to vision input, offer a clear alternative (re-upload as a file) and do not stage a doomed action.
- Classify apply outcomes. For a confirmed permanent validation failure with no effect, translate the error, close the unusable proposal, and offer a new valid action. For transient failures, preserve the original operation for a safe retry. For timeout/disconnection or unknown outcome, reconcile the execution receipt and attachment list before retrying or closing. Never replay an attachment blindly.
- Repair the profile publish path so an approved draft either produces the server-computed diff and a publish review, or remains visibly draft with a recoverable next action. Never claim the new field is live before published-profile readback.
- For numeric costs, keep structured values authoritative and avoid stale duplicated prose. Either make cost descriptions derive from structured values or have the agent update related prose only when the user asks for that broader edit. Currency may come from supported schema metadata or an explicit user declaration; distinguish stored denomination from “GYD as you specified” rather than discarding the user's context or inventing a currency. Any schema improvement must remain generic across Apps and currencies.
- Batch compatible app/track/entry creation into one approval summary when risk policy permits; retain per-item attribution and verification.

**Acceptance:** The receipt workflow either files a real stored upload after one explicit approval and verifies it, or fails before approval with an understandable recovery path. Profile publication has a complete approve → diff → publish → readback lifecycle. Cost displays match the structured value; currency provenance and historical prose are clear. Compatible app setup does not trigger a separate blessing for every ordinary record.

**Bounded implementation units:** Execute UX-06a (upload/file identity and attachment recovery), UX-06b (profile draft/diff/publication recovery), and UX-06c (structured cost/currency presentation) independently. Approval batching belongs to UX-02 rather than a second implementation in UX-06. A user request to change only Cost must not silently rewrite free-form description text; surface any inconsistency briefly or show the structured value distinctly from historical prose.

### UX-07 — Token, cost, and latency readout correctness

**Priority:** P1 for incorrect accounting; P2 for display improvements

**Likely surface:** existing LiteLLM usage/cost adapter, run usage aggregation, persisted message metadata, and chat observability UI.

- Investigate the large token readouts seen during the smoke run. Determine whether each figure is per request, accumulated across model calls, cached input, output, reasoning-token usage, or duplicated aggregation. Large numbers alone are not evidence of a billing error.
- Carry provider-reported usage and LiteLLM cost provenance through the existing adapter and logging boundary. Count each provider request once using existing request/run identifiers; do not count reasoning tokens twice when they are already included in completion usage.
- Distinguish reported cost, estimated cost, and unavailable pricing. Never turn missing cost into `$0`; do not present a zero-price local model or an unpriced cloud model as a universally free call. Preserve bring-your-own-key and tenant attribution.
- Label elapsed time and cumulative call counts precisely. Preserve metadata after reload and in subsequent conversations; a UI event replay must not create another billing event.

**Acceptance:** A multi-call browser turn's readout reconciles with its request-level usage/cost records; streamed and non-streamed results use the same accounting contract; unavailable cost is labelled; reload/retry does not double count. Confirm frontend display separately from ledger correctness.

### UX-08 — App dashboards from ordinary user descriptions

**Priority:** P1 qualification gap; remediate only reproduced failures

**Likely surface:** `integral-dashboards` skill, dashboard discovery/create/update tools, `backend/app/services/dashboard_service.py`, widget data-source contracts, and the App dashboard renderer.

Dashboard creation was not evaluated in the earlier Home Maintenance CRUD journey. Tool availability is not proof that the resident can translate a user's requested overview into correct, useful widgets. Run this qualification before beginning remediation and record its findings alongside the other concerns.

- Ask for an overview in ordinary language without naming dashboard tools, widget types, field keys, or query configuration. The resident must discover the existing App, its published schema, records, and existing dashboards; select suitable supported widgets; and ask only for material ambiguity.
- Interpret the actual business fields: a Job's custom Status may differ from platform entry status; costs belong to Jobs rather than People & Trades. Bind widgets to the correct App/Track, business field, filters, units, and supported aggregation. Inspect the live widget contract rather than assuming that the starter suggester implements requested metrics.
- Reconcile every displayed count, total, grouping, and upcoming list with known synthetic records. Exclude deleted records; distinguish unknown cost from zero; avoid describing a mixture of estimates and actual costs as money already spent. A bounded result page must not be represented as an exhaustive aggregate.
- If a requested calculation or visualization is unsupported, explain the limitation and offer the closest accurate supported view. Never create a decorative chart, silently substitute all-record counts for unfinished jobs, or invent totals.
- Approve one concrete dashboard proposal, inspect the actual rendered App page, and reload. A persisted dashboard configuration is insufficient proof: verify widget data, loading/error/empty states, readable labels, and useful links to the underlying records.
- Ask for a modification in ordinary language. Update the same dashboard instead of creating a duplicate; preserve unrelated widgets. Change an underlying synthetic record and verify the affected widget refreshes correctly, including after reload.
- Verify permission filtering for widgets and drill-downs; use the existing open-Core versus declared-App query boundary. A hidden record must not leak through totals or groups. Do not introduce domain-specific Core branches to make the example pass.

**Acceptance:** The assistant creates and revises a useful App dashboard from a plain-language request; supported metrics and lists match the source records; unsupported metrics are disclosed; the dashboard survives reload and reflects subsequent record changes. Widget links follow UX-01 dismissal behavior, and dashboard writes follow UX-02 approval semantics.

**Initial fixture:** Home Maintenance contains two live jobs: kitchen tap (To do, due 2026-10-11, cost 25,000, Andre Test) and porch light (Done, due 2026-10-08, cost 9,200, Casey Test). The gutter job was deleted. Expected live-job count is 2, unfinished count is 1, and total recorded numeric costs are 34,200 if that aggregate is supported. Define whether “coming up” includes finished jobs before checking a list; use the browser run's current date and timezone. Record any intervening fixture changes before using these expectations.

**Layperson prompts:**

1. “Can you give my Home Maintenance app a simple overview page? I want to see how many jobs still need doing, what is coming up next, and how much all the jobs cost.”
2. “Put the unfinished jobs first and show who is doing each one. Keep the other useful parts.”
3. “The tap job is finished now. Mark it done.” Then inspect whether unfinished counts/lists change without recreating the dashboard.
4. On a separate synthetic App, “Give me a useful overview of this app,” to assess sensible suggestions without an explicit widget list.

### UX-08 initial browser findings (2026-10-06)

Environment: local UI at `http://localhost:9006`, Integral Smoke Tester workspace, Integral AI resident harness, DeepSeek `deepseek-v4.1-flash:cloud`, existing Home Maintenance fixture. This is one exploratory round, not completion of the dashboard qualification gate.

- **Browser-observed:** A plain-language request for a simple overview produced an eight-widget proposal. After approval, the App still had zero dashboards, including after reload. The approved action remained awaiting application without a useful visible error.
- **Assistant-reported diagnosis, consistent with source schema:** Count widgets contained `field: null`; application rejected that value because `DataSourceSpec.field` is a string with an empty-string default. Confirm the exact execution error/receipt during remediation. The corrected shape omitted the field. Validate the complete dashboard request against the canonical execution schema before staging approval so users cannot approve a payload already known to be invalid; any compatibility normalization belongs in the adapter and must preserve semantics.
- **Browser-observed recovery:** Cancelling the failed proposal cleared the inbox. The host continuation still told the user to cancel an open card without checking current state. A new ordinary-language refinement successfully staged and created one three-widget board; no duplicate existed.
- **Browser-observed metrics:** “Jobs still to do” displayed 1, and “Total recorded cost” displayed 34,200, matching the two live records. This confirms these metrics for this small fixture only.
- **Browser-observed proposal/render mismatch:** The assistant promised a table with due dates and assignees. The rendered widget displayed only Record, Status, and Updated. Persisted widget configuration readback cannot justify claiming columns that the renderer does not support. Make the exposed widget capability/schema reflect the frontend's actual supported configuration, reject or explain unsupported columns, and verify the rendered result before claiming completion.
- **UX concern:** The original proposal included a category chart even though the earlier Category field change had remained unpublished. Require published-schema validation for all widget field bindings, and verify the particular field's availability before asserting that this chart is meaningful.

- **Browser-observed revision:** A plain-language request to replace the list with a status chart updated the existing dashboard, preserved both metrics, and kept the dashboard count at 1. The chart rendered Done: 1 and To do: 1.
- **Browser-observed live refresh and persistence:** After the assistant marked the synthetic tap job Done through its approval flow, the dashboard updated without recreation to unfinished: 0, Done: 2, and total cost: 34,200. Reload preserved all three values. Both live fixture jobs are now Done; subsequent tests must use that updated baseline.
- **Browser-observed reporting mismatch:** The completion message predicted that the unfinished tile would drop to 1, although the rendered tile correctly showed 0. It also described a GYD-labelled tile while the actual number had no suffix. Require post-write widget-data readback, not just saved-config readback or mental prediction, before claiming exact displayed results. The earlier cost description also speculated about pounds/cents despite the fixture's explicit GYD wording; connect this to UX-06c provenance and unit handling.

Evidence is saved at `/tmp/integral-dashboard-first-smoke.jpg` (initial failed application) and `/tmp/integral-dashboard-smoke-verified.jpg` (final dashboard after record update and reload). Supported creation after recovery, in-place revision, and live refresh passed this one round. Strict upcoming/assignee presentation did not meet the requested behavior. Permission filtering, broader data volumes, and cross-model repetition remain untested.

## Execution order and deliverables

1. **Reproduction and instrumentation:** Capture minimal browser steps and current state for UX-01 through UX-05; identify supported provider behavior for UX-03; run UX-08 dashboard qualification. Record failing path and expected state before editing.
2. **Correctness first:** Fix entry dismissal/URL state, transcript follow behavior, and attachment staging/materialization. These can strand users or imply a completed write that did not happen.
3. **Approval clarity:** Remove duplicate confirmation copy and batch compatible ordinary setup writes without broadening approval grants.
4. **Observability and finish:** Restore the intended collapsed activity disclosure, improve compact status transitions, then re-run all flows together.

Coding-agent implementation should keep each package to a narrow PR-sized change, avoid unrelated harness refactors, update generated capability artifacts only if tool surfaces change, and add focused regression coverage for each reproduced defect. Do not commit until the repository’s mandated clean-build, hook, and relevant-test gates pass. Browser evidence is a separate required gate and must use ordinary layperson prompts.

### Package closure contract

For each package, record the reproduced failure, smallest owning module, applicable invariants, implementation decision, meaningful regression checks, browser result, and outstanding limitations. Link the exact evidence and revision. The same layperson journey must be re-run in the browser after each implementation round; targeted unit tests alone cannot close a package.

Preserve the relevant contracts in `docs/INVARIANTS.md`: I-CHAT-01 (human-facing names rather than raw node IDs), I-APPROVAL-01/02 (approval execution/audit semantics), I-APP-DEF-01 and I-PROFILE-02 (definition and profile change authority), I-GRAPH-01/02 (persistence primitives and structural reachability), I-CRUD-01 (canonical service writes), and I-SUBSTRATE-01/I-EXT-01 (generic Core and App boundaries). Declare any affected invariant explicitly in the implementation notes.

Frontend-only packages can proceed independently after reproduction. UX-06a requires verified upload identity before the receipt acceptance journey; UX-06b requires a durable approved revision before publish review. UX-02 batching requires reliable per-item effects/receipts before claiming one completed setup. Run UI polish after these event/state contracts are stable.

## Browser acceptance journey

Run this end-to-end on the local Integral UI with a fresh synthetic app and ordinary user language:

1. Ask the assistant to set up a practical app; review one concise design and approve one itemized setup.
2. Populate several synthetic records from chat; verify created records and relationships in the app UI.
3. Ask a search question, then update one field and verify only that field changed.
4. Open a linked entry from the chat and dismiss it by close button, Escape, and browser Back; verify the track remains available.
5. Attach a synthetic file to a named record; confirm the staged operation targets the actual stored upload; approve once; verify the file appears on the record. Also test an ambiguous destination and make sure the assistant asks one concise clarification.
6. Delete one explicitly disposable synthetic record; verify the remaining records.
7. Expand the activity disclosure during and after a tool-using turn; inspect ordered tool calls, outcomes, optional supported user-facing reasoning, compact typography, and collapsed default.
8. Generate output taller than the viewport; verify bottom-follow, manual scroll-up pause, and return-to-latest behavior.
9. Ask for an App overview using the UX-08 prompts; inspect rendered widgets, reload, revise the same dashboard, and verify its values after an underlying record update.

## Additional regression and release gates

| Gate | Required proof |
| --- | --- |
| Repetition | Three fresh ordinary-user rounds for the repaired paths, with one round after reload; record success/failure and approval count rather than relying on a single ideal prompt. |
| Approval parity | Verbal “yes” and “no”, buttons, and inbox decisions affect the same exact proposal once; changed proposals require a new decision. |
| Race/recovery | Double click, repeated event, close during entry fetch, reload during upload/application, stale approval, and unknown write outcome cause no duplicate effect or reopened dismissed dialog. |
| Tenant boundary | A second authorized test principal/workspace cannot discover, inspect, attach, or approve the first workspace's file/proposal by guessing its identifiers. Use existing test accounts/fixtures; request access only if this proof cannot otherwise be run. |
| Provider behavior | Run the main journey on the configured DeepSeek/Ollama model and a representative GPT control when configured. Clearly report provider/model failures separately from UI/runtime failures; do not require absent credentials to implement deterministic fixes. |
| Responsive/accessibility | Full-page and dock chat, narrow viewport, keyboard-only dismissal/disclosure, reduced motion, and long text remain usable. |
| Accounting | Per-turn calls, tokens, cost/source, and elapsed time match request-level evidence without event replay duplication. |

Track completion latency, number of approval decisions, number of clarifying turns, tool calls, request tokens, cost/source, and verified task outcomes. Compare the same scenario before and after remediation; set performance targets from this baseline rather than inventing provider response guarantees. Retain redacted evidence only, with synthetic data and no credentials or private reasoning.

Use screenshots and visible UI readback as evidence. Report each gate separately: code tests, browser journeys, live provider/model, and unresolved limitations. Do not treat a successful API call, a tool list, or a green unit test as browser proof.

## Non-goals

- Do not show private hidden chain-of-thought.
- Do not remove required approval or authorization checks to reduce friction.
- Do not infer a currency without schema metadata or explicit user-provided context; distinguish user-declared currency from stored schema metadata.
- Do not rewrite or replace the resident harness as part of this UX remediation.
- Do not use production or real personal data in smoke journeys.
