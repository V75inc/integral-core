# WP-07.1 evidence — capability search and lay-user routing

**Status:** Pydantic AI skill discovery and review-before-build proposal path
verified on GPT-4.1; Ollama GLM-5.3:cloud fails the real-world acceptance run.
Cost and latency remain high for a simple request; BYOK and scope are covered
by automated tests, not yet by a browser BYOK run.
**Revision:** `8d06de28` on `feat/pydantic-ai-harness-v1`.
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

8. After the experiment below was discarded, the same equipment-register
   request was run again against GPT-4.1. The assistant loaded
   `integral-scaffold`, checked the blueprint (one rejected schema followed by
   a passing check), saved a proposal, and stopped at “Proposed — nothing has
   been built.” The UI showed the explicit confirmation boundary. It completed
   in 36.2s with 97.1k tokens, $0.1222, and six additional calls (seven
   provider requests total). This
   re-confirms the Pydantic AI workflow and correct usage/cost display on the
   deployment key route. It remains expensive, and the first invalid blueprint
   is an avoidable retry.

9. A plain workspace question, “How many apps do I have in this workspace?”,
   loaded `integral-workspace`, called `integral_list_apps`, and answered that
   the workspace has no apps. It completed in 5.9s with 27.4k tokens, $0.0366,
   and three recorded tool steps (four provider requests total). This shows
   basic tool routing works, but also quantifies a material baseline cost for
   a short read-only request.

10. A live GLM-5.3:cloud run used the same ordinary equipment-register request
    while an uncommitted experiment deferred all four scaffold lifecycle
    tools and told the model to search for skill-named operations. Pydantic
    AI's `search_tools` was exercised twice with near-equivalent queries; the
    results did not include `integral_propose_design` before coverage passed.
    The model then sent a blueprint rejected for extra `key` fields and a
    routine containing both `cron` and `run_at`. The retry exceeded the
    harness-wide 120,000-token limit at 149,434 cumulative tokens before a
    proposal was saved. The UI showed no proposal and no build; this Ollama
    run is a failure. Its UI did not show a price because LiteLLM has no
    pricing data for this route; that remains “unavailable,” not zero.

The experiment in item 10 was reverted. The four lifecycle operations remain
directly callable after the initial skill-discovery gate; the rest of the
large catalogue remains deferred behind upstream Pydantic AI `ToolSearch`.
This keeps the library's deferred-discovery surface for the broad catalog
without adding redundant model search turns to the scaffold happy path. This
is an evidence-based compatibility choice, not a lexical or authorization
gate. No user changes were discarded.

The current browser smoke server was isolated on the `integral_pydantic_smoke`
PostgreSQL database and used the authenticated Administrator workspace. The
GPT runs used the deployment OpenAI route. Workspace BYOK resolution and
tenant/session isolation are exercised by the existing model-route,
credential-resolver, scoped-store, and runtime tests; these smoke runs do not
claim an end-to-end BYOK browser result.

## Remaining acceptance work

### Earlier manually cancelled GLM attempt — post-load stall (2026-10-05)

On a branch-backed browser frontend, with `integral_native` explicitly
selected, an ordinary equipment-tracking request called
`search_capabilities` once. It ranked `integral-scaffold` first, and the
Pydantic AI `load_capability` call returned that skill's instructions. The
trace also shows one `integral_list_apps` and one `integral_list_tracks`; it
does not show repeated invocation of the same tool. The raw route in backend
logs was `glm-5.3:cloud` via `ollama_chat`. The assistant then remained in the
generating state for over two minutes without final text or a completion
usage readout. The isolated turn was cancelled through the UI; it produced
no proposal or graph write.

**Result: FAIL after successful discovery and skill load.** This records a
post-tool continuation stall, not a repeated-tool loop. Investigate the
provider request and Pydantic AI continuation after a large skill result.
Detailed browser evidence is in
`docs/product/harness-evidence/wp-03/task-03.3.3-native-browser-smoke.md`.

- Reduce the simple lookup baseline (27.4k tokens / $0.0366) and the design
  path's 97.1k tokens / $0.1222; capture per-request input/output usage and
  compaction events to identify the dominant context cost.
- Fix the GLM-5.3:cloud blueprint correction failure and rerun the same lay-user
  browser scenario until it saves a reviewable proposal, shows the confirmation
  boundary, and performs no build before approval. Do not raise the 120k
  aggregate budget to mask the failure.
- Run one authenticated browser turn using a workspace-owned BYOK credential,
  then verify the selected model, cost source, tenant attribution, and no key
  disclosure in UI or logs.
- Exercise a no-skill question and a tool-search case to prove that capability
  routing can choose a direct answer, a skill, or a tool appropriately.
- Complete `make verify` and required browser evidence before treating WP-07 or
  V1 as complete.

### Fresh GLM lay-user run — budget stopped the turn

A branch-backed in-browser run of “Can you help me organize a simple tool
register for our maintenance crew?” loaded `integral-scaffold` and read the
workspace, then used eight steps: `search_capabilities`, `load_capability`,
`integral_list_apps`, `write_plan`, `integral_check_design_coverage`,
`search_tools`, `search_conversation_history`, and a second
`integral_check_design_coverage`. It made five model requests and stopped at
Pydantic AI's configured 120,000 cumulative-token budget (actual observed
139,288, because the request that crossed the limit had completed). The UI
showed a safe generation-limit message; it did not save a proposal or write to
the graph. This rules out an infinite repeated-tool loop in this run, but fails
the acceptance path due to excessive/slow GLM generation and unnecessary
exploration. Total model latency was 205.6 seconds; request 3 alone took
137.1 seconds.

Per-request input/output tokens were 3,808/341, 6,964/168, 17,962/15,857,
38,905/6,211, and 45,156/3,916. The browser displayed `$0.0000`, with each
call marked `litellm_response`; treat this as the provider-reported value, not
confirmed actual Ollama Cloud billable spend. Request 3's reported completion
tokens also exceeded the configured 8,192 `num_predict`, so the effective
provider-side output cap needs independent verification. Do not raise the run
budget to conceal this behavior. See WP-03 native browser evidence for the
full trace and distinction from the earlier manually cancelled attempt.

### Structured-tool translation follow-up — 2026-10-05

The native Integral tool adapter now normalizes object/array values encoded as
JSON strings only when the declared schema requires that structure. The live
branch browser showed the blueprint reaching `integral_check_design_coverage`
as an object; two substantive blueprint errors were corrected, coverage then
passed, and `integral_propose_design` persisted a proposal. This proves the
provider representation wobble no longer prevents broker validation. The user
still saw the safe generation-limit error after the proposal, at 135,033 total
tokens over eight requests; the UI cost remained `$0.0000` / source
`litellm_response`. No build occurred. Result: **PARTIAL**; retain the 120k
limit and finish the user-visible completion/recovery path before acceptance.
