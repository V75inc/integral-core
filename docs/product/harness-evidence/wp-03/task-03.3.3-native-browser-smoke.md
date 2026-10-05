# WP-03.3.3 — Native browser smoke evidence

**Date:** 2026-10-05
**Branch:** `feat/pydantic-ai-harness-v1`
**Environment:** isolated Integral Core development stack; frontend `127.0.0.1:9011`, backend `127.0.0.1:4011`, dedicated PostgreSQL database `integral_v1_branch`
**Harness:** Integral AI / Pydantic AI Harness
**Model:** `gpt-4.1-mini` (OpenAI control)

## Scenarios

### Scaffold design, confirmation boundary

In the browser, requested a simple Guyana maintenance-company equipment register with serial number, description, condition, storage location, purchase date, photo, staff checkout/return dates, and the Integral scaffold skill. The prompt explicitly said design only and to wait for confirmation before building.

The assistant returned a proposed Equipment Register app with one Tools track, all requested fields, Table and Calendar views, and no demo entries. It explicitly said that nothing had been built and asked for confirmation or changes. No build confirmation was sent and no App was created.

The response displayed model, aggregate token spend, cost, elapsed time, and provider-call count.

### Workspace and tracks lookup

In a separate browser conversation, asked: “What workspace is this, and are there any tracks in it? Please check the workspace rather than guessing.” The trace showed successful calls to `integral_get_scope` and `integral_list_tracks`. The assistant correctly identified `Harness V1 Smoke` and reported no tracks.

The response displayed `gpt-4.1-mini`, 10.1k tokens, `$0.0022`, 4.0 seconds, and two provider calls.

Reloading the browser restored both conversations and the complete lookup response, including its tool-step trace and usage readout.

After the in-app browser connection was restored, repeated the workspace lookup from a fresh conversation. The assistant again verified `Harness V1 Smoke`, reported no tracks, and displayed `gpt-4.1-mini`, 10.1k tokens, `$0.0014`, 7.4 seconds, and two provider calls.

## Result and boundary

Both user-visible smoke scenarios passed on the isolated branch stack. The tests exercised the existing native `/messages` streaming path and its graph-backed chat transcript. The scaffold confirmation boundary held: the proposal remained unbuilt.

The PostgreSQL worker suite also simulated worker death at both terminal boundaries: after the stable assistant transcript was persisted but before WorkItem terminalization, and after the final event was committed but before transcript persistence. Retrying the same claimed turn reconciled the committed result and terminalized without calling the provider a second time in either case. A further regression verifies that a provider stream ending without `message-finish` is failed with a durable terminal error and preserves partial output instead of presenting the turn as successful.

After browser access was restored, a new isolated conversation asked: “Please check this workspace's actual data: what workspace is this, and how many tracks does it currently have? Give their names if any.” The live 9011 browser returned the correct `Harness V1 Smoke` workspace and reported zero tracks. It showed `gpt-4.1-mini`, 7.4k tokens, `$0.0021`, 5.8 seconds, and one provider call. This verifies authenticated browser access and another real-input lookup on the isolated branch stack.

On 2026-10-05, reconnected to the isolated in-app browser tab and ran another
fresh real-input lookup: “What is the exact workspace name, and how many tracks
are in it? Use the workspace data, not assumptions.” The browser returned
`Harness V1 Smoke` and zero tracks, with `gpt-4.1-mini`, 4.0k tokens,
`$0.0016`, 4.1 seconds, and one provider call. The message composer cleared
after send and the assistant turn reached a completed state. This is a repeat
smoke of the existing native `/messages` path, not the durable WorkItem path.

The 9010 user session was also inspected without sending or retrying anything. Its selected scaffold request remains visibly in progress; the debug envelope reports `running` and a reasoning-only content part, with no user-facing answer. This is the existing `jvagent` provider path, so it is not evidence about the gated V1 WorkItem worker. The active conversation was left unchanged to avoid duplicating a possible in-flight tool operation.

These scenarios do **not** exercise the WP-03.3.3 durable WorkItem producer, committed event replay endpoint, WorkItem cancellation, or multi-process recovery; those paths remain gated and unaccepted. Browser reload here proves transcript persistence only, not WorkItem replay or that a model request was not rerun after a transport interruption.

## Native GLM post-load stall — 2026-10-05

Opened a separate frontend on `127.0.0.1:9012` pointed at this worktree's
backend on `127.0.0.1:4011`; the existing `9011` frontend proxies to another
checkout's backend and is not valid branch evidence. Created a synthetic local
smoke account and workspace, selected **Integral AI** in Settings → Agent, and
used this ordinary request:

> I need to keep track of the tools my maintenance crew uses and who has each
> one. Can you help me set that up?

