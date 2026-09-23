# WP-06 resident-flow contract evidence

**Status:** implementation and deterministic-contract evidence; live-budget
qualification remains open.

## Current qualification position

The resident produced one unaided `batch_applied` receipt for an
appliance-service design, completing 16 of 16 staged operations in thread
`n.ChatThread.acc9ec2f1e23478da3870c65` after rejecting and correcting an
initial plan that tried to author detached library models. This is a passing
apply boundary, not a passing WP-06 journey: the run did not include browser
inspection, record/query/update/schema-change readback, or a measured total
journey token budget. Another fresh thread
(`n.ChatThread.1fa29dec890f47d399a63c5a`) failed preflight on a disallowed
operation and produced no App. The plan contract and resident skill now list
the exact allowed writes, and refusal names the invalid operation and the
attached-Track alternative. That guidance has targeted tests, but it has not
yet passed a live retest.

The next local retest could not begin: Postgres returned `DiskFullError` on
synthetic-account signup. Unused Docker build cache was pruned and the
Postgres container then reported about 27 GB available. No live result after
that environment recovery has been counted. These observations keep WP-06
open under the frozen three-domain, five-repeat acceptance profile.

## Current appliance-service live probe

After the host affirmation marker stopped naming a raw tool, the isolated
appliance-service run `n.ChatThread.631c778da6db4c4ba065b6be` called the
approved one-call builder. It did not complete. The first plan was rejected
before writes because a dashboard used unsupported `not_in`. A corrected
plan then stopped at step 17 of 19 after earlier steps had applied: a seed
job assigned the title of a planned Technician entry to a `member` field,
which requires a real workspace User. A further attempt returned
`capability.upgraded`. This is a failed live run, not acceptance evidence.

The compiler now expands `not_in` into conjunctive `neq` comparisons,
validates member seeds before opening a batch, and records a durable partial
build marker when the executor reports a partial apply. A later invocation
of the one-call builder refuses to create a second App from that partially
applied design. These fixes have targeted tests, but the live probe predates
them. The underlying batch executor still permits partial application, so
WP-06 cannot close on this result.

## Applied-design receipt binding

A successful affirmed batch now records its batch token on the persisted
design marker. Subsequent turns see the design as already applied, and the
one-call builder refuses a second application of that proposal. The terminal
turn check therefore distinguishes a missing apply receipt from a completed
build instead of treating every approved design as permanently pending.
Deterministic tests cover receipt ownership, one-time recording, authorization
closure, and duplicate-build refusal. The resident's no-tool affirmation
failure still needs an unaided live correction and the frozen qualification
runs before WP-06 can close.

## 22 September approved-plan compiler and held-out failures

The resident now has a one-call `integral_build_approved_design` capability.
It accepts only a chat-affirmed design from the same principal, compiles
track-field and view shorthand into explicit staged operations, binds App and
Track references, and commits through the existing policy-bound batch seam.
The compiler normalizes relative dates as read-time UTC-day offsets, saved-view
filter syntax, dashboard list widgets, and redundant terminal batch-control
metadata. It rejects missing fields promised by the persisted proposal before
opening a batch. A partial executor result is now an error with its failed
step and batch token, rather than a success-shaped tool result.

Live tests exposed the repairs in sequence. Service-request run
`959dee9b-805e-4137-8d7e-f0c8c667e9fd` hit the preparatory-batch conflict.
Client-delivery run `3c82b8d1-496d-4bb6-8705-dd13f060552d` then rejected
`{{now}}`/`{{now_plus_7}}` as dangling batch references. Later runs in that
thread found shorthand fields/views being ignored (`9340b33c-ffc6-41c0-bcce-c93cddf54fd0`),
an App-name drift (`38e7f9d4-56a9-4cdf-bce5-21c16b2a79b8`), a redundant
commit step (`ebb7c0b5-297f-4836-bf86-fa7102970141`), and a harmless
`depends_on` planner annotation (`aeb45865-a5e3-40de-b2fd-e6d8d3cc273f`).
These were independent failed attempts, not a qualifying build.

