# WP-09.2 — Fresh-invocation recovery and reconciliation

## Objective

Restore a prior safe checkpoint or reconstruct a user turn that was persisted
but never dispatched, while refusing ambiguous paid requests and side effects.

## Inputs and boundaries

- Plan: `docs/product/PYDANTIC_AI_HARNESS_IMPLEMENTATION_PLAN.md`, WP-09.
- Existing owner: `PydanticAIProvider._resume_history`, encrypted
  model-request observations, Core Harness checkpoint manifests, and scoped
  `AgentRun`/`ChatMessage` records.
- Core remains the authority for execution scope and tool effects.
- Message reconstruction accepts a single, scoped text-only user message.
- Prior tool effects, pending approvals, or any physical model dispatch prevent
  turn reconstruction; unknown model outcomes retain the more specific
  unsettled-request conflict.
- A present but invalid safe-checkpoint pointer is a hard stop. It cannot be
  bypassed by looking for a newer snapshot.
- This task does not implement cross-process approval transfer or
  kill-at-every-boundary qualification.

## Acceptance for this slice

- Dispatch intent or unknown provider outcome blocks checkpoint loading.
- Equal persisted timestamps resolve conservatively in favor of uncertainty.
- Known terminal provider outcomes are not considered unsettled.
- Unresolved tool effects block checkpoint loading.
- Every persisted chat turn is linked from its Core `AgentRun` without copying
  prompt content into run metadata.
- A previous text-only turn can be rebuilt only when the prior run has no model
  request, tool effect, or pending approval.
- The gate is verified with deterministic tests; no model request is issued.

## Verification command

```sh
cd backend
.venv/bin/python -m pytest tests/native_harness/wp_05 tests/native_harness/wp_06 tests/native_harness/wp_09 -o addopts='' --strict-markers -q
```

## Handoff

Implemented as the first safe fresh-invocation recovery slice. WP-09 remains
open for cross-process approval continuation, kill-at-boundary and two-store
divergence qualification, corrupt codec and key-rotation proof, and additional
browser-visible provider qualification.
