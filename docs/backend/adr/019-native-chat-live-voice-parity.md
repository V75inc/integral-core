# ADR 019 — Native chat as the live voice conversation

Status: Proposed, implementation in progress (2026-11-25)

## Context

The signed-in browser call is a conversational model and a transport to the
same resident harness as typed chat. It must support speech-only operation
and mixed speech, typing, attachments and GUI decisions in one thread. A call
remains bound to one workspace, human and agent profile. Telephone admission
and switching workspace or profile mid-call are outside this release.

The latest disposable-workspace smoke produced a real `integral_open_view`
call whose result said there was no active browser view. The resident staged
the new Water the plants task, and the GUI subsequently committed an
Updates applied message. The voice projection did not report it. The
ordinary chat history preserves the delegated requests and results, but the
provider's audio and full spoken dialogue are not recorded in server logs.
Therefore the precise unwanted spoken wording cannot be reconstructed.
Earlier synthetic browser trials exposed lengthy repetition, invented
constraints during delegation, and confusion between discovery and action.

## Decision

1. **Use the existing native navigation contract.** Capture the originating
   browser's bounded page context. Pass it with ordinary voice turn admission.
   The resident resolves the named resource and invokes `integral_open_view`.
   Deliver a typed navigation event only for a successful foreground turn,
   through the existing authorized event projection. Revalidate the resource,
   current caller and destination at delivery. The browser checks its current
   thread, route, focus, draft, uploads and queue. A real route transition
   acknowledges the destination. Recovered/history/background events never
   navigate. Reuse the mounted chat companion so a call survives the transition.
   A suppressed or unavailable route is reported accurately.
2. **Keep conversation concise and grounded.** Ordinary social conversation
   stays on the realtime model. Submit the complete finalized spoken request
   to native chat once for workspace work. Keep provider arguments, tool
   diagnostics, technical labels and hidden reasoning out of speech. Start
   with the answer, speak the meaningful change or missing detail, and offer
   more if needed. Avoid speaking a second acknowledgment for typed input,
   arbitrary intermediate tool data, and duplicated final output. Preserve
   full authorized details visually. A spoken outcome must not invent facts
   or claim execution before a receipt. Track latency and word count separately.
3. **Report committed outcomes across interfaces.** Observe the exact thread's
   durable approval and execution receipts, including GUI actions and voice
   work. Use immutable proposal identity and verified state transitions, not
   aggregate count differences or free-form UI text. Announce once at a
   speaking pause, rechecking authority and outcome at first audio. A decision
   accepted but not applied, a rejected decision, an expired proposal,
   successful application, and a failed effect are distinct. Do not turn a
   historical receipt or a model-generated message into an effect claim.
4. **Review conversationally.** Offer a concise, exact prompt when one
   eligible proposal is ready: identify the operation, resource, changed
   fields and relevant values. Use the existing independent confirmation and
   canonical decision service for private reversible effects. For a new
   record, derive the review from canonical validated payload and current
   Track schema, including the required fields and exact dates. Extend
   supported effects individually, including revision and readback. An
   incidental yes or provider tool argument cannot authorize an effect.
   Bind confirmation to the immutable proposal and invalidate on mutation,
   expiry, interruption or loss of authority. Material/external and
   destructive/security effects retain their specified independent approval
   requirements. If a review requires a stronger interaction, explain the
   specific requirement and keep the caller's place.

## Architecture and migration

Keep the pinned Pydantic AI harness and existing broker, skills, tool
manifest, work admission, staging and event journal. The realtime adapter
owns speech turn timing, correlation, playback and scoped delivery. No
second resident, shadow work queue, arbitrary URL tool or new effect owner.
No stored audio or credentials in chat. Future shared-transcript work uses
a distinct persisted finalized utterance and idempotent admission reference,
with retention and correction semantics documented before implementation.
Existing conversation and WorkItem identities remain valid.

The remaining work is ordered in the reassessment below. Reuse
`agent_navigation.open_view_for_dispatch`, `agentNavigationPath`,
`chat_turn_events`, `native_voice_thread_status`, `native_voice_review`
and `approval_decisions`; extend them only where necessary.
The existing voice-first plan covers transcript, steering and attachments.

## Invariants

- I-HARNESS-01/02: bind accepted context to the current native thread; host
  page context is bounded data and confers no model authority.
- I-ACCESS-01 and I-WORK-08: recheck disclosure and resource policy, and
  distinguish historical output from current authority.
- I-WORK-01/02/03/04/09: preserve one work admission, one effect owner,
  canonical idempotency, and same-thread receipt identity.
