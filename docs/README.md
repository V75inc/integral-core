# Integral documentation

Central index for **stable, maintained** documentation.

## Documentation lanes

| Lane | Location | Use when |
|------|----------|----------|
| Onboarding | [README.md](../README.md) | First run (Docker, source, or a released wheel with `integral init` / `integral web`), monorepo map, env vars |
| Product strategy | [product/](product/) | Vision, requirements, architecture, roadmap, BYOA |
| Technical reference | **`docs/`** (substrate, backend, ops) | Implementing features, authoring profiles |
| Conventions (agents) | [AGENTS.md](../AGENTS.md) | jvspatial patterns, hooks, commands |

Agent/GSD phase artifacts are **gitignored** and are not part of published repo documentation.

## Product ([product/](product/))

| Doc | Purpose |
|-----|---------|
| [product/CONCEPT.md](product/CONCEPT.md) | Product vision |
| [product/PRD.md](product/PRD.md) | Requirements, personas, epics |
| [product/ARCHITECTURE.md](product/ARCHITECTURE.md) | System design, data model, access model (§9) |
| [product/RESIDENT_HARNESS.md](product/RESIDENT_HARNESS.md) | Singular resident harness spec ([ADR-003](backend/adr/003-singular-resident-harness.md)) |
| [product/ROADMAP.md](product/ROADMAP.md) | Milestone sequencing |
| [product/CORE_FINISH_STATUS.md](product/CORE_FINISH_STATUS.md) | Current finish-state status: implemented work, unproven gates, and ordered remaining program |
| [product/CORE_ACCEPTANCE_LEDGER.md](product/CORE_ACCEPTANCE_LEDGER.md) | Candidate-specific qualification gates, evidence, and limitations |
| [product/FOUNDATION_EXTENSION_SAAS.md](product/FOUNDATION_EXTENSION_SAAS.md) | Foundation-first reframe: open-core boundary, extension contract, SaaS entitlements |
| [product/FOUNDATION_PUBLIC_DEVELOPER_SPRINT.md](product/FOUNDATION_PUBLIC_DEVELOPER_SPRINT.md) | Next sprint: coding-agent work packages for the public extension platform and independent Asset Register proof |
| [product/HARNESS_RUNTIME_SUBSTRATE_GAP_PLAN.md](product/HARNESS_RUNTIME_SUBSTRATE_GAP_PLAN.md) | Current assessment and coding-agent closure plan for a harness-backed runtime substrate with intrinsic agentive queryability |
| [product/INTEGRAL_CORE_EXTRACT.md](product/INTEGRAL_CORE_EXTRACT.md) | What ships in public integral-core vs commercial packages/apps |
| [product/CORE_PIN.md](product/CORE_PIN.md) | Commercial dependency-pin runbook for Core releases |
| [product/BYOA.md](product/BYOA.md) | External-agent surface — MCP only |

## Substrate and platform

| Doc | Purpose |
|-----|---------|
| [INVARIANTS.md](INVARIANTS.md) | Graph contiguousness, edge naming, substrate contracts |
| [platform/extension-contract-v1.md](platform/extension-contract-v1.md) | F0 App extension contract (ToolContext, hooks, lifecycle) |
| [developer/quickstart.md](developer/quickstart.md) | Example-led guide to author a custom Integral App, including the distro `agent.override.yaml` |
| [platform/extension-contract-governance.md](platform/extension-contract-governance.md) | Semver / deprecation stub for the extension contract |
| [operational-models/README.md](operational-models/README.md) | Operational Model vocabulary and authoring path |
| [operational-models/README.md](operational-models/README.md) | Technical OperationalModel implementation reference |
| [platform/operational-model.md](platform/operational-model.md) | Operational Model overview and learning path (compatibility filename) |

### Operational Model technical deep dives