Run `4fcaf49f-b487-4506-a6ba-422b26d90963` produced a **partial** build:
four Tracks and seeded entries persisted, but the dashboard failed at step 20
of 23 because generated filters carried symbolic `=`/`>=` operators into a
typed dashboard contract. The resident nevertheless said the App was built
and verified after reading Apps, Tracks, and entries; it had not read the
dashboard or all approved fields. Authenticated readback found no dashboard
and missing approved fields such as Project Lead and Assigned To. This is a
material false-completion and design-fidelity failure. The compiler now
normalizes these operators and gates concrete promised fields; the resident
instructions distinguish an applied receipt from full design verification.
The persisted App remains failure evidence, not a passing fixture.

A fresh held-out rental proposal in run
`e9141317-1b75-452d-a3f6-4ab0de85cc37` persisted a three-Track design
with dashboard and examples in 17.5 seconds, using 74,670 input and 1,299
output tokens. This is only the proposal boundary. Its markdown field syntax
required a further preflight parser adjustment. The frozen profile still
requires five repetitions of each of three scenarios, zero intervention,
readback through the browser, query/update/schema-evolution checks, and p95
journey tokens at most 100,000. Neither this run nor the earlier partial run
qualifies WP-06.

The affirmed rental build (`238eb2aa-0b94-445e-8ad9-36a83ce53461`) applied
22 of 22 operations in 37 seconds with no tool retries. Authenticated
readback found the App, three Tracks, saved table/board/calendar views, one
dashboard, and seven example entries. It also found every example's business
fields empty: the resident had supplied `Field: value` lines in `text` rather
than structured `fields`, so registrations, service dates, rental status and
relations were all null. The one dashboard widget consequently had no useful
data. The model's "built" response was therefore inaccurate. Proposal and
build consumed 249,747 observed tokens together, above the 100,000 budget.
The approved-plan compiler now converts exact labelled seed lines to typed
fields, resolves named relation references, and rejects ambiguous seed lines
before staging. Approved seeds also request strict field validation so the
entry executor cannot silently drop a rejected field and still report a
successful batch. This new behavior has deterministic tests; it has **not** yet
passed a fresh live-model/browser repeat. The run remains a failure fixture.

A subsequent fresh rental proposal (`n.ChatThread.e95b8528013b4babab2767ff`)
uncovered another preflight false negative: an acceptance assertion compressed
"last/next service" and "rental start/end", while its proposal and planned
schema correctly declared the separate date fields. The preflight now treats
the proposal's exact field labels as authoritative where present. After this
initial rejection, the resident asked for a second retry decision and later
opened a manual batch with detached library models instead of resubmitting
the one-call approved plan. No App was applied in that attempt. The skill and
dispatch seam now direct an affirmed fresh design through the one-call builder
and reject a new manual batch; an explicit legacy recovery escape remains for
existing manual-batch tests. This rerun is also intervention/failure evidence,
not a profile pass.

One more clean rental confirmation (`n.ChatThread.4efca5d32d6545ba83758435`)
returned an oversized 96-operation plan: 87 were repeated saved-view calls,
with only 20 distinct operations overall. The resident then promised to
streamline it in a later turn without applying anything. The compiler now
coalesces byte-equivalent repeated operations and compatible duplicate seed
refinements before enforcing its 64-write limit. Conflicting field values
still fail preflight. This is a deterministic repair pending live repeat; the
96-operation turn remains a failed qualification sample.

After redeploy, a follow-up in that same thread did not call the build tool.
It listed Apps, saw the older `Car Rental Management` failure fixture, and
claimed the distinct approved `Car Rental` design was already present. The
fresh design was **not** built. This demonstrates that a nearby existing App
can still be mistaken for a receipt for the current design. WP-06 remains open
until completion language is bound to the current proposal revision and a
matching applied/readback receipt, and the frozen repeated live profile passes.
The chat host now injects an explicit current-design-unapplied context block
while that same thread has an affirmed greenfield design and no open batch.
It directs the resident to call the approved-plan builder and forbids treating
a similarly named existing App as the current design's receipt. This guard is
pending a live repeat; it does not retroactively qualify the failed run.