- I-APPROVAL-01/02/04/05 and I-WORK-05: unchanged effect classifications,
  exact human confirmation, attributable decision and verified outcome.
- I-GRAPH-01/02: no new detached records; any future transcript projection
  must use the chat graph.
- I-RUNTIME-01/02: bound repeated reads and delivery state. Apply the
  configured operation limits before authorization grants.
- I-SPEECH-01: provider credentials remain server-side.

## Acceptance and evidence

Use isolated fixtures for cross-workspace, invalid destination, stale tab,
scope loss, reconnect, two tabs, drafts, upload and pending review. In Mac
Chrome ask voice to open Todo Tracker, open Tasks, query urgent records,
create a titled task, hear concise review, approve conversationally and hear
the verified applied result. Repeat with a GUI approval and rejection.
Check actual browser route and record readback independently. Exercise a
second approval, changed schema, revoked access, failed apply, replay and
interruption. Confirm no duplicate turns, approvals or announcements.
Measure time to acknowledgment versus useful audio and verify spoken
content from captured synthetic playback alongside the saved transcript.
Full release needs physical microphone testing and a complete relevant gate.

## Active completion goal and qualification (2026-11-25)

Complete this ADR through the same agent tools and canonical work/approval
paths used by chat. Keep it proposed until the browser journeys and the
remaining release checks pass.

- [x] Expose routing through the existing `integral_open_view` manifest,
  binding and workspace, entries and insights skills. Resolve accessible
  resource IDs before requesting a route. The server checks the current
  caller, thread, workspace and resource again on delivery.
- [x] Correlate a one-use browser arrival response with the live request.
  A page revision replaces raw URL equality, fixing thread deep links.
  Chrome with an isolated socket fixture opened the real authorized App,
  acknowledged its loaded page context, retained the call and preserved a
  draft on a second request. This proves the browser path with a
  representative relay event.
- [x] Make rapid terminal decisions discoverable without observing the
  live proposal first. The existing atomic terminal receipt retains
  thread/run correlation and intent/request hashes, with no approval token,
  payload or result. Old receipts retain their prior behavior. Each outcome
  is rechecked before speech. Failed application and subsequent success
  receive separate announcements. A bounded cursor reads new retained terminal
  receipts even for older work. PostgreSQL qualified twelve decisions sharing
  one resolution timestamp across two pages and rechecked exact work ownership.
- [x] Qualify private scalar record creation with the workspace assistant.
  Use the existing canonical proposal form and field validator to read the
  effective destination, title, defaults and date. The Entry transaction
  checks the reviewed schema and effective values before writing. App hooks,
  relation/file/computed fields, long bodies, tags and compound effects
  retain their card path. The independent challenge still owns confirmation.
  PostgreSQL qualification corrected a new task's date, confirmed it,
  read back the stored values and replayed confirmation with one record.
- [x] Qualify the live-provider correction and confirmation journey with
  synthetic microphone input and captured browser playback.
  Initial runs hit DeepSeek's usage limit. The local demo launcher was
  still forcing that earlier test route after the user saved single-model
  GPT-6.1 Sol. That override and temporary diagnostics were removed.
  A second issue in the pinned SDK required reviewed native Responses and
  streaming metadata. Without it, tool choice was refused or simulated
  streaming duplicated argument JSON and triggered output validation retries.
  Core now supplies that metadata through the SDK's supported registration
  API. A fresh typed browser turn persisted GPT-6.1 Sol execution and
  `integral_open_view`. A subsequent real browser voice call opened
  Todo Tracker, received its rendered acknowledgment and retained the call.
  Real Chrome calls queried urgent records, staged a new record and revised
  its date by field label. In the successful create/correct/approve call,
  the independent recognizer accepted “I approve” and the six challenge
  digits. Captured playback confirmed the saved title, status, priority
  and corrected date. A separate database read found exactly one matching
  record dated November 30. An earlier mismatch left its proposal pending.
  Physical microphone and speaker qualification remains outstanding.
- [x] Exercise App, Track, record and saved-view arrival in Chrome with a
  synthetic relay event. An unsent draft and a staged attachment each
  prevented a navigation, preserved the input and kept the call connected.
- [x] Check published-profile review isolation. The realtime session omits
  the workspace review and correction tools. Stale provider requests for
  either tool cannot enter the review flow. Published profiles run the
  capabilities admitted by their own connection profile.
- [x] Exercise a typed creation request and GUI approval during the same
  live Chrome call. In 32 seconds of post-decision playback there was one
  announcement that the change was applied. A separate database read found
  one matching record. A stalled-playback fixture qualified receipt retry
  after the pending speech queue fills, with one queued announcement.
