# Native chat approval and CRUD browser qualification

**Date:** 2026-10-05
**Runtime:** branch `feat/pydantic-ai-harness-v1`; Vite on `127.0.0.1:9012`,
Integral API on `127.0.0.1:4011`; `integral_native` with LiteLLM route
`ollama_chat/glm-5.3:cloud`.
**User profile:** signed-in `Harness Smoke Test` workspace.
**Application/track:** existing `Equipment Register` / `Equipment` app and
track, created during the earlier lay-user scaffold qualification.

## Browser workflow

Used ordinary chat requests without naming skills or tools:

1. “Add a spare 10m extension cord to the equipment register. We just bought
   it; serial number SMOKE-EXT-2026; condition new; store it in the workshop.”
2. The agent selected the existing Equipment track, proposed one entry, and
   displayed the fields and a single approval card. The composer remained
   available while the card was open.
3. “Yes, please go ahead.” was entered in the normal chat composer. The exact
   staged create was consumed by the shared executor; the card closed without
   a second Prompt Sheet continuation. The assistant read back the saved entry,
   including serial number, condition, location, and unassigned status.
4. “Actually, move that spare extension cord to the north storage room.”
   staged one location update. “Yes.” applied it, and the assistant read back
   the new value. It also spotted the stale location in the description and
   offered a concise correction; after “Yes, update the description too,” it
   staged that change, accepted a second “Yes,” and confirmed the description
   was consistent.
5. “Find the spare extension cord with serial number SMOKE-EXT-2026 and tell
   me where it is stored.” returned the saved entry and “North storage room.”
6. “Delete that spare extension cord entry; we don't need this test item
   anymore.” staged a soft delete, clearly described as reversible by an
   administrator. “Yes.” applied it.
7. A final ordinary search for serial `SMOKE-EXT-2026` returned zero results.

The created test entry is `n.Entry.bcc9f457bc53414580557851` in conversation
`n.ChatThread.50898781103b493992307d85`. Its workspace was the dedicated smoke
workspace. The final visible browser state is captured in
[`post-delete-search.png`](post-delete-search.png).

## Findings and remediation

The first browser run exposed an undefined `native_turn` variable in
`_start_user_turn`; after correcting that, it exposed a second undefined
`attachment_ids` reference. Both requests failed before an entry was created.
The function now derives native mode from its owned thread and checks its
resolved `attachment_file_parts` argument. A third attempt used a bare model
name as an environment override and was rejected by the expected
`provider/model` route validation; that was test configuration and was corrected
to `ollama/glm-5.3:cloud`.

The full CRUD cycle then passed through the browser. The earlier routing
failures are regression evidence: lower-level approval-service tests did not
exercise all of `_start_user_turn`'s request wiring, so browser qualification
remains necessary for this path.

## Chat attachment filing and undo follow-up

On the same branch runtime, uploaded a generated receipt containing the exact
serial `DR-001`, then asked in ordinary language, “Please file this where it
belongs.” The native model selected the existing Cordless Drill entry in the
Equipment Register, explained the serial match and sibling receipt, and asked
for one approval. The user approved with “Yes, attach the receipt.” in chat;
the assistant then confirmed the file on that entry. The first attempt was
served by a backend process that had not loaded the in-progress receipt code,
so it is not evidence for the current implementation. The second attempt ran
against the reloaded branch backend.

The current implementation initially disabled Undo because the staged token
held both the `attachment.attach` effect receipt and a `policy.deny` audit
record. Rollback assessment treated the audit-only denial as an unsupported
mutation. It now excludes `policy.deny` from effect inversion assessment while
retaining that audit record. After reloading the browser, Undo became available
for the attachment action. The normal Undo flow detached the receipt, restored
its chat ownership, and the entry's attachment list returned from three items
to two: the previous synthetic receipt and the pre-existing sample PDF. The
chat still displayed the uploaded receipt, verifying that rollback preserved
the source upload. After a full page reload, the transcript still displayed
the `Undone:` receipt, confirming the rollback notice is persisted and
reconciled into the browser UI.

The model readouts showed `glm-5.3:cloud`, 75.4k tokens / 23.1s for the filing
turn and 39.3k tokens / 4.5s for the approval follow-up. No dollar cost was
shown for this unpriced route. Focused backend tests for attachment filing,
rollback assessment, and policy evaluation passed after the change. This
qualifies one receipt filing, ordinary chat approval, persisted readback, and
recovery on Ollama; it does not qualify broader CRUD, tenant isolation, OpenAI,
or release acceptance.

## Limits of this qualification

- This is one authenticated workspace and one Ollama cloud model route. It does
  not establish tenant-isolation across two live browser principals or qualify
  OpenAI in this round.
- The soft delete removed the item from ordinary register/search results. Admin
  restore remains the stated recovery path; the chat Undo control reported that
  no effect receipt was available for this action.
- The turn readout showed model, token count, elapsed time, and tool-call count.
  This screenshot does not prove token-cost data was displayed or persisted.
- This smoke does not establish release readiness or complete the V1 goal.

## Follow-up: natural approval and chronological undo notice (2026-10-06)

In the branch browser runtime (`127.0.0.1:9012`, dedicated Harness Smoke Test
workspace), asked without naming tools or skills: “Add a compact bit holder to
the equipment register, serial SMOKE-UNDO-20261006, new, kept in the north
cupboard.” The agent staged one Equipment entry. The normal chat composer
accepted “Yes, add it.” while the approval card was open; one create was
executed and the assistant read back the saved fields. The turn readouts showed
`glm-5.3:cloud`, 56.9k tokens / 10.5s for staging and 31.7k tokens / 4.5s for
approval.

Used the product’s normal Undo action on this explicitly disposable smoke
record. The prior inline `Undone:` receipt was attached to the original staged
message, before the later approval and “Done” response, making the transcript
chronologically misleading. Undo now appends a persisted assistant note at the
end: “Undo complete: … was reversed. The earlier completion and readback
messages describe the state before this undo.” Reloaded the conversation and
verified the note remained at the end. The synthetic entry was reversed. This
smoke establishes typed approval and readable recovery ordering for this
single Ollama/workspace flow; it does not establish cross-tenant isolation,
OpenAI, pricing data, or V1 completion.

## Same-principal workspace conversation scope (2026-10-06)

In the same authenticated browser session, switched from the `Harness Smoke
Test` Personal workspace to the `Harness Smoke Workspace` organization
workspace. The Agent conversation list was empty in the organization workspace;
returning to the Personal workspace restored its prior conversations, including
the smoke conversation. No message was sent and no data was changed during this
check. This is browser evidence that the conversation list follows the active
workspace for one principal. It does not prove isolation between distinct
principals or cover direct API access to another workspace's thread.