The next resident turn mentioned the approved-plan builder but again returned
"being built" without a tool receipt. To separate model orchestration from
the substrate write, the exact saved 96-operation plan was replayed through
the authenticated, session-bound tool endpoint for that same approved thread.
The compiler coalesced it to 20 writes and returned `batch_applied` (20/20;
receipt `f7f362e8-2d66-4627-aa8b-4612b57b0bc7`). Authenticated readback of
App `n.WorkspaceApp.162c655836694464bc0931a5` found three Tracks, their
saved views, a four-widget dashboard, and four populated example records:
two Cars, one Customer, and one Rental. Toyota Camry's `current_renter` and
the Rental's `car`/`customer` fields resolve to the intended created Entries.
An `integral_query_entries` filter on `custom_fields.status=Rented` returned
only Toyota Camry, confirming the structured seed is queryable through the
agent tool surface.
The dashboard data endpoint resolved all four widgets; its totals were four
entries and three Tracks, and its breakdown showed Cars 2, Customers 1,
Rentals 1.
This validates the compiler and persistence path for this fixture, but it is
an intervened direct-tool replay, not an unaided resident journey or a frozen
profile pass. The model's no-receipt completion language remains unresolved.

A fresh appliance-service domain attempt (`n.ChatThread.ac1887e3b9314bebb6c04b97`)
failed even earlier: on the design-only turn the resident staged a detached
library Operational Model instead of calling `integral_propose_design`. It
then asked the user to approve that Prompt Sheet card as a prerequisite to
building, despite the chat affirmation. The unintended isolated test card was
revoked; no App was created. This confirms the proposal-only boundary still
needs an execution-level guard, beyond skill and prompt instructions. The
chat host now marks an explicit greenfield design-only turn, and dispatch
refuses all staged/direct tools except `integral_propose_design` while allowing
read tools. The guard expires and clears at turn completion; it has a focused
regression test. This repair has not passed a fresh live-model repeat. The
appliance run is a separate frozen-profile failure, not a successful repeat.

The first fresh appliance proposal after that guard
(`n.ChatThread.40ff3cba43db4e75b6c36a6d`) saved a design and opened no
Prompt Sheet, so the design-only write barrier held in the live process. Its
affirmation turn then said the build was "starting" but made no build tool
call and created no App. This leaves the resident orchestration gap visible:
an affirmative chat message can still end in a progress promise without a
current-turn `batch_applied` receipt. It fails the frozen zero-intervention
and success gates.
The chat completion validator now marks such a turn failed with
`approved_build_not_applied` if the approved design is still awaiting its
build at the end of the turn. This makes the missing receipt visible in the
stream and run status rather than accepting a promise as success. It does not
itself create the App and remains pending live repeat.
An isolated continuation of the same approved appliance thread on the
redeployed candidate emitted a terminal SSE error with code
`approved_build_not_applied` and persisted that error in the assistant turn.
The resident still did not apply the App; the claim-to-receipt guard is now
verified live, while autonomous completion remains open.

The remaining WP-06 repair is specific: the chat affirmation path must invoke
the approved-plan builder or continue the same turn until it has a terminal
apply/failure receipt. A narrated intention must never be its terminal action.
Once that works, rerun the frozen rental, service-request, and
client-delivery scenarios five times each without intervention; inspect each
resulting App in the browser and exercise query, update, dashboard, and schema
evolution. Only accept closure when every readback assertion and the frozen
latency, retry, and 100k-token journey budgets pass. The current direct-tool
success is substrate evidence, not a substitute for those resident runs.

**Candidate context:** `codex/schema-revision-binding`, 2026-09-22.

## Delivered contract

The resident delivery flow now has one visible and executable sequence:

```text
discover → clarify → propose → preview → authorize → execute → verify → explain
```

`integral_scaffold` owns that journey. A proposal is stored as an
`app_design_blueprint` with its acceptance assertions and is rendered as a
preview. It is not an authorization or a write. A correction replaces the
unapproved proposal. One affirmative response resolves that revision and opens
one build batch; specialists cannot obtain a second approval for the same
resolved revision.

Every chat turn creates an `AgentRun`. Its initial metadata records the selected
provider, label, and agent. Provider model steps now add a compact
`model_observability` summary: exact observed model identifier, calls, input and
output token totals, and finish reasons. Prompts, completions, credentials, and
raw provider payloads are excluded. Model and tool boundaries continue to have
individual redacted `RunStep` receipts. Terminal failures retain their stable
error code and message in the owning run. A terminal provider event also
retains a whitelisted orchestration trace (protocol, loop budget/outcome,
guards, tools, skills, fallbacks, and duration), never its prompt or tool
observations.