- [x] The owner's browser smoke reported much improved voice
  experience. It exposed a follow-through bug and slow long tasks. In the
  subsequent synthetic Chrome call, a proposed library lending app was
  built and verified after “Looks good, let's build it”. The live relay
  now retains bounded completed answer context and reconciles the exact
  terminal worker if its earlier observer has not finished. The same
  conversation produced the build and audible confirmation.
- [ ] Qualify live latency after the history summarization repair, confirm
  the full covered-screen microphone and speaker pilot, and complete the
  release gate.

The latest native voice unit gate passes 104 tests. A separate profile and
adaptive-context gate passes 108 tests. The earlier combined focused
CI-environment gate passed 164 tests covering voice, routing,
approval decisions, proposal readback, the tool manifest, skills and the
generated capability map. The disposable PostgreSQL gate passes 31
contract tests, including creation with date correction and brief notes.
The full frontend suite passes 3,265 tests. Isolated Chrome browser
navigation passes at desktop and 390-pixel widths. App, Track and focused
record arrival retain the same call. Model-routing, SDK transport and
AI-configuration regression checks pass 224 tests after the selected-model
repair. The full backend diagnostic run reported 40 failures including
subtests, 10,131 passes and 1,416 skips. Its failed files cover dependency
audit fixtures, import/authorization audits, execution-scope schemas and
channel/skill contracts. A comparison against the earlier pre-ADR-change backend run matched all
40 failure IDs including subtest multiplicity. This does not establish a
green release build. These checks qualify their stated
paths. The full backend gate and the physical-device pilot remain open. In the
follow-through investigation, two recent heavy turns took approximately
108 and 116 seconds with 8 and 10 first-attempt successful GPT-6.1 Sol
requests. Two zero-tool summary requests took around 32 seconds each in
the latter. History compaction now permits a single successful history
summary in one admitted run despite subsequent accounting changes. The
regression exercises changed metadata, a second model step, and a new
run. The output validation and context admission limits remain intact.
The subsequent two read-only browser checks on the synthetic app completed
in 12.0 and 11.7 seconds with four model requests each and zero zero-tool
requests. They are not equivalent build latency benchmarks.
The latest focused voice/compaction/import gate passed 164 tests; a
targeted rerun of the previous broad-suite failure areas reproduced 36
failure IDs (including subtests), with no new failures in that selection.
The adapter import, channel discovery, execution scope schema and skill
fixture checks passed on rerun. Reconciliation of the broader candidate
then repaired the remaining synthetic audit fixtures and placed profile
membership creation under the shared authorization fence. The targeted
baseline rerun passes 102 tests and 28 subtests, and the actual dependency
audit passes with no high or critical frontend findings. The earlier
candidate-specific release approval remains unchanged and expired.
The latest PostgreSQL voice suite passed 31 tests, and six additional
PostgreSQL published-profile contracts passed with the membership fence.
The distribution contract and reproducible wheel checks passed after
refreshing the packaged connector schema. A fresh full release gate is
required for the combined candidate.

The numbered reassessment below records the findings that motivated this
work. This checklist describes their current implementation and qualification.

### Earlier browser evidence

- Navigation now passes validated page context to the native worker and
  delivers its canonical navigation event only from the same successful
  live work and page generation. A Mac Chrome provider call opened the
  actual Todo Tracker route and retained the same voice controls. Browser
  acknowledgement was subsequently implemented and exercised with an
  isolated browser socket fixture. Deeper Track/record routes and manual
  page changes during a provider call need end-to-end qualification.
- The initial live lookup misrouted an open request to quick capability
  discovery; explicit browser-open intent now goes to the resident.
- The latest GUI-approval test staged a rename of an isolated synthetic
  record. The captured speech read the old and new title, asked whether
  to approve or reject, and after clicking Approve spoke “Your approved
  change was applied.” The initial attempts failed because receipt polling
  was paused while speech was active and a completed WorkItem had cleared
  its run ID. The adapter now observes during playback and reconstructs
  the deterministic successful attempt run identity. It rejects a proof
  for a different attempt.
- The earlier independent verbal decision slice covered supported record edits.
  A unique exact eligible edit no longer requires the caller to first
  select its record through a separate navigational turn. The independent
  one-use confirmation remains required; selected different targets and
  changed proposals are rejected. Creation now has the narrow independently
  qualified scalar path described above. Other unqualified effects retain
  their card.
- Conversational brevity instructions and removal of generic typed-input
  announcements reduce noise. The provider can still vary spoken wording;
  natural conversational quality, overlap and completion latency need a
  wider live evaluation.