| Doc | Topic |
|-----|--------|
| [operational-models/VIEW_PALETTE.md](operational-models/VIEW_PALETTE.md) | View types and contracts |
| [operational-models/REGION_SYSTEM.md](operational-models/REGION_SYSTEM.md) | Apex-style region widgets + create_wizard step kinds |
| [operational-models/AGENT_CONTRACT.md](operational-models/AGENT_CONTRACT.md) | Agent MCP tools for models |
| [operational-models/DRAFT_PUBLISH.md](operational-models/DRAFT_PUBLISH.md) | Draft/publish lifecycle |
| [operational-models/MIGRATIONS.md](operational-models/MIGRATIONS.md) | Schema migrations |
| [operational-models/COMPOSITES.md](operational-models/COMPOSITES.md) | Composite field types |
| [operational-models/PLUGINS.md](operational-models/PLUGINS.md) | Signed code plugins |
| [operational-models/META_WIDGETS.md](operational-models/META_WIDGETS.md) | Composable meta-widgets |
| [operational-models/COMPOSITION_PATTERNS.md](operational-models/COMPOSITION_PATTERNS.md) | Anchor and relation patterns |

## Backend reference

| Doc | Purpose |
|-----|---------|
| [backend/app-bundles-v1.md](backend/app-bundles-v1.md) | Manifest v2, App lifecycle, cross-App relations |
| [backend/connectors.md](backend/connectors.md) | Add native or MCP packages to the Connector library ([ADR-010](backend/adr/010-connector-subsystem-architecture.md)) |
| [backend/workspace-agent-profile.md](backend/workspace-agent-profile.md) | Per-workspace resident jvagent skill overlay (base + tenant tiers) |
| [backend/ai-chat.md](backend/ai-chat.md) | Parallel conversation streams, turn lifecycle, WS events ([ADR-005](backend/adr/005-single-worker-until-shared-turn-state.md)) |
| [backend/conversation-use-cases.md](backend/conversation-use-cases.md) | CUCS — conversational scenarios for core + bundle skills, where they live, Integral's assertion namespaces |
| [backend/model-credentials-byok.md](backend/model-credentials-byok.md) | Platform vs per-user BYOK LLM keys (hybrid / strict / operator setup) |
| [backend/operational-model-authoring-and-library.md](backend/operational-model-authoring-and-library.md) | Authoring workflow and library merge |
| [backend/operational-model-packages.md](backend/operational-model-packages.md) | Packages and v1→v2 migration |
| [backend/operational-model-search-index.md](backend/operational-model-search-index.md) | Search index behavior |
| [backend/seeded-apps/content-factory.md](backend/seeded-apps/content-factory.md) | Content Factory reference App |
| [backend/payroll-apps-design.md](backend/payroll-apps-design.md) | Standard design for country-specific payroll apps (tracks, wizard/register pattern, calc engine, collision rules) |

Package setup: [backend/README.md](../backend/README.md). Agent bundle: [agent/README.md](../agent/README.md).

## Operations

| Doc | Purpose |
|-----|---------|
| [ops/DEPLOY.md](ops/DEPLOY.md) | Swarm deploy, worker concurrency, merge-mode observation budgets, ownerless-App backfill |
| [ops/OPERATIONAL_MODEL_SIGNING.md](ops/OPERATIONAL_MODEL_SIGNING.md) | Profile signing for catalog trust |

## Hosted SaaS

Decided shape: Amazon ECS on Fargate. Product entitlements stay in
[product/FOUNDATION_EXTENSION_SAAS.md](product/FOUNDATION_EXTENSION_SAAS.md).

| Doc | Purpose |
|-----|---------|
| [saas/README.md](saas/README.md) | ECS decision, constraints, and reading order |
| [saas/comparison.md](saas/comparison.md) | Why ECS was chosen and why EKS was rejected |
| [saas/high-level-infrastructure.md](saas/high-level-infrastructure.md) | Integral's version of the ECS high-level infrastructure poster |
| [saas/architecture.md](saas/architecture.md) | Platform, planes, request path, onboarding, and wake diagrams |
| [saas/cells.md](saas/cells.md) | Pooled and siloed cells, what may sleep, cost floor |
| [saas/control-plane.md](saas/control-plane.md) | Placement, Stripe, and why this is not the SaaS Builder Toolkit |

## Changelog and contributing

- [CHANGELOG.md](../CHANGELOG.md) — breaking changes and releases
- [CONTRIBUTING.md](../CONTRIBUTING.md) — hooks, tests, PR expectations

## Agent / IDE entry points

- [AGENTS.md](../AGENTS.md) — repo conventions for AI coding agents
