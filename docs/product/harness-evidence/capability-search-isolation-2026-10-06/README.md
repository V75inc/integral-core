# Capability search and tenant-scope smoke — 2026-10-06

**Branch:** `feat/pydantic-ai-harness-v1`
**Runtime:** branch Vite UI at `127.0.0.1:9012`, branch API at
`127.0.0.1:4011` (`/health` returned HTTP 200 with database connected).
**Principal:** separate synthetic `Isolation QA User B` session.
**Harness:** `integral_native` displayed as **Integral AI**; model route
`ollama_chat/glm-5.3:cloud`.

This is a real browser smoke on the in-progress capability-search change. It is
one isolated read flow, not V1 acceptance or release qualification.

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

These runs show improved behavior for this exact-identifier read. The earlier
`integral_query` `internal_error` remains unresolved for semantic/conceptual
queries; this change makes no claim to fix that route. Existing-workspace hits,
cross-workspace lookup, and semantic-retrieval recovery remain separate
qualification cases.

The final source revision passed `make verify`: substrate guards,
pre-commit/lint/type checks, reproducible wheel/import validation,
CI-faithful backend smoke, 1,308 frontend tests, and the full backend suite.
PostgreSQL, Atlas, and seeded-package integrations skipped where their required
services or fixtures were unavailable. The normal frontend lint warning
backlog remains non-blocking.
