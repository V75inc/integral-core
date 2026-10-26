# Changelog

This pre-launch change record describes the supported Integral Core runtime.
Package publication and deployment are separate release gates; a local version
bump does not establish that a release is available on a package index.

## Unreleased

- Fix owned App deletion with anchored collections and populated shared Tracks: avoid deleting anchored siblings twice, and retain shared records without granting destructive authority over them. Clarify App versus dashboard deletion in resident skills, and leave an open App page after its agent-approved deletion completes.

## 0.1.1rc35 — 2026-10-26

- Keep concurrent sign-ins and background activity from rejecting valid credentials when only the activity timestamp changed; retain current account, credential and refresh-token guards.
- Authorize entry update/delete staging before loading private preview details; execution still rechecks current authority.
- Initialize native model-library metadata off the event loop before accepting traffic, avoid duplicate Core skill parsing, and separate local preparation spans from model/provider latency diagnostics.
- Require explicit App public-page directories in development/preview and keep the dev-page plugin out of production builds.

## 0.1.1rc34 — 2026-10-25

- Bound resident task procedures and tool-result history while protecting current work, pending writes and governed App-read discovery.
- Let the Light routing pass nominate a freshly authorized workflow for Heavy execution, reducing repeated discovery calls without changing model settings or tool authority.
- Add bounded App record pages and streaming explicit counts, with rooted schema selection, live authorization and per-window containment/block-query reuse.
- Improve scoped read guidance, source links and recoverable query errors for App developers and the resident harness.
- Check incoming contributor attribution across complete history and make hydration regression checks independent of test import order.

## 0.1.1rc33 — 2026-10-25

- Strengthen semantic and hybrid search with independent lexical indexing, bounded candidate ranking, integrity manifests, repair and reuse of verified unchanged vectors.
- Build embedding model changes in a separate space before verified activation; keep serving/building progress visible and support revision-bound cancellation. Previous-provider use requires explicit continuation consent.
- Give focused resident lookups direct access to the governed query surface, bound automatic Light routing and reuse guarded custom-provider connections within a turn.
- Reduce Feed/Track page hydration and redundant type lookups; preserve ranked search results through authorized list APIs and avoid retry storms after rate limiting.
- Index canonical attachment metadata, native document text and source-bound image descriptions/visible text from configured multimodal models, without OCR. Add image-processing controls and file-specific search selectors while preserving live access, scan state and source identity.
- Streamline local verification and parallelize contract/PostgreSQL qualification with isolated worker databases, bounded pools and retained reports. Preserve all qualification lanes and cross-process exactly-once checks.

