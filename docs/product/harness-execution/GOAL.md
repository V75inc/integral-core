# Integral resident harness V1 goal

Updated by user direction on 2026-10-05. This amendment governs the execution
direction where the earlier plan prescribes unnecessary orchestration or
additional capabilities. It records the revised objective; it does not prove
implementation or release acceptance.

## Objective

Implement a V1 Pydantic AI resident harness in Integral Core on the dedicated
`feat/pydantic-ai-harness-v1` branch, preserving existing work. Its primary
purpose is to fulfil ordinary user requests through Integral's authorized,
standard-compliant skills and substrate tools, with accurate, verified results
and minimal conversation.

Use the straightest supported engineering path: Pydantic AI owns model/tool
iteration and the selected native skill/discovery behavior; a thin Integral
adapter reuses Core's catalog, broker, identity, conversation/session,
execution, approval, credential and usage services. Keep dependency-specific
behavior behind supported adapters and extensions so library upgrades do not
require rewriting Integral's contracts.

The normal flow is **request → discover the relevant capability → load its
skill → execute its tools → verify the result → respond**. Maintain one
coherent capability discovery path, including the requested
`search_capabilities` surface. Ranking provides candidates; the model selects
the appropriate workflow from the request, conversation and observed substrate
state. Avoid lexical intent gates, duplicated discovery, custom agent loops,
and added classifiers or workflow layers without evidence of a necessary gap.

Reuse enterprise requirements as execution boundaries: enforce tenant and
principal isolation and live tool permissions, preserve graph-backed
conversation/session continuity and recovery, honor BYOK, and capture LiteLLM
model, token, cost, timing and outcome facts accurately. Unknown cost must remain
explicit. Discovery and model decisions never grant authority or prove an
effect occurred.

Execute work already authorized by the user's request. When an action requires
approval or a material choice, present one concise, specific proposal and
accept clear ordinary chat or speech approval for that exact pending action.
Persist and validate the authority before execution. Avoid repeated approvals,
offer-then-design-then-confirm cycles, and mandatory approval dialogues for
work the user has already authorized. Preserve explicit design-only requests
and actual action-policy boundaries.

Enable additional harness surfaces only when a demonstrated Integral workflow
requires them. Judge progress by successful user outcomes rather than feature
count, scaffolding, or the amount of code written.

## Recalibrated implementation priorities

1. Establish one resident execution path: Integral admits the scoped request,
   Pydantic AI runs the agent, and Integral's broker executes authorized tools.
   Retire overlapping legacy orchestration from that path as each replacement
   is qualified. Library adoption alone is not evidence of correct integration.
2. Expose a coherent task interface. Capability discovery reveals relevant
   skills and callable tool schemas through supported library mechanisms.
   Compose app setup through its proposal/build contract so implementation
   primitives do not create competing user approval workflows.
3. Resolve approval and recovery at the existing authority boundaries. A
   natural reply approves only the exact persisted pending action. Cancellation,
   failed runs and uncertain effects require accurate readback and reconciliation
   before execution can continue; never replay uncertain writes blindly.
4. Remove unnecessary planning, discovery and approval stages. Retain an
   extension only when a reproduced workflow shows a concrete gap and its
   responsibility cannot be met cleanly by the library or existing Core service.
5. Qualify the complete ordinary-user journey before expanding scope. A saved
   proposal, passing unit tests or a successful isolated tool call does not
   establish that setup, approval, execution and verification work together.

## Findings incorporated into the goal

- The integration must use Pydantic AI and Pydantic AI Harness as the resident
  runtime, with library-owned iteration, tool validation, bounded retries,
  usage limits and supported skill/disclosure mechanisms. Integral supplies
  its existing substrate and authority services through thin adapters. Do not
  retain a competing legacy execution path inside the native run.
- Capability discovery must yield usable skills and callable tools together.
  Loading a skill must make its declared, authorized tools available without
  another discovery cycle. A bounded failure must explain the outcome and
  permit safe continuation; merely stopping a tool loop does not satisfy V1.
- The successful equipment-register build and record readback are partial
  workflow evidence. They do not qualify failed-turn recovery, ordinary chat
  approval of staged records, or final OpenAI compatibility. Close these gaps
  before declaring the resident harness functional.
- Natural approval must apply to the exact pending action through existing
  Integral authority contracts. Remove repeated reviews and unnecessary
  dialogue requirements. Any added interpretation adapter must have a
  demonstrated purpose, reuse the metered model route and confer no broader
  authority than the persisted proposal permits.
- Preserve raw LiteLLM accounting before normalization. Distinguish reported
  cost, estimated cost and unknown cost; an unpriced SDK placeholder must not
  become a false zero. Qualify cost readback and tenant/BYOK ownership across
  the actual native model routes, including OpenAI and GLM cloud.
- Keep dependency adaptations behind public extension points. Review each
  additional layer for a concrete responsibility; remove it when the library
  or an existing Core contract already provides that responsibility. Future
  surfaces are outside the V1 critical path unless required for an ordinary
  Integral user workflow.

## Evidence required

- Independently validate each integration slice through the applicable
  repository gates; satisfy the commit gates before committing.
- Run real browser smoke tests in every build/test round and for every major
  feature, against the exact branch runtime and configuration being evaluated.
- Act as a lay user: use ordinary requests without skill/tool names, hidden
  implementation instructions, or coaching that masks routing failures.
- Cover existing-record work, missing destination/setup, scaffold delivery,
  one natural approval, explicit design-only work, refusal/cancellation,
  continuation/recovery, and visible model/usage/cost metadata across the
  qualified model routes, including OpenAI and Ollama `glm-5.3:cloud`.
- Verify persisted effects and receipts, record failures and unresolved
  outcomes, and distinguish source tests, browser behavior and release evidence.
- Do not call V1 functional or complete until ordinary user requests produce
  correct verified results with minimal conversation and all retained
  enterprise requirements have authoritative evidence.
