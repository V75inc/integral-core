# ADR 019 — Native chat as the live voice conversation

**Status:** Browser implementation in review. Broader hands-free qualification remains open.

## Context

The signed-in browser call is a conversational model and a transport to the
same resident harness as typed chat. It must support speech-only operation
and mixed speech, typing, attachments and GUI decisions in one thread. A call
remains bound to one workspace, human and agent profile. Telephone admission
and switching workspace or profile mid-call are outside this release.

A spoken request can change the same workspace state as a typed request.
A browser route, a signed-in user's decision and a saved change each have
different owners. The call needs to distinguish them before telling the
caller what happened.

## Decision

Engaging a live call makes speech the primary way Integral communicates for
the duration of that call. This applies inside the app, even with chat visible.
Typing, attaching a file or clicking an approval card contributes to the same
conversation and leaves the call in voice. Honor an explicit request to put
details in writing. The authenticated adapter exposes the actions it can
perform, such as opening a browser route or performing independent confirmation.
Neither model needs to infer whether the caller is looking at a screen.

1. **Use the existing native navigation contract.** Capture the originating
   browser's bounded page context. Pass it with ordinary voice turn admission.
   The realtime workspace assistant exposes `open_integral_page` separately from spoken directory browsing. It submits a read-only resident turn for a named destination or a revalidated choice from the last spoken list. The resident resolves the resource and invokes `integral_open_view`.
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

### Resident permissions and spoken decisions

The workspace assistant uses one confirmation preference for the signed-in
user in each workspace. **Ask for approval** is the default. **Approve for me**
covers eligible simple record creates and edits in owned private lists.
**Custom** narrows that scope to saved lists, preserving the selection when
another mode is active. **Full access** permits record creates and edits
where the caller already has write access, including ordinary App save hooks.
Other effects retain their existing decision requirements.

The worker binds the preference revision at admission. At decision time it
rechecks the grant, the exact proposal, work attempt, principal, workspace
and target. A later preference change cannot approve an earlier request.
Published profiles and connector calls cannot borrow this workspace-assistant
preference. The canonical writer validates the effect and records the outcome.

For an eligible pending review, the call voices the exact proposed change and
captures the caller’s reply after playback. The configured Speech to Text
provider independently recognizes an unqualified English affirmative or
negative. Ambiguity, a correction, expired scope or unavailable verification
leaves the change pending. A supported revision needs a fresh decision.
A private record plus one one-time reminder to the same chat can be reviewed
together. Execution and recovery use the batch journal, including partial
completion. More complex batches retain detailed review.

## Invariants

- I-HARNESS-01/02: bind accepted context to the current native thread; host
  page context is bounded data and confers no model authority.
- I-ACCESS-01 and I-WORK-08: recheck disclosure and resource policy, and
  distinguish historical output from current authority.
- I-WORK-01/02/03/04/09: preserve one work admission, one effect owner,
  canonical idempotency, and same-thread receipt identity.
- I-APPROVAL-01/02/04/05 and I-WORK-05: preserve effect classification,
  attributable decisions under the applicable policy, and verified outcomes.
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

The [conversation contract](../voice-chat-conversation-contract.md) specifies
turn admission, playback, review and recovery. The [voice-first qualification
guide](../../product/VOICE_FIRST_OPERATION.md) covers the browser journeys and
remaining device checks. Published-profile queue and steering parity, files
submitted during active work, physical audio and sustained mobile use require
separate qualification. Telephone admission is deferred.
