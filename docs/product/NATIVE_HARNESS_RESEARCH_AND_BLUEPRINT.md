# Native Harness Research and Blueprint

**Status:** Recommendation for architect review

**Date:** 2026-10-04

**Scope:** Python-native alternative to, or successor for, jvagent as Integral's resident harness.

**Implementation follow-up:** [Pydantic AI implementation plan](PYDANTIC_AI_HARNESS_IMPLEMENTATION_PLAN.md) reconciles this initial assessment with the embedded streaming provider and existing PostgreSQL work kernel. Its implementation decisions take precedence over exploratory infrastructure choices below.

## Executive recommendation

Implement an **Integral-owned resident binding by composing Pydantic AI and Pydantic AI Harness** behind the existing provider contract. Adopt upstream planning, discovery, context management, skills loading, persistence, instrumentation and runtime controls through tenant-scoped Integral adapters. Preserve the existing PostgreSQL work kernel, broker, graph conversations, approval authority and usage facts. Integral AI is the default binding; retain jvagent as an explicitly selectable compatibility harness.

This revision supersedes the initial recommendation to use Pydantic AI primarily as a model/tool protocol layer with a custom Integral loop and a new durability engine. The [revision 2 implementation plan](PYDANTIC_AI_HARNESS_IMPLEMENTATION_PLAN.md) contains the authoritative adoption matrix, dependencies, state/store boundaries and evidence gates. Custom loop behavior and additional workflow engines need a demonstrated gap.

LangGraph remains a comparative option if a bounded workflow requirement cannot be met by the composed harness and current work kernel; it is not a planned dependency. Integral retains its provider-neutral authority contracts while Pydantic supplies substantially more native harness behavior.

## What Integral already owns

The architecture should preserve the separation in `RESIDENT_HARNESS.md` and ADR-003:

- Integral owns principal/facet identity, workspace scope, policy evaluation, capability catalogues, tool dispatch, staging and approval, idempotency, receipts, provenance, MCP parity, routines, and memory policy.
- The harness owns model interaction, bounded reasoning/tool selection, streaming, and provider-session adaptation.
- External harnesses remain valid providers through the same Integral contracts.

This division is already emerging in code. `services/execution_runs.py` persists `AgentRun` and `RunStep` records and immutable capability snapshots. `tooling/dispatch.py` gates tool execution and can require a repair tool after specific refusals. The primary chat path is embedded and streaming through `services/chat_providers/jvagent_provider.py`; a legacy HTTP connector also exists. The Orchestrator still owns model-loop decisions, while Core owns governance and receipts. The implementation plan extends the existing provider protocol and work kernel rather than replacing a supposed HTTP-only primary path.

## Assessment of jvagent

### Strengths to retain as requirements

- **Production-shaped application harness:** it packages a server, multi-tenant conversation model, per-user memory, channels, plugin lifecycle, and model loop. Replacing it means taking responsibility for these integration concerns.
- **Python and graph fit:** its object-spatial model and existing jvspatial integration align naturally with the current Core stack.
- **Useful loop simplicity:** the published design describes a continuation check followed by a bounded think-act-observe loop. A small loop is understandable and can work well for straightforward tasks.
- **Skills and tool ecosystem:** YAML app/agent declarations, action plugins, and Markdown skills provide a pragmatic authoring surface. Integral already has a substantial investment in these overlays.
- **Existing deployment value:** jvagent has accumulated compatibility work and operational knowledge, so keep it selectable during the native harness rollout.

### Gaps and risks for Integral's target

These are architecture-level risks supported by the current integration and published design, not a claim that every jvagent run fails:

