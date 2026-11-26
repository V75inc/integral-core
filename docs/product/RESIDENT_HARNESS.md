# The resident harness

Integral hosts one resident mind per active binding, faceted by principal and scope. The default binding is Integral AI through Pydantic AI. Integral AI is the only resident harness; There is no harness selector.

The native resident is always registered, including when no model credentials have been configured yet. First-run model setup selects a model provider, not a different harness. There is no jvagent runtime, dependency, embedded HTTP surface, or selectable compatibility binding. Stored frontend settings that name a removed provider are normalized to Integral AI.

## Division of responsibility

The harness owns model interaction and reasoning-loop mechanics. Core owns authenticated execution scope, capabilities, policy, staging, effect boundaries, product transcripts, session records, and immutable usage observations. A model output or provider handle cannot select identity or tenancy.

Native workspace chat and browser voice share the caller’s **Permissions** setting. Core stages the proposal and checks the policy revision bound to the turn before applying an eligible record edit. Switching modes preserves the caller’s Custom selections. App access, effect validation and execution receipts remain the responsibility of the canonical writer. See the [resident permission modes](../backend/voice-chat-conversation-contract.md#resident-permissions).

## Harness engineering principle

Use the Pydantic AI Harness and Pydantic AI as fully as practical for the resident agent's runtime behavior. Before implementing or extending a custom harness mechanism, inspect the supported facilities and public extension points in the pinned library versions and prefer them when they meet Integral's requirements. Custom code belongs at Integral's substrate integration boundary or fills a documented gap where the libraries do not provide a suitable facility.

Review existing custom behavior against this principle as it is touched; do not remove safeguards merely because an upstream feature has a similar name. Verify functional and security parity, including authenticated scope, current policy, staging, effect receipts and recovery, before replacement. Discovery and model-provider optimizations remain subject to the same boundary.

Native model routing resolves saved profile and single/dual-model settings
for each turn. Local demo launchers must use that resolver.
The reviewed OpenAI Sol models requiring Responses for function calls use
LiteLLM's supported model registration and Responses bridge, with native
streaming metadata. This fills a gap in the pinned SDK's model table that
otherwise rejects tool choice or fabricates duplicate streamed arguments.
Integral retains the original model identity, dispatch admission, credentials,
usage observations and effect boundaries. Custom endpoints retain their
Chat Completions route. These metadata updates preserve existing pricing
and do not choose a different model. [OpenAI model contract](https://developers.openai.com/api/docs/models/gpt-6.1-sol)

The agentive layer is always loaded. `AGENTIVE_ENABLED` is not a supported kill switch. Core-only mode filters domain packages; it does not remove resident intelligence.

## Context and skills

Current workspace, thread, binding, and generation define execution. App instructions appear only from active accessible Apps. Skill descriptions support discovery and bodies load progressively. They do not confer permission.

Record requests use the App's current fields and allowed values. If a caller's
wording could mean several values, the resident asks for clarification. A
request for one record does not itself authorize proposing a new field or a
new choice in the App's schema. This applies to typed and delegated voice work.

Principal facets express personal, organization-facing, and system contexts without a peer-agent fleet. Wider facet experience and cross-client matrices require qualification; the architectural discriminator alone is not a finished product journey.

## Continuity and effects

HarnessSession attaches to ChatThread and shares its authorized namespace. Product messages, encrypted execution records, checkpoints, effects, traces, and usage facts have separate duties. Provider handles remain opaque.

Native chat uses durable WorkItems by default on PostgreSQL. Accepted request fingerprints, worker input, event replay, stable transcript writes, lease fencing, cancellation, and transcript convergence are required for every turn. Current access applies to resumed tools and approval execution. Legacy request-bound turns remain an explicit opt-out only.

## External participants

External agents use MCP under the same relevant policy and scope perimeter. There is no A2A delegation fabric. Outbound MCP connectors expose external tools through their own declared trust boundary.

Read [chat lifecycle](../backend/ai-chat.md), [BYOA](BYOA.md), [work mandates](../backend/work-mandate-contract.md), and [qualification](../ops/QUALIFICATION.md).

### Bounded working context

The resident follows I-CONTEXT-02: long-lived encrypted execution evidence is
separate from the bounded context submitted to a model. Native capabilities
continue to own execution, disclosure, summarization and checkpointing. Core's
retention policy expires old disclosures at settled step boundaries, preserves
current work and pending effects, and admits the final request against the
selected model's usable budget. Expired procedures can be rediscovered. Existing
sessions normalize on valid resume; no chat deletion is required. See the
[working-context contract](../backend/ai-chat.md#working-context-and-long-conversations) and the
[qualification report](../ops/qualification/harness-context-lifecycle.md).
