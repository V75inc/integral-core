# Capability search and tenant-scope smoke — 2026-10-06

**Branch:** `feat/pydantic-ai-harness-v1`
**Runtime:** branch Vite UI at `127.0.0.1:9012`, branch API at
`127.0.0.1:4011` (`/health` returned HTTP 200 with database connected).
**Principal:** separate synthetic `Isolation QA User B` session.
**Harness:** `integral_native` displayed as **Integral AI**; model route
`ollama_chat/glm-5.3:cloud`.

This is a real browser smoke on the in-progress capability-search change. It is
one isolated read flow, not V1 acceptance or release qualification.

## Follow-up: ordinary equipment lookup after skill-boundary revision

On the same isolated User B principal and branch runtime, a fresh chat asked:

> Is anything filed about routine equipment maintenance?

The live trace loaded `integral-insights`, called `integral_query` once, then
used one `integral_query_entries` fallback because the semantic call returned
`degraded: true` with zero candidates examined. Both calls carried succeeded
Core receipts scoped to User B's personal workspace and returned zero matches.
No App-owned data boundary was reported in either result. The final answer
reported the empty Core-readable result without proposing a new register. The
response readout showed `glm-5.3:cloud`, 48.0k tokens, 16.4 seconds, and three
additional calls. This is a degraded-retrieval fallback smoke, not proof that
semantic indexing works or that App-owned records were searched.

The earlier multi-tool answer in this file's history had overclaimed the
workspace search and volunteered to create a tracker. After revising the
insights and scaffold skill boundaries, this rerun removed the unsolicited
setup offer and qualified the result to readable Core entries. The search still
required the one fallback because semantic retrieval is degraded in this
runtime; reducing those two justified reads further would require restoring
semantic retrieval or accepting a weaker answer.

The final changed source passed `make verify` on this checkout. The gate
reported all substrate guards, formatter/type checks, clean wheel import,
CI-faithful backend smoke, 221 frontend files / 1,309 frontend tests, and the
full backend suite passing. PostgreSQL-, Atlas-, and fixture-dependent skips
remain outside this evidence. Log: `/tmp/integral-core-make-verify.log`.

## Lay-user request and result

In a fresh Integral AI chat, the user asked:

> Can you find serial QA-WRENCH-1011 in my workspace?

The visible answer reported no matching record. The expanded live trace showed:

- `integral_get_scope` bound the run to User B's personal workspace.
- `integral_query_entries` searched for `QA-WRENCH-1011`; its successful receipt
  returned `total: 0`, `entries: []`, and the same bound workspace ID.
- `integral_list_apps` and `integral_list_tracks` returned the empty workspace
  state before the model stated that no register existed.
- No record from the other QA principal appeared in the answer.

The initial smoke completed in about 15 seconds with four visible Integral
tool calls. A repeated run against the then-current build took 57 seconds,
reported 30.2k tokens, and used three extra model calls. It called
`search_capabilities`, then chose the workspace-attachments skill/tool for a
serial lookup; the model recovered by using `integral_query_entries` and listing
Apps and Tracks. This revealed a wrong capability recommendation.