1. **The loop is a bounded generic tool cycle.** The published Orchestrator is intentionally one-loop and thin. That is easy to extend with tools, but it does not by itself provide explicit plan state, progress criteria, evidence tracking, recovery branches, or task-specific control transitions. Integral has added repair constraints around the loop, which is evidence that important behavior is now being enforced around it.
2. **Reasoning continuity remains provider-specific.** The primary embedded adapter already exposes streaming events, and Core records capability execution. The remaining problem is stable, Integral-owned model-loop checkpoints and recovery across provider/session changes. The legacy HTTP connector is coarser, but it is not the primary migration target.
3. **Execution reliability is not the same as reasoning efficacy.** A bounded loop and retries help contain a run; they do not ensure the agent decomposes a complex request well, notices missing evidence, validates outcomes, or recovers intelligently. These require explicit control policies plus task-level evaluations.
4. **Runtime coupling is material.** Workspace skills are converted into jvagent `SkillDoc` objects, tool namespace rules include jvagent assumptions, and staging contains compatibility logic for jvagent history and transcript closure. Replacing it is not just changing a model caller.
5. **The current model adapter constrains provider choice.** `backend/pyproject.toml` describes LiteLLM as jvagent's only model backend. That simplifies deployment but makes provider-specific capabilities and model API evolution less directly controlled by Integral.
6. **Documentation and implementation need reconciliation.** The harness spec has periodically described capabilities differently from the live manifest and implementation. A replacement project must start with a measured capability and behavior inventory, not assume the specification is exact.

### What not to conclude

The current evidence does not establish that jvagent's basic loop is categorically inferior on model quality, latency, or cost. Those are empirical questions. The supported conclusion is narrower: the current Integral boundary does not give Core enough control over loop state and recovery to meet the ambition of a highly effective resident without substantial additional harness-owned machinery.

## Python harness comparison

| Candidate | Best fit | Strengths for Integral | Main concern | Recommendation |
|---|---|---|---|---|
| **Pydantic AI + Harness** | Composable native resident embedded in Integral | Python-first typed loop plus planning, skills/discovery, context, memory, persistence, instrumentation and controls | Tenant/graph/work authority and billing still need Integral adapters; Harness 0.x APIs and selected extras require qualification | **Recommended composed harness; use broader surfaces through Core contracts** |
| **LangGraph** | Explicit stateful workflow and human-review graphs | Strong checkpoint/store separation, interrupts and resumability; clear state transitions; durable thread-scoped execution | More orchestration machinery than a singular conversational loop needs; can tempt business flow logic into a framework-specific graph; the persistence layer requires a deliberate storage/retention contract | **Keep as a targeted option for long-running deterministic workflows** |
| **OpenAI Agents SDK** | OpenAI-oriented agent runtime with tools, handoffs, guardrails and tracing | Small Python surface; built-in loop, sessions, tracing and MCP integration | Provider/runtime coupling is at odds with a first-party, model-neutral Integral harness; its multi-agent handoff emphasis is not Integral's singular-resident model | **Useful provider adapter or benchmark baseline, not default kernel** |
| **Microsoft Agent Framework** | Enterprise workflow orchestration across Python/.NET | Strong workflow abstractions, integration with Microsoft's agent ecosystem | Larger ecosystem commitment; Python workflow surface includes experimental APIs; does not map as cleanly to a lean Integral-owned loop | **Monitor; not first choice** |
| **Keep/extend jvagent** | Existing server, channel, graph-memory and plugin harness | Lowest near-term migration cost; full deployment harness already exists | Continues coupling loop and Integral run governance across a coarse provider boundary; changes require jvagent and Integral coordination | **Maintain as baseline and fallback during pilot** |

### Why Pydantic AI wins for this design

The project needs Pythonic composition, typed tool contracts, provider choice and a capable governed loop. Pydantic AI Harness supplies reusable behavior across those needs. Its composable capabilities let Integral retain graph and operational authority without rebuilding every harness feature. The benefit must be established with the capability proof and paired evaluations, not inferred from the catalogue.

LangGraph is stronger where workflow topology itself is the problem: explicit branches, checkpoints, interrupts, and recovery from a particular state. For Integral, that should be an optional execution mode for explicit work plans and durable routines, not mandatory overhead on every chat turn.

## Proposed blueprint: Integral Native Harness Runtime

### 1. Stable contracts

Define provider-neutral internal protocols under `backend/app/agentive/runtime/`:

- `HarnessProvider`: `stream_turn(request) -> AsyncIterator[HarnessEvent]`, `resume_turn(run_id, input)`, `cancel_turn(run_id)`.
- `ModelAdapter`: model selection, request/response normalization, usage/cost metadata, timeout and retry classification.
- `CapabilityResolver`: builds the authorized, versioned tool set for the current principal, workspace, app focus, and run snapshot.
- `ToolInvoker`: invokes only through Integral's current capability broker and dispatch path.
- `RunStore` / `CheckpointStore`: Integral records are authoritative; framework checkpoints are opaque implementation state referenced by `AgentRun`.
- `HarnessEvent`: typed events for run started, assistant delta, plan/progress update, tool proposed, tool started, tool result, approval required, checkpoint saved, warning, terminal result.

