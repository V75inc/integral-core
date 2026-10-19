# Native turn progress and graceful completion

## Library integration

| Responsibility | Mechanism |
| --- | --- |
| Execution, accounting, hard limits | Pydantic AI Agent and UsageLimits |
| Advance usage warnings | Harness WarnNearLimits, configured from the live allowance |
| Deferred discovery and skills | Harness ToolSearch and Skills |
| History reduction | Harness ClearToolResults and SummarizingCompaction, with Core's existing evidence-preserving projections |
| Scoped durable run history | Harness StepPersistence and Core's scoped store |
| Live authority, effects and approvals | Integral capability broker |
| Detecting repeated evidence and requesting a grounded finish | Small invocation-local Integral policy using capability/model settings hooks |

The warning adapter renders upstream usage warnings as dynamic instructions.
It does not insert apparent user turns or let a warning marker remove real user
text. No framework source is patched and no replacement executor is introduced.

The upstream ToolOutputLimits default spills payloads to a process-local file
store whose handles can be read in later runs. That is not a tenant-authorized
readback contract, so it is not enabled blindly. Likewise, a second-model
TrajectoryJudge adds model usage and has durable-execution restrictions; it is
not necessary for the deterministic repeated-evidence checks here. Active-turn
payload sizing and missing App query contracts remain separate concerns.

## Contract

The host reserves a tool-free answer before request, aggregate token, or tool
call limits are exhausted. It also requests an answer when three equivalent
reads return the same evidence, four consecutive tools fail, or four discovery
rounds expose no new callable tool or active skill. These are invocation-local
signals, independent of the user's wording and domain.

Observations occur after the normal live broker call. They never cache a result,
grant access, retry an effect, or replay a write. Writes invalidate read
fingerprints. Changing pagination arguments or returned evidence is productive.
Only hashes of read arguments/results are retained in memory; receipt identities
do not count as new evidence. Progress state is not checkpointed or shared
between runs, principals, or workspaces.

The model is instructed to answer from verified results, identify the precise
missing capability/evidence, and preserve the distinction between staged,
applied, and verified changes. It must not infer an actor from a modification
timestamp or enumerate unrelated records to work around a missing contract.
Completion validators stop requesting additional tools when finalizing and
return factual unfinished proposal/build status from host receipts.

If the provider proposes a batch larger than its remaining tool allowance,
Core suppresses the entire batch before dispatch. When another response fits,
Pydantic AI's ModelRetry requests one corrective, tool-free answer grounded in
earlier results. If the provider ignores tool-free choice, or no request remains,
Core returns a fixed, content-free unfinished response. No partial batch is
executed. The existing hard framework limits remain defense in depth.

## Qualification

Generic FunctionModel regressions cover repeated reads/refusals, pagination,
changing data, uncached writes, independent runs, disclosure progress, aggregate
token headroom, reserved tool calls, oversized batches, provider noncompliance,
and completion-validator behavior. No production query or private record is
embedded in a regression test. The broker authority seam is explicitly mocked;
actual function-tool dispatch and Pydantic AI usage enforcement are exercised.

This change controls execution and completion. It cannot create a missing App
read contract or prove an answer when the available tools lack the needed data.
Token reserve is an estimate using the latest measured input size, not a
guarantee against an unexpectedly oversized provider response. Live provider
quality and deployment qualification remain separate from deterministic tests.

## Local results

- Frozen-source default backend suite (`TESTING=1 DEBUG=false`, six workers):
  **6,936 passed, 952 skipped**, 16 warnings. Default domain/slow exclusions and
  optional PostgreSQL/deployment gates remain unchanged.
- Latest native harness suite after the batch-handoff refinement:
  **779 passed, 54 PostgreSQL-only skips**, five warnings.
- Generic progress plus existing context-budget regressions: **27 passed**;
  the final batch refinement also passed all **23 progress cases**.
- CI-faithful smoke lane (`make verify-ci`): passed.
- Staged source guards and full pre-commit framework: passed, including
  formatting, flake8 and mypy across 767 source files.

These are deterministic local checks. No live paid-provider replay or production
deployment was performed for this follow-up.
