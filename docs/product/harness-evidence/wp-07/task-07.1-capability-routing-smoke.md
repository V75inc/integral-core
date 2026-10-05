# WP-07.1 evidence — capability search and lay-user routing

**Status:** Pydantic AI skill discovery and review-before-build proposal path
verified in two OpenAI browser turns; cost/latency and Ollama acceptance remain
open.
**Revision:** working tree based on `3d86c716` on `feat/pydantic-ai-harness-v1`.
**Date:** 2026-10-05.

## Pydantic AI composition

The native runtime constructs a fresh Pydantic AI `Agent` for each authorized
Integral turn. It composes the upstream Harness `Skills`, `ToolSearch`,
`Planning`, `Instrumentation`, `StepPersistence`, `ClearToolResults`, and
`ConversationSearch` capabilities. Integral supplies the LiteLLM-backed model,
brokered function tools, encrypted tenant/principal/thread/session-scoped step
store, and permission-filtered standard Agent Skills. Capability loading is the
upstream Pydantic AI `load_capability` surface; tool discovery is
`search_tools`. The separate Integral `search_capabilities` tool searches the
turn's authorized skill and tool catalogue and cannot authorize a tool call.

WP-07 adds semantic skill ranking using Integral's existing local MiniLM
embedding model, fused with lexical skill-description ranking. It makes no
auxiliary provider call and keeps tenant skill text inside the authorized local
runtime. If local embeddings are unavailable, capability search falls back to
the existing lexical ranker. The search result remains advisory; Pydantic AI
loads the selected skill, while the Integral broker remains the authorization
and effect boundary.

The runtime now passes a callable strategy directly to Pydantic AI's upstream
`ToolSearch` capability. It semantically ranks only the deferred definitions
Pydantic AI supplies for this run, with BM25 reciprocal-rank fusion as a
secondary relevance signal. It does not inspect user wording to allow or deny
tools. Tool definitions still come from the authorized Integral catalogue;
every invocation is revalidated by the live broker. If local embeddings fail,
the strategy degrades to lexical ranking, not a broker or policy fallback. The
project now pins `pydantic-ai-harness==0.36.0`; this includes the upstream
serializing-history fix needed by the JvSpatial-backed
`SnapshotHistorySource`. Integral continues to own tenant scope, persistence,
LiteLLM route/BYOK resolution, accounting, and effect authorization.

## Automated evidence

- Focused capability, provider-stream, broker, and WP-07 tests: **35 passed**.
- After lowering the compaction trigger, the focused WP-07 + routing/provider
  subset passed: **22 passed**.
- Black check, isort check, flake8 on changed files, and `git diff --check`:
  passed.
- `make verify`: **all checks passed**, including pinned mypy, reproducible
  wheel/import, CI-faithful backend smoke, frontend types, and frontend suite
  (220 files / 1,305 tests). Full backend suite passed with environment-gated
  Postgres/Atlas cases skipped. The 16 repository guards were also rerun with
  the owned changes temporarily staged; all passed, and the changes were
  returned to the unstaged working tree afterward.
- A separately attempted lint of the untouched broker test file reported two
  existing issues at lines 403 and 631; changed files pass lint.
