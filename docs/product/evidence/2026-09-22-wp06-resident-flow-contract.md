# WP-06 resident-flow contract evidence

**Status:** implementation and deterministic-contract evidence; live-budget
qualification remains open.

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
and each API qualification export. The compiler derives outcome, timing,
tokens, retry count, and trace reference from the export; it rejects prompts,
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
