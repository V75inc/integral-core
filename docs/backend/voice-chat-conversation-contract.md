# Shared conversation contract for text and live voice

## Provider protocol boundaries

OpenAI Realtime and GPT-Live use different session and event contracts. The
provider transport registry selects only documented exact model IDs and fixed
official endpoints. The GPT-Live primary WebSocket starts with `session.start`
in client-delegation mode, waits for the resolved configuration, streams PCM,
and requests `session.close` with bounded cleanup. Provider transcript deltas,
delegation IDs and commentary acknowledgments are observations, not complete
caller turns, spoken-delivery receipts, or approvals. A host-committed full
utterance is required before the delegation ledger admits a task; the existing
resident and review authority remain separate. Silent context and speech use
distinct provider events. The pinned SDK does not provide the newer Live client.

The browser route has a protocol-specific Live media adapter under qualification.
Synthetic sessions exercise the original browser framing, native resident
admission, independent exact-review audio preparation and post-playback caller
capture. The real Live provider does not furnish an ordinary audio-completion
or response-cancel event. A commentary ACK accompanied by transcript and PCM
is evidence of offered speech, not proof of complete playback. Live permits
only up to 800 ms of automatic acknowledgment delay; a timely silent status
append suppresses pending automatic acknowledgments but cannot retract earlier
speech. Result updates carry the original delegation ID. A voice approval
still requires the exact verified review and fresh, independently captured
caller answer, never a delegation or transcript event. The existing capture
size budget is unchanged; rendered review audio has a separate bounded
verification path using the same authorized Speech to Text credential.

Voice assignment continues to allow only the qualified Realtime IDs. Do not
publish GPT-Live as an enabled voice choice until provider-level playback,
interruption, result delivery, follow-up/steering and the pending telephone
relay's caller-scope contract are qualified. This is an implementation
checkpoint, not a claim of equivalent product support.

This contract covers signed-in browser calls attached to an Integral Native
conversation. The [voice-first guide](../product/VOICE_FIRST_OPERATION.md)
describes how to use and qualify spoken navigation, review and readback.
Telephone admission is deferred. Public visitor chat has its own identity
and conversation boundary.

## One conversation, two interfaces

The native harness owns committed messages, attachments, authorized tools,
turn admission, approvals, and durable work. Text and live voice enter through
adapters for that same conversation. A live voice model is the low-latency
conversational surface, not another worker, database, or source of authority.

- **Text input:** the existing chat endpoint admits the typed turn once. It
  renders its receipt and result in text as before. A bound live call may
  notice the new turn and read or speak its committed result; it must not
  resubmit the typed request or independently answer from uncommitted output.
- **Voice input:** small talk stays in the Realtime interaction. A bounded
  lookup can use a capability discovered from the reviewed catalog. Larger
  work goes through the canonical harness once. The authorized queue,
  steering, cancellation and approval paths remain the same.
  The shared chat carries the request in the caller’s voice. For example,
  “Can you put ‘Send the revised quote’ on my list for Friday?” keeps the title
  and date intact. For a new action or substantial task, the voice model
  supplies a short task-specific plan, e.g. “I’ll add the revised quote to
  your Friday list.” The relay requests that private spoken recap before
  starting the resident task. It waits for synthesis completion, with a
  bounded fallback if synthesis is unavailable or the caller interrupts;
  playback alone does not determine whether already requested work runs.
  Quick lookups and clarification replies do not require an extra speech turn.
  For verified queued or running work, the relay offers a brief status update
  after 15 seconds, then after another 30 and 60 seconds if still active.
  It checks the exact receipt again before audio, waits for a conversational
  pause, and gives completed results and review precedence over progress.
  It never resubmits or automatically cancels work to provide an update. A verified terminal result or committed typed assistant reply has priority
  over ordinary small talk. The relay observes completion even during speech,
  cuts only the ordinary response and flushes its queued PCM. The browser keeps
  earlier verified readout audio queued; it reports discarded or already-played
  samples for the exact relay-issued playback handles so unheard provider
  history can be trimmed. A natural transition is optional. User speech,
  tool-bearing responses, confirmations and other verified readouts retain
  precedence. A tool call that completed while an ordinary response was being
  cut is processed through the original turn after the readout; partial tool
  calls are never executed. Cancellation here targets speech synthesis by
  response ID, never the resident task.
  For a workspace lookup the voice model calls the resident first and speaks its answer. After the delegation, only a requested acknowledgment or authorized readout can speak until the caller speaks again, including after the readout completes. Buffered speculative lookup speech is discarded. The acknowledgment is a short line to voice. It is never replayed to the model as a new user request. A denial is reported when the authorized lookup returns one.
  When speaking record details, the same renderer shapes structured values
  into conversational sentences, rather than reading schema keys, JSON or
  punctuation. Preserve requested facts, exact dates, amounts, names, status,
  significant digits and missing/unknown information. The original saved
  result is unchanged; an explicit quotation or exact review readback uses
  the existing verbatim path instead.
  A clarification such as “Friday afternoon” goes to the resident as a short reply.
  Confirmation and the receipt still depend on the actual proposal and outcome.
