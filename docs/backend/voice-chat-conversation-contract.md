# Shared conversation contract for text and live voice

This is the contract for a signed-in Integral Native conversation. It does not
expose a visitor route or give a public channel access to a staff conversation. The
[voice-first operation plan](../product/VOICE_FIRST_OPERATION.md) covers the proposed
hands-free review, navigation and authenticated phone experience.

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
- **Attachments:** a submitted, visible attachment can be acknowledged by named
  metadata. The user has not attached anything merely by selecting it in a
  composer. Presence does not mean extraction or analysis succeeded. Use the
  existing scoped file path to inspect content when needed; do not send an
  original, base64 bytes, extracted text, URLs, or credentials as event context.
  Convey readiness only when the authoritative permission-checked state is
  available.
- **Approvals:** approvals and outcomes use canonical workflow state, not the
  speech model's assertion. An acknowledgment can tell the user to review the
  item in chat without claiming approval or execution. Spoken authorization
  must use separate exact confirmation and the existing approval gate.

## Realtime routing and interruption

Voice accepts a bounded browser page context over the authorized call and
passes it through native context verification. A successful live delegated
turn may deliver the existing navigation event to the originating browser
after rechecking the current resource and scope. The browser checks its
current route, visibility, composer text, attachment draft and queued
follow-ups before navigating. Navigation is limited to the local paths
covered by the existing native tool. Recovered events do not navigate.
The originating browser echoes a one-use request identity after its loaded
page context matches the exact route and focused resource. A page revision
correlates origin without retaining unsafe URL queries. A preserved page or
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
tracked in the [voice parity ADR](adr/019-native-chat-live-voice-parity.md#active-completion-goal-and-qualification-2026-11-25).

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
list with opaque identifiers and server-held page cursors. Selecting a number
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
readable and its recorded revision is current. Initially this covers title,
status and typed date edits. Voice projects the current record and the actual
staged payload. The same projection must still match when playback begins.
Simple creation also uses the existing proposal form and canonical field
validator to present the Track, type, title and effective scalar values.
The writer checks the same schema and values inside its effect transaction.
An optional, empty Markdown field permits simple creation. Populated rich text,
hooks, relation/file/computed fields, long body content, tags and compound
effects require their existing card. Published-profile review remains a
separate qualification item.

A browser that supports independent review confirmation can request a decision
on one exact eligible record change. An installed local device
voice reads the exact change and a one-use six-digit challenge. When the
browser has no suitable local voice, the trusted host can generate the exact
WAV offline using macOS speech tools or an installed eSpeak NG/eSpeak binary
on Linux. The browser plays that WAV and confirms playback completion. A
missing or failed local synthesizer leaves the proposal pending. The realtime model never
receives the challenge. The relay drops microphone frames during readback and
captures only a bounded response after playback completion. The configured
batch Speech to Text adapter transcribes that response independently. An
explicit decision (for example, “I approve”) and all six digits, a current matching proposal and live
authorization are required before the canonical staged decision service is
called. Silence, interruption, mute, expiry, a changed proposal or recognition
failure leave the decision unchanged. The challenge and recording are
ephemeral in Core. The configured transcription provider receives only the
bounded confirmation audio under its normal service policy. Destructive and security effects retain their existing card gate.
A spoken correction identifies a proposed title or typed date by its unique
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
change within the same conversation. Closing the chat, switching conversations
or leaving the document still releases its call resources.

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
