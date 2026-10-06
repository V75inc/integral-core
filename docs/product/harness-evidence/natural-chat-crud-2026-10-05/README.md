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