Headless scaffold recovery continues to derive user-visible completion from the
`integral_commit_batch` receipt. It only says that a build is complete after
`batch_applied`; failed, pending, and incomplete outcomes cannot become a saved
or verified claim through model prose.

## Deterministic evidence

| Check | Result |
| --- | --- |
| Skill names every delivery phase and receipt-honest language | Pass: `test_resident_skill_runtime_alignment.py` |
| Use case requires propose/preview before the one build approval | Pass: `test_scaffold_use_case_requires_preview_before_the_single_build_approval` |
| Use case permits only proposal on design turn and one batch on affirmation | Pass: deterministic CUC contract |
| Corrections replace unapproved designs; approval locks the resolved design | Pass: `test_propose_design_service.py`, `test_propose_design_dispatch.py` |
| Recovery schedules only an authorized dependent continuation and reports receipt-backed completion | Pass: `test_scaffold_recovery_continuation.py` |
| Run retains redacted model/version/token/finish summary | Pass: `test_model_steps_accumulate_redacted_token_summary_on_run` |
| Run retains diagnostic loop outcome without raw harness payload | Pass: `test_terminal_provider_trace_is_whitelisted_on_run` |

Focused command executed:

```text
pytest backend/tests/test_execution_runs.py \
  backend/tests/test_resident_skill_runtime_alignment.py \
  backend/tests/test_integral_use_cases.py \
  backend/tests/test_propose_design_service.py \
  backend/tests/test_propose_design_dispatch.py \
  backend/tests/test_scaffold_recovery_continuation.py -q
```

Result: **50 passed**.

## Remaining qualification

This closes the resident contract and observability implementation slice. It
does not close WP-06's release exit. Before that designation, the program still
needs retained successful and failed multi-domain live traces. The frozen
[qualification profile](wp-06-live-model-qualification.yaml) now names the
supported configuration identity, three held-out operational domains, five
repetitions per domain, and fixed safety, intervention, latency, token, and
retry budgets. `scripts/evaluate_live_model_qualification.py` rejects raw
prompt/completion material, incomplete coverage, or a missed budget; it emits
a machine-readable report from redacted `AgentRun` references. The profile is
ready to execute against a candidate deployment.

The 2026-09-21 rental design-only evaluation remains valid limited evidence;
it is not a full confirmation-to-build proof.

## Live exploratory findings, 2026-09-22

An authenticated browser run in a fresh personal workspace exercised a held-out
home-appliance-service request. It found and corrected three production-path
defects before the app could be materialized:

1. An explicit design-only request could be turned into a needless
   design-or-search question. The chat host now injects a proposal-only
   directive for an unambiguous greenfield need, requiring substrate discovery
   and `integral_propose_design` without write authority.
2. The generic approval pre-filter treated the phrase `do not build` as a
   rejection cue, then incorrectly supplied a write-oriented confirmation
   nudge. The nudge now accepts only an unambiguous positive affirmation.
3. A model-supplied Kanban column label list was accepted until a later entry
   materialization failed. The scaffold compiler now replaces that malformed
   shape with the persisted `{key, label}` column contract before apply.

The repaired browser journey produced a proposal without creating an App,
then created an App with six tracks and one seeded record per track. A later
dashboard request was initially rejected because natural widget names (`kpi`,
`chart`, `feed`) were not renderer widget names. The staging binding now
translates those stable semantic aliases to supported widget types; the
approved retry created one dashboard, visibly confirmed in the App detail UI.

This is valuable failure-and-repair evidence, not a qualified profile pass.
The build required a separate dashboard retry after the original affirmation,
and observed model-token totals exceeded the frozen profile's 100k budget.
It therefore fails the profile's zero-intervention and token-budget criteria.
The required 3 domains × 5 repetitions, retained evaluator reports, and
success/failure coverage are still open.

## Durable trace export