- **Attachments:** a submitted, visible attachment can be acknowledged by named
  metadata. The user has not attached anything merely by selecting it in a
  composer. Presence does not mean extraction or analysis succeeded. Use the
  existing scoped file path to inspect content when needed; do not send an
  original, base64 bytes, extracted text, URLs, or credentials as event context.
  Convey readiness only when the authoritative permission-checked state is
  available.
- **Approvals:** during a call, speak the essential proposed change. Offer
  independent spoken confirmation for eligible effects. A decision made on
  a card enters the same workflow. Announce the verified outcome once.
  When a stronger review is required, explain what remains pending and the
  specific step that can complete it.
  The call asks about the action directly, for example “Would you like me to
  add ‘Review the proposal’ to Today?” Answer once, for example
  “Yes, please” or “No, thanks.” A qualified answer such as “Yes, but make it
  tomorrow” leaves the proposal pending. The chat keeps **Review details** available.
  Recurring schedules, agent runs and write grants retain their respective reviews.

### Resident permissions

Open **Permissions** in the workspace assistant’s composer. The setting applies to the signed-in person’s new chat and voice turns in this workspace.

| Mode | What the resident can do without asking |
| --- | --- |
| Ask for approval (default) | Read. Every proposed change waits for a decision. |
| Approve for me | Make supported simple record additions and edits in private lists you own. Changes involving hooks, assignments or more complex fields still need review. |
| Custom | Make the same simple edits in selected private lists and changes to Home and dashboards in explicitly selected eligible private Apps. Checking an App selects both eligible categories; each can be adjusted individually. |
| Full access | Create and edit records across apps where you have write access, including shared lists. The canonical writer enforces current access, app rules, record revisions and normal save hooks. |

Choose **Custom** to view or edit your saved selection. Switching to **Ask for approval**, **Approve for me** or **Full access** keeps those selections. Returning to Custom shows the previous choices. A list or App that loses eligibility cannot authorize an edit. Existing list-only selections remain list-only until the new App layout selection is explicitly saved.

Mode changes take effect on new turns. Previously pending proposals retain their review. Deletion, sharing, connector actions, batches and other effects outside record creation, update, and explicitly selected private App presentation changes keep their existing review requirements. Without a standing grant, independently confirmed bounded Home label edits and dashboard rename/layout/default edits can be approved by voice. Complex queries, native content, widget replacement and actions retain visual review unless the explicit App presentation grant covers the canonical change. An eligible single leaf record deletion can be confirmed independently by voice after a readback of the exact record and permanent consequence; say “yes, delete it” or “no”. Standing grants never authorize deletion. Independently verified review outcomes enter the realtime model’s dialogue as a quiet status update without proposal data or new authority; explicitly renewed requests start a fresh proposal rather than replaying the old decision. Ambiguous or changed targets and anchored, batch, App and Track deletion retain their stronger review. The previous single-list and multi-list settings read back as Custom when nonempty and Ask for approval when empty.

### Turn detection

The OpenAI realtime voice session uses semantic voice activity detection with
balanced (`auto`) eagerness. It waits for a more complete spoken thought rather
than treating every short pause as a finished request. The provider still
supports interruption, and the relay retains generation fences and current
authority checks. Independent confirmation capture is unchanged. This affects
turn detection only, not the member's selected model or resident model routing. Speculative ordinary audio has a bounded 350 ms initial hold to avoid playing a syllable before a tool handoff takes ownership. Verified acknowledgments and results are not held. Genuine barge-in still fences playback.