The latest real browser rerun completed in 63 seconds, reported 84.5k tokens,
and used four extra model calls. Its visible trace had 11 operations, including
two `load_capability` calls and broad workspace reads. It used
`integral_query_entries` successfully (zero matches, with a successful receipt
scoped to User B's workspace). It also attempted `integral_query`, which
returned an opaque `internal_error`; the model disclosed this, then used the
successful keyword result and empty App/Track lists to answer. This is truthful
recovery, but the latency, token volume, and extra calls are poor and the
semantic retrieval error needs separate diagnosis.

The turn readout showed `glm-5.3:cloud`; raw trace evidence from the first run
reported `costSource: unavailable`, and no dollar cost was shown. Token/time
readout and cost qualification are distinct: these smokes do not prove a
charge amount for the Ollama cloud route.

After the final staged verification, a fresh browser run completed in 33
seconds at 34.8k tokens and four extra model calls. The visible trace had six
operations: two `integral_query_entries` searches (one repeated with a broader
keyword), `integral_list_tracks`, `integral_list_apps`,
`integral_list_workspaces`, and `search_conversation_history`. The record
searches returned zero and the three workspace reads established the empty
state. This run did not call the semantic `integral_query` tool, so it avoided
the earlier internal error. It still spent more calls and tokens than the
simple lookup warrants, including an unnecessary conversation-history read.
Message Debug confirmed `provider_id: integral_native` and
`provider_label: Integral AI`.

## Discovery change covered by source tests

Capability search now includes input-schema descriptions and enum values in
local semantic documents, searches argument contracts as a separate field, and
fuses lexical and semantic *ranks* rather than adding uncalibrated raw scores.
Negative routing clauses in skill summaries are omitted from retrieval text,
while complete skill content remains available when loaded. Static tool vectors
are reused for repeated searches during the same run. When local embeddings are
unavailable, lexical ranking remains available and the response identifies
that fallback. A live run against the revised retriever now recommends
`integral-entries` for a specific record lookup; the primary tool candidate is
`integral_query` for cross-workspace retrieval. Its runtime error remains open.

Focused evidence: `backend/tests/native_harness/wp_06/test_capability_search.py`
passed 13 tests, covering semantic argument-description retrieval, selected
scaffold workflow routing, negative skill-routing text, an imperfect-embedding
serial lookup regression, and honest lexical fallback reporting. Black,
isort, and flake8 passed on the changed backend files. The staged full
`make verify` gate passed, including 1,308 frontend tests and the full backend
suite. PostgreSQL-only, Atlas, and seeded-package integrations were skipped
because their required services or fixtures were unavailable.

## Limits and follow-up

- This empty-workspace read does not verify a hit against a real existing entry,
  create/update/delete behavior, receipt filing, cross-process recovery,
  OpenAI routing, speech approval, or staged-write confirmation handling.
- The extra App/Track reads were needed to support the model's assertion that
  the workspace had no containers. If the desired product answer is only
  “nothing matched,” that broader assertion and its extra reads should be
  avoided unless the model has evidence for it.
- The provider returned no cost value (`costSource: unavailable`). The product
  must preserve and expose unknown cost distinctly; this turn does not satisfy
  priced-route analytics qualification.
- The browser run exposes tool choices and receipts, but not the full ranked
  candidate list. Direct browser verification of every recommended candidate
  remains outstanding.

## Follow-up: exact-identifier lookup — 2026-10-06

This follow-up used the same ordinary request on the same authenticated,
synthetic principal and branch runtime. The request remained:

> Can you find serial QA-WRENCH-1011 in my workspace?

### Failure reproduced

Before the latest adapter guidance, the browser completed in 2m19s with
`glm-5.3:cloud`, 236.3k tokens and 12 steps. It first called
`integral_query`, which returned `internal_error` twice. A successful exact
`integral_query_entries` read returned zero matches. The model then loaded
`integral-scaffold`, called design coverage twice, and persisted a Tool
Register proposal that included the searched serial as a new record. It did
not build an App or create an Entry. This was the wrong workflow for a lookup.

### Corrected retests

After separating identifier search from semantic retrieval, treating a
successful empty lookup as a read-only result, and limiting conversation
history search to missing prior-discussion details, a fresh browser turn
completed in 12 seconds with three exact/variant `integral_query_entries`
calls. It returned no match and did not scaffold, but still issued two
unsupported identifier variants.

The final retest added guidance to rely on a successful zero-result search for
a user-supplied identifier and not invent alternate spellings. A fresh browser
turn completed in 7.9 seconds (`glm-5.3:cloud`, 9.6k tokens, one visible tool
call / one step). `integral_query_entries` searched `QA-WRENCH-1011`, returned
a successful receipt with `total: 0` and `entries: []`, and the assistant
reported no match. It did not create a record, propose an App, make extra
variant searches, or search conversation history. It invited the user to
provide another identifier or detail if needed.

These runs show improved behavior for this exact-identifier read. At this
point, the earlier `integral_query` `internal_error` remained unresolved for
semantic/conceptual queries; a later investigation and fix are recorded below.
Existing-workspace hits and cross-workspace lookup remain separate
qualification cases.

The final source revision passed `make verify`: substrate guards,
pre-commit/lint/type checks, reproducible wheel/import validation,
CI-faithful backend smoke, 1,308 frontend tests, and the full backend suite.
PostgreSQL, Atlas, and seeded-package integrations skipped where their required
services or fixtures were unavailable. The normal frontend lint warning
backlog remains non-blocking.

## Follow-up: pgvector capability fallback — 2026-10-06

The correct branch UI/runtime is `127.0.0.1:9012` → `127.0.0.1:4011`, with
the isolated `integral_v1_branch` PostgreSQL database and Integral AI native
harness. An ordinary browser request asked:

> Can you find anything about keeping company equipment in good working condition?

The native harness selected `integral_query` in `hybrid` mode. Before the fix,
the local PostgreSQL server rejected `CREATE EXTENSION IF NOT EXISTS vector`
with SQLSTATE `0A000` because pgvector was not installed. The adapter leaked
that known deployment capability gap to generic dispatch handling, producing
`internal_error`. The model then made broad keyword and workspace reads to
recover, taking 25.4 seconds and 38.3k tokens across seven steps.

The fix adds a provider-neutral `EmbeddingStoreUnavailable` signal. The
pgvector adapter raises it only for PostgreSQL's specific missing-vector
extension error; permission failures and unrelated database errors still
propagate. The retrieval boundary recognizes the signal, marks semantic
retrieval unavailable for the process, and falls back to the existing
permission-gated graph path. A direct dispatch against the same isolated
database returned `mode: graph`, `requested_mode: hybrid`, `degraded: true`,
and zero results rather than an error.

After the fix, a fresh lay-user browser request asked:

> Do we have anything filed about keeping equipment serviceable?

The live run used `provider_id: integral_native`, `provider_label: Integral AI`,
and `ollama_chat/glm-5.3:cloud`. Debug showed one `integral_query` call with
`isError: false`; its result had `mode: graph`, `requested_mode: hybrid`, and
`degraded: true`. The assistant accurately reported no matching data in the
empty workspace and did not expose an internal error. It took 2m29s (73.4k
tokens; cost unavailable) and six steps. The semantic error is fixed, but this
run also exposed excessive latency/token use and unnecessary capability
discovery/loading; this is not V1 acceptance.

Focused regression coverage passes for adapter translation, preservation of
unrelated PostgreSQL errors, and graph fallback. The isolated branch database
does not have pgvector, so the live pgvector integration suite remains
unavailable; its real failure was reproduced by in-process dispatch against
that same database before the fix.

## GPT-5.4 ordinary-user scaffold smoke — 2026-10-06

To compare the stable GPT control models, the branch runtime was configured
with `openai/gpt-5.4` (platform credentials, isolated browser database) and the
same fresh-conversation prompt previously exercised with GPT-4.1:

> We keep losing track of when the mowers are serviced. Can you help us sort that out?

The UI confirmed `gpt-5.4`; the response readout showed 178.3k tokens, $0.1867,
54.6 seconds, and seven additional model calls. GPT-4.1 on the same prompt had
shown 107.8k tokens, $0.1092, 20.7 seconds, and five additional calls. In this
single run GPT-5.4 took 2.6x the time, 1.7x the reported token spend, and 1.7x
the cost. This is a directional comparison from one run per model, not a
repeatable benchmark.

GPT-5.4 completed the requested workflow through a successful
`integral_propose_design` receipt, targeting the existing Crew Tools app. It
returned a saved design proposal and did not build anything, as expected before
user confirmation. Relative to the GPT-4.1 run, which proposed in prose and
asked for another formal proposal step, GPT-5.4 followed the proposal-record
path and ended with one confirmation request.

The design quality still needs correction: the discovered app already has a
single Tools track whose purpose covers all crew tools, serial number, status,
service due date, and current holder. GPT-5.4 nevertheless proposed a second
Mowers register with overlapping fields, plus a Service Log linked to that new
track and four new views. The minimum extension should preserve the existing
Tools track and add only service-event history linked to each existing tool,
with only the view(s) needed for that history. The model also described the
proposal as “the smallest useful fix” despite the duplicated register and
unrequested service board/calendar. No design was confirmed and no workspace
objects were built. This run confirms better workflow completion than GPT-4.1
in this example, but GPT-5.4 is slower, more expensive, and still over-scopes
the design; it is not yet a V1 quality pass.

### GPT-5.4 retest after fixing entity reuse guidance

The existing `Tools` schema was inspected in the browser and confirmed to
already represent each tool, with serial number, status, current service due
date, holder, and return date. The scaffold skill was tightened to require new
event relations to point to the matching existing entity Track, prohibit a
parallel register or copied source-of-truth fields, and add only the views
needed for the requested workflow. Its focused runtime-alignment regression
was added; the capability map was regenerated and the focused harness/skill
tests passed.

The exact same ordinary-user prompt was run in a fresh GPT-5.4 chat. It again
used seven steps and recorded a successful `integral_propose_design` receipt
against Crew Tools. This time it proposed one Service Log track linked to the
existing `Tool` entry type and one Service history table, with no parallel
Mowers register, board, calendar, or sample data. The proposal remained
unbuilt and asked for one confirmation. The readout showed 173.2k tokens,
$0.1270, and 35.3 seconds. Compared with GPT-5.4's previous design on the same
prompt, time fell from 54.6s to 35.3s and reported cost from $0.1867 to
$0.1270, while token count stayed similarly high (178.3k to 173.2k). This
single rerun shows the targeted skill correction worked for this scenario;
it is not broad model qualification, and latency/token volume remain high.
The final `make verify` invocation exited successfully, including substrate
guards, format/type checks, wheel import validation, CI-faithful backend smoke,
the 221-file/1,309-test frontend suite, and full backend suite. PostgreSQL,
Atlas, and seeded-package cases skipped where their required services or
fixtures were unavailable. The staged-index substrate guards noted that
nothing was staged, so those specific checks were vacuous for the unstaged
working tree; no commit was made.

## GLM 5.3 maintenance register CRUD smoke — 2026-10-06

The browser runtime was switched back to `ollama_chat/glm-5.3:cloud` on the
isolated branch runtime and the existing Maintenance Log in Crew Tools. These
were fresh lay-user requests in the Integral AI chat; no skill or tool names
were supplied.

The request “Log today’s blade sharpening for QA Mower Beta in the maintenance
history” reached the correct track. The streamed tool trace emitted a reused
provider tool-call index warning, which the LiteLLM adapter normalized, and
the agent then read available tracks, entries, and the target track schema.
After six steps (72.2k tokens, 32.1 seconds, five additional calls), it staged
one maintenance record, resolved the mower by its QA-MOWER-002 serial, set the
date to the current date, and left unspecified worker and cost fields blank.
After approval, the agent read the saved row back with its generated entry
link and exact field values (four steps, 101.0k tokens, 36.3 seconds, four
additional calls).

A separate request, “Find the blade sharpening entry for QA Mower Beta,” found
that row and returned its track and entry links, tool relation, date, and
status (19.1k tokens, 7.4 seconds). The edit request changed only `work_done`
to “QA smoke test: blade sharpening”; after approval, the agent confirmed the
updated value by readback, revision 2 (the staged turn used 70.6k tokens,
10.4 seconds, and two additional calls; approval/readback used 99.8k tokens,
27.1 seconds, and three additional calls). Finally, a plain deletion request
staged the correct QA entry as a reversible soft delete without affecting the
mower. After approval, the Maintenance Log showed zero entries and the agent
reported the entry no longer resolved while the source mower remained intact
(staged turn: 81.6k tokens, 13.4 seconds, two additional calls; confirmation:
76.1k tokens, 12.1 seconds, two additional calls).

This is successful browser evidence for create, exact readback, search,
update, verified readback, and delete on a track created through the native
harness. It also surfaced a performance problem: a simple create plus
readback consumed over 170k reported tokens across multiple model calls, and
the initial request performed overlapping broad reads before schema lookup.
The stream-index warning was recovered by the existing compatibility adapter,
but this single observed recovery does not qualify all concurrent or malformed
tool-call shapes. Token and time readouts appeared; the Ollama route continued
to report no monetary cost, so charge analytics remain unqualified. No source
change in this turn altered CRUD semantics; the existing tool-boundary,
provider translation, token budget, and verified-build fixes were exercised
against this browser flow. The QA row was soft-deleted and does not remain in
the register.

### Natural chat approval and cancellation follow-up

In the same isolated browser chat, a second ordinary request staged
“Drive belt replacement — QA Mower Beta.” Replying “Yes, go ahead.” applied
that exact create and returned its linked record and fields after a verified
readback (20 seconds, 138.5k tokens, four additional calls). A later delete
request was staged for that exact row. “No, cancel that.” caused no deletion;
the assistant reported that the row remained, confirmed by the row still being
visible in the track. After the user issued a new delete request, the agent
correctly said the old decision had been cancelled and presented a fresh
pending action. “Yes, delete that test entry.” approved the new action; the
agent verified the entry no longer resolved and reported the source mower was
untouched. The track UI then showed zero entries. Approval/cancel behavior was
correct for these typed natural-language turns, including binding to the
current pending action rather than reusing a prior decision. The assistant
needed five steps to process cancellation (178.3k tokens, 28.6 seconds, five
additional calls); the subsequent fresh delete/approval/readback used four
steps (159.6k tokens, 22.1 seconds, four additional calls). This confirms the
authority boundary but leaves a major efficiency gap. Typed approval is
qualified here; live speech capture was not exercised in this run.
