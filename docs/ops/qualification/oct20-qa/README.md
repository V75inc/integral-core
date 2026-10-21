# October 20 QA remediation qualification

Date: October 21, 2026. Scope: only the ten issues beginning at **Oct 20th Testing** in the [testing document](https://docs.google.com/document/d/1DNRtiZ2-_D1fYuUGk4JJEutV3d6-IQ9gedRLkqN88ZA/edit?tab=t.0#heading=h.7qjazj739nk).

Implementation extends PR #168 head `b44385f38d6414dd830a3c91f3bef35208651ec3` in an isolated worktree. The unrelated original checkout was preserved. The [approved assessment and plan](assessment-plan.md) records the original findings; its future-tense stop point is historical.

## Revised UI direction

After reviewing the preview, the user requested a conventional compact image mosaic shared by feed and entry detail, and monochrome line-icon reactions. The final requested choices are thumbs up, thumbs down, love, happy and sad, revealed on desktop hover or tap/keyboard activation. The main trigger always displays an outline thumbs-up, including when another reaction is selected. Revealing the menu never mutates the selection. This supersedes the original six-reaction palette and comment emoji picker. Historical reaction data remains preserved; new selections use the five current choices. Anonymous/public Like behavior retains its existing authority contract.

## Issue disposition

| # | Report | Result and evidence |
|---|---|---|
| 1 | Red stopped-request error | Retained the existing neutral cancellation presentation. A restored legacy `work.cancelled` transcript shows a muted stopped message in the real browser ([capture](screenshots/stopped-request.jpg)). Live provider cancellation during text/tool execution was not repeated. |
| 2 | Nested pasted list text disappears | Converter now preserves sibling nested lists and other readable list children, including explicit list numbering. Regression exposes the original omission. Synthetic rich HTML and long plaintext were pasted in the browser. The original reporter's exact clipboard payload was unavailable. |
| 3 | Composer scrolling glitches | Retained native textarea scrolling. Long plaintext and formatted nested-list browser paste/scroll checks passed. This is a focused desktop check, not an exhaustive browser/zoom/reference matrix. |
| 4 | Obsolete durable-image admission error | Existing durable upload/model dispatch remains. Added actionable friendly messages for unsupported, invalid and excessive images. No external vision model was called for this change; [October 20 native image smoke](../2026-10-20-native-image-input-smoke.md) is historical provider evidence, not a fresh result at this commit. |
| 5 | Duplicate comments | Canonical-ID merging, stale-fetch/scope guards, synchronous submit locking and pure count updates prevent event/HTTP races. Two browser sessions showed three distinct stored/rendered comments without duplication or reload ([capture](screenshots/comments-mention.jpg)); regression covers delayed fetch/mutation ordering. |
| 6 | `Test 3` rejected | Moderation now respects standalone numeric token boundaries while retaining within-word leet and alphabetic obfuscation checks. Authenticated/public create/update/comment regressions pass. A `Test 3` entry persisted in the local QA browser fixture. |
| 7 | Stretched single image | Shared gallery uses a compact responsive mosaic with `+N` overflow, preserving aspect ratio and preferred cover. The same gallery remains visible inline in entry detail. Multi-image tiles crop proportionally; the full viewer retains the uncropped original. Creation time/ID give both surfaces a stable order; only an explicit cover is promoted. Browser checked six synthetic images, viewer navigation to the hidden sixth image, and 390px width ([feed](screenshots/feed-gallery.jpg), [detail](screenshots/detail-gallery.jpg), [mobile feed](screenshots/mobile-gallery.jpg), [mobile detail](screenshots/mobile-detail-gallery.jpg)). |
| 8 | Reactions and comment emoji | A compact outline reaction control on posts/comments reveals five monochrome outline choices (thumbs up/down, love, happy, sad) on desktop hover, tap or keyboard activation, one selection per principal/target, idempotent selection and self retraction. The expanded comment emoji picker was removed per the revised direction. Typed relationship edges preserve untouched legacy reactions. Browser verified post thumbs-up→thumbs-down, Love and Sad selection, a persistent thumbs-up trigger, persisted detail state, comment selection without closing detail, matching image order and hidden-image navigation ([capture](screenshots/comment-reaction.jpg)). PostgreSQL tests cover concurrency, retries and authority. |
| 9 | Invisible pending design | Approvals exposes current designs and exact chat links. Explicit, bound discard rejects ambiguous/partial effects and retires only the captured design's authority. Unrelated native writes no longer inherit a blanket design prohibition. Browser discovered/opened/discarded a synthetic pending design and confirmed it stayed cleared after reload ([before](screenshots/design-discovery.jpg), [after](screenshots/design-discarded.jpg)). PostgreSQL tests cover stale markers, unrelated drafts/tokens and transaction rollback. A live provider interrupted-build conversation was not repeated. |
| 10 | Mention notification requires reload | Live/replay release now proves current Notification recipient, structural edge, identity aliases and scope. Only a minimal invalidation envelope is released; linked entries require current read authority. In two browser sessions, a Commenter's mention updated the owner's open comments and unread badge without reload. PostgreSQL regression includes actual event dispatch, HTTP replay, isolation and revoked-entry access. |

## Boundaries and design

- No new floating Nodes. Reactions are typed User→Entry/Comment edges; notification admission verifies `HAS_NOTIFICATION`.
- Workspace membership, current role, exclusions and identity aliases remain authoritative. Viewer self retraction is retained; selecting reactions requires Commenter or stronger authority.
- Existing POST reaction toggle and public Like contracts remain compatible. New PUT selects idempotently; DELETE retracts. Untouched multi-reaction legacy state is preserved until that actor explicitly selects a current reaction.
- Design discard is a compare-and-set transaction under the thread lock. Active work, unassociated drafts and uncertain/partial build effects refuse destructive recovery. Captured authority is revoked atomically; retries only finish cleanup for immutable retired token IDs.
- No provider configuration, version bump, deployment or merge is part of this change.

## Validation

The qualification uses locked backend dependencies (Python 3.11, jvspatial 0.2.2), Node 24, a disposable local PostgreSQL container and two synthetic QA users. Browser API/frontend ports were 4469/9149. Screenshots contain synthetic content only.

Checks completed after the revised UI direction:

- Full frontend: **344 files / 2,021 tests passed**; TypeScript and ESLint passed (zero errors; warning backlog remains nonblocking).
- Targeted PostgreSQL: **136 passed** across reactions, notification release/replay, moderation, design decisions/HTTP/discard transactions, and image-input contracts.
- Additional reaction/permission/owner-governance check: **56 passed / one expected PostgreSQL-only skip**; dedicated PostgreSQL reaction contracts: **20 passed**.
- Core-only and extension-contract lanes passed; the CI PostgreSQL lane passed. These preceded the final presentational revision; the final five-choice contract was separately requalified after the UI revision.

The full default backend suite passed: **7,221 passed / 984 skipped**, with 18 warnings. This full run qualified the graph, access, persistence and lifecycle implementation before the final reaction-palette value change; the final accepted values and API behavior were then rerun through dedicated local/PostgreSQL contracts and CI-faithful smoke. CI-faithful smoke passed. Expected skips cover opt-in PostgreSQL, external-service, slow and disposable-restore lanes; relevant PostgreSQL contracts were exercised separately. The full `make verify` gate is executed by its component targets: full backend and frontend suites independently, then guards, pre-commit, pinned formatting, lint/types, wheel proof and CI-faithful smoke on the implementation worktree and an isolated backend CI copy. The final frontend menu and API palette were requalified after their last revisions. This isolates temporary App fixtures created by the full suite from the packaging/formatting lane. No claim is made that every exploratory acceptance combination in the approved plan was browser-tested; unperformed live provider scenarios are identified above.

Final production frontend build passed. Guards, pre-commit, pinned formatting, lint/types and wheel import checks passed. ESLint reported zero errors and 481 existing warnings. The wheel includes the final built UI; two builds were byte-identical with SHA-256 `6585e4d91904dae25dbcb05049fcdaee45e0dcb5779d964b52f9471b11ab8e33`.

## CI dependency-audit follow-up

The first remote run on `a49ea444` failed the backend dependency audit: locked `pypdf==6.19.0` had five advisories. The minimum is now `pypdf>=6.21.1`, and `uv.lock` resolves 6.21.2. The unchanged backend audit passes with no known vulnerabilities. PDF attachment extraction, document signatures and Operational Model derivation are rechecked against the updated lock. The earlier full-suite and wheel hashes above describe the QA implementation before this dependency-only follow-up.

## Home AI setup follow-up

The single-user Home card previously rendered a fixed model/API-key setup prompt. It now reads the shared model-credential query used by AI Models settings. An active configuration, including a keyless local model, shows the Home chat composer and Manage AI settings. Saving/removing credentials updates that same query cache immediately. Missing or inactive configuration retains optional setup; loading/failure displays an explicit checking/unavailable state and retry rather than incorrectly asserting setup is missing. A configured model is not a claim of live provider connectivity. Server Home behavior remains unchanged. Existing Integral One UI bundles need a rebuild to receive this upstream change.
