# WP-00.4 — Adoption decision evidence

## Decision

Integral Core adopts Pydantic AI Harness as the opt-in V1 native runtime, using
the exact locked `pydantic-ai-harness[skills]==0.30.0` release and resolved
Pydantic AI Slim 2.54.0 API. LiteLLM SDK 1.101.x remains the sole model
transport. The native provider does not become default during this work; the
existing jvagent path remains available as rollback and as the current
default unless a later, explicitly authorized decision changes it.

This selects the broader useful Harness surfaces already composed in
`backend/app/agentive/harness/runtime.py`: Instrumentation, ToolSearch,
Planning, Skills, and StepPersistence. They are constructed per invocation
with Core-owned broker tools, tenant-scoped encrypted persistence, and
permission-filtered skill sources. Harness does not become an authority layer
for Integral. Integral remains authoritative for identity, tenant fences,
tool policy, approval, admission, usage facts, and transcript persistence.

## Evidence reviewed

- WP-00.1 inventories the existing provider, conversation, work, accounting,
  skill, and observability boundaries and names the missing native surfaces.
- WP-00.2 proves the selected upstream capabilities compose against TestModel
  and that deferred tool calls can be continued in a fresh invocation; it
  explicitly does not prove tenant security or durable approval.
- WP-00.3 rejects stock direct provider routing for production because it
  would bypass Integral's LiteLLM SDK path. The custom LiteLLM SDK transport
  and request observations now exist in `litellm_model.py` and
  `model_observations.py`, but no paid/provider-backed route was exercised.
- The current lock pins Harness 0.30.0 and LiteLLM 1.101.4. Current installed
  metadata reports MIT for Harness 0.30.0, Pydantic AI Slim 2.54.0, and
  LiteLLM 1.101.4. No transitive license inventory is claimed.
- WP-06 has a browser exact-answer smoke receipt. WP-09 has PostgreSQL store,
  recovery, corruption, key-overlap, and partial-sweep-resume tests. These
  are slice receipts, not full V1 acceptance.

## Surface decisions

| Surface | V1 decision | Qualification owner |
|---|---|---|
| Planning, ToolSearch, Skills, StepPersistence, Instrumentation | Adopt through fresh per-run composition and Core adapters | WP-06/07/11 |
| Model transport | LiteLLM SDK adapter only; no direct provider SDK bypass | WP-04/05/12 |
| Brokered operations | Core broker remains final authorization/effect boundary | WP-06/08 |
| Deferred approval / Ask User | Do not expose until durable fresh-invocation continuation works | WP-08/09/10 |
| Memory, compaction, spill, private skill resources | Defer until Core scope, revocation, and accounting adapters qualify | WP-07/11/13 |
| Code Mode, sandbox, SubAgents, voice, provider-native shortcuts | Disabled/unadvertised unless separately qualified and approved | WP-13/14 |
| Business paywall and commercial plans | Outside Core; use generic admission and opaque attribution contracts | WP-12 plus Business assignment |

## Calibrated remaining execution path

The current ledger has foundational contracts and encrypted session/checkpoint
work, but it is not an acceptance ledger for the complete coding-agent plan.
WP-00.3 is accepted for dependency dispatch based on the adapter/route tests
and local browser path; it keeps credentialed paid routes unqualified. WP-03 durable submission/admission,
WP-05.3 reconciliation evidence, and WP-07 context/skill controls remain
unfinished before dependent WP-08 and WP-10 tasks. WP-12 admission is also a
required dependency for promoting the native provider. Later WPs remain mandatory for
observability, commercial analytics contracts, external clients, package
migration/rollback, efficacy, security, and release qualification.

## Unresolved proof requirements

- Supported Python-version matrix and full transitive license/SBOM review.
- Credentialed provider request, physical retry/fallback identity, cancellation
  ambiguity, and provider usage/cost reconciliation under a separately
  approved budget.
- Independent efficacy and cost corpus with frozen model/configuration.
- Full browser continuity, tenant-isolation, approval, refresh/reconnect, and
  export/retention evidence.
- A clean artifact install and full repository gates with no unexplained
  failures before any release/default decision.
