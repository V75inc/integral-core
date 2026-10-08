# Native batch continuation follow-up

Base: `9f860e2b62d56608570dc1b3101a8a1361102bfe`, `codex/pr-113-staging`. Local isolated repair: `/tmp/integral-core-native-batch-followup`, branch `codex/native-terminal-batch-followup`. Prepared as a separate review branch; no package release.

## Finding

After rejecting a two-create batch, the UI showed Change not applied, but automatic continuation `aadd0978-5f64-4909-a6a2-b69b15a4aa77` repeated the old rehearsal and invited approval of the rejected proposal. User text remained empty. A scoped, read-only checkpoint inspection confirmed the model received the batch `integral_commit_batch` receipt as `revoked`, with `state_source=current_core_staging` and `context_projection=terminal_receipt_summary_v1`. The evidence identifies a model interpretation/host-task presentation failure, not a missing rejection or executed write.

## Proposed generic repair

The router marks native Prompt Sheet outcome continuations in trusted `extra_data`. For that textless event only, the native adapter derives a concise immediate host task from the latest receipt already reconciled by Core. It includes only token, kind, terminal state and source; proposal prose never becomes instructions. An initial run-instructions-only variant still repeated the earlier response (`c16969b3-3343-4606-8b35-91c3e8395a5a`). The next experiment appends a public Pydantic `ModelRequest` containing only a `SystemPromptPart`, establishing an explicit host event after the old assistant response, without a synthetic human turn. Revocation means acknowledge rejection, do not invite approval/restage or repeat the earlier deliverable. Consumed outcomes still require checking application/error/rollback facts and record readback. Other textless tasks and authored user turns are unaffected.

Preserves I-SUBSTRATE-01 and I-EXT-01: no App/domain branch, skill router or founder terms. Preserves approval authority and user-channel provenance: this creates no approval, mutation, synthetic user message or permission. Tools remain broker-authorized. App provenance/response contracts remain outside Core.

## Evidence and limits

Initial focused receipt/provider tests passed 59 tests; expanded with chat host/image regressions passed 71. Foreign, pending, blessed and unverified receipts cannot become the new host task; generated proposal prose is excluded. Browser repetition and cross-domain testing remain necessary. This is a local experiment overlay on the refreshed distribution, not published Core. Full `make verify` remains required before integration/commit.


## Mixed-type create failure

Approving the corrected Experiment + Test Material batch saved step one, then failed step two: fields for canonical `test_asset` were validated against the default Experiment. Independent browser track readback showed exactly one draft Experiment and no material. The resolver matched display names but ignored `_manifest_entry_type_key` stored in EntryType.form_schema. The destination has the correct mapping (`Test Material` -> `test_asset`). Local repair resolves the canonical key within the destination profile before legacy name matching and fails closed if an explicitly requested type cannot resolve; an omitted type retains default behavior. Generic regression uses `artifact` / `Supporting document`, with an unresolved-type refusal before any endpoint call. Recovery must create only the missing material, linked to the verified existing Experiment, not retry the entire batch.

Revoking a partially applied batch does not imply zero effects: host acknowledgment must check retained progress and rollback facts. No data was purged during this qualification.

## Explicit host-event browser results

With the system-only request boundary, missing-material recovery `22add6c4-d13a-4749-b74e-e7609d1e4bac` passed: one Test Material created, accurate consumed acknowledgment in 3.5 seconds, governed readback, both exact relations and Integral attribution retained. Independent Track UI confirmed two records, the existing unrun Experiment plus its draft material. This recovery did not retry the earlier partially applied batch.

Mixed-type title batch rejection `aa13190a-f283-41da-8f8c-fd2fac018947` passed: revoked acknowledgment in 4.5 seconds, unchanged-title governed readback, no restaging. Message Debug user text was empty. Independent Track UI confirmed the original titles and relations. These are two live cases on the default DeepSeek cloud model, not statistical reliability evidence or cross-domain qualification.

The expanded selected regression set initially found an architectural import violation (232 passed / one failed). Moving SystemPromptPart into the maintained Pydantic compatibility adapter corrected it: **233 passed** including moderation, staged batch/resume/update, chat images, native runtime/stream/receipt/compatibility, capabilities and model observations. Full make verify has not been rerun on this local patch.

### Fresh complete linked batch

After the canonical-type and host-event repairs, `98f7b4cf-c751-443b-814d-346a3b5b0166` passed a fresh two-create batch: Fictional owner check (Experiment) plus Owner check worksheet (Test Material). Independent Track UI confirms the material relates to the newly created Experiment and existing Opportunity, draft status, Integral attribution and fictional-only scope. Both creates applied without errors/rollback; accurate automatic acknowledgment in 4.7 seconds, empty user text. This qualifies the tested intra-batch reference, not all mixed-type schemas. Screenshot: `/tmp/integral-venture-smoke-20261007/evidence/core9f860e2-complete-linked-batch.jpg`.

All 16 substrate guards passed against explicitly staged repair files; pinned Black 24.8.0 and isort 6.0.0 checks passed for the seven changed Python files. Full make verify remains open.

