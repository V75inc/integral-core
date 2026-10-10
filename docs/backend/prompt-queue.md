# Questions and staged review in conversation

The prompt queue provides a shared review surface for clarifying questions and staged-write decisions. ChatThread stores the durable queue; the interface presents its current item above the composer.

A question can be answered or skipped where supported. A staged write requires its explicit review path. Canceling pending review must revoke the corresponding pending changes rather than merely hiding the card.

While a blocking item is open, dispatch gates prevent later tool actions from silently bypassing it. Reconnect and reload must agree with stored state. Approval transitions still check current permission and execution status.

A clear native chat approval or rejection belongs to the current model turn. The queue reconciles its terminal state and retains the review receipt, but returns `resume_required=false` when every resolved item has a verified chat decision in that exact principal, workspace, and conversation. The browser appends the receipt after the stream settles without starting a second model run. Card decisions, questions, mixed reviews, expiry, and unavailable tokens retain ordinary host continuation. This prevents duplicate replies after verbal approval without weakening approval or replay protection.

Test queue ordering, question-only skip, cancel-all revocation, stale decisions, reload, and readback of applied changes. Use the [chat lifecycle](ai-chat.md) and [user guide](../user-guide/README.md#11-review-proposed-changes) for the surrounding experience.
