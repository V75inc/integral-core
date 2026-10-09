# The resident harness

Integral hosts one resident mind per active binding, faceted by principal and scope. The default binding is Integral AI through Pydantic AI. jvagent is a selectable compatibility harness; Echo is for development and smoke checks.

## Division of responsibility

The harness owns model interaction and reasoning-loop mechanics. Core owns authenticated execution scope, capabilities, policy, staging, effect boundaries, product transcripts, session records, and immutable usage observations. A model output or provider handle cannot select identity or tenancy.

The agentive layer is always loaded. `AGENTIVE_ENABLED` is not a supported kill switch. Core-only mode filters domain packages; it does not remove resident intelligence.

## Context and skills

Current workspace, thread, binding, and generation define execution. App instructions appear only from active accessible Apps. Skill descriptions support discovery and bodies load progressively. They do not confer permission.

Principal facets express personal, organization-facing, and system contexts without a peer-agent fleet. Wider facet experience and cross-client matrices require qualification; the architectural discriminator alone is not a finished product journey.

## Continuity and effects

HarnessSession attaches to ChatThread and shares its authorized namespace. Product messages, encrypted execution records, checkpoints, effects, traces, and usage facts have separate duties. Provider handles remain opaque.

Native durable chat is off by default. Enabled worker paths require fingerprint validation, lease fencing, replay, cancellation, and transcript convergence proof. Current access applies to resumed tools and approval execution.

## External participants

External agents use MCP under the same relevant policy and scope perimeter. There is no A2A delegation fabric. Outbound MCP connectors expose external tools through their own declared trust boundary.

Read [chat lifecycle](../backend/ai-chat.md), [BYOA](BYOA.md), [work mandates](../backend/work-mandate-contract.md), and [qualification](../ops/QUALIFICATION.md).
