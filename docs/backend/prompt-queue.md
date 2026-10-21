# Questions and staged review in conversation

The prompt queue provides a shared review surface for clarifying questions and staged-write decisions. ChatThread stores the durable queue; the interface presents its current item above the composer.

A question can be answered or skipped where supported. A staged write requires its explicit review path. Canceling pending review must revoke the corresponding pending changes rather than merely hiding the card.

While a blocking item is open, dispatch gates prevent later tool actions from silently bypassing it. Reconnect and reload must agree with stored state. Approval transitions still check current permission and execution status.

A completed ordinary write, rejection, cancellation, expiry or unavailable
approval ends with the host's concise review receipt and `resume_required=false`.
The browser persists that receipt without starting a model run to restate it.
Approval success still requires the executor's terminal result; merely blessing
a token, a failed application or partial progress does not close it as applied.
The receipt states the recorded outcome, not an independent fresh readback.
Applied-change receipts include local links from executor resource metadata or
the staged target: individual records for batch results, or the owning collection
or App when no record destination is available. Entry links preserve collection
context where known. Rejections do not show applied-result links. These shortcuts
use ordinary resource routes and do not grant access or trigger another model run.

Continue the agent only when the resolved review unlocks unfinished work:
answered/skipped questions, an approved design that still needs building, or
an approved private profile revision that still needs its separately reviewed
publication. A verified decision made inside an existing native chat turn
belongs to that turn and never launches a second one. Passive polling and
reloads cannot authorize another run.

Test queue ordering, question-only skip, cancel-all revocation, stale decisions, reload, and readback of applied changes. Use the [chat lifecycle](ai-chat.md) and [user guide](../user-guide/README.md#11-review-proposed-changes) for the surrounding experience.