Provider request context should be immutable for a turn and include the authenticated principal, facet, workspace, app/track focus, locale, current capability snapshot ID, and a budget/deadline. No provider-supplied field may widen that context.

### 2. Turn controller

Use composed upstream lifecycle capabilities with a thin Integral coordinator. The following are required behavioral phases, not instructions to implement a duplicate state machine:

1. **Resolve:** validate identity and workspace scope; load conversation and current workspace/app skill overlay; resolve a versioned capability snapshot.
2. **Understand:** classify task shape, missing information, risk, and likely completion criteria. Ask the user when required information is absent; avoid tool calls when a direct answer suffices.
3. **Plan:** for nontrivial requests, produce a compact typed plan with success criteria, evidence needed, and allowed capability categories. Simple requests skip visible planning.
4. **Act:** request one or a small safe batch of tool calls; revalidate every call against the immutable run snapshot and live policy; dispatch through Integral. Writes remain staged unless the policy explicitly allows direct execution.
5. **Observe:** consume structured results, distinguish success/failure/partial results, verify important postconditions with read tools, and update completion criteria.
6. **Repair or ask:** retry only when the error is classified as recoverable and budget remains; otherwise request user input or stop with a truthful explanation.
7. **Finalize:** emit a concise response tied to evidence and receipts; persist usage, trace references, outcome, and the next resumable state if incomplete.

Bounded budgets are explicit: wall-clock deadline, model calls, tool calls, output tokens, spend ceiling, retry count, and maximum parallel tools. Budgets are policy inputs, not prompt suggestions.

### 3. Control plane and safety invariants

- **Integral remains authority:** no framework tool calls Core services directly. Every effect goes through the capability broker, policy gate, staging/approval system, idempotency boundary, and audit receipt.
- **Revalidate on resume:** principal, workspace membership, capability version, and policy are checked again when a paused run resumes. Checkpoints never preserve authorization.
- **Replay-safe effect semantics:** internal transactional operations use stable logical effect keys and receipts; uncertain external outcomes require reconciliation or suspension. No blanket exactly-once external-effect promise.
- **Separate three kinds of state:** conversation transcript; resumable orchestration checkpoint; durable substrate facts/memory. Do not let framework transcript persistence become a second source of truth.
- **Version everything that affects a run:** prompt/skill version, model/provider, tool manifest and capability snapshot, controller version, and checkpoint schema.
- **Failure is a product state:** cancellation, timeout, budget exhaustion, provider error, policy denial, waiting for approval, and successful completion are distinct terminal or suspended outcomes.
- **No hidden agent fleet:** one resident per active binding remains. Internal plan steps and deterministic services are not independent peer agents.

### 4. Skills, memory, streaming, and MCP

- Keep Integral skill documents declarative and provider-neutral. Build adapters from the current workspace overlay to Pydantic AI instructions/toolsets; do not persist framework objects.
- Treat long-term memory as governed Integral knowledge (scratch-memory and promotion flow). Short-term working summaries are run artifacts with explicit provenance and retention.
- Normalize streaming to Integral's typed `HarnessEvent` stream. Preserve event IDs and run/step correlation across reconnects.
- Build the same capability descriptions for resident tools and MCP from the canonical manifest/broker. MCP and resident calls must share authorization and receipt logic.
- Adopt upstream context strategies with an Integral protected-state adapter: summarize completed work with provenance, retain unresolved criteria and relevant tool receipts, and never discard pending approvals or failed-step details.

### 5. Observability and evaluation

Record a redacted trace with: run ID, model/provider, prompt and skill versions, capability snapshot, model request/response metadata, selected plan and completion criteria, tool calls/results, policy decisions, approval waits, retries, token/cost/latency, termination reason, and verified postconditions. Do not persist hidden chain-of-thought; persist concise rationale/evidence summaries and operational decisions.

Build a fixed evaluation set from actual Integral tasks before choosing a winner. Include:

- simple conversational questions;
- ambiguous requests where it must clarify;
- multi-step schema and knowledge work;
- retrieval where evidence is absent, contradictory, or permission restricted;
- invalid tool calls requiring repair;
- staged writes, approval pause/resume, revocation, and policy changes during suspension;
- transient provider/tool failures, duplicate delivery, cancellation, and restart recovery;
- long history/context pressure and app/workspace skill overlays;
- MCP/resident parity.