### When the caller cannot see chat

Every live call follows the same conversation rule. Speak the useful answer,
the decision needed or the reason work stopped. For a long result, give the
short answer and make the rest available by voice on request. If the caller
asks to put a list in chat, post it once and give a brief spoken receipt.
If they only want a written answer, honor that request. These choices apply
to that request. They do not switch the rest of the call to text.

For a quick record directory, the current verified browser App, Track or saved View is the implicit scope when no spoken location has been explicitly selected. A denied, invalid or changed resource never widens silently to the workspace. Explicitly different or workspace-wide questions follow the resident path. The browser is the only live call adapter today. Use the capabilities of the
authenticated adapter for individual actions. Opening an App on screen
requires a bound browser and its render acknowledgment. Selecting an App
as the subject of the next question requires authorized resource resolution.
If a future audio connector cannot render a page, the caller can still
select the App and ask about its records. An unavailable written destination
or confirmation method must have an accurate spoken explanation.

The same conversation holds the task, its receipts and the caller's place.
Typing, uploading or clicking a card may change that state. React to the
meaningful outcome at a pause. Avoid echoing the user's input, narrating
interface events, or resubmitting completed work. Interrupting speech stops
playback. Ending the call ends spoken delivery. Neither cancels admitted work. A temporary microphone mute, suspended audio context or stalled
capture in the browser shows an interruption notice and fences uncommitted
input. The same call resumes capture when the audio route returns; a deliberate
microphone mute remains effective. A permanently ended track requires a new call.
The connection retains its ten-minute safety limit and reports that limit when
reached. Completion of one resident task is not a signal to terminate a call.
A transport that advertises end-call support exposes `end_current_voice_call`
for an explicit current-call instruction (“Please hang up now”). Its protected
control is bound to the current authenticated socket and a completed response
to the latest caller speech, with a conservative explicit-clause check. The
realtime model interprets the utterance and supplies the literal ending clause
(in English translation if needed); an ambiguous goodbye, conditional request,
negation or work cancellation is not a hang-up. An explicit end during captured
review speech leaves the proposal undecided. The relay stops forwarding microphone input,
may speak one short tool-free farewell, signals the browser to drain that
farewell and closes with a bounded fallback even if synthesis or playback fails.
The browser releases its media tracks, worklet, audio context and socket through
normal call cleanup. This control remains available after the workspace tool
budget is exhausted. It never cancels admitted work, changes a review decision,
or removes chat receipts. Automatic inactivity prompting and disconnection
remain a separate requirement.

An explicitly written lookup carries its delivery choice on that request
through queue promotion. On successful completion the call speaks a receipt.
For an ordinary spoken answer, read linked resources by their display names.
Their route targets remain in the saved message. When the caller requests
the wording of a saved reply, read a bounded passage aloud. “Continue” and
“repeat” refer to the same reply in this call. Recheck the conversation and
the saved content before releasing another passage.

## Realtime routing and interruption

A question about an existing record or the contents of a list goes to the resident,
even when nothing has run in the call yet. A question about the progress of a
request already sent to the resident reads that conversation’s execution status.

During an active workspace lookup, an independent spoken question can enter the
durable FIFO behind that exact work item. The relay reads the queue receipt
aloud. The worker delivers the answer separately. If the original work finishes
during admission, the relay can submit the additional question once after
verifying the preceding successful result and idle thread. A correction in that
state requires a fresh instruction. A pending approval keeps its own controls.

For a numbered choice from the current spoken directory, the relay verifies the
selection and supplies the existing navigation tool with the canonical destination
IDs. Legacy name-only requests may use a unique exact directory match. For named
navigation with the complete caller request, resource kind, leaf title and parent
qualifications are retained; the resident resolves the actual destination, not its
containing App, and seeks clarification for an ambiguous or unavailable match.
The resident can request the route directly.
Resource labels stay in the data portion of the prompt. The browser confirms a
successful render before voice announces arrival.

