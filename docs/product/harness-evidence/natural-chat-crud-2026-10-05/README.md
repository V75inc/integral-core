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

## Natural refusal of a staged create (2026-10-06)

On the same branch runtime and smoke workspace, requested in ordinary language:
“Please add a low-profile magnetic tool tray to the Equipment register, serial
SMOKE-REFUSE-20261006, new, stored in the north cupboard.” The harness staged
one create for approval and stated that it had not yet been recorded. Replied
in the normal chat composer: “No, don't add it. Leave the register unchanged.”
The pending approval was withdrawn. The assistant confirmed the record was not
added (GLM cloud, 16.1k tokens / 3.5s).

Verified with a fresh ordinary chat lookup for the unique serial. The agent
used an exact serial filter on Equipment and a search across readable tracks;
both returned zero matches. The readback completed in 6.3 seconds and the UI
showed `glm-5.3:cloud`, 33.8k tokens, and one additional provider call. This
qualifies one natural-language refusal and post-refusal readback in one
workspace on Ollama cloud; it does not establish refusal reliability across
other action types, providers, tenants, or principals.

## Primary-model staged-write decision tool (2026-10-06)

Removed the native chat endpoint's regex/word-list approval interception.
Integral Native now receives an `integral_resolve_pending_write` tool only when
Core supplies pending staged-write references for that conversation. The model
chooses whether to call it; Core maps the opaque reference to its server-held
token, re-reads the pending item in the bound user/session/workspace, and uses
the shared staged-write executor. The raw staging token is not included in the
tool arguments. Rejected actions use the same scoped lookup and Core state
transition.

In the branch browser runtime at `127.0.0.1:9012`, used ordinary wording with
the existing Equipment app: “Add a cordless drill to the equipment register.
Its serial number is QA-DRILL-1010, description cordless drill, condition Good,
and it is on the workshop shelf.” Integral staged the create. While the Approval
card was visible, the normal composer accepted: “That looks right, please add
it.” The trace showed `integral_resolve_pending_write` with only
`decision=approve` and an opaque `item_reference`; Core returned
`state=consumed`. A second tool call queried the saved entry, and Integral
reported the serial, condition, and location from that readback. The model
readout showed `glm-5.3:cloud`, 51.5k tokens, 5.9 seconds, and two calls.

Focused approval, broker, design, and staging-continuity tests passed (56
tests); `compileall` and `git diff --check` passed. This browser run demonstrates
a single natural approval and readback on Ollama. It does not qualify rejection
with the new model-selected tool, ambiguous requests, another provider, or
multi-principal tenant isolation. The staged Approval card remains visible,
although natural-language chat approval works alongside it.

## Primary-model staged-write rejection (2026-10-06)

Followed the approval smoke with a rejection through the same current
model-selected tool path (not the earlier legacy parser path). In the
`Integral Harness Isolation QA` workspace on the branch browser runtime at
`127.0.0.1:9012`, asked without naming a skill or tool: “Add a small adjustable
wrench to the equipment register, serial QA-WRENCH-1011, in good condition on
the workshop shelf.” The agent staged one create for the existing Equipment
track. Replied in the ordinary chat composer: “Actually, don't add it. Leave
the register as it is.”

The expanded trace showed `integral_resolve_pending_write` called with
`decision=reject` and opaque `item_reference=75bf82d50b0554e2`; Core returned
`ok=true`, `decision=reject`, `state=revoked`. A separate ordinary lookup for
`QA-WRENCH-1011` called `integral_query_entries` scoped to the Equipment track
and returned `entries=[]`, `total=0`. This verifies that this specific
model-selected rejection revoked the pending write and that no matching entry
was persisted. Both turns displayed `glm-5.3:cloud`, token counts, elapsed time,
and tool-call counts; no dollar cost was displayed for this unpriced model
route.

This closes the earlier evidence gap for one natural rejection on the new tool
path. It remains a single workspace/principal and Ollama route; it does not
qualify OpenAI, distinct-principal tenant isolation, speech approval, ambiguous
requests, or the broader V1 acceptance criteria.

## Fresh-tab continuation readback (2026-10-06)

After the scoped-store and request-preparation tests, opened a fresh browser tab
against the same branch runtime and authenticated smoke principal. Integral
restored the existing conversation, including the prior staged wrench request
and its rejection. Asked in ordinary language: “Can you check whether
QA-WRENCH-1011 is listed in the equipment register?” The visible trace showed
`integral_query_entries` called with that serial and the existing Equipment
track; its successful receipt returned `entries=[]`, `total=0`. The response
identified the rejected request and did not claim the entry had been created.
The readout showed `glm-5.3:cloud`, 40.2k tokens, 9.3 seconds, and one tool
call; no dollar cost was displayed for this unpriced route.

This is fresh-tab transcript continuity plus a new persisted readback for one
principal and workspace. It is not a process-restart recovery test or a
distinct-principal isolation test.

## Plain-language workspace and track lookup (2026-10-06)

On the active browser chat, asked: “What workspace is this, and are there
any tracks?” No skill or tool names were supplied. `glm-5.3:cloud` returned the
workspace name, identified it as personal, and named its one Equipment track
and parent Equipment Register app. The expanded UI trace shows a single
`integral_get_scope` tool call, completed in 15.2 seconds. There was no
repeated-call loop, and the answer matched the workspace navigation state.
The message readout reported 42.9k tokens across two provider requests and no
dollar cost. The tool trace had one Integral tool step. This qualifies direct
scope selection for this plain-language lookup on one principal/workspace; it
does not by itself qualify a broader track search or another provider.

Repeated the exact request from a confirmed blank conversation. The harness
loaded `integral-navigation`, then called `integral_get_scope` and
`integral_list_tracks`, returning the same workspace and its Equipment track.
The readout showed 12.9k tokens over two provider requests (10,835 input and
2,031 output), 21.3 seconds total (20.4 seconds to first token), and unavailable
provider cost. This fresh-chat result confirms the basic lookup path but
exposes high latency and material prompt overhead even without prior transcript
history. The earlier 42.9k turn is therefore not explained only by its long
history. No repeated Integral tool call occurred in either lookup.

## Harness labels (2026-10-06)

The same branch UI’s Settings → Agent view presents the inactive embedded
harness as **jvagent** and the active native harness as **Integral AI**. The
native provider keeps `integral_native` as its internal routing ID; the UI's
technical detail associates that ID with the Integral AI label. This confirms
the requested distinction without changing persisted provider identifiers.

## Branch validation (2026-10-06)

Against branch head `603e368c`, `make verify` completed successfully: substrate
guards, pre-commit format/lint/type checks, reproducible wheel build and import,
CI-faithful backend smoke, all 1,308 frontend tests across 221 files, and the
full backend suite. PostgreSQL-only integration cases skipped because
`INTEGRAL_TEST_DB=postgres` was not enabled; pgvector/Atlas integration cases
also skipped where their backing services were unavailable. The run reported
Pydantic Settings forward-reference and existing deprecation/skill-frontmatter
warnings, but no failures. The browser smoke above was run on the same branch
runtime before this gate.