Score task completion, evidence correctness, unnecessary tool calls, policy/staging violations, recovery rate, truthful failure handling, p50/p95 latency, cost, and resumability. Security and unauthorized-effect violations are hard failures, not weighted tradeoffs. Compare jvagent and the candidate on identical task inputs, models where possible, and tool snapshots.

## Migration path

### Phase 0 — baseline and contract inventory

Collect representative conversations and traces; inventory actual jvagent capabilities, prompt/skill behavior, tool call formats, streaming events, session IDs, retries, cancellation, and history semantics. Resolve spec-versus-code discrepancies. Freeze the evaluation corpus and baseline metrics.

### Phase 1 — provider-neutral boundary

Extend the existing `ChatBackendProvider` protocol and registry rather than promoting the legacy HTTP connector. Normalize event streaming, errors, cancellation, and resume semantics. Adapt jvagent without changing its loop. Ensure `AgentRun`/`RunStep` records link to provider run/session references.

### Phase 2 — native-runtime pilot

Implement one Pydantic AI-based runtime with the same Core tool catalogue and skills overlay. Start with read-only requests and staged-proposal flows. Run shadow or user-selected pilot traffic; do not dual-execute effects. Compare traces and evaluation outcomes to jvagent.

### Phase 3 — durable work and approval resume

Add checkpoint/restart for in-flight turns, human approval waits, cancellation and revocation revalidation. Use Harness StepPersistence through a custom jvspatial StepStore with the current WorkItem kernel; an additional engine requires demonstrated inability to meet a required recovery behavior. Keep checkpoint payloads minimal and encrypted/retained under explicit policy.

### Phase 4 — controlled default switch

Make the binding selectable per deployment and then per opted-in workspace. Define rollback using existing provider/session IDs and transcript compatibility. Retire jvagent dependencies only after parity, migration, and operational evidence show no remaining required surface.

## Decision gates

Do not decide on framework popularity. Advance only if the pilot demonstrates:

1. Better task completion and evidence-grounded answers on the frozen suite.
2. No regression in Integral policy, staging, idempotency, and audit invariants.
3. Recoverable runs across process restart and approval waits with no duplicate side effects.
4. Equivalent or better tool/MCP parity and skill-overlay behavior.
5. Acceptable p95 latency, operating cost, developer burden, and trace usability.

First evaluate the broader Harness planning, repair, context and persistence capabilities through Integral adapters. If a required behavior still fails, record a bounded gap and compare an upstream extension, local adapter or targeted alternative. Preserve the provider protocol and independent Core contracts through that decision; do not add a custom loop or workflow engine by default.

## Source notes

Repository evidence:

- `backend/pyproject.toml`: pins `jvagent==0.1.8rc20` and describes LiteLLM as jvagent's only model backend.
- `backend/app/services/chat_providers/jvagent_provider.py`: primary embedded streaming path with legacy HTTP fallback; `backend/app/agentive/connectors/jvagent_connector.py`: separate legacy HTTP interaction connector.
- `backend/app/agentive/services/execution_runs.py`: Core-owned `AgentRun`, `RunStep`, and capability snapshots.
- `backend/app/agentive/tooling/dispatch.py`: Core tool policy enforcement and repair-required results.
- `backend/app/agentive/AGENTS.md`, `docs/product/RESIDENT_HARNESS.md`, and `docs/backend/adr/003-singular-resident-harness.md`: singular resident, ops-layer ownership, staging, MCP, proactivity, and known gaps.

External primary sources:

- [jvagent repository and architecture overview](https://github.com/TrueSelph/jvagent)
- [Pydantic AI Harness](https://pydantic.dev/docs/ai/harness/)
- [Pydantic AI overview](https://pydantic.dev/docs/ai/overview/)
- [Pydantic AI durable execution](https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/)
- [Pydantic AI model/provider integrations](https://pydantic.dev/docs/ai/models/overview/)
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/)
- [Microsoft Agent Framework workflows](https://learn.microsoft.com/en-us/agent-framework/concepts/workflows/functional)

Framework features and availability change quickly. Recheck package versions, API stability, licensing, provider support, and deployment requirements during Phase 0/1 before pinning any new dependency.