Voice accepts a bounded browser page context over the authorized call and
passes it through native context verification. A successful live delegated
turn may deliver the existing navigation event to the originating browser
after rechecking the current resource and scope. The browser checks its
current route, visibility, composer text, attachment draft and queued
follow-ups before navigating. Navigation is limited to the local paths
covered by the existing native tool. Recovered events do not navigate.
The originating browser echoes a one-use request identity after its loaded
page context matches the exact route and focused resource. A page revision
correlates origin without retaining unsafe URL queries. The browser publishes
its changed route before acknowledging a render and does not resend identical
route snapshots. A reordered acknowledgment waits for the exact next context
transition; unrelated route changes or interrupted requests cannot confirm it.
Immediate and durable result observers share a single navigation delivery.
A verified arrival contributes a quiet navigation receipt to the voice model's
conversation so a subsequent short named destination goes to the resident
with the current browser context; the voice model does not preemptively ask
for a parent or resource kind. A preserved page or
an arrival timeout produces a distinct result. Rechecking browser feedback
does not confer resource authority.

The call observes exact staged review provenance and terminal execution
receipts independently of playback. Newly resolved observed proposals can
announce application, rejection or expiry at a quiet pause. The caller's
approval card display message is not proof of an effect. A successful
completed worker may clear its lease run ID; recover the deterministic run
for that exact attempt when checking provenance.
Terminal receipts also retain private thread/run and intent/request hashes
so an immediate card decision can be found after its live row disappears.
They contain no approval token or command contents. Application errors and
later success are distinct announcements. After its initial snapshot, a call pages
retained decisions by resolution timestamp and receipt ID. It rechecks the exact
originating work even when that work is older than the recent-turn window.
Completed readouts free capacity for later updates. If all readout slots are
pending, the receipt cursor waits for capacity. Qualification and limits are
tracked in the [voice parity ADR](adr/019-native-chat-live-voice-parity.md#acceptance-and-evidence).

The selected voice model receives microphone audio directly through Integral’s
authenticated relay and returns speech on the same connection. Ordinary
conversation needs one realtime response. The workspace assistant can discover
a reviewed quick read for a simple lookup. A published profile sends workspace
lookups through its bound durable harness. Both can check their current chat.
The model receives only the tool descriptions supported for its call. The call
never changes its bound workspace or profile. Changing either requires a new
call.

A combined lookup and action, a filtered question, or a count goes to the
resident as one goal with the caller's App, date, ownership and requested
effect intact. A directory or title match is a complete answer only to a
browsing or title-lookup request. The call identity operation reads the
workspace's admitted display name and the current agent name. Neither label
grants access.

If the caller asks for a directory in writing, the workspace assistant can post
a numbered list in the bound private chat. “Put that list in the chat” uses the
page supplied to audio playback. Integral checks its expiry, display order and
resource access again in the note transaction. A changed or unavailable page
requires a fresh request. A caller can also name a directory to post directly.
Only the write result goes back to the model for speech. The same note admission
and active-turn protection apply to both forms.

On explicit request, the call can append one bounded plain-text assistant
note to its own authorized conversation. It uses the ordinary message
projection and invalidation event. A repeated call identifier cannot append a
duplicate or substitute different text. Appending changes the thread revision,
so the relay declines during an active or parked turn. The existing task
continues to publish its normal status. A note is neither a task nor a write
approval. A waiting-for-review notification and the eventual terminal receipt
are delivered independently and checked against the current authorization.

For the workspace assistant, numbered App, Track and saved-view lists and record search
use the existing scoped read services. The relay retains a bounded, expiring
list with opaque identifiers and server-held page cursors. For speech it sends
only the ordered display names, collection and applicable paging or workspace
facts. The model phrases the reply in the existing audio response. An explicit
repeat reads the authorized page again even though its names are absent from
the model’s ordinary conversation history. Selecting a number
re-reads the exact page and revalidates the chosen resource; a changed list
must be presented again. A later workspace request receives the selected
resource in its trusted host context, after another current resource check.
No identifier or cursor is supplied by the speech model. Before playing a
private numbered list or selected-location readout, the relay revalidates
that projection. Published profiles continue to use only their declared,
authorized operations. The direct workspace navigation tools are not offered
to them.

A current-review read can speak the exact supported fields of one pending
record edit tied to the accepted voice work and run, if the target is still
readable and its recorded revision is current. This covers title, status and schema-declared bounded literal text, select, number, boolean and date edits. Conversational review also supports a bounded literal record description replacement, including plain paragraphs and flat bullet lists; this does not expand Auto or Custom list permissions. The same field projection qualifies an eligible standing private-list grant; both typed and spoken turns use it. Composite, reference, file, computed and other unsupported fields retain review. Voice projects the current record and the actual
staged payload. The same projection must still match when playback begins.
Simple creation also uses the existing proposal form and canonical field
validator to present the Track, type, title and effective scalar values.
The writer checks the same schema and values inside its effect transaction.
A Markdown field can be empty or contain a short, single line of literal text. Its full value is included in the independent spoken review. A description of up to 800 characters may contain plain paragraphs or a flat bullet list; the complete readable content is spoken, and the original body remains in the exact approval fingerprint and writer fence. Rich or linked content, hooks, relation/file/computed fields, descriptions over the bound and tags retain their existing review gate.
A single proposal containing one supported record creation and one one-time
reminder in this chat can use spoken review. Both operations must be literal
and independently reviewable. Approval binds the ordered pair; the record and
the reminder have separate execution receipts. Other compound effects retain
their existing review.  Published-profile review remains a
separate qualification item.

A conversational call can confirm the exact eligible pending proposal in the same chat regardless of which App or Track is on screen. The call, work origin, current proposal and authority—not browser focus—bind the decision. After reconnect, the caller can return to one eligible pending proposal in the same chat. The server checks its work attempt and proposal identity again. At a speech turn boundary the relay offers steering only while its foreground work is known to be queued or running. A reply to a finished clarification can enter the same chat as the next turn.

The configured realtime model renders the exact eligible proposal and a short
question in the call’s voice in a separate tool-free session. Core checks the
returned caption before releasing the audio. After successful playback the
relay captures one bounded microphone response. The configured batch Speech
to Text adapter transcribes it independently of the conversational model.
A clear affirmative approves the proposed change and a clear negative rejects
it. The single-use window belongs to the call and exact proposal. Before
submitting the decision, Core rechecks the caller, current proposal and expiry.
A reply with new information cannot approve the original proposal. For these
replies only, the configured call model interprets the bounded transcript,
reviewed question, field labels and account-local date in an isolated text
request. A request for details repeats the verified question. An explicit
supported edit uses the existing revision service and starts a fresh spoken
review. A pause or unresolved condition leaves the change pending. Silence,
interruption, mute and a changed proposal invalidate the decision window. The recording is ephemeral in Core. The
transcription provider receives the bounded response under its service policy.
A recording played into an open microphone can sound like assent. This
mechanism verifies an answer in the authenticated call, not speaker identity.
Destructive and security-sensitive effects retain their existing review gate.
A spoken correction can change a title or typed date. During record creation it can also replace a short literal text or Markdown note. Select a field by its unique
Operational Model field label or key. Creation is scoped to its destination
Track and an edit to its record. On reconnect, selecting the same target can
recover a single pending replacement. Correction uses the existing Entry
proposal revision service and its optimistic form stamp. The old proposal and
linked WorkApproval are retired. The replacement does not inherit capability
proof or any prior approval. A reconnected caller can reselect the record and
recover the canonical revised proposal in the same conversation. Once a
canonical effect is consumed, a fresh scoped read of the saved fields provides
the spoken outcome. A merely recorded approval remains distinct from
an applied edit.

The caller can explicitly request cancellation of the last task accepted in
the call. This uses the existing chat cancellation and audit service, against
the exact owned WorkItem. A running task may still be stopping. Parked human
reviews stay in the approval path, and stopping work does not undo effects.
A call reaching its tool budget announces the limit and disables further tool
requests for that call; already accepted work retains its ordinary lifecycle.

On a spoken interruption, the browser stops queued audio. For an ordinary
assistant reply, it reports the unplayed 24 kHz samples using a call-local
handle. The relay maps that handle back to the provider item and trims its
conversation history to the browser’s playback position, including when the
provider finished generating before playback caught up. Older clients still
stop playback locally. Private readouts and greetings are outside the
provider conversation. Interrupting either leaves the durable task and its
receipt intact.

Pydantic AI’s realtime session supports reporting played audio on interrupt.
Integral uses the provider WebSocket adapter here because the relay also needs
per-response private input, tool selection, and correlation metadata to
authorize an out-of-band readout before sending audio. Workspace work continues
through the resident harness. See the [Pydantic AI realtime codec](https://pydantic.dev/docs/ai/api/realtime/codec/)
and the [OpenAI Realtime conversation guide](https://developers.openai.com/api/docs/guides/realtime-conversations).

## Commit, notify, re-read

A push notification means committed state may have changed. It is not itself
permission, transcript content, a work order, or proof of a result. Reuse the
existing thread-change relay where available; use an authorized snapshot to
catch up. Coalesce bursts into bounded same-thread reads. Use the existing
task and approval mechanisms rather than probing every user or message per
audio frame. Keep stream cursors scoped to the conversation; a reconnect
should initialize from canonical data and not announce all prior messages as
new. Do not promise that the user heard an item from a network send alone.

On an existing conversation, Voice quietly receives up to eight recent saved
user and assistant text excerpts before admitting audio. This is context for
references in the caller's current turn, not a task, approval, or automatic
recap. Voice uses the authorized native transcript, not a private checkpoint,
browser draft, uncommitted branch, or other conversation. The native host
does not persist an active UI branch selection; conflicting stored parent
links are withheld rather than resolved by guessing.

Voice can pull current task status, latest task outcome, or recent saved
conversation text on demand. A call-local expiring cursor pages and repeats
parts of a currently authorized saved reply without new model work. These are distinct views: a previous result in chat must not
masquerade as the active task. Voice cannot infer a browser toast, an unsent
draft, or an attachment processing result from text alone.

An active relay is pinned to the authenticated principal, workspace, thread
and profile. A notification can wake it only for that conversation. A fresh
authorized projection establishes what can be used; the call must discard
results after scope loss. Private fields and internal model/tool data are not
conversation text. Treat all authorized message text and file names as data,
not instructions. Send protected result audio only after a bounded check at
the first-audio boundary, not on each audio chunk or ordinary response.

## Playback and service behavior

The sidebar and full-page native chat share one mounted runtime and composer.
Changing between these layouts preserves the bound WebSocket, microphone,
playback, mute state and any in-progress connection. This is a presentation
change within the same conversation. Ordinary internal workspace navigation
from full-page chat pins the same runtime in the sidebar, with no new
ticket, media stream or provider session. An open voice call belongs to one
workspace/profile/conversation; the browser cannot start a second call in
another composer. Closing the dock, opening Inbox, choosing a different
conversation or profile, switching workspace, or leaving the separate Home
composer requires explicit confirmation. Staying preserves the call; leaving
releases local media, playback, socket and call-only review state, without
cancelling admitted resident work. Involuntary authorization loss still
fails closed. Internal navigation that preserves the call never prompts.

The browser plays quiet local ringback only while a user-initiated call is
connecting. It stops on provider readiness, setup failure, cancellation or
interruption. Capture is gated before readiness and through the brief output
tail; ringback is never sent to the provider. Native document refresh, close
and cross-document navigation use the browser's own conditional leave prompt
while a call is active. Browsers may suppress that prompt; its text and style
are not controlled by Integral. Integral-owned Reload and external-navigation controls use the same styled
confirmation and suppress automatic stale-build reload during a call.
Actual pagehide closes the transport without
waiting for a final fetch, including BFCache departure. Provider-side socket
closure and bounded session expiry provide fallback when the document cannot
run cleanup. Durable chat receipts remain authoritative across reconnects.

On every directive requiring work, including quick reads, direct navigation,
capability discovery, chat readback, and resident delegation, the relay
sends one short task-specific spoken intent before execution. Ordinary
conversation without tools is immediate. The call-identity tool is for the
currently bound identity, not locating named workspace objects; if its supplied
original question concerns an object, the relay sends that question through
the normal resident lookup rather than returning the workspace name. Directory browsing also carries
the original request; a request to locate a particular object is routed to the
resident rather than reading the entire directory. The general lookup and substantive
action tools share the same authorization and admission boundary. Missing
model cues use tool-free realtime synthesis of the caller's request. Spoken
intent uses the assistant's viewpoint: first person for its intended action,
second person for the caller's belongings or tasks, and the actual ownership
for shared or third-party things. The same guidance applies to supplied cues
and fallback synthesis. A cue is a semantic guide, not a script; preserve
literal names and quotations and keep the resident's caller-authored request
unchanged. Direct
tools use concise grounded cues. A provider response with several tool
calls shares one combined cue. Arbitrary pre-tool speech does not establish
a task recap; only verified matching spoken intent avoids replay. Once a tool is known,
ordinary speculative audio is suppressed; queued ordinary audio is discarded
before the direct-tool cue. Audio already played cannot be recalled. The
gate is released after the authorized cue audio is sent and synthesis
completes. If no cue audio is delivered, the operation is not silently
admitted; the caller can retry. Barge-in after the cue has begun preserves
already accepted durable work and fences stale direct reads. A verbal
review decision retains its independent confirmation, and an explicit
hang-up uses its bounded farewell and teardown.
A short explicit
instruction to implement the current chat plan is delegated as a new
request referencing the shared thread; it is neither a chat readback nor
authorization to bypass a staged effect. When an explicitly referenced, verified
plan is followed by a durably admitted build request, suppress only that
plan’s pending spoken replay; its saved chat remains available. When a verified result arrives
during ordinary conversation, stop stale playback immediately. Do not
cancel a still-forming caller response that may contain a tool decision:
wait for its bounded completion, dispatch a correlated completed tool once,
then deliver the verified result. A stalled provider is reported as a
retryable conversational failure without inventing task completion.
An explicit authenticated hang-up has a short bounded provider tool-output
acknowledgment and a browser teardown fallback after the relay confirms the
ending, so missing farewell or final control frames cannot strand a call.

For browser calls, set the API's `FRONTEND_ORIGIN` to the exact staff web
origin, including scheme, hostname and port. For example, a local preview
opened at `http://127.0.0.1:19218` must use that origin. `integral web`
forwards the browser's Origin on the WebSocket upgrade. The microphone
processor loads as a same-origin JavaScript asset under the normal script policy.

A completed delegated answer is also retained as a bounded, quiet historical
context update for the voice model so an explicit request to continue a plan
can refer to the same conversation. The next request is admitted as new work
under normal review rules. If the earlier observer is still active, the relay
rechecks its exact terminal receipt before releasing the observer.

Conversation and durable work continue independently. Readouts wait for a
natural pause; policy and delivery bookkeeping are server-owned. Ordinary
speech and barge-in do not cancel durable work. Deduplicate delivery by a
durable source identity within a call, and distinguish prepared audio from
audio actually passed to the client. If an acknowledgment or audio is missing,
use a bounded retry or leave the canonical text available, never re-execute
the task to get a second notification. Diagnostic counters must not contain
conversation text, user content, filenames, tokens, or provider payloads.

## Qualification before claiming local readiness

| Scenario | Required evidence |
| --- | --- |
| Typed while voice is active | One existing typed admission, same thread, no second Heavy call, then speech at a pause. |
| Attachment in chat | Submitted, accessible file awareness by safe metadata; no pre-submit, denied, hidden, raw or unconfirmed readiness disclosure. |
| Voice delegates while caller chats | Background work remains active; completion stays in chat and is offered to the call once. Verify empty and missing provider replies as well. |
| Queue, steer and stop | Separate work remains queued, a targeted correction stays on the active work, and only explicit authorized stop follows existing cancellation. |
| Review item | Read pending and terminal outcomes from canonical same-thread authority; no inferred approval or unauthorized action. |
| Existing conversation and reconnect | Prior saved dialogue is usable for the next spoken follow-up without a recap or replay, and earlier exchanges are recallable. No duplicate submission, sibling/cross-conversation data, or disclosure after revocation. |
| Latency | No new per-frame or always-on message polling; count protected-boundary checks and measure actual device/provider timings separately. |

Automated provider-frame and disposable data tests validate the integration
contracts. A real signed-in browser/microphone test is a separate gate for
provider routing, perceived latency, audio playback, and concurrent UX.


Explicit historical caller recall may use the source-linked working-memory
index without a new resident turn. The spoken-intent gate remains mandatory.
Only a verified, dated caller quotation is eligible for the direct voice
path; stale, missing or competing sources delegate the original question to
the resident. Current workspace facts always use current authorized reads.
See [ADR 020](adr/020-source-linked-working-memory.md).

Spoken intent and result responses address the authenticated person directly as you or your. An available display name is quoted context for occasional direct address, never a third-person possessive or authority. The message passed to the resident retains the person’s original perspective.
