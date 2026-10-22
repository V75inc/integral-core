# Integral Core: October 20 QA assessment and proposed remediation

Review date: October 21, 2026 (America/Guyana).

## Scope and baseline

Reviewed only the ten numbered issues under **Oct 20th Testing** through the end of the [supplied testing document](https://docs.google.com/document/d/1DNRtiZ2-_D1fYuUGk4JJEutV3d6-IQ9gedRLkqN88ZA/edit?tab=t.0#heading=h.7qjazj739nk), including its embedded screenshots. Earlier dated findings are excluded.

Baseline: [PR #168](https://github.com/V75inc/integral-core/pull/168), exact head `b44385f38d6414dd830a3c91f3bef35208651ec3`. Package metadata identifies this tree as `0.1.1rc29`, with `jvspatial==0.2.2`. PR #168 itself changes only `.github/workflows/ci.yml`; this review assesses the complete application at its head, including inherited code. The head was fetched and rechecked during review.

Source was examined in an isolated archive at `/tmp/integral-pr168-review`. The active checkout has extensive unrelated uncommitted work; none of it was used as the review baseline or changed. No implementation, commit, push, deployment, or external-document edit was performed.

Statuses distinguish source evidence and automated probes from actual browser reproduction. No two-user browser acceptance run or live receipt/model test has been performed against this snapshot. “Implemented” below means the relevant replacement exists in this head, with closure still requiring the named acceptance check.

## Issue-by-issue assessment and plan

### 1. Large red “You stopped this request” message

**Assessment: implemented in current source; browser closure pending.**

[Thread.tsx:631](https://github.com/V75inc/integral-core/blob/b44385f38d6414dd830a3c91f3bef35208651ec3/frontend/src/features/ai-chat/components/Thread.tsx#L631) renders cancellation as a small muted status with a stop icon. [Runtime restoration:584](https://github.com/V75inc/integral-core/blob/b44385f38d6414dd830a3c91f3bef35208651ec3/frontend/src/features/ai-chat/useAIChatRuntime.ts#L584) maps persisted `work.cancelled` errors to cancellation status, and tool cancellation is also muted. These changes directly address the screenshot.

**Plan:** retain the current implementation. Verify Stop during a text response and during a tool call, then reload both conversations. Close if each shows one neutral stop indication and no red error card. If a legacy transcript still renders an error, correct its normalization rather than restyling genuine failures.

### 2. Nested Google Docs list text disappears when pasted

**Assessment: partially addressed; a remaining loss-of-text case is reproducible.**

The composer now converts clipboard HTML to Markdown, and ordinary nested lists have tests. However, [clipboardHtmlToMarkdown.ts:41](https://github.com/V75inc/integral-core/blob/b44385f38d6414dd830a3c91f3bef35208651ec3/frontend/src/components/chat/clipboardHtmlToMarkdown.ts#L41) processes only direct `LI` children of a list. A nested list directly under `OL`, beside an `LI`, is discarded:

```html
<ol><li>Parent</li><ul><li>Nested text</li></ul><li>Next</li></ol>
```

Current output is `1. Parent\n2. Next`; “Nested text” is lost. The equivalent well-formed nesting inside an `LI` survives. This synthetic probe demonstrates a real converter gap, but the exact Google Docs clipboard payload from the original report has not been captured.

**Plan:** capture the supplied prompts' actual `text/html` and `text/plain` clipboard forms in a browser; normalize document-generated list structures while preserving all readable children in order. Use a text-preserving fallback when conversion cannot safely represent the HTML. Retain link sanitization, inline formatting, selection replacement, and numbering/indentation.

**Acceptance:** both supplied prompts retain every numbered item and nested bullet, in order, through paste, edit, send, and transcript reload. Cover well-formed nesting, sibling nested lists, and Google Docs wrappers; no text-only assertions that overlook hierarchy.

### 3. Pasted text glitches while scrolling the composer

**Assessment: likely addressed for ordinary text; visual verification remains necessary.**

[TaggableComposer.tsx:87](https://github.com/V75inc/integral-core/blob/b44385f38d6414dd830a3c91f3bef35208651ec3/frontend/src/components/chat/TaggableComposer.tsx#L87) uses native textarea text when there are no entity references, retains scroll position during resizing, and aligns the highlight mirror with the textarea's actual client width. These target likely sources of the reported shifting. Existing DOM tests cannot prove smooth browser scrolling.

**Plan:** first verify the current implementation with the long supplied prompts, with and without selected @/# references. Exercise scrollbar appearance, long lines, mid-text edits, browser zoom, light/dark mode, and the full page/docked chat surfaces. If necessary, align typography, wrapping, padding, and scroll positions across the textarea and mirror, and avoid resize work on scroll alone.

**Acceptance:** scrolling and editing never shift the text relative to the caret or highlights; value, selection, and scroll position remain stable. Close without further code if the current head passes.

### 4. Unhelpful “Durable chat image admission is not enabled yet” error

**Assessment: old blocker superseded; current image path is implemented.**

That exact error no longer exists in application source. The [image adapter](https://github.com/V75inc/integral-core/blob/b44385f38d6414dd830a3c91f3bef35208651ec3/frontend/src/features/ai-chat/attachments/imageAttachmentAdapter.ts) persists images as real chat uploads. [ServerChatProvider.ts:147](https://github.com/V75inc/integral-core/blob/b44385f38d6414dd830a3c91f3bef35208651ec3/frontend/src/features/ai-chat/providers/ServerChatProvider.ts#L147) rejects incomplete uploads with actionable reattachment guidance; native turns send attachment IDs instead of inline image copies. [chat_image_inputs.py](https://github.com/V75inc/integral-core/blob/b44385f38d6414dd830a3c91f3bef35208651ec3/backend/app/services/chat_image_inputs.py) validates ownership, storage content, image limits, and model access before pixel dispatch.

**Plan:** verify an actual receipt image on a supported vision model through extraction and the existing governed write/approval flow. Exercise upload failure and a model without vision support. Where guidance remains incomplete, distinguish upload failure, model capability, and operator configuration; offer the actual relevant model/settings route only where one exists. Do not invent an image-enablement switch.

**Acceptance:** receipt data comes from the image; destination records are verified after authorized creation; errors give a working next step. No extra toggle is required for the supported native path.

### 5. Duplicate admin comments that disappear after another user comments

**Assessment: credible remaining race; exact two-user symptom not yet reproduced.**

[EntryDetail.tsx:1010](https://github.com/V75inc/integral-core/blob/b44385f38d6414dd830a3c91f3bef35208651ec3/frontend/src/components/entries/EntryDetail.tsx#L1010) refetches on comment events, while [the submit handler:1148](https://github.com/V75inc/integral-core/blob/b44385f38d6414dd830a3c91f3bef35208651ec3/frontend/src/components/entries/EntryDetail.tsx#L1148) blindly appends the HTTP result. If the refetch already includes that comment before the HTTP response is appended, the same ID appears twice. A later authoritative fetch replaces the array, matching the reported disappearance. The shared comments hook also blindly appends, and the comment-tree builder does not deduplicate its input roots.

**Plan:** reproduce and log server comment IDs and request/event ordering using admin and Commenter sessions. Merge comments by canonical ID across HTTP responses and event refetches, guard stale fetches and repeated submissions, and keep comment counts derived from the unique authoritative collection. Keep React state updaters pure. Change backend creation idempotency only if the reproduction shows two distinct persisted records.

**Acceptance:** one stored comment and one rendered row under event-before-response, response-before-event, delayed refetch, double-submit, and a second user's reply. Verify entry popup, feed preview, wiki comment surface, and counts.

### 6. Harmless title/body “Test 3” rejected by moderation

**Assessment: confirmed open by direct probe with locked dependencies.**

[content_moderation.py:151](https://github.com/V75inc/integral-core/blob/b44385f38d6414dd830a3c91f3bef35208651ec3/backend/app/services/content_moderation.py#L151) delegates to `better-profanity==0.7.0`. The current function returns true for `Test 3` and `**Test 3**`, but false for `Test 2`. The library's blocked `teste` entry compares equal to `test3` through its obfuscation substitutions. This is a cross-token numeric/leet false positive; the Markdown-formatting regression fixes do not resolve it.

**Plan:** define and implement a boundary-aware moderation policy that accepts harmless numbered labels without disabling the filter. Constrain ambiguous cross-token substitutions while retaining explicitly supported detection of actual profanity and deliberate obfuscation. Keep validation in the shared service for create/update/comments and public equivalents; preserve authored text exactly.

**Acceptance:** `Test 3` title and body create successfully through the actual New Post request, updates also succeed, and representative benign labels, Markdown, true profanity, and intentional obfuscations behave correctly. Test both authenticated and public write paths. Avoid a hardcoded exception for this one title.

### 7. Single stretched feed image rather than a multi-image preview

**Assessment: requested gallery remains unimplemented.**

[EntryCard.tsx:537](https://github.com/V75inc/integral-core/blob/b44385f38d6414dd830a3c91f3bef35208651ec3/frontend/src/components/entries/EntryCard.tsx#L537) renders a single full-width cover image. Other files render as filename chips. There is no primary-plus-thumbnails image layout. Source inspection alone does not establish the original file's resolution or prove that stretching is the sole cause of blurriness.

**Plan:** implement a shared responsive image preview with one primary image and smaller previews, using a bounded visible set and a `+N` indicator. Use adequate authorized preview resolution, preserve image proportions, avoid unnecessary upscaling, and open the selected attachment in the existing viewer. Keep non-image attachments as file controls and honor blocked/unavailable-file handling.

**Acceptance:** verify 1, 2, 5, and more-than-visible-limit images, mixed file types, portrait/landscape/tiny files, slow loads, mobile widths, keyboard access, and correct image selection. No unauthorized attachment URLs or excessive full-size image downloads.

### 8. Six post/comment reactions and a comment emoji picker

**Assessment: partially present foundation; substantial feature work remains.**

[EntrySocialActions.tsx](https://github.com/V75inc/integral-core/blob/b44385f38d6414dd830a3c91f3bef35208651ec3/frontend/src/components/entries/EntrySocialActions.tsx) and EntryCard already expose a basic Like action. Entry reaction endpoints exist, so post reactions are not wholly absent. They do not provide the requested six-choice interface; their displayed total combines reaction types. Comments have no equivalent reaction interface, and [CommentComposer.tsx](https://github.com/V75inc/integral-core/blob/b44385f38d6414dd830a3c91f3bef35208651ec3/frontend/src/components/entries/comments/CommentComposer.tsx) has no emoji picker.

**Plan:** propose six shared reactions: Like 👍, Love ❤️, Haha 😂, Wow 😮, Sad 😢, Angry 😡; keep Like the primary action and omit Dislike. Implement one active reaction per principal/target, atomic change/removal, consistent counts, and shared feed/detail controls. Add a compact comment-reaction control and a keyboard-accessible emoji picker that inserts text at the caret without submitting the comment.

This needs a small substrate design before implementation: put relationship state on typed User→Entry/Comment reaction edges, with unique principal/target identity and canonical service mutations. Reconcile existing `Entry.reactions` dictionaries through a bounded, idempotent migration and compatibility adapter so old reactions are not lost. Explicitly handle legacy users who hold multiple emoji reactions rather than silently choosing one; preserve their data until a documented reconciliation rule is applied. Check existing published API consumers before changing toggle semantics. User lists behind counts are optional and can be deferred.

**Acceptance:** add/change/remove across feed and popup with immediate consistent totals, concurrent users, retries, role changes, and reload. Commenter+ may react; retain existing self-retraction semantics. Enforce workspace gates and exclusions. Emoji insertion preserves Unicode and mentions. Anonymous/public reaction behavior remains governed by its existing contract rather than silently gaining new permissions.

### 9. Invisible pending design blocks unrelated changes

**Assessment: confirmed discoverability gap; full blocking sequence still needs reproduction.**

[StagedChatApprovals.tsx:32–36](https://github.com/V75inc/integral-core/blob/b44385f38d6414dd830a3c91f3bef35208651ec3/frontend/src/components/approvals/StagedChatApprovals.tsx#L32) deliberately excludes `design_proposal` because design confirmation belongs in chat. Separately, `ChatThread.design_proposed` is treated as pending until a build receipt exists. The screenshot shows a stopped build followed by requests to forget the previous design, yet the agent sends the user to an inbox that hides that class of decision. Existing expiry and durable staged-token recovery do not by themselves prove that a thread's design marker is cleared or actionable.

**Plan:** model the active blocker explicitly: design ID/revision, thread, target resource, state, and available actions. Make unresolved designs discoverable on Approvals with a link back to the exact chat/review context and an explicit discard/reject path; preserve the distinction between confirming a design and authorizing an executable staged write. Reconcile token, work, design-marker, and prompt-queue state on reject, expiry, restart, and interrupted-build recovery. Scope blocking to operations dependent on the design. An unfinished authorized build must remain recoverable, with visible resume/discard actions; do not silently drop it or permit duplicate execution. Guidance must derive from current server state and a working route.

**Acceptance:** recreate the screenshot sequence: propose QA Testing 20, confirm, interrupt, then request changes to Track AAA and explicitly abandon the old request. The user can inspect and resolve the genuine decision; unrelated authorized work proceeds; discarded/expired state does not return after reload or restart. Repeat for pending, approved-not-applied, partially applied, consumed, revoked, and missing-token cases. No execution after revocation or loss of actor/workspace binding.

### 10. Admin mention notification appears only after reload

**Assessment: confirmed event-release defect; browser closure pending.**

The producer already emits recipient-scoped `notification.create` events and the frontend already invalidates notifications on receiving them. However, [event_access.py](https://github.com/V75inc/integral-core/blob/b44385f38d6414dd830a3c91f3bef35208651ec3/backend/app/services/event_access.py#L112) contains no `Notification` resource audience branch and [falls through to false:291](https://github.com/V75inc/integral-core/blob/b44385f38d6414dd830a3c91f3bef35208651ec3/backend/app/services/event_access.py#L291). A direct async probe returned false for a Notification event even when the subscription-policy evaluator allowed it. Both live delivery and replay use the event release guard. A 60-second query staleTime is not a periodic notification poll and cannot fix this missing delivery.

**Plan:** add explicit canonical recipient authorization for Notification events: fetch the current Notification, prove recipient ownership and its structural relationship, handle auth-subject/User-node aliases, and release only the permitted minimal envelope. Project or omit linked entry information according to current resource access without suppressing the valid notification invalidation signal. Validate both WebSocket delivery and replay scope filtering/cursors; do not broadly allow all user-scoped snapshots. Retain reconnect/fallback support.

**Acceptance:** with two signed-in browser sessions, a Commenter mentions the admin and the badge/inbox update without reload (target: within two seconds on a healthy local stream). Disconnect/reconnect and verify bounded recovery, alias-ID cases, preference opt-out, repeated delivery, recipient isolation, deleted notifications, and resource-access revocation.

## Proposed execution order

1. **Baseline closure checks:** browser-verify #1, #3, #4; record evidence and close only passing cases. Capture the exact clipboard and two-user race fixtures for #2/#5.
2. **Correctness fixes:** #6 harmless-content rejection, #2 pasted-content loss, #5 comment reconciliation, then #10 recipient-authorized notification delivery. Add regression cases that currently expose each gap.
3. **Decision lifecycle:** #9, with backend and frontend changes reviewed together. This must precede broad agent workflow acceptance.
4. **Feed presentation:** #7 shared attachment gallery.
5. **Social interactions:** #8 edge/API migration and shared reaction/emoji controls as a distinct feature change.
6. **Qualification:** repeat all ten acceptance scenarios against the resulting exact head. Run `make verify` for substrate changes, CI-faithful `make verify-ci`, relevant PostgreSQL contracts, and inspect live checks only after a separately authorized push. Run `make capability-map` if tools, bindings, core skills, or examples are changed.

Suggested responsibility boundaries: frontend owns paste/scroll/status/gallery/shared controls; backend owns moderation, recipient release, reaction persistence/migration, and decision lifecycle; integration validation owns the two-browser scenarios and readback. These are implementation responsibilities, not delegated tasks launched during this review.

Preserve I-GRAPH-01/02 (rooted graph and Object distinction), I-ROLE-01/02 and I-ACCESS-01 (current scoped authority), I-APPROVAL-01/02/04/05 and I-WORK-05 (bound, truthful, fail-closed decisions), I-CRUD-01 (canonical service mutations), and I-SUBSTRATE-01 (domain-neutral Core). New HTTP contracts stay in schemas and use `@endpoint`; the plan does not add a separate agent fabric or bypass the harness.

## Evidence gathered

- Downloaded the current DOCX export and read only the October 20 section onward; inspected the screenshots for the moderation and stuck-design reports.
- Fetched and rechecked PR head `b44385f38d6414dd830a3c91f3bef35208651ec3`; its only changed file is the CI workflow.
- Backend environment installed from this snapshot's lockfile using `uv sync --frozen --extra dev --extra test` (Python 3.11, jvspatial 0.2.2).
- **80 backend tests passed** across `test_content_moderation.py`, `test_chat_image_inputs.py`, `native_harness/wp_06/test_native_design_approval.py`, and `test_notification_router.py`. These do not cover the newly exposed exact benign-label and Notification release gaps end to end.
- **63 frontend tests passed across nine files**, covering clipboard conversion/composition, notification hooks, event invalidation, approval empty states, image adapters, friendly errors, and cancellation/parallel streams. Dependencies were installed from this head's `package-lock.json` using `npm ci --ignore-scripts`. Node 23.10.0 produced package engine warnings; repeat release qualification on a supported Node LTS. An earlier exploratory run used the active checkout's dependencies; only this locked run is counted here.
- Direct probes reproduced the exact `Test 3` false positive, converter omission on sibling nested-list HTML, and Notification event denial after a successful subscription-policy evaluation. The clipboard and event probes are narrow code-level reproductions, not claims of browser reproduction of the original reports.
- Existing PR checks were green for backend guards, contracts, smoke, frontend, PostgreSQL, artifacts, and the aggregate; Docker verification was skipped. Green checks do not close the ten reported scenarios.
- Full `make verify`, PostgreSQL execution for these new scenarios, real browser acceptance, and live model receipt processing remain future qualification work. No production-readiness claim is made.

**Stop point:** this is the proposed plan for review. Implementation starts only after the user asks to proceed.