See [search rollout and recovery](docs/ops/DEPLOY.md#search-rollout-and-recovery)
before upgrading an existing index. Scanned document pages, audio/video content,
qualified rollback/retention and production relevance/capacity targets remain
outside this release's completed qualification. Publication and deployment are
separate from this version declaration.

## 0.1.1rc32 — 2026-10-24

- Bound resident-harness context and model-call history, improve turn compaction and refresh the qualified Pydantic AI runtime.
- Renew active sessions consistently across hosted and desktop deployments, and show the installed Integral Core version in Settings → About.
- Expose permission-scoped App passage search through the SDK while preserving caller visibility and workspace boundaries.
- Make timeout and PostgreSQL dispatch-lease regression checks deterministic under release-test load.

## 0.1.1rc31 — 2026-10-23

- Simplify provider setup and chat model controls, improve model settings feedback, and make lengthy messages and usage details easier to read.
- Improve model routing, clarify tool activity, and preserve the responsive layout across chat sizes.

## 0.1.1rc30 — 2026-10-22

- Consolidate App discovery, installation review and full package details in Manage Apps; retire the duplicate Operational Models Settings and catalog screens.
- Keep attachment mosaics consistent between feeds and detail views, with full-image navigation and five outline reactions available on hover, tap or keyboard activation.
- Reconcile comments and mention notifications across delayed requests and live events, preserving recipient, workspace and read-authority checks.
- Expose pending App designs with guarded recovery/discard, preserve nested pasted lists and accept harmless numbered labels in moderation.
- Align local display-name readback and Home AI setup state with saved profile and credential settings, and update the vulnerable PDF dependency.
- Retain parallel CI lanes while keeping source qualification tied to the exact release revision.

- Allow hosted package view frames from the configured public SPA URL as well as the OAuth frontend origin; normalize configured origins and reject malformed CSP sources without disrupting valid parents.

## 0.1.1rc29 — 2026-10-21

- Personalize chat with the signed-in profile name throughout conversations and welcome new users with a brief explanation that Integral can build Apps and set up their workspace as they chat.
- Center the workspace setup acknowledgement and make inline message, tool and approval errors expandable, preserving the full API error when reopening a chat.

- Restore external App handler namespaces when replaying compiled references after extraction or restart.

- Update vulnerable backend/frontend dependencies; replace FastEmbed's incompatible Pillow cap with pinned text-only ONNX inference, retain MiniLM semantics and isolate new artifact profiles during backfill.

- Guide unconfigured chat to Settings → AI Models with a friendly setup notice and an in-app link, including after reopening a saved conversation; offer the same help for a model connection that needs attention.

- Let the agent open permitted Apps, collections, records and saved views in the current tab through `integral_open_view`, preserving chat and suppressing navigation from history, background or interrupted turns.

- End completed write approvals, denials and cancellations with the host review receipt; reserve agent continuation for answered questions, approved designs and unpublished profile revisions that need further work.
- Expose registered App tool discovery and invocation together in record and attachment skills, distinguish registered tool keys from declared queries, and encode binary checkpoint data safely in session exports.

- Refresh chat approvals at committed staging results, reply start and turn completion; retain refresh signals arriving during an active status check so new cards do not wait for idle polling.
- Use contrasting toast surfaces in both themes, vertically centre messages and controls, and allow up to three message lines with status-coloured left borders.

- Preserve approval and other server continuations through native image-input preparation; keep internal host receipts out of chat bubbles after reload and carry exact receipt IDs into scoped readback.

- Add native image understanding from persisted chat uploads, with live image-access and content checks before every model attempt, durable reload/follow-up support, and clear image-format/model errors.

- Close verified setup workflows with a brief result and current resource links; clear applied design state and avoid repeated summaries of unchanged history consuming the response allowance.

- Highlight short chat invitations to confirm or reply; show stopped requests as neutral status notes in live chat and saved history.

- Describe live chat activity in one short line, including reading, search checks and answer preparation; keep retry and cancellation notices current.
- Reuse repeated parent-App checks during Track discovery while retaining fresh canonical admission; route installed-App record questions through their registered read tools or declared queries.
- Bound silent model streams and enforce their full request deadline, preserving uncertain usage and effect receipts during interruption.

## 0.1.1rc28 — 2026-10-20

- Keep current-origin and recognized placeholder record citations inside the active installation and tab; preserve external website links.

- Let App owners edit their installed Apps and current Tracks through the same permission and approval contracts; retain separate package publication authority.
- Add durable, itemized collection updates with current-permission/revision checks, partial-result receipts and interrupted-work reconciliation; reduce repeated Track-list permission scans.
- Add declarative Track/EntryType search policies, durable passage indexing and rebuild/status capabilities. Use local versioned FastEmbed/pgvector embeddings, hybrid retrieval, current-source/permission checks, bounded cited context and recoverable keyword fallback.

## 0.1.1rc27 — 2026-10-20

- Preserve common clipboard formatting as editable Markdown, including nested lists and document inline styles; retain visual-editor limitations explicitly in release qualification. Validate cancellation targets through the typed API contract.

- Read back the current typed App design before building, preserve entry-type identities and fields, validate View configurations before approval, and adapt equivalent board/sort shorthand without relaxing persisted validation. Keep failed-step details readable and typed record links local.

- Give chat failures and recovery everyday wording; bind active skills to their declared workflow tools, expose validated Home starters, and keep approval replies brief while preserving authorization and source checks.

- Polish tag chips across filters, records and editors; remove the redundant Tags prefix and keep entry-tag filters inside Filters instead of the Track header.

- Delay full conversation-title previews until 700 ms of sustained hover/focus, and cancel pending previews during navigation and dismissal.
- Resolve durable chat cancellation from the stored WorkItem key rather than an Object query that incorrectly targets a nested ID.

- Acknowledge chat cancellation only after backend release, retain retryable failures, and fence retries to the observed turn.
- Apply live tag additions through durable approved command batches, preserve existing tags and colors, and explain invalid or duplicate names.
- Keep pasted chat drafts editable, reopen tag selection consistently, and render feed action menus outside clipping containers.
- Remove unused Kanban field options with record/default checks and consistent updates to affected boards; expose authorized library model deletion from the model picker.
- Show readable App/Entry change reviews, retire all Enhance actions and their recommendation service/tool, keep tag filters compact, and filter the global feed by Track-qualified tag identity before pagination.

## 0.1.1rc26 — 2026-10-19

- Validate same-App seed relations during installation using the current human installer authority. Keep incomplete Apps private, respect live access denials, and preserve required-hook rollback.

## 0.1.1rc25 — 2026-10-19

- Require aiohttp 3.14.4 or newer and qualify the 3.14.5 patch release to remediate transport dependency advisories blocking publication.

- Recover Home authoring turns and retain tool outcomes without replaying completed effects (#158).
- Finish stalled native harness turns within request, tool and token budgets, with grounded completion and truthful scaffold outcomes (#158).
- Brand the session renewal dialog and preserve the workspace behind a translucent blur. Increase default access lifetime to 60 minutes and silent renewal activity eligibility to 15 minutes; expose session timing as deployment configuration (#158).

## 0.1.1rc24 — 2026-10-19

- Renew expired sessions in place, preserving the open page and drafts, with same-account password reauthentication when refresh is rejected. Keep temporary outages retryable and do not automatically resubmit interrupted actions or chat turns (#156).
- Preserve CORS headers on authentication rejections so cross-origin clients can handle session expiry correctly (#155).

## 0.1.1rc22 — release candidate

- Extend the finite API deadline to 120 seconds and explain uncertain write outcomes without offering an automatic retry (#150).
- Continue new interactive chat turns after terminal recovery conflicts without replaying old tool effects or clearing pending approvals (#151).

## 0.1.1rc21 — release candidate

- Add a standalone desktop launcher with a managed local Core runtime and a shortcut to BYOK model settings. Installer signing and platform release qualification remain separate distribution gates.
- Preserve workspace App rows during failed background refreshes and distinguish loading from an empty directory.
- Publish workspace scope before dependent requests, display governed tool summaries and refresh partial batch results.
- Show provider credential validation messages in model settings.

## 0.1.1rc20 — release candidate

- Allows owning App mutation facades to maintain readonly computed relations while retaining direct-edit and target-read restrictions.

- Incorporates PR #145 and the reviewed PR #144 integration: transactional audit delivery, Agent/UI recovery, access-policy and generic extension fixes.
- Consolidates maintained documentation and retires chronological review reports; preserves outstanding hardening work in a current backlog.
- Fresh-wheel Business qualification is recorded in `docs/ops/RELEASE_QUALIFICATION.md`.

## 0.1.1rc19 — 2026-10-16

- Preserve JSON array order and primitive types when saving entry edits, while
  normalizing representations only for fields with a known non-JSON type.
  Optional select fields can be cleared with None; required and read-only
  fields retain their restrictions.
- Require signed service authentication on MCP paths, including nested URLs;
  only the exact system uplink routes retain their signature exemptions.

- New Apps and Tracks default to Private, including catalog, anchored and scratch Tracks. App visibility offers Private, Workspace and Public; App Inherit is removed and rejected by the API. Explicit authorized sharing remains available; saved access settings are preserved. Agent creation schemas now use the canonical Workspace spelling.

- Retry recoverable model failures up to three total attempts with inline retry
  progress, distinct attempt accounting and fresh admission; never restart a
  complete agent run or replay already delivered output.
- Continue completed conversations safely after uncertain earlier model attempts
  by checking original tool receipts while preserving unknown billing evidence.
- Add native approval claims, atomic effect receipts and recovery for App/Track/
  Entry mutations and identified comments; retain reconciliation for unresolved
  effects and explicit single-process limits for local adapters.
- Bind audit/event history to current workspace authority with bounded keyset
  windows and per-recipient snapshot filtering, and recheck stream release access.

- Background jobs for Apps: `ToolContext.enqueue_job`, `report_progress` and
  `job_status` run an App's own tool as a durable, resumable work item (kind
  `app_tool`) as the person who queued it, with progress and results shown in
  background tasks. App jobs run on their own in-process loop; the supported
  `WORKERS` baseline is unchanged. See `docs/platform/background-app-jobs.md`
  and ADR 014.

- Scheduled tools: a tool may declare `schedule` (cron, optional timezone) and
  Core queues it once per due slot as the App's installing person.

- A generic `region-system/processing-status` region and an optional
  `min_height` for App extension views.

- Compact message debug request context by omitting empty optional session/focus
  fields and identical page-context focus duplicates; retain diagnostic evidence.

- Clarify the chat activity line with Thinking, observable tool activity, and
  distinct finished/stopped/interrupted states; explain expandable usage details.

- Ordinary chat starts with compact public dialogue and no workspace functions;
  the configured model can request operational context within the same turn.
  Approvals, builds and recovery retain the operational harness. Conversation
  replies preserve operational checkpoints and omit automatic title inference.

- Home card accent borders follow the selected Workspace identity color and
  retain the theme fallback when no Workspace color is configured.

### Fixed

- Recheck current workspace membership and privileged-tool authority when App
  jobs are queued and on every execution attempt. Apply canonical work observer
  authorization to App job status so members cannot inspect another member's
  progress, failure details or result references.

- A bundle tool that updates its own App's records now resolves the App in the
  workspace where it is installed, not only in the caller's personal workspace.
- Keep ad-hoc Track relation identity stable through display renames, including
  bounded recovery from retained creation snapshots for legacy Tracks.
- Give passive agent polling and approval actions independent bounded rate
  budgets; retain token preflight in the action budget and retry-after responses.
- Retain failed/skipped approvals as unapplied, label explicit application
  retries, and distinguish an applied write from a failed review refresh.

- Show the actual App and Track collaborator count to invited readers while
  keeping collaborator names and access details restricted to managers.

- Resolve dashboard widgets with bounded parallel reads and independent live
  permission checks, reducing measured local dashboard latency while preserving
  widget results, ordering and error handling.
- Batch scoped bundle entry reads through canonical audience admission, avoiding
  repeated point authorization cascades while retaining current membership,
  exclusions, direct grants and active-App checks.

- Keep Kanban relation labels and unchanged board state stable so mounting a
  board cannot trigger a render loop that stalls route navigation.

- Publish admitted entry fields and revisions before save hooks run, preserving
  nested updates and transaction rollback without false revision conflicts.
- Prevent same-task save hooks from recursively re-entering themselves on the
  same entry, while allowing independent tasks and subsequent saves.

## 0.1.1rc18 — 2026-10-13

- Pin published jvspatial 0.2.2 and remove the transaction compatibility adapter.
  Restore admin inventory and owner labels, invited-user track listing, and
  quiet successful database operations while preserving error logs and metrics.
- Prevent passive approval reconciliation from starting another model run;
  retain explicit-action continuations and single receipts across refresh races.
- Streamline Home and Admin, unify chat composer capabilities and theme styles,
  send Home and Enhance requests immediately, and dismiss the floating welcome
  hint after acknowledgement or a user message.
- Correct search-icon spacing, navigation and profile routing, and remove
  internal IDs from ordinary lists, details, pickers and missing-name fallbacks.
- Default native turns to 1,200,000 tokens with bounded context retrieval.
- Refresh documentation and add PostgreSQL, browser and artifact evidence.

- Let trusted hosts supply temporary commercial entitlement access without
  creating durable grants. Recheck installed package access during execution,
  settings finalization, resume and startup; retain generic read/export access
  when entitlement access is removed.
- Expose read-only effective workspace model-key readiness so host quota notices
  can reflect validated owner BYOK without exposing credentials or updating
  usage telemetry.
- Continue safely after terminal failed or cancelled assistant runs when scoped
  receipts permit it; preserve uncertain provider usage and unresolved-write
  fences. Close provider streams in their owning task during worker interruption.
- Preserve App editor controls when non-admin access responses hide the
  collaborator directory, using the caller's current server-resolved role.
- Store per-user IANA time zones, infer unset zones from the authenticated browser,
  and expose an editable Profile setting. New reminders and relative-date query
  plans inherit the account zone while existing and approved schedules retain
  their saved zone; ambiguous local one-shots require an explicit offset.

## 0.1.1rc17 — 2026-10-12

- Typed relation writers require current target-read authority and canonical
  Entry/Track/App containment. Declared cross-App targets resolve through the
  existing workspace and instance contract, and verified owning App IDs are
  stored on reference edges. Denials preserve the source record and links;
  delegated provenance does not supply independent authorization.
- Establish governed access-control lifecycle checks and preserve authorization
  fences across staged operations.
- Return App and Track nodes to the caller's active graph context after creation
  so subsequent writes remain usable and inside the enclosing transaction.

## 0.1.1rc16 — 2026-10-09

- Integral AI, powered by Pydantic AI, is the sole resident harness and is
  always active after installation. No activation flag or harness selector is
  required. Saved provider choices cannot replace the resident.
- Model setup uses Settings → AI Models. A missing model produces actionable
  setup guidance while Integral AI remains active.
- Model configuration exposes one primary chat model and optional voice input;
  obsolete harness slots and JVAgent compatibility are removed.
- Native discovery returns bounded model overviews and precise capability
  results before loading full definitions. Rejected tool calls retain structured
  refusal codes, and interrupted budget failures preserve completed effects.
- Core requires Python 3.11 or newer. Managed installations isolate their
  settings and enforce authentication, credential encryption and rate limits.
- Core installs as one wheel with ordinary PyPI runtime dependencies. Portable
  resident skills ship with the wheel and use the same instructions as a source
  checkout.
- Managed local startup reports the actual resident identity. A previously
  running installation must be restarted before it can be reused by the desktop
  launcher.
- Business's desktop launcher consumes the public Core startup contract and
  bundles Core without a separate agent runtime.
- Packaged HTML is not cached across upgrades. Business clears its HTTP cache
  before opening the local workspace so a prior interface cannot mask the
  installed resident or reference removed assets.