Focused gates cover native routing, independent review, private receipt
projection, PostgreSQL replay and scope checks, and frontend navigation
and call controls. This ADR remains proposed until the remaining journeys
are qualified.

## Reassessment and remaining work

The local browser trials establish two individual observations. The tab
opened Todo Tracker and kept the microphone connected. In a different call,
the caller heard a rename proposal, clicked its card and heard that it
applied. Neither trial demonstrates the complete create-by-voice journey.
Keep the same realtime model and resident harness as the baseline while
closing the following gaps. No comparative model trial has established a
model-capacity cause.

1. **Close the browser navigation loop.** The relay marks a navigation as
   delivered when it sends the event. The browser can silently decline it
   for a draft, upload, queue or stale origin. There is also a reproducible
   mismatch on the native thread deep link. The browser compares its raw
   pathname and query with the server's sanitized URL, which strips
   `?thread=…`. The same open request can therefore work on `/agent` and
   be discarded on a deep-linked conversation. Keep URL sanitization.
   Correlate a one-use navigation request with the originating tab, page
   revision, bound thread and authorized work. Return an outcome when the
   destination renders, the browser preserves the current page, or the
   request expires. Browser feedback describes presentation only. It
   confers no permission and cannot certify an effect. Exercise App, Track,
   focused record and saved view from both chat layouts and a deep link.
   Include a visible background window, a draft, access loss after dispatch
   and a late acknowledgment. Only the matching rendered destination merits
   an audible “opened.”
2. **Make a decision observable even when it precedes the first poll.**
   The three-second watcher reads up to eight recent turns and eight
   proposals. Its notification requires seeing the same proposal in an
   awaiting or authorized state first. If a card resolves between reads and
   the live proposal is gone, its terminal marker alone cannot reconstruct
   its thread provenance. Also, a failed apply has no dedicated spoken
   transition. Reuse the committed decision and execution paths to make a
   thread-scoped outcome discoverable by cursor. For linked work,
   `WorkApproval` already carries work/run identity, staging intent and
   execution references. Verify coverage for both linked and ordinary
   staged proposals. The prompt queue's best-effort change event can wake
   a reader, but cannot establish application. Where existing receipts
   lack the exact thread correlation, write the minimum correlation at
   the owning transition, atomically or through its existing recovery path.
   A wake-up or a card's display message alone proves nothing. Start at call
   admission, reconcile missed wake-ups, and recheck scope at disclosure.
   Gate on immediate click, reject, supersede, expiry, approval followed
   by failure, retry followed by success, two simultaneous proposals and
   voice confirmation racing a card click. Each distinct verified outcome
   is attributable to the right task and heard once during an uninterrupted
   call. Reconnection must never repeat a decision or execute work.
3. **Qualify one creation end to end.** The generic policy classifies
   `create_entry` as private reversible, but the voice decision service
   accepts only eligible `update_entry` proposals. The create path can
   resolve types, supply defaults and run App hooks at execution. A schema
   revision alone does not establish what a hook will do. Start with a
   single qualified creation whose destination, effective values and
   consequences can be established in advance. Share the canonical create
   validation, bind that projection to the decision, and fence execution
   against changes before writing. Keep unqualified defaults, hooks or
   compound actions on their existing review path. Use the independent
   readback and one-use spoken challenge. Test “Add Water the plants for
   today” against the caller's calendar date, correct the date, confirm,
   and read back the stored title, date and Track. A concurrent card click
   or replay must leave exactly one record. Changing schema, permissions or
   effective values must invalidate the old confirmation. Run separately
   for the workspace assistant and a restricted published profile. Current
   voice review is workspace-assistant only.
4. **Judge conversation by playback throughout these changes.** The captured
   rename repeats that review is pending before it asks for a decision.
   A one-to-two-sentence prompt has not eliminated repetition. For routine
   events, assign one spoken response to each task transition. Make the
   proposal name the meaningful change and ask one question, for example
   “Rename Copper Lighthouse to Copper Beacon?” Preserve the separate
   exact confirmation readback. Coordinate the worker reply, proposal and
   card-outcome announcements so they cannot each narrate the same result.
   Compare synthetic microphone input with the text actually admitted to
   the worker: today the delegate takes a model-supplied request, not a
   verified verbatim transcript. Include negation, an interruption and a
   follow-up while a task finishes. Score captured playback for factual
   accuracy, repeated facts, unsolicited narration and time to the first
   useful answer. Retain only consented synthetic recordings for these
   tests. The final gate is one covered-screen run from opening the App
   through hearing the saved value, followed by mixed-input and device
   qualification in the [voice-first plan](../../product/VOICE_FIRST_OPERATION.md).