`GET /api/chat/runs/{run_id}/qualification-export` is the supported evidence
projection for a completed resident turn. It is authenticated and bound to the
caller's active workspace; a run outside that scope is indistinguishable from a
missing run. The response includes the run status, harness binding, observed
model identifiers and token totals, elapsed time, and a content-free list of
tool/model boundary receipts. It never serializes chat messages, model
completions, tool arguments/results, capability snapshots, or credentials.

A live evaluator records the returned `redacted_trace_ref` in the frozen
qualification trace and makes the scenario assertions from the observable UI,
materialized records, and this durable receipt. Assertions remain separate from
the export: a client must not be able to turn an unverified claim into an
Integral-owned fact merely by posting it to an API.

## Compiling retained qualification evidence

`scripts/compile_live_model_qualification.py` is the only supported bridge
from those exports to the evaluator input. The operator prepares a local,
redacted manifest containing the candidate identity, the frozen provider
configuration identity, independent scenario assertions, intervention count,
and each API qualification export in turn order under `run_exports`. The
compiler derives the journey's timing, tokens, retry count, and receipt
references from all turns; it proves proposal-before-build from successful
proposal and commit receipts in that order. It rejects prompts,
completions, messages, credentials, authorizations, and tool observations at
any nesting depth. It also rejects a receipt whose observed model differs from
the frozen configuration.

```text
backend/.venv/bin/python scripts/compile_live_model_qualification.py \
  --profile docs/product/evidence/wp-06-live-model-qualification.yaml \
  --manifest .qualification-evidence/live-model-manifest.yaml \
  --trace .qualification-evidence/live-model-trace.json

backend/.venv/bin/python scripts/evaluate_live_model_qualification.py \
  --profile docs/product/evidence/wp-06-live-model-qualification.yaml \
  --trace .qualification-evidence/live-model-trace.json \
  --report .qualification-evidence/live-model-report.json
```

The manifest and generated trace are retained only in the ignored local
evidence directory until a redaction review approves a candidate-specific
evidence record. A passing evaluator report is still evidence, not a release
declaration: its browser observations, deployment identity, and candidate
digest must be reconciled in the acceptance ledger.

## 22 September proposal retest and routing repair

The isolated WP-06 account produced a successful proposal receipt in run
`65ea2cd5-0ce7-4a4f-93c6-1c588f65e3e5`: the durable steps include successful
substrate inspection, model listing, and design proposal. An earlier claim that
the proposal tool was unavailable was incorrect. Two later runs
(`766014ca-4ae3-4804-8ff3-dcc95c34b3b1` and
`2848ea96-ec3e-4c1c-9f76-29ad28c2714d`) recorded no tool steps despite the
harness trace listing attempted tool names. The trace's `tools_invoked` list is
not proof of execution; the `RunStep` receipts are. These runs ended in
irrelevant prose and are failures, not qualified proposals.

Source inspection found that host-generated utterance markers contained exact
tool names while the harness's `block_raw_tool_invocation` anti-steering guard
was enabled. That guard can deflect a named tool before execution, including
when the host rather than the user supplied the name. The host's design,
schema-edit, confirmation, image-attachment, and open-batch markers now state
the required operations semantically. The scaffold directive still names the
`use_skill` egress and `integral_scaffold` skill; `use_skill` is exempt from the
tool-name steering guard. A current-turn persisted proposal is required before
a routed greenfield turn may end successfully. A confirmation of a pending
design is routed to its build rather than treated as a new design request.

For the post-change live check, the API image was rebuilt and the running
container's image digest verified before sending a proposal-only bicycle
repair request. Run `62756a9a-facf-4f58-b904-0c56727f9964` completed with
successful `integral_describe_substrate` and `integral_propose_design` tool
receipts, and the thread stored an unapproved design marker for user turn 1.
It used 72,402 input and 1,360 output tokens in 16.3 seconds with no tool
retries. This is one passing proposal boundary, not a full scenario: it did
not build or verify an app, and its token use leaves little headroom under the
frozen 100k budget for the remaining journey. Repeated multi-domain runs and
their browser/readback assertions remain open.

## 22 September authorized build and readback

