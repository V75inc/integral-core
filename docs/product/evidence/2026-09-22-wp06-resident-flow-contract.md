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
