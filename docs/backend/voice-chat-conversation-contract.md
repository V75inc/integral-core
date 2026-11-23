# Shared conversation contract for text and live voice

This is the contract for a signed-in Integral Native conversation. It does not
expose a visitor route or give a public channel access to a staff conversation.

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
  must never substitute for an existing approval gate by inference.

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
conversation text on demand. These are distinct views: a previous result in chat must not
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