Additional consumed continuation `c5e0df74-0879-4660-bc10-c5201328bc95` passed one Decision create/readback (3.0 seconds, empty user text), independently confirmed in Track UI. Cross-track assistant-link navigation did not move from the original Track; direct navigation to the displayed link opened the correct saved record. This UI finding remains unresolved and is separate from the harness repairs.

## Pre-push qualification environment

The initial full gate reached the full backend suite with one teardown error: LiteLLM's first import discovered the other Core checkout's `.env` through the shared virtualenv and added three environment settings (`INTEGRAL_NATIVE_HARNESS_ENABLED`, `INTEGRAL_NATIVE_MODEL`, `JVSPATIAL_TEXT_NORMALIZATION_ENABLED`). The per-test environment leak guard correctly rejected that side effect. The isolated worktree contains no `.env`.

The initial `PYTHON_DOTENV_DISABLED=1` experiment cleared the transport leak, but the full suite then correctly failed `test_cwd_dotenv_fills_unset_variables`: it disabled Integral's intentional dotenv loading as well. That global workaround is not the qualification command.

`LITELLM_MODE=PRODUCTION` disables only LiteLLM's development auto-discovery during tests, preserving Integral's own explicit dotenv behavior. The combined transport and distro-init selection passed all **33 tests**. The full gate is rerun as `LITELLM_MODE=PRODUCTION make verify`. No guard, test or assertion is skipped, and no runtime/deployment environment is changed.

### Final gate

**PASS** `LITELLM_MODE=PRODUCTION make verify` on 2026-10-07: staged substrate guards, all pre-commit hooks, pinned formatting/lint, frontend lint/types, public wheel build and isolated import, CI-faithful backend smoke run, full frontend suite (**256 files / 1,473 tests**) and full backend suite. No test exclusion was added. Standard optional PostgreSQL and unseeded domain-package skips remain; this is not a PostgreSQL-lane qualification claim. Earlier statements that full make verify remained open describe the preceding browser qualification, before this pre-push gate.


## Active-task grounding and upgrade identity follow-up

A later downstream browser case required several record reads and destination schemas before one multi-record approval card. The default native DeepSeek Flash cloud run loaded the correct focused skill but repeated successful reads and hit the tool limit without staging. Correcting the downstream skill's native `rows` contract did not resolve repetition. A controlled replay of the scoped step-3 checkpoint through the deployed `ClearToolResults` policy cleared the App/Track reads and earliest record read while the task still needed them. Durable checkpoints retained those payloads; the library clears request-only model history. This is not evidence of failed persistence or absent skill installation.

The generic adapter now applies result clearing to completed-turn history and preserves the current user turn's exact typed tool sequence. Library usage/tool limits still bound the active task. The existing five-pair historical window, loaded-skill exclusion and scope/authorization rules remain. No App identifiers, founder rules, extra authority, synthetic user messages or model overrides are introduced. Regression coverage proves that twelve active read pairs survive, older completed work is cleared, and the source messages remain unchanged.

The same qualification found an independent populated-App upgrade defect: impact analysis derived type identity from display labels even when `_manifest_entry_type_key` exists. Unchanged packaged types with different display labels were falsely reported removed. Impact analysis now prefers the canonical manifest key, with the historical name fallback for legacy types. Regression coverage includes canonical identity despite a different display name, a genuinely removed canonical key still rejected, and legacy fallback. Migration enforcement remains active; no dummy migration or guard bypass was used. The same queued local upgrade succeeded after the repair, and its attached query predicate matched the current library.

In-browser comparison on the native Pydantic binding and unchanged default model then completed the original full request: six unique grounding queries, five schemas, one batch, two creates, six partial updates and one commit (25 tool steps, 57.4 seconds). Approval continuation carried empty user text, verified all affected record types in six queries, and completed in 10.8 seconds. Independent record dialogs confirmed relation history and persisted changes. A subsequent returning-user question resolved the chosen direction and one next action without writes. These establish one multi-record workflow comparison, not full App acceptance or enterprise qualification. Aggregate UI token readings were 300.7k for the proposal and 157.7k for acknowledgment, with cost unavailable; this is not a cost-reduction claim.

Remaining substrate recommendation: deterministic migration rejection repeatedly escaped the work recovery loop while its WorkItem remained running, and the Background Tasks UI showed no lifecycle work. The identity repair allowed this specific job to succeed, but terminal failure classification and lifecycle task visibility need separate generic recovery coverage. Downstream brief-history formatting and stale visible-body presentation remain App/UX findings, not reasons to alter generic routing.

Validation: targeted adapter, operational-model impact and upgrade-safety tests passed. Full `LITELLM_MODE=PRODUCTION make verify` passed (exit 0, `verify: all checks passed`), including staged guards, pre-commit, frontend checks and suite, wheel/import checks, CI-faithful smoke and full backend suite. Log: `/tmp/integral-native-working-context-verify.log`. Standard optional PostgreSQL/unseeded fixture skips remain; this does not qualify every PostgreSQL transaction case. Preserved invariants: I-APP-DEF-01 (canonical contract identity and upgrade authority), backend-authoritative workspace scope, existing policy/staging checks, and I-SUBSTRATE-01/I-EXT-01 (no domain behavior in Core).