- A direct standalone mypy invocation previously failed in the installed
  third-party NumPy stub (`Type statement is only supported in Python 3.12 and
  greater` under the repository's Python 3.14 runtime); the repository-pinned
  mypy gate passes.

## Real browser evidence

Browser: authenticated Integral UI at `http://127.0.0.1:9011/agent`, native
Integral AI binding, GPT-4.1 route. Each input was an ordinary lay-user request:

> I run a small maintenance company. Can you suggest a simple way to track our
> tools? I'd like to review it before anything is created.

Observed attempts during this work:

1. Discovery returned a weak specialist match and the assistant returned a
   generic outline; no skill was loaded and no design proposal was saved.
2. After hybrid semantic/lexical ranking, `integral-scaffold` became the top
   skill result, but the assistant still returned generic prose without loading
   the Pydantic AI skill or saving the requested reviewable proposal.
3. Stronger routing instructions did not change that behavior. The same
   request remained prose-only; no app or other graph object was created.
4. A proposed Pydantic output-validator gate that required explicit capability
   disposition caused a failed browser turn (`Something went wrong on our
   side`). That gate was removed. It is not accepted as a fix.
5. After removing that gate, the same request completed normally in 33.2s at
  $0.0240, but again returned generic prose without loading the surfaced
  `integral-scaffold` skill or recording a proposal. No graph writes occurred.

6. A fresh ordinary-user request included a concrete equipment-register need
   and explicitly asked to review the draft before anything was created. The
   agent used Pydantic AI's `load_capability` to load `integral-scaffold`,
   called `integral_check_design_coverage` twice (the first schema was
   rejected because a relation omitted `target_entry_types`; the second
   passed), then saved a proposal and stopped at the design confirmation.
   The UI showed **“Proposed — nothing has been built”** and
   **“Confirm this design, or tell me what to change.”** No build tool was
   called. The turn completed in 45.3s with 7 provider requests and reported
   104,576 input + 2,592 output = 107,168 tokens / $0.1544. Input rose from
   3,149 tokens on request 1 to 22,052 on request 7; the configured 24k
   `ClearToolResults` trigger therefore did not fire during this run. This is
an end-to-end positive skill/proposal result, but its context spend and
  latency need optimization.

7. After installing that callable strategy, a fresh ordinary-user browser turn
   again loaded `integral-scaffold`, obtained a saved equipment-register design,
   and stopped at the review boundary without building. The visible metadata
   reported GPT-4.1, 86.1k tokens, $0.1210, 30.4 seconds, and five tool steps.
   The raw debug response matched the rendered Markdown. Backend dispatch logs
   show one rejected coverage attempt (`invalid_blueprint`), one valid coverage
   retry, and one successful proposal. This confirms end-to-end discovery and
   the approval boundary on the updated Pydantic `ToolSearch` path, while also
   showing an unnecessary correction call and material token spend. This run
   did not request purchase date, photo, or checkout/return dates, so it does
   not qualify those requirements.

The first five browser attempts reported $0.0184, $0.0127, $0.0232, $0.0297,
and $0.0240 in provider cost respectively ($0.1080 total). The sixth reported
$0.1544; aggregate provider-reported cost for these six attempts was $0.2624.
Provider-reported usage and cost were visible in the chat metadata. This proves
one successful Pydantic AI skill load and review-before-build proposal flow,
not yet consistent performance across models or repeated runs.

After the sixth attempt, the upstream `ClearToolResults` token trigger was
lowered from 24,000 to 12,000 so older search and settled tool results compact
before later requests grow this large. The focused tests and changed-file
format/lint checks pass with the new threshold. The seventh browser run below
used the reduced threshold.

The seventh browser run occurred after the trigger had been reduced and still
reported 86.1k total tokens. Since this number is cumulative usage over the
full multi-request turn, it does not establish the token count of any single
request or prove the compaction threshold failed. Per-request raw usage needs
to be inspected before making a compaction or billing conclusion.

## Remaining acceptance work

- Reduce and measure the seventh turn's 86.1k-token / $0.1210 cost and correct
  the extra rejected coverage call without clearing Pydantic AI's typed
  capability-load state or weakening approval and tenant enforcement.
- Complete the live Ollama GLM-5.3:cloud scenario. Earlier browser attempts
  stalled after tool activity; the new 180-second per-request timeout has not
  yet been validated against Ollama. Acceptance requires a saved, reviewable
  design proposal, a visible confirmation request, no build before approval,
  accurate usage/cost metadata, and reload-safe state.
- Exercise a no-skill question and a tool-search case to prove that capability
  routing can choose a direct answer, a skill, or a tool appropriately.
- Complete `make verify` and required browser evidence before treating WP-07 or
  V1 as complete.