The backend registry exposed `integral_native`; LiteLLM logged the configured
raw model `glm-5.3:cloud` through provider `ollama_chat`. Pydantic AI called
`search_capabilities` once. It ranked `integral-scaffold` first with the
workspace-authorized skill catalogue, then invoked `load_capability` for that
skill. The UI trace also showed `integral_list_apps` and
`integral_list_tracks`; this run did not repeat the same tool call. Despite
the successful skill load, the assistant remained in a generating state for
over two minutes without final text or a completion usage/cost readout. I
cancelled the isolated turn through the UI. No design proposal or graph write
was produced.

**Result: FAIL — native skill discovery and load passed; post-load turn
completion did not.** This is evidence of a post-tool continuation stall, not
a reproduction of repeated tool invocation. The cancellation route returned
HTTP 200. The smoke used the existing native `/messages` path; it does not
qualify durable WorkItem dispatch or replay. Investigate the provider request
and Pydantic AI continuation after a large `load_capability` result before
claiming the lay-user scaffold flow works on GLM-5.3:cloud.

### GLM-5.3:cloud bounded-run diagnosis — 2026-10-05

Repeated the ordinary-user scenario on a fresh chat in the same isolated
branch stack: “Can you help me organize a simple tool register for our
maintenance crew?” The run did not hang in the backend or invoke a tool
indefinitely. It completed five LiteLLM model requests, ran eight tools/steps,
and then Pydantic AI raised its configured `UsageLimitExceeded` at 139,288
cumulative tokens against Integral's 120,000-token run budget. The UI rendered
this safe-stop message: “This turn reached Integral's safe generation limit
before it could finish. Start a new conversation with a shorter request and
try again.” No proposal or graph write occurred.

The visible tool trace was `search_capabilities`, `load_capability`,
`integral_list_apps`, `write_plan`, `integral_check_design_coverage`,
`search_tools`, `search_conversation_history`, and a second
`integral_check_design_coverage`. This is bounded but inefficient exploration;
it is not a repeated identical-tool loop. Per-request telemetry showed
3,808/341, 6,964/168, 17,962/15,857, 38,905/6,211, and 45,156/3,916
input/output tokens. The third response took 137.1 seconds; all five requests
took 205.6 seconds. The final error was correctly mapped to the run's safe
generation limit.

The chat metadata displayed `glm-5.3:cloud`, 139.3k tokens and `$0.0000`;
each call's cost source was `litellm_response`. This records what LiteLLM
reported for this route and does not establish the actual marginal price of
the user's Ollama Cloud subscription. Confirm whether an explicit zero from
this route means no per-token charge before presenting it as billable-cost
truth. The 15,857 completion-token observation on request 3 also exceeded the
configured Ollama `num_predict` value of 8,192; verify provider semantics and
the effective outbound limit before relying on that setting as a hard cap.

**Result: FAIL — capability discovery and workspace grounding worked, and the
host stopped safely at its token budget, but the lay-user task did not reach a
reviewable proposal.** Evidence points to GLM's large/slow generations plus
excessive planning and repeated context, rather than a network transport that
remained stuck. Keep the run budget; do not raise it to mask this failure.
The next change should reduce avoidable framework/tool/context work behind the
native binding adapters, then rerun this same browser scenario and the OpenAI
control before qualifying the route.

### Schema-driven native tool-argument normalization — 2026-10-05

Added an Integral-owned adapter at the native broker boundary that decodes a
JSON string only where the advertised JSON Schema requires an object or array.
It resolves local schema references and leaves malformed or wrong-shaped
values untouched for the existing authoritative broker validation. A focused
browser run against the restarted branch backend exercised the fix with this
ordinary prompt:

> I need a simple way to keep track of our maintenance tools and who has
> borrowed them. Can you help me set it up?

The first live `integral_check_design_coverage` call visibly arrived at the
broker with `blueprint` as an object, confirming the string-to-object adapter
ran. The first two blueprints still had genuine schema defects (a relation
missing `target_entry_types`, then a duplicate field item ID); the third check
passed with `status: buildable`. `integral_propose_design` then returned a
successful receipt and persisted the reviewable Tool Tracking outline. No
build or graph write occurred. The trace had one capability search, one skill
load, one app-list read, three coverage checks, and one proposal call; it did
not repeat an identical successful read.

The assistant nevertheless ended with the safe generation-limit message
instead of showing the saved proposal. The eight provider requests accumulated
130,423 input and 4,610 output tokens (135,033 total); their recorded latencies
sum to 28.1 seconds. The UI showed `$0.0000` sourced from `litellm_response`,
which remains unverified billable-cost data for this Ollama Cloud route.

**Result: PARTIAL — structured argument normalization is verified in-browser
and the proposal is safely recorded, but the user-facing turn still fails at
the 120,000-token run limit after that successful tool effect.** The next
acceptance step must make the completed proposal visible without weakening the
run budget or replaying its write, then repeat the same lay-user scenario.
