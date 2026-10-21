# Chat images, approvals and navigation — local candidate qualification

Date: 2026-10-20. Candidate: `codex/dev-read-chat-latency`, local changes on
`66b4c4cd79d822d205f03c3e32309d89aa764add`. This is not a published package,
CI result, production deployment, or full repository release qualification.

The sections below retain the chronological local exercises and failures found
during implementation. Their test counts describe those snapshots, not a single
combined run. The final pre-push checks are recorded separately at the end.

## Contract

Native chat submits canonical uploaded attachment IDs, not duplicate browser
base64. Pydantic AI receives the stored pixels as `BinaryContent` alongside the
user's authored text. Current primary graph records, canonical ownership edges,
workspace membership, owning-entry access, scanner state, storage SHA256, byte
size and decoded format/dimensions are checked before dispatch. This applies to
restored image history and every retry. A refusal before SDK entry settles its
attempt as failed, avoiding an unknown request that would block continuation.
Older uploads keep later turns on the operational checkpoint path even outside
the recent-text window. Images confer neither write approval nor broader access.

Reference: [Pydantic AI image input](https://pydantic.dev/docs/ai/core-concepts/input/).
Current product contract: [chat image input](../../backend/ai-chat.md#image-input).

## Live local exercise

- Frontend: `http://127.0.0.1:19011/agent`; backend: `127.0.0.1:19045`.
- Existing isolated PostgreSQL fixture: `core_read_chat_smoke`, port 55464.
- Principal/workspace: synthetic QA Followup account and its workspace.
- Actual configured model: `ollama_chat/deepseek-v4.1-flash:cloud`; Ollama's
  model metadata advertised vision support. The model route was not switched.
- Final PNG fixture: neutral filename `sample.png`, SHA256
  `aa1d32a61155ad9f06f0b59d741ca5d76712ad4faadb555bea13a1efd126ca34`.
- Uploaded via authenticated multipart API, then submitted with attachment IDs.
  WorkItem was accepted, settled `succeeded`, and saved transcript read back.
- Correctly read **HARBOR 9351**, orange triangle, blue square, bar values
  **18 / 42 / 27**, and highest bar **B = 42**. Observed provider turn duration
  approximately 9.3 seconds; this single fixture is not an accuracy benchmark.
- Image-only upload also succeeded and produced an acknowledgment and question.
- Chrome rendered the persisted messages. A follow-up asking for previously
  unmentioned visual details correctly identified purple bar C and an upward
  triangle. Reloading during that follow-up retained the completed result.
- Cross-thread attachment reuse: rejected with HTTP 400 before acceptance.
- Blocking the fixture upload after a successful turn prevented a visual answer.
  Restoring its scan state let the **same durable chat** answer again. The first
  exercise exposed stale hydrated attachment state; current primary reads fixed
  it. No live business records were changed.

Raw local evidence remains under `/private/tmp/integral-image-smoke/`; fixture
bytes, credentials and transient API tokens are not committed.

## Automated checks

- Focused backend image, SDK transport, chat attachment, worker, streaming,
  provider, progress, adaptive-context and capability-map tests: **233 passed**.
- Complete frontend suite: **340 files, 1,959 tests passed**.
- Frontend TypeScript and production build passed. Existing large-chunk build
  warning remains.
- Focused mypy for image-input service and SDK transport passed with Python
  3.12 and silent imported-module diagnostics; this is not a full-project check.
- Backend flake8, black/isort checks and whitespace checks passed for touched
  image implementation files. Full default backend suite was not rerun.

## Limits

Chrome's upload tool rejected local fixture paths outside its configured roots.
Therefore the live upload used the local API; persisted display and follow-up
interaction were checked in Chrome, while composer upload/reference behavior
was covered by frontend tests. A complete automated browser file-picker upload
was not exercised. Local services remain available for the user's own upload.

Verified live format: PNG. JPEG, WebP and GIF are accepted by the bounded
format-validation path, but were not each exercised against the actual model.
No dev/prod deployment, package publication, version bump, commit or push was
performed as part of this candidate qualification.

## Approval continuation regression follow-up

The user's subsequent local project creation exposed a missing integration
case: a durable approval outcome is a `system` ChatMessage, but visual-input
preparation admitted only `user` messages even when no image was supplied.
The write succeeded; preparation of its acknowledgment then failed with
`work.policy_denied` / `chat image message is unavailable`, before SDK dispatch.

- Text-only system messages now continue; system/assistant/tool messages cannot
  introduce new pixels, and foreign-thread messages remain rejected.
- The same persisted conversation completed a new host-only readback turn
  without another write. Chrome independently opened the generated project
  Track and showed its owning project Entry in the breadcrumb.
- Reload no longer displays internal system receipts as conversation bubbles.
  The original error remains as historical evidence; it was not deleted.
- After the approval recovery and a backend restart, a Chrome follow-up in the
  same chat correctly answered "Bar B is the highest, at 42." The composer
  returned to idle and no new records were requested or created by that turn.
- Focused image/provider/receipt/worker checks: **133 passed**; frontend durable
  reload checks: **4 passed**. Frontend TypeScript, implementation flake8,
  image-service mypy with Python 3.12 and whitespace checks passed.
- The live readback took approximately **72.9 seconds**, including a physical
  model attempt that hit the 30-second idle timeout and recovered on retry.
  It also performed unnecessary collection discovery before scoped readback.
  Exact receipt Entry/Track IDs are now repeated at the host-event boundary
  with the direct readback route; unit coverage verifies that projection.
  A live latency improvement from that projection is not yet established.

Raw local evidence: `/private/tmp/harness-failure-transcript.json`,
`/private/tmp/harness-approval-replay-result.json`, and the
`/private/tmp/harness-continuation-*.log` files. This follow-up does not establish
full harness reliability across all models, deployments or task categories.

## Card decisions and registered-tool follow-up

Inspection of the user's subsequent Warehouse barcode rollout conversation
showed **12 model calls, 301,211 input tokens and approximately 25.1 seconds**
for a redundant acknowledgment after the executor had already saved the entry.
Its checkpoint showed a registered App tool key sent to declared-query
dispatch, followed by `Unknown tool name: integral_call_workspace_tool`:
the loaded record skill had not disclosed that invocation tool.

Current candidate changes:

- Ordinary terminal approvals, denials, cancellation, expiry and unavailable
  tokens retain the host receipt without starting another model turn.
  Questions, approved designs and reviewed unpublished profile revisions keep
  their unfinished-work continuation; cancel-all never resumes answered items.
- Record/attachment skills disclose registered-tool discovery and invocation
  together. Catalog metadata names the invocation wrapper and input argument.
  Skills distinguish registered tool keys from declared query keys and direct
  known IDs through scoped resolution. No query or permission gate was removed.
- The invocation wrapper recognizes currently registered read-only tools in a
  read-only turn. Actual dispatch still checks live membership and App access.
- Inspecting this image-bearing history exposed an export failure when PNG
  bytes were decoded as UTF-8. Dataclass exports now encode bytes as base64;
  the authenticated owner export succeeded and supplied the above evidence.

Black, flake8 and focused Python 3.12 mypy cover the changed implementation
files; capability documentation was regenerated. No new approval smoke or
automated regression suite was run for this subsequent policy/skill revision.
Historical failures remain in the saved conversation. This candidate remains
uncommitted and is not release qualification.

## Requested Chrome smoke retest

The local app was reloaded and a separate synthetic conversation created:
`n.ChatThread.d8eabee7cc57496ca7e118ac`. Its mock project is
`n.Entry.ebfdbccba47c483da08011d8` in the existing Internal Projects Track.

The first browser approval caught a missing HTTP projection: the service's
`resume_required=false` did not reach the browser, which defaulted to starting
another turn. Mark-write, question and cancel HTTP responses now preserve that
decision, with regression coverage for both boolean values. A duplicate build
also now hits its one-attempt guard before cleared approval/skill state can
misdirect the model toward a second approval.

After those fixes:

- Approved the mock project's change to Done in Chrome. The sheet closed with
  one short host receipt. Browser `/messages` POST count stayed **4 → 4**.
- Proposed moving it back to In progress, then rejected the card. The sheet
  showed "Change not applied"; `/messages` POST count stayed **5 → 5**.
  Independent authenticated Entry readback still reported **done**.
- A saved-record question returned the correct status. A collection question
  exposed a printed XML output-tool protocol in the conversational pass.
  The adapter now escalates printed tool protocols using the original input,
  without displaying or executing their text. The stream translator rejects
  XML protocols alongside JSON variants. The retry returned **Warehouse barcode
  rollout** through the registered `integral_call_workspace_tool`, with no
  unknown-tool error or declared-query-key substitution. An earlier generic
  query received the expected App boundary refusal before the governed route.
- Reload preserved all **17** chat messages, the concise outcome receipts and
  an idle composer; no new WorkItem started and the Entry remained Done.
- Triggered actual watch/unwatch toasts in both themes, then restored the
  original un-watched state and dark theme. Dark-app toast: RGB 244/244/246
  with RGB 32/32/36 text. Light-app toast: RGB 41/41/46 with RGB 250/250/250
  text. Both had `align-items:center`, a 4px status border and **0px** message
  centre offset. The message retained a three-line clamp.
- A generated bare `/n.Track...` link also appeared during smoke. The generic
  Markdown renderer now repairs local typed resource paths to their existing
  routes; external-origin links keep their normal external handling.

Validation: **236 focused backend tests passed** (queue/API projection, broker,
event protocols, adaptive routing, image/session persistence and workspace tool
contracts); **1,963 tests / 340 files passed** in the complete frontend suite,
plus the subsequent focused UI cases. Production frontend build, focused mypy
for six source files, implementation black/flake8 and whitespace checks passed.
The existing bundle-size warning remains. Full default backend and release CI
were not rerun. Smoke artifacts are the `/private/tmp/approval-smoke-*.log`
files and `/private/tmp/approval-smoke-transcript.json`.

All changes remain local and uncommitted. Local services are left running.


### Navigable closing confirmations

Applied-change host receipts now render safe local Markdown links from consumed
execution navigation metadata, typed App command object references, or the staged
resource target. Entry links retain collection context where supplied. Batch
references are deduplicated; rejected changes do not present applied-result links.
Links use existing authenticated routes and grant no new access.

Chrome: approved a description update on the isolated handover project; the host
receipt exposed View record. Clicking it opened the correct record, displayed
the saved description, and kept the conversation available. The receipt and link
survived reload. Runtime logs recorded one request for the user turn, followed by
mark-write and system-message only on approval, with no follow-up model request.

Validation: 67 focused backend tests, 14 frontend component tests, frontend
production build, backend-configured flake8 and whitespace checks passed. The
existing production bundle-size warning remains. No release or deployment.


### Agent-directed navigation

Added the manifest/service-backed `integral_open_view` tool with bound browser
chat, owner, workspace, live resource and optional saved-View read checks. The
public event stream projects a dedicated navigation event; raw tool results
remain private. Only the live foreground originating chat consumes it after
successful completion. Recovery/history, changed routes/scopes/principals,
failed/canceled turns, unmounted surfaces and composer drafts suppress routing.
The router preserves the same conversation through the companion panel.

Chrome used everyday wording, without tool names or approval instructions:

- “Take me to Internal Projects.” opened the correct collection in the same tab.
- “Show me this as a list.” discovered and selected the existing All projects
  table (`n.View.0978893820b447d9be9b1be9`) with no new view or data mutation.
- Reload retained that saved-view URL and selected tab, and kept the same chat;
  prior navigation requests did not replay and reset the view.

The first candidate selected the tool but did not navigate because the public
stream deliberately strips tool results. A dedicated destination-only event
fixed this without broadening raw-result visibility; both requests passed after
that fix. No hosted site, release, push, or deployment was involved.

Validation: 104 backend tests (navigation authority, event projection, public
events, dispatch, manifest reconciliation and generated capability map); 16
focused frontend tests (typed destinations, live deferred navigation, draft
protection, durable recovery). The full frontend suite passed 341 files / 1,976
tests before the final composer/unmount guard refinements; focused tests and
production build were rerun after those refinements. Backend-configured flake8,
Python 3.12 mypy for the new service, and whitespace checks passed. The existing
production bundle-size warning remains. No full backend/release CI claim.


## Final pre-push verification

The candidate's full default backend selection ran with `TESTING=1`, four xdist
workers and `--dist loadfile`: **7,091 passed, 970 skipped, five failed** (8,066
selected; domain-App and slow markers retain the repository's default exclusion).
The earlier serial attempt was interrupted to switch workers and is not counted
as a pass. The five findings were:

- The directly available tool expectation omitted `integral_open_view`.
- Two authenticated MCP catalog tests still expected 127 tools instead of 128.
- Manifest summary totals did not include the new read/P0 tool.
- Image preparation imported `BinaryContent` directly from the SDK rather than
  through Integral's compatibility adapter. The architecture guard remains intact;
  the adapter now re-exports that same SDK type.

After correcting those integration expectations/metadata and the adapter import,
the complete affected modules plus image transport, navigation and capability-map
tests passed: **208 passed**. No full backend restart was performed after these
corrections; the full-run failure report and corrected-module result are separate
evidence. The corrections do not change executor or model-request behavior.

Other gates:

- CI-faithful smoke (`make verify-ci`, two auto workers, `TESTING=1`,
  `DEBUG=false`, no local `.env`): passed before the final corrections above.
- Full frontend: **341 files / 1,978 tests passed** on the production-source tree.
- TypeScript and production build: passed; existing bundle-size warning remains.
- ESLint: **zero errors**, 468 warnings in the tracked backlog.
- Full pre-commit framework: passed, including pinned formatters, flake8, backend
  mypy, source-convention checks and commit-attribution tooling.
- All 16 staged substrate/packaging guards: passed before the final corrections;
  the normal commit hook rechecks its guards on the staged candidate.
- Reproducible wheel/import gate: passed; this is a local wheel built under the
  unchanged rc28 version, not a newly published or release-qualified artifact.

Fresh PR CI is still required. The prior browser observations remain bounded to
the isolated local workspace and configured model; none establish a deployed
Business update, publication or production rollout.