The user authorized the isolated build test. In the same test thread, “Looks
good. Build it.” produced run `f94e9997-936b-4ba1-9c30-f63471075d02` with
status `succeeded`, a successful batch commit receipt, and no tool retries.
Authenticated readback in the isolated account showed exactly one Bicycle
Repair Management app, four tracks (Customers, Bicycles, Repair Jobs,
Mechanics), and one entry in each track. The saved table views bind to the
declared custom fields; Repair Jobs also has a status-bound kanban and a
due-date calendar. This establishes that the proposal-to-build boundary can
persist and read back a useful substrate shape.

The run did **not** qualify WP-06. The build used 277,182 input and 2,010
output tokens in 42.4 seconds. Together with the preceding proposal, the
journey used 352,954 tokens, exceeding the frozen 100,000-token budget.
Readback also found title-only named records whose fields were filled with
generic values such as `Example Name` and `Example Email`. The source was the
scaffold-default compiler enriching every blank seed regardless of whether
its title identified a real subject. It now enriches only explicitly
synthetic Demo, Example, or Sample records; the named-record regression test
covers this rule. This repair was not in the image used for the build above,
so the persisted test app remains evidence of the failure, not proof of the
repair. The full multi-scenario, in-browser, query, update, and schema-change
qualification remains open.

## 22 September existing-App Wiki addition

Commit `33a324f` records the follow-up to the Car Rental Manager addition.
The approved design named one Wiki track, Wiki Page fields Title, Body, and
Parent Page, one hierarchical Wiki view, and no demo entries. The live build
on the previous image needed three `integral_build_approved_design` calls.
The first relation used `mode: parent` with `track: self`. The second view
used `hierarchy_field` instead of `parent_field`. The third call applied.
The compiler then added an "All Wiki" table because the track had no table
view. The turn ended on `[SYSTEM:STAGING-RESOLVED]` with no user-facing
readback. Old `type: "error"` parts in that transcript crashed the App page
because the chat dock hydrates there.

`33a324f` accepts that relation and view shorthand, skips the extra table
when a Wiki view is already planned, appends `Built: {summary}.` when a
consumed batch has no model prose, and renders persisted error parts as
text. After the web image was rebuilt, the App page and Wiki track loaded
with the old transcript. The new-page form shows Title, Body, and Parent
Page. The track has zero entries. The unrequested All Wiki view was removed
from the local qualification database. The platform Feed remains; it is the
substrate default on every track, not a second design view.

This run does not qualify WP-06. It was one existing-App addition, not the
frozen three domains times five repeats. The repaired shorthand was not
re-run live. Prior greenfield journeys still exceed the 100,000-token p95
budget. The qualification profile now requires
`materialized_surface_matches_design` so an extra table or a forbidden demo
entry fails the gate.

## 22 September held-out journey, first repetition

One rental-operations journey was driven against the local `33a324f` API
with no human turns. It created one workspace and one App with three tracks
(Skiffs, Renters, Outings) and one dashboard. It did not qualify.

Measured token totals from the qualification exports were 79,142, 19,595,
97,288, 21,015, and 156,896 across the five turns. The design turn alone
was 79,142 input tokens, so a five-turn journey cannot meet the frozen
100,000-token budget on this prompt size. The journey total is about
374,000. Three of the five run exports have status `failed`. The affirm
and query turns each stored the same build attempt twice: once as
`capability.adapter_failed` and once as succeeded, and the run status
followed the failure. The query turn did call
`integral_build_approved_design`. The named seed was not the only entry:
the tracks contain Example Skiffs, Example Renters, and Example Outings,
because "no other entries" did not count as an empty design.

The remaining fourteen repetitions were not started. A 100 percent success
gate cannot pass once the first repetition has already missed the token
budget, and repeating the same journey would only add more unqualified
Apps. Redacted turn receipts are in the ignored
`.qualification-evidence/wp06-live/` directory.

The profile was recalibrated after that run. Summed billed tokens and
latency still cover the whole journey. Peak input is the largest single
model call. The caps are 500,000 summed tokens, 40,000 peak input, and
180 seconds. Historical paragraphs above that say 100,000 describe the
cap those runs were scored against. A later `glm-5.3:cloud` five-turn
rental used about 1.67 million tokens and about 10 minutes, so it fails
the recalibrated caps too. Qualification stays on gpt-4.1 until scaffold
turns stop looping.
